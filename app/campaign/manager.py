"""Campaign_Manager: campaign creation, lifecycle, status, and completion.

The Campaign_Manager owns the Campaign lifecycle and all Campaign_State
transitions (Requirements 1, 2.4 to 2.8, 13, 14, 15, 16.1, 16.2). It validates
creation requests, persists campaigns and their day items, manages approval,
topic edits, pause/resume, completion, and computes status. All business rules
live here; DynamoDB access is delegated to :class:`CampaignStore` and
transition legality to :mod:`app.campaign.state`. Time comes from an injected
:class:`Clock` so the manager stays deterministic and testable.

Design reference: design.md "Campaign_Manager"; Requirements 1, 2, 13, 14, 15,
16.1, 16.2.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.campaign.models import (
    Campaign,
    CampaignDay,
    CampaignState,
    DayState,
)
from app.campaign.state import assert_campaign_transition
from app.campaign.store import CampaignStore
from app.common.clock import Clock
from app.common.errors import (
    StateTransitionError,
    ValidationError,
)

__all__ = [
    "DEFAULT_TIMEZONE",
    "NAME_MAX",
    "DESCRIPTION_MAX",
    "TOPICS_MIN",
    "TOPICS_MAX",
    "TOTAL_DAYS_MIN",
    "TOTAL_DAYS_MAX",
    "CreateCampaignRequest",
    "CampaignStatus",
    "CampaignManager",
]

DEFAULT_TIMEZONE = "Africa/Nairobi"

NAME_MIN = 1
NAME_MAX = 200
DESCRIPTION_MAX = 1000
TOPICS_MIN = 1
TOPICS_MAX = 365
TOTAL_DAYS_MIN = 1
TOTAL_DAYS_MAX = 365

# ISO-8601 calendar date (YYYY-MM-DD) and 24-hour HH:MM. Format-only checks;
# calendar validity of the date is checked separately.
_START_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXECUTION_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class CreateCampaignRequest:
    """A campaign creation request (Requirement 1.1).

    ``timezone`` is optional and defaults to Africa/Nairobi when empty
    (Requirement 1.2). ``topics`` carries the initial (proposed or supplied)
    sequence; its length must equal ``total_days`` for a well-formed campaign.
    """

    name: str
    total_days: int
    start_date: str
    execution_time: str
    topics: tuple[str, ...]
    description: str = ""
    content_style: str = ""
    timezone: str = ""
    notification_email: str = ""
    notification_phone: str | None = None
    publisher_selection: str = ""
    # Optional explicit id. When empty a unique id is generated (Requirement
    # 1.8). A caller may supply a stable id (for example the configured
    # campaign_id the Scheduler targets) so scheduled runs resolve the right
    # campaign. Must be unique; creating over an existing id is rejected.
    campaign_id: str = ""


@dataclass(frozen=True)
class CampaignStatus:
    """Computed campaign status (Requirement 13.1, 13.2).

    Every field is always present; counts default to 0, timestamps and URL
    collections default to empty/None rather than being omitted
    (Requirement 13.2).
    """

    campaign_id: str
    campaign_state: CampaignState
    total_days: int
    current_day: int | None
    completed_days: int
    failed_days: int
    next_execution: str | None
    last_execution: str | None
    publication_urls: tuple[str, ...]


class CampaignManager:
    """Creates and manages campaigns; delegates persistence to the store."""

    def __init__(self, store: CampaignStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    # ------------------------------------------------------------------
    # Creation (Requirement 1)
    # ------------------------------------------------------------------

    def create_campaign(self, request: CreateCampaignRequest) -> Campaign:
        """Validate and persist a new campaign in Campaign_State DRAFT.

        Enforces every field bound (Requirements 1.1, 1.3, 1.4, 1.5, 1.6,
        15.5), defaults the timezone (1.2), assigns a unique id (1.8), records
        ISO-8601 created/updated timestamps (1.7), and writes both the campaign
        META item and one PLANNED day item per topic. On any violation raises a
        :class:`ValidationError` naming every offending field, and persists
        nothing.
        """
        self._validate_create(request)

        now = self._clock.now_iso()
        # Use the caller-supplied id when given (so a Scheduler-targeted
        # campaign resolves), else generate a unique one (Requirement 1.8).
        # A supplied id must not already exist.
        if request.campaign_id.strip():
            campaign_id = request.campaign_id.strip()
            if self._store.get_campaign(campaign_id) is not None:
                raise ValidationError(
                    message=f"campaign '{campaign_id}' already exists",
                    fields=("campaign_id",),
                )
        else:
            campaign_id = str(uuid.uuid4())
        timezone = request.timezone or DEFAULT_TIMEZONE

        campaign = Campaign(
            campaign_id=campaign_id,
            name=request.name,
            description=request.description,
            campaign_state=CampaignState.DRAFT,
            total_days=request.total_days,
            timezone=timezone,
            start_date=request.start_date,
            execution_time=request.execution_time,
            content_style=request.content_style,
            notification_email=request.notification_email,
            notification_phone=request.notification_phone,
            publisher_selection=request.publisher_selection,
            created_at=now,
            updated_at=now,
        )
        self._store.put_campaign(campaign)
        self._write_day_items(campaign_id, request.topics, now)
        return campaign

    def _validate_create(self, request: CreateCampaignRequest) -> None:
        """Collect and raise every creation-request violation at once."""
        offending: list[str] = []

        if not request.name or len(request.name) < NAME_MIN:
            offending.append("name")
        elif len(request.name) > NAME_MAX:
            offending.append("name")

        if len(request.description) > DESCRIPTION_MAX:
            offending.append("description")

        if request.total_days < TOTAL_DAYS_MIN or request.total_days > TOTAL_DAYS_MAX:
            offending.append("total_days")

        if not _START_DATE_RE.match(request.start_date) or not _is_real_date(
            request.start_date
        ):
            offending.append("start_date")

        if not _EXECUTION_TIME_RE.match(request.execution_time):
            offending.append("execution_time")

        topic_count = len(request.topics)
        if topic_count < TOPICS_MIN or topic_count > TOPICS_MAX:
            offending.append("topics")
        elif any(not topic.strip() for topic in request.topics):
            offending.append("topics")
        elif topic_count != request.total_days:
            # A well-formed campaign has exactly one topic per day.
            offending.append("topics")

        if offending:
            # De-duplicate while preserving order.
            seen: dict[str, None] = {}
            for name in offending:
                seen.setdefault(name, None)
            fields = tuple(seen.keys())
            raise ValidationError(
                message=(
                    "campaign creation rejected; invalid or missing fields: "
                    + ", ".join(fields)
                ),
                fields=fields,
            )

    def _write_day_items(
        self, campaign_id: str, topics: tuple[str, ...], now: str
    ) -> None:
        """Persist one PLANNED Campaign_Day per topic (day numbers start at 1)."""
        for index, topic in enumerate(topics, start=1):
            self._store.put_day(
                CampaignDay(
                    campaign_id=campaign_id,
                    day=index,
                    day_state=DayState.PLANNED,
                    topic=topic,
                    updated_at=now,
                )
            )

    # ------------------------------------------------------------------
    # Topic edits and approval (Requirements 2.4 to 2.8)
    # ------------------------------------------------------------------

    def submit_topics(self, campaign_id: str, topics: list[str]) -> Campaign:
        """Replace the stored topic sequence for a DRAFT campaign.

        Replaces the sequence only when its length equals ``total_days`` and
        every entry is non-empty (Requirement 2.6). Otherwise rejects, retains
        the previously stored sequence, and raises a :class:`ValidationError`
        naming the mismatch (Requirement 2.7). Only permitted while the
        campaign is in DRAFT.
        """
        campaign = self._require_campaign(campaign_id)
        if campaign.campaign_state is not CampaignState.DRAFT:
            raise StateTransitionError(
                current_state=campaign.campaign_state.value,
                requested_state=campaign.campaign_state.value,
                scope="campaign",
                message=(
                    "topic edits are permitted only while the campaign is in "
                    f"DRAFT; current state is '{campaign.campaign_state.value}'"
                ),
            )

        if len(topics) != campaign.total_days:
            raise ValidationError(
                message=(
                    f"submitted topic sequence length {len(topics)} does not "
                    f"equal total day count {campaign.total_days}; previous "
                    "sequence retained"
                ),
                fields=("topics",),
            )
        if any(not topic.strip() for topic in topics):
            raise ValidationError(
                message=(
                    "submitted topic sequence contains an empty topic; previous "
                    "sequence retained"
                ),
                fields=("topics",),
            )

        now = self._clock.now_iso()
        self._write_day_items(campaign_id, tuple(topics), now)
        return campaign

    def approve(self, campaign_id: str) -> Campaign:
        """Approve a DRAFT campaign, advancing DRAFT -> APPROVED -> READY.

        Only permitted from DRAFT; any other state is rejected with the current
        state named and preserved (Requirements 2.4, 2.5, 2.8, 16.1, 16.2).
        """
        campaign = self._require_campaign(campaign_id)
        after_approved = self._transition(campaign, CampaignState.APPROVED)
        return self._transition(after_approved, CampaignState.READY)

    # ------------------------------------------------------------------
    # Pause / resume (Requirement 14)
    # ------------------------------------------------------------------

    def pause(self, campaign_id: str) -> Campaign:
        """Pause a RUNNING or READY campaign (Requirements 14.1, 14.4)."""
        campaign = self._require_campaign(campaign_id)
        return self._transition(campaign, CampaignState.PAUSED)

    def resume(self, campaign_id: str) -> Campaign:
        """Resume a PAUSED campaign to RUNNING (Requirements 14.3, 14.5).

        Resume is a stricter operation than the raw READY/PAUSED -> RUNNING
        transitions the state machine allows: it is permitted *only* from
        PAUSED. A resume requested from any other state is rejected with the
        current state named and preserved (Requirement 14.5), even where the
        state machine would otherwise permit a move to RUNNING.
        """
        campaign = self._require_campaign(campaign_id)
        if campaign.campaign_state is not CampaignState.PAUSED:
            raise StateTransitionError(
                current_state=campaign.campaign_state.value,
                requested_state=CampaignState.RUNNING.value,
                scope="campaign",
                message=(
                    "resume is permitted only from PAUSED; current state is "
                    f"'{campaign.campaign_state.value}'"
                ),
            )
        return self._transition(campaign, CampaignState.RUNNING)

    def transition(self, campaign_id: str, target: CampaignState) -> Campaign:
        """Transition a campaign to ``target`` if the move is permitted.

        Enforces the Campaign_State machine (Requirements 16.1, 16.2); rejects
        illegal moves with current and requested state named and the stored
        state preserved.
        """
        campaign = self._require_campaign(campaign_id)
        return self._transition(campaign, target)

    # ------------------------------------------------------------------
    # Completion (Requirement 15.1, 15.3)
    # ------------------------------------------------------------------

    def complete_if_final(self, campaign_id: str, published_day: int) -> Campaign:
        """Set COMPLETED when the published day reaches the total day count.

        Completion is decided by comparing ``published_day`` against the
        campaign's configured ``total_days`` (never a hard-coded count,
        Requirement 15.3). When ``published_day >= total_days`` the campaign is
        transitioned RUNNING -> COMPLETED (Requirement 15.1); otherwise the
        campaign is returned unchanged.
        """
        campaign = self._require_campaign(campaign_id)
        if published_day >= campaign.total_days:
            return self._transition(campaign, CampaignState.COMPLETED)
        return campaign

    # ------------------------------------------------------------------
    # Status (Requirement 13.1, 13.2)
    # ------------------------------------------------------------------

    def status(self, campaign_id: str) -> CampaignStatus:
        """Compute campaign status with every field always present.

        Returns current day (lowest non-terminal day, or None), completed and
        failed day counts, next/last execution timestamps (None when unknown),
        recorded publication URLs, and Campaign_State (Requirements 13.1,
        13.2). Raises :class:`ValidationError` when the campaign does not exist
        (Requirement 13.3), modifying no data.
        """
        campaign = self._require_campaign(campaign_id)
        days = self._store.query_days(campaign_id)

        completed = sum(1 for d in days if d.day_state is DayState.PUBLISHED)
        failed = sum(1 for d in days if d.day_state is DayState.FAILED)

        current_day: int | None = None
        for d in days:
            if d.day_state not in (DayState.PUBLISHED,):
                current_day = d.day
                break

        publication_urls = tuple(
            d.publication_url for d in days if d.publication_url
        )
        last_execution = _max_or_none(
            d.published_at for d in days if d.published_at
        )

        return CampaignStatus(
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.campaign_state,
            total_days=campaign.total_days,
            current_day=current_day,
            completed_days=completed,
            failed_days=failed,
            next_execution=None,
            last_execution=last_execution,
            publication_urls=publication_urls,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_campaign(self, campaign_id: str) -> Campaign:
        """Load a campaign or raise a not-found error (Requirements 13.3, 14.6)."""
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise ValidationError(
                message=f"campaign '{campaign_id}' was not found",
                fields=("campaign_id",),
            )
        return campaign

    def _transition(
        self, campaign: Campaign, target: CampaignState
    ) -> Campaign:
        """Assert legality, apply the guarded conditional write, and reload.

        The transition legality is checked in-memory first (clear error), then
        the store applies a conditional write guarded by the expected current
        state so concurrent writers cannot corrupt state.
        """
        assert_campaign_transition(campaign.campaign_state, target)
        now = self._clock.now_iso()
        self._store.update_campaign_state(
            campaign_id=campaign.campaign_id,
            expected_current=campaign.campaign_state,
            new_state=target,
            updated_at=now,
        )
        return self._require_campaign(campaign.campaign_id)


def _is_real_date(value: str) -> bool:
    """Return True when ``value`` is a real calendar date in YYYY-MM-DD form."""
    from datetime import date

    try:
        year, month, day = (int(part) for part in value.split("-"))
        date(year, month, day)
    except (ValueError, TypeError):
        return False
    return True


def _max_or_none(values: object) -> str | None:
    """Return the maximum ISO-8601 string from an iterable, or None if empty."""
    items = list(values) if hasattr(values, "__iter__") else []
    string_items = [v for v in items if isinstance(v, str)]
    return max(string_items) if string_items else None
