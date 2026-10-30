"""Orchestrator: coordinate one campaign-day execution end to end.

The Orchestrator is pure coordination: it selects the eligible day, drives the
Day_State machine, and delegates every business rule to the injected
collaborators (store, generator, validator, publisher, notifier, clock,
metrics). It never contains generation, validation, or publishing logic itself
(design "Orchestrator"). Each stage records state through the store's
conditional writes so the state machine and idempotency are enforced at the
data layer.

Execution flow for one run (Requirements 3, 4, 5, 7, 9, 12, 15):

1. Select the lowest-numbered PLANNED or FAILED day, preferring FAILED on a tie
   (Requirements 3.1, 3.2). No eligible day, or a campaign already at/over its
   total day count, ends without publishing (Requirements 3.5, 3.6).
2. Require the approved topic; a missing topic ends without publishing and
   leaves state unchanged (Requirement 3.4).
3. GENERATING -> generate -> GENERATED, or FAILED on generation failure
   (Requirements 4.1, 4.8, 4.9).
4. Validate; on pass GENERATED -> READY, on any failure FAILED with the article
   retained and a failure notification listing every violation
   (Requirements 5.13, 5.14).
5. READY -> PUBLISHING -> publish with bounded exponential-backoff retries and
   ambiguous-result verification; on success mark PUBLISHED via a conditional
   write, record the URL and timestamp, advance/complete, and notify success
   (Requirements 7, 9, 12). Auth/secret failures fail safe with no retry
   (Requirements 8.4, 8.5, 12.6).

Design reference: design.md "Orchestrator", "Idempotency Design", "Error
Handling"; Requirements 3, 4, 5, 7, 8.4, 8.5, 9, 12, 15.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from app.campaign.idempotency import derive_idempotency_key
from app.campaign.manager import CampaignManager
from app.campaign.models import Campaign, CampaignDay, CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.clock import Clock
from app.common.errors import (
    AuthenticationError,
    GenerationError,
    IdempotencyConflict,
    PublicationError,
)
from app.common.metrics import MetricName, MetricsEmitter
from app.generation.generator import ContentGenerator, ThreadTimeoutRunner, TimeoutRunner
from app.generation.models import GenerationSpec
from app.notify.notifier import NotificationContext, Notifier
from app.publishing.adapter import PublicationResult, PublishingAdapter
from app.validation.validator import ContentValidator

__all__ = [
    "MAX_PUBLISH_ATTEMPTS",
    "MAX_WRITE_RETRIES",
    "ERROR_SUMMARY_MAX",
    "ExecutionOutcome",
    "ExecutionResult",
    "Orchestrator",
]

# Bounded publication attempts with exponential backoff (Requirement 12.2, 12.5).
MAX_PUBLISH_ATTEMPTS = 5
# Bounded retries for a non-duplicate PUBLISHED conditional-write failure
# (Requirement 9.5).
MAX_WRITE_RETRIES = 3
# Sanitized error summary length recorded on a FAILED day (Requirement 7.5).
ERROR_SUMMARY_MAX = 500
# Backoff base seconds; doubles each attempt (Requirement 12.2).
_BACKOFF_BASE_SECONDS = 1.0
# Hard bound on a single publish attempt (Requirement 7.6).
PUBLISH_TIMEOUT_SECONDS = 60.0


class ExecutionOutcome(str, Enum):
    """The terminal outcome of one Orchestrator run."""

    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    NO_ELIGIBLE_DAY = "NO_ELIGIBLE_DAY"
    MISSING_TOPIC = "MISSING_TOPIC"
    ALREADY_COMPLETE = "ALREADY_COMPLETE"
    DUPLICATE_PREVENTED = "DUPLICATE_PREVENTED"


@dataclass(frozen=True)
class ExecutionResult:
    """A structured summary of one run for callers and tests."""

    outcome: ExecutionOutcome
    day: int | None = None
    publication_url: str = ""
    error_summary: str = ""


class Sleeper(Protocol):
    """Injectable sleep so backoff is real in production and instant in tests."""

    def __call__(self, seconds: float) -> None:
        """Sleep for ``seconds``."""
        ...


class VerifyingPublisher(Protocol):
    """A publisher that can also verify an ambiguous result (Req 12.3, 12.4)."""

    def publish_article(self, article: object) -> PublicationResult: ...

    def verify_published(self, article: object) -> bool: ...


class Orchestrator:
    """Coordinates a single campaign-day execution (Requirements 3, 4, 5, 7)."""

    def __init__(
        self,
        store: CampaignStore,
        manager: CampaignManager,
        generator: ContentGenerator,
        validator: ContentValidator,
        publisher: PublishingAdapter,
        notifier: Notifier,
        clock: Clock,
        metrics: MetricsEmitter,
        topic_arn: str,
        sleeper: Sleeper | None = None,
        publish_timeout_runner: TimeoutRunner | None = None,
    ) -> None:
        self._store = store
        self._manager = manager
        self._generator = generator
        self._validator = validator
        self._publisher = publisher
        self._notifier = notifier
        self._clock = clock
        self._metrics = metrics
        self._topic_arn = topic_arn
        self._sleep: Sleeper = sleeper or (lambda seconds: time.sleep(seconds))
        self._publish_timeout: TimeoutRunner = (
            publish_timeout_runner or ThreadTimeoutRunner()
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, campaign_id: str) -> ExecutionResult:
        """Run one campaign-day execution and return its outcome.

        Selects the eligible day and drives it through generation, validation,
        and publication. Never publishes twice and never raises on an expected
        domain failure; unexpected errors propagate for the Lambda to surface.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            return ExecutionResult(ExecutionOutcome.NO_ELIGIBLE_DAY)

        days = self._store.query_days(campaign_id)

        # Completion check by configured total_days, never a hard-coded count
        # (Requirements 3.6, 15.3).
        highest_published = max(
            (d.day for d in days if d.day_state is DayState.PUBLISHED),
            default=0,
        )
        if highest_published >= campaign.total_days:
            return ExecutionResult(ExecutionOutcome.ALREADY_COMPLETE)

        selected = self.select_eligible_day(days)
        if selected is None:
            return ExecutionResult(ExecutionOutcome.NO_ELIGIBLE_DAY)

        if not selected.topic.strip():
            # Missing approved topic: end without publishing, state unchanged
            # (Requirement 3.4).
            return ExecutionResult(
                ExecutionOutcome.MISSING_TOPIC, day=selected.day
            )

        # First scheduled or manual run moves a READY campaign to RUNNING
        # (design State Machines: "READY -> RUNNING"). This must happen before
        # the day publishes so that final-day completion can transition
        # RUNNING -> COMPLETED (Requirement 15.1); publishing the final day
        # while still READY would otherwise leave the campaign uncompletable.
        if campaign.campaign_state is CampaignState.READY:
            campaign = self._manager.transition(campaign_id, CampaignState.RUNNING)

        # Resilience: an unexpected error mid-pipeline must never leave the day
        # stranded in a non-terminal, non-eligible state (GENERATING/GENERATED/
        # READY/PUBLISHING). Any such state is not retryable (retry runs only
        # from FAILED). So on any unexpected exception we force the day to
        # FAILED from its current stored state, keeping it recoverable, then
        # re-raise so the failure is still visible to the caller/logs.
        try:
            return self._run_day(campaign, selected)
        except Exception:  # noqa: BLE001 - convert to a recoverable FAILED day
            self._force_fail(campaign, selected.day)
            raise

    # ------------------------------------------------------------------
    # Eligible-day selection (Requirements 3.1, 3.2, 3.6)
    # ------------------------------------------------------------------

    @staticmethod
    def select_eligible_day(days: list[CampaignDay]) -> CampaignDay | None:
        """Return the lowest-numbered PLANNED or FAILED day, FAILED first.

        Among all eligible days the lowest day number wins; when a PLANNED and
        a FAILED day share that number, the FAILED one is chosen so retries are
        preferred (Requirements 3.1, 3.2). Returns None when none are eligible.
        """
        eligible = [
            d for d in days if d.day_state in (DayState.PLANNED, DayState.FAILED)
        ]
        if not eligible:
            return None
        lowest = min(d.day for d in eligible)
        at_lowest = [d for d in eligible if d.day == lowest]
        for day in at_lowest:
            if day.day_state is DayState.FAILED:
                return day
        return at_lowest[0]

    # ------------------------------------------------------------------
    # Per-day pipeline
    # ------------------------------------------------------------------

    def _run_day(self, campaign: Campaign, day: CampaignDay) -> ExecutionResult:
        """Drive one selected day through generate -> validate -> publish."""
        # Retry from FAILED transitions back to GENERATING (Requirement 16.5);
        # a fresh PLANNED day starts from PLANNED.
        current_state = day.day_state

        # --- Generation (Requirements 4.1, 4.8, 4.9) ------------------
        self._store.update_day_state(
            campaign.campaign_id, day.day, current_state, DayState.GENERATING,
            self._clock.now_iso(),
        )
        try:
            article = self._generator.generate(
                GenerationSpec(
                    campaign_id=campaign.campaign_id,
                    day=day.day,
                    topic=day.topic,
                )
            )
        except GenerationError as error:
            return self._fail(
                campaign, day, DayState.GENERATING, "generation", error.message
            )

        self._metrics.emit(MetricName.ARTICLES_GENERATED)
        # Persist the generated article and move to GENERATED. CampaignDay has
        # no learning_takeaway field, so persist the supported article fields.
        self._store.put_day(
            CampaignDay(
                campaign_id=campaign.campaign_id,
                day=day.day,
                day_state=DayState.GENERATED,
                topic=day.topic,
                title=article.title,
                description=article.description,
                body_markdown=article.body_markdown,
                tags=article.tags,
                attempt_count=day.attempt_count,
                idempotency_key=derive_idempotency_key(
                    campaign.campaign_id, day.day
                ),
                updated_at=self._clock.now_iso(),
            )
        )

        # --- Validation (Requirements 5.13, 5.14) ---------------------
        existing = self._store.get_day(campaign.campaign_id, day.day)
        existing_state = existing.day_state if existing else None
        result = self._validator.validate(article, day.topic, existing_state)
        if not result.is_valid:
            reason = "; ".join(v.message for v in result.violations)
            return self._fail(
                campaign, day, DayState.GENERATED, "validation", reason
            )
        self._store.update_day_state(
            campaign.campaign_id, day.day, DayState.GENERATED, DayState.READY,
            self._clock.now_iso(),
        )

        # --- Publication (Requirements 7, 9, 12) ----------------------
        return self._publish(campaign, day, article)

    # ------------------------------------------------------------------
    # Publication with retry / backoff / verify / fail-safe
    # ------------------------------------------------------------------

    def _publish(
        self, campaign: Campaign, day: CampaignDay, article: object
    ) -> ExecutionResult:
        """Publish with bounded backoff, ambiguous verification, and fail-safe."""
        self._store.update_day_state(
            campaign.campaign_id, day.day, DayState.READY, DayState.PUBLISHING,
            self._clock.now_iso(),
        )

        last_error = "publication failed"
        for attempt in range(1, MAX_PUBLISH_ATTEMPTS + 1):
            # Increment the attempt count before each attempt (Requirement 12.7).
            self._store.increment_attempt_count(
                campaign.campaign_id, day.day, self._clock.now_iso()
            )
            try:
                pub = self._publish_timeout.run(
                    PUBLISH_TIMEOUT_SECONDS,
                    lambda: self._publisher.publish_article(article),  # type: ignore[arg-type]
                )
            except TimeoutError:
                # No result within 60s: determinate publication timeout, no
                # retry of this attempt beyond the bounded loop (Requirement 7.6).
                return self._fail(
                    campaign, day, DayState.PUBLISHING, "publishing",
                    "publication timed out after 60 seconds",
                )
            except AuthenticationError as error:
                # Fail safe: no automatic retry (Requirements 8.5, 12.6).
                self._metrics.emit(MetricName.AUTHENTICATION_FAILURES)
                return self._fail(
                    campaign, day, DayState.PUBLISHING, "authentication",
                    error.message,
                    remediation=error.remediation or "Re-authenticate and retry.",
                )
            except PublicationError as error:
                last_error = error.error_summary or error.message
                if error.transient and attempt < MAX_PUBLISH_ATTEMPTS:
                    self._backoff(attempt)
                    continue
                return self._fail(
                    campaign, day, DayState.PUBLISHING, "publishing", last_error
                )

            # Ambiguous result: verify before any re-publish (Req 12.3, 12.4).
            if pub.ambiguous:
                if self._verify_published(article):
                    return self._record_published(campaign, day, pub.publication_url)
                last_error = pub.error_summary or "ambiguous publication result"
                if attempt < MAX_PUBLISH_ATTEMPTS:
                    self._backoff(attempt)
                    continue
                return self._fail(
                    campaign, day, DayState.PUBLISHING, "publishing", last_error
                )

            if pub.is_success:
                return self._record_published(campaign, day, pub.publication_url)

            # Determinate failure: no retry (Requirement 7.5).
            last_error = pub.error_summary or "publication failed"
            return self._fail(
                campaign, day, DayState.PUBLISHING, "publishing", last_error
            )

        # Exhausted attempts (Requirement 12.5).
        return self._fail(
            campaign, day, DayState.PUBLISHING, "publishing",
            f"maximum retry count ({MAX_PUBLISH_ATTEMPTS}) reached: {last_error}",
        )

    def _record_published(
        self, campaign: Campaign, day: CampaignDay, url: str
    ) -> ExecutionResult:
        """Conditionally mark PUBLISHED, record outcome, complete, notify.

        Uses the store's conditional PUBLISHED write. If it is blocked because
        the day is already PUBLISHED, treat it as a prevented duplicate: record
        one DuplicateRunsPrevented and end without republishing (Requirements
        9.3, 9.4). Non-duplicate write failures are retried up to 3 times
        (Requirement 9.5).
        """
        now = self._clock.now_iso()
        for write_attempt in range(1, MAX_WRITE_RETRIES + 1):
            try:
                self._store.mark_published(
                    campaign.campaign_id, day.day, url, now, now
                )
                break
            except IdempotencyConflict:
                self._metrics.emit(MetricName.DUPLICATE_RUNS_PREVENTED)
                return ExecutionResult(
                    ExecutionOutcome.DUPLICATE_PREVENTED,
                    day=day.day,
                    publication_url=url,
                )
            except Exception:  # noqa: BLE001 - non-duplicate write failure
                if write_attempt >= MAX_WRITE_RETRIES:
                    return self._fail(
                        campaign, day, DayState.PUBLISHING, "publishing",
                        f"failed to record PUBLISHED for day {day.day}",
                    )
                continue

        self._metrics.emit(MetricName.ARTICLES_PUBLISHED)

        # Advance/complete by configured total_days (Requirements 7.7, 15.1).
        completed = self._manager.complete_if_final(campaign.campaign_id, day.day)
        if completed.campaign_state.value == "COMPLETED":
            self._metrics.emit(MetricName.CAMPAIGNS_COMPLETED)

        self._notifier.notify_success(
            NotificationContext(
                campaign_id=campaign.campaign_id,
                day=day.day,
                topic_arn=self._topic_arn,
                campaign_name=campaign.name,
                title=day.title or "",
                publication_url=url,
            )
        )
        return ExecutionResult(
            ExecutionOutcome.PUBLISHED, day=day.day, publication_url=url
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _verify_published(self, article: object) -> bool:
        """Verify an ambiguous result if the publisher supports it, else False."""
        verify = getattr(self._publisher, "verify_published", None)
        if callable(verify):
            try:
                return bool(verify(article))
            except Exception:  # noqa: BLE001 - treat verification failure as unknown
                return False
        return False

    def _backoff(self, attempt: int) -> None:
        """Sleep with exponential backoff: 1s, 2s, 4s, ... (Requirement 12.2)."""
        self._sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    def _fail(
        self,
        campaign: Campaign,
        day: CampaignDay,
        from_state: DayState,
        stage: str,
        reason: str,
        remediation: str = "",
    ) -> ExecutionResult:
        """Mark the day FAILED, record the sanitized error, and notify."""
        summary = reason[:ERROR_SUMMARY_MAX]
        if stage == "generation":
            self._metrics.emit(MetricName.GENERATION_FAILURES)
        elif stage == "publishing":
            self._metrics.emit(MetricName.PUBLICATION_FAILURES)

        self._store.update_day_state(
            campaign.campaign_id, day.day, from_state, DayState.FAILED,
            self._clock.now_iso(), last_error=summary,
        )
        self._notifier.notify_failure(
            NotificationContext(
                campaign_id=campaign.campaign_id,
                day=day.day,
                topic_arn=self._topic_arn,
                campaign_name=campaign.name,
                stage=stage,
                error_reason=summary,
                remediation=remediation,
            )
        )
        return ExecutionResult(
            ExecutionOutcome.FAILED, day=day.day, error_summary=summary
        )

    # Non-terminal day states from which FAILED is reachable (design State
    # Machines): a crash in any of these must be converted to FAILED so the
    # day stays retryable.
    _NON_TERMINAL_STATES = frozenset(
        {
            DayState.GENERATING,
            DayState.GENERATED,
            DayState.READY,
            DayState.PUBLISHING,
        }
    )

    def _force_fail(self, campaign: Campaign, day_number: int) -> None:
        """Force a stranded day to FAILED after an unexpected error.

        Reads the day's current stored state and, when it is a non-terminal
        state, transitions it to FAILED so it is recoverable via retry rather
        than stuck (retry runs only from FAILED). This runs inside exception
        handling, so it must never raise: any error here is swallowed and the
        original exception is left to propagate. A best-effort failure
        notification is sent.
        """
        try:
            stored = self._store.get_day(campaign.campaign_id, day_number)
            if stored is None or stored.day_state not in self._NON_TERMINAL_STATES:
                return
            self._store.update_day_state(
                campaign.campaign_id,
                day_number,
                stored.day_state,
                DayState.FAILED,
                self._clock.now_iso(),
                last_error="unexpected error during execution",
            )
            self._metrics.emit(MetricName.PUBLICATION_FAILURES)
            self._notifier.notify_failure(
                NotificationContext(
                    campaign_id=campaign.campaign_id,
                    day=day_number,
                    topic_arn=self._topic_arn,
                    campaign_name=campaign.name,
                    stage=stored.day_state.value.lower(),
                    error_reason="unexpected error during execution",
                    remediation="Review the logs and retry the day.",
                )
            )
        except Exception:  # noqa: BLE001 - recovery must not mask the original error
            return
