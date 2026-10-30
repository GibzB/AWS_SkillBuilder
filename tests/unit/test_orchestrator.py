"""Unit and property tests for the Orchestrator.

Feature: aws-builder-automation
Feature: aws-builder-automation, Property 3
Feature: aws-builder-automation, Property 5
Feature: aws-builder-automation, Property 7
Feature: aws-builder-automation, Property 12
Feature: aws-builder-automation, Property 20
Feature: aws-builder-automation, Property 21
Feature: aws-builder-automation, Property 22

Validates: Requirements 3.1, 3.2, 5.14, 7.5, 8.5, 9.2, 9.3, 9.4, 9.5, 12.2,
12.3, 12.4, 12.5, 12.6, 12.7.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignDay, CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.errors import AuthenticationError, GenerationError, PublicationError
from app.generation.models import Article
from app.notify.notifier import Notifier
from app.orchestrator.orchestrator import (
    MAX_PUBLISH_ATTEMPTS,
    ExecutionOutcome,
    Orchestrator,
)
from app.publishing.adapter import PublicationResult
from app.validation.validator import ValidationResult, ValidationViolation
from tests.conftest import (
    AlwaysTimeoutRunner,
    FakeDynamoDBClient,
    FakeSNSClient,
    FixedClock,
    ImmediateTimeoutRunner,
    NoSleep,
    RecordingMetrics,
)

pytestmark = [pytest.mark.unit]


# ----------------------------------------------------------------------
# Fakes for injected collaborators
# ----------------------------------------------------------------------


def _article(campaign_id: str, day: int, topic: str) -> Article:
    return Article(
        campaign_id=campaign_id,
        day=day,
        topic=topic,
        title=f"Learning {topic}",
        description="A short honest note.",
        body_markdown=f"# {topic}\n\nOne thing I learned about {topic} is useful.",
        tags=("aws",),
        learning_takeaway="Keep it simple.",
    )


class FakeGenerator:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def generate(self, spec: object) -> Article:
        if self._error is not None:
            raise self._error
        s = spec
        return _article(s.campaign_id, s.day, s.topic)  # type: ignore[attr-defined]


class PassValidator:
    def validate(
        self, article: object, approved_topic: str, existing: object
    ) -> ValidationResult:
        return ValidationResult(is_valid=True, violations=())


class FailValidator:
    def __init__(self) -> None:
        self._violations = (
            ValidationViolation("TITLE_TOO_LONG", "title", "title too long"),
            ValidationViolation("TAG_COUNT_INVALID", "tags", "too many tags"),
        )

    def validate(
        self, article: object, approved_topic: str, existing: object
    ) -> ValidationResult:
        return ValidationResult(is_valid=False, violations=self._violations)


class ScriptedPublisher:
    """Returns queued results or raises queued exceptions, in order."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls = 0
        self.verify_returns = False
        self.verify_calls = 0

    def publish_article(self, article: object) -> PublicationResult:
        self.calls += 1
        item = self._script.pop(0) if self._script else PublicationResult.success("u")
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    def verify_published(self, article: object) -> bool:
        self.verify_calls += 1
        return self.verify_returns


def _seed_campaign(
    client: FakeDynamoDBClient, topics: list[str], state: CampaignState
) -> tuple[CampaignStore, CampaignManager, str]:
    store = CampaignStore(client, "t")
    manager = CampaignManager(store, FixedClock())
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="Test",
            total_days=len(topics),
            start_date="2026-01-01",
            execution_time="09:00",
            topics=tuple(topics),
        )
    )
    # Drive to the requested runtime state.
    manager.approve(campaign.campaign_id)  # READY
    if state in (CampaignState.RUNNING, CampaignState.COMPLETED):
        manager.transition(campaign.campaign_id, CampaignState.RUNNING)
    return store, manager, campaign.campaign_id


def _orchestrator(
    store: CampaignStore,
    manager: CampaignManager,
    generator: object,
    validator: object,
    publisher: object,
    sns: FakeSNSClient | None = None,
    metrics: RecordingMetrics | None = None,
    sleeper: NoSleep | None = None,
) -> Orchestrator:
    return Orchestrator(
        store=store,
        manager=manager,
        generator=generator,  # type: ignore[arg-type]
        validator=validator,  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        notifier=Notifier(sns or FakeSNSClient(), FixedClock()),
        clock=FixedClock(),
        metrics=metrics or RecordingMetrics(),
        topic_arn="arn:aws:sns:::t",
        sleeper=sleeper or NoSleep(),
        publish_timeout_runner=ImmediateTimeoutRunner(),
    )


# ----------------------------------------------------------------------
# Property 12: eligible-day selection
# ----------------------------------------------------------------------


def _day(day: int, state: DayState) -> CampaignDay:
    return CampaignDay(
        campaign_id="c", day=day, day_state=state, topic="t", updated_at="now"
    )


@settings(max_examples=200)
@given(
    states=st.lists(
        st.sampled_from(list(DayState)), min_size=1, max_size=8
    )
)
def test_property_12_selection(states: list[DayState]) -> None:
    days = [_day(i + 1, s) for i, s in enumerate(states)]
    selected = Orchestrator.select_eligible_day(days)
    eligible = [d for d in days if d.day_state in (DayState.PLANNED, DayState.FAILED)]
    if not eligible:
        assert selected is None
        return
    lowest = min(d.day for d in eligible)
    assert selected is not None
    assert selected.day == lowest
    # FAILED preferred when both share the lowest number.
    at_lowest = {d.day_state for d in eligible if d.day == lowest}
    if DayState.FAILED in at_lowest:
        assert selected.day_state is DayState.FAILED


# ----------------------------------------------------------------------
# Happy path + Property 3 (never published twice)
# ----------------------------------------------------------------------


def test_happy_path_publishes_and_completes() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    metrics = RecordingMetrics()
    orch = _orchestrator(
        store, manager, FakeGenerator(), PassValidator(),
        ScriptedPublisher([PublicationResult.success("https://mock/a")]),
        metrics=metrics,
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    day = store.get_day(cid, 1)
    assert day is not None and day.day_state is DayState.PUBLISHED
    assert day.publication_url == "https://mock/a"
    assert store.get_campaign(cid).campaign_state is CampaignState.COMPLETED  # type: ignore[union-attr]
    assert metrics.count("ArticlesPublished") == 1
    assert metrics.count("CampaignsCompleted") == 1


def test_property_3_second_run_prevents_duplicate() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM", "S3"], CampaignState.RUNNING)
    metrics = RecordingMetrics()
    pub = ScriptedPublisher(
        [PublicationResult.success("u1"), PublicationResult.success("u1")]
    )
    orch = _orchestrator(store, manager, FakeGenerator(), PassValidator(), pub, metrics=metrics)
    orch.execute(cid)  # publishes day 1
    published_day = store.get_day(cid, 1)
    # Force a redundant run against the already-PUBLISHED day 1 by making it the
    # only eligible day again is not possible (it's PUBLISHED). Instead, re-run
    # _record_published directly to prove the conditional write blocks.
    from app.orchestrator.orchestrator import ExecutionOutcome as EO

    result = orch._record_published(store.get_campaign(cid), published_day, "u1")  # type: ignore[arg-type]
    assert result.outcome is EO.DUPLICATE_PREVENTED
    assert metrics.count("DuplicateRunsPrevented") == 1
    # State unchanged.
    assert store.get_day(cid, 1).day_state is DayState.PUBLISHED  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# Property 7: validation failure
# ----------------------------------------------------------------------


def test_property_7_validation_failure() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    sns = FakeSNSClient()
    orch = _orchestrator(
        store, manager, FakeGenerator(), FailValidator(),
        ScriptedPublisher([]), sns=sns,
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    day = store.get_day(cid, 1)
    assert day is not None and day.day_state is DayState.FAILED
    # Article retained.
    assert day.title
    # Failure notification lists every violation message.
    import json

    msg = json.loads(sns.calls[0]["Message"])
    assert "title too long" in msg["reason"]
    assert "too many tags" in msg["reason"]


# ----------------------------------------------------------------------
# Generation failure
# ----------------------------------------------------------------------


def test_generation_failure_marks_failed_no_publish() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    pub = ScriptedPublisher([])
    orch = _orchestrator(
        store, manager,
        FakeGenerator(GenerationError("bedrock down", reason="unavailable")),
        PassValidator(), pub,
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert pub.calls == 0  # never published
    assert store.get_day(cid, 1).day_state is DayState.FAILED  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# Property 20: auth failure fails safe, no retry
# ----------------------------------------------------------------------


def test_property_20_auth_failure_no_retry() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    pub = ScriptedPublisher([AuthenticationError("session expired", remediation="re-auth")])
    metrics = RecordingMetrics()
    orch = _orchestrator(store, manager, FakeGenerator(), PassValidator(), pub, metrics=metrics)
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert pub.calls == 1  # no automatic retry (8.5, 12.6)
    assert metrics.count("AuthenticationFailures") == 1


# ----------------------------------------------------------------------
# Property 21: bounded backoff + attempt accounting
# ----------------------------------------------------------------------


def test_property_21_transient_backoff_and_max_attempts() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    transient = PublicationError("temporary", transient=True, error_summary="temp")
    pub = ScriptedPublisher([transient] * MAX_PUBLISH_ATTEMPTS)
    sleeper = NoSleep()
    orch = _orchestrator(
        store, manager, FakeGenerator(), PassValidator(), pub, sleeper=sleeper
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert pub.calls == MAX_PUBLISH_ATTEMPTS  # exactly 5 attempts (12.2, 12.5)
    # Backoff doubles: 1, 2, 4, 8 (between the 5 attempts).
    assert sleeper.delays == [1.0, 2.0, 4.0, 8.0]
    # Attempt count recorded per attempt (12.7).
    assert store.get_day(cid, 1).attempt_count == MAX_PUBLISH_ATTEMPTS  # type: ignore[union-attr]


def test_transient_then_success_recovers() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    transient = PublicationError("temp", transient=True, error_summary="t")
    pub = ScriptedPublisher([transient, PublicationResult.success("u-final")])
    orch = _orchestrator(store, manager, FakeGenerator(), PassValidator(), pub, sleeper=NoSleep())
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert result.publication_url == "u-final"
    assert pub.calls == 2


# ----------------------------------------------------------------------
# Property 22: ambiguous result verified before re-publish
# ----------------------------------------------------------------------


def test_property_22_ambiguous_verified_as_published() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    ambiguous = PublicationResult(status="FAILURE", error_summary="?", ambiguous=True)
    pub = ScriptedPublisher([ambiguous])
    pub.verify_returns = True  # verification confirms it was published
    orch = _orchestrator(store, manager, FakeGenerator(), PassValidator(), pub, sleeper=NoSleep())
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert pub.verify_calls == 1  # verified before any re-publish (12.3, 12.4)
    assert pub.calls == 1  # no second publish attempt


# ----------------------------------------------------------------------
# No eligible day / missing topic / already complete
# ----------------------------------------------------------------------


def test_no_eligible_day_when_all_published() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    orch = _orchestrator(
        store, manager, FakeGenerator(), PassValidator(),
        ScriptedPublisher([PublicationResult.success("u")]),
    )
    orch.execute(cid)  # publishes the only day -> complete
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.ALREADY_COMPLETE


def test_publication_timeout_marks_failed() -> None:
    """A publish that never returns within 60s is a determinate failure (7.6)."""
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    pub = ScriptedPublisher([PublicationResult.success("u")])
    orch = Orchestrator(
        store=store,
        manager=manager,
        generator=FakeGenerator(),  # type: ignore[arg-type]
        validator=PassValidator(),  # type: ignore[arg-type]
        publisher=pub,
        notifier=Notifier(FakeSNSClient(), FixedClock()),
        clock=FixedClock(),
        metrics=RecordingMetrics(),
        topic_arn="arn:aws:sns:::t",
        sleeper=NoSleep(),
        publish_timeout_runner=AlwaysTimeoutRunner(),
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert "timed out" in result.error_summary
    assert store.get_day(cid, 1).day_state is DayState.FAILED  # type: ignore[union-attr]


def test_unexpected_error_leaves_day_failed_not_stuck() -> None:
    """Resilience: an unexpected crash mid-pipeline must leave the day FAILED.

    Regression for a live bug where a KeyError during generation left the day
    stranded in GENERATING (not retryable). The orchestrator now forces the day
    to FAILED and re-raises.
    """
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)

    class BoomGenerator:
        def generate(self, spec: object) -> Article:
            raise KeyError("unexpected template failure")  # NOT a GenerationError

    orch = _orchestrator(
        store, manager, BoomGenerator(), PassValidator(), ScriptedPublisher([])
    )
    with pytest.raises(KeyError):
        orch.execute(cid)
    # The day was forced to FAILED, so it remains retryable (not GENERATING).
    day = store.get_day(cid, 1)
    assert day is not None
    assert day.day_state is DayState.FAILED


def test_run_from_ready_transitions_running_then_completes() -> None:
    """A manual run from READY moves to RUNNING then COMPLETED on the final day.

    Regression for a live bug: publishing the final day while the campaign was
    still READY left completion (RUNNING -> COMPLETED) impossible.
    """
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.READY)
    orch = _orchestrator(
        store, manager, FakeGenerator(), PassValidator(),
        ScriptedPublisher([PublicationResult.success("https://mock/a")]),
    )
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert store.get_campaign(cid).campaign_state is CampaignState.COMPLETED  # type: ignore[union-attr]


def test_missing_topic_ends_without_publishing() -> None:
    client = FakeDynamoDBClient()
    store, manager, cid = _seed_campaign(client, ["IAM"], CampaignState.RUNNING)
    # Blank the topic on day 1.
    day = store.get_day(cid, 1)
    assert day is not None
    store.put_day(replace(day, topic="   "))
    pub = ScriptedPublisher([])
    orch = _orchestrator(store, manager, FakeGenerator(), PassValidator(), pub)
    result = orch.execute(cid)
    assert result.outcome is ExecutionOutcome.MISSING_TOPIC
    assert pub.calls == 0
    assert store.get_day(cid, 1).day_state is DayState.PLANNED  # type: ignore[union-attr]
