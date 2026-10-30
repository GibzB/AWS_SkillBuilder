"""Campaign_State and Day_State transition rules (Requirement 16).

The allowed transitions are encoded as explicit, inspectable data: a mapping
from each state to the frozen set of states it may transition to. This keeps
the state machines auditable and testable (the property tests in task 2.3 can
enumerate the tables) rather than hiding the rules inside a chain of
conditionals.

All functions here are pure: they read the transition tables and either return
a boolean or raise :class:`~app.common.errors.StateTransitionError`. They never
mutate any state; preserving the stored state on rejection is the caller's
responsibility, and is trivially satisfied because a rejected transition simply
never writes (Requirements 16.2, 16.4).

Design reference: design.md "State Machines"; Requirements 16.1-16.6.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from app.campaign.models import CampaignState, DayState
from app.common.errors import StateTransitionError

__all__ = [
    "CAMPAIGN_TRANSITIONS",
    "DAY_TRANSITIONS",
    "CAMPAIGN_SCOPE",
    "DAY_SCOPE",
    "is_allowed_campaign_transition",
    "assert_campaign_transition",
    "is_allowed_day_transition",
    "assert_day_transition",
    "assert_day_retry",
]

CAMPAIGN_SCOPE = "campaign"
DAY_SCOPE = "day"


def _freeze(
    table: Mapping[CampaignState, frozenset[CampaignState]]
    | Mapping[DayState, frozenset[DayState]],
) -> MappingProxyType:  # type: ignore[type-arg]
    """Wrap a transition table in a read-only view so it cannot be mutated."""
    return MappingProxyType(dict(table))


# ---------------------------------------------------------------------------
# Campaign_State transition table (Requirements 16.1, 16.2)
#
# DRAFT     -> APPROVED
# APPROVED  -> READY
# READY     -> RUNNING, PAUSED
# RUNNING   -> PAUSED, COMPLETED
# PAUSED    -> RUNNING
# COMPLETED -> (terminal)
# ---------------------------------------------------------------------------
CAMPAIGN_TRANSITIONS: Mapping[CampaignState, frozenset[CampaignState]] = _freeze(
    {
        CampaignState.DRAFT: frozenset({CampaignState.APPROVED}),
        CampaignState.APPROVED: frozenset({CampaignState.READY}),
        CampaignState.READY: frozenset(
            {CampaignState.RUNNING, CampaignState.PAUSED}
        ),
        CampaignState.RUNNING: frozenset(
            {CampaignState.PAUSED, CampaignState.COMPLETED}
        ),
        CampaignState.PAUSED: frozenset({CampaignState.RUNNING}),
        CampaignState.COMPLETED: frozenset(),
    }
)


# ---------------------------------------------------------------------------
# Day_State transition table (Requirements 16.3, 16.4, 16.5, 16.6)
#
# PLANNED    -> GENERATING
# GENERATING -> GENERATED, FAILED
# GENERATED  -> READY, FAILED
# READY      -> PUBLISHING, FAILED
# PUBLISHING -> PUBLISHED, FAILED
# PUBLISHED  -> (terminal)
# FAILED     -> GENERATING (retry only)
# ---------------------------------------------------------------------------
DAY_TRANSITIONS: Mapping[DayState, frozenset[DayState]] = _freeze(
    {
        DayState.PLANNED: frozenset({DayState.GENERATING}),
        DayState.GENERATING: frozenset({DayState.GENERATED, DayState.FAILED}),
        DayState.GENERATED: frozenset({DayState.READY, DayState.FAILED}),
        DayState.READY: frozenset({DayState.PUBLISHING, DayState.FAILED}),
        DayState.PUBLISHING: frozenset({DayState.PUBLISHED, DayState.FAILED}),
        DayState.PUBLISHED: frozenset(),
        DayState.FAILED: frozenset({DayState.GENERATING}),
    }
)


def is_allowed_campaign_transition(
    current: CampaignState, requested: CampaignState
) -> bool:
    """Return whether ``current -> requested`` is a permitted campaign move.

    Pure lookup against :data:`CAMPAIGN_TRANSITIONS`; performs no mutation
    (Requirement 16.1).
    """
    return requested in CAMPAIGN_TRANSITIONS[current]


def assert_campaign_transition(
    current: CampaignState, requested: CampaignState
) -> None:
    """Validate a campaign transition, raising on rejection.

    Raises :class:`StateTransitionError` (``scope="campaign"``) naming the
    current and requested state when the move is not permitted; the stored
    state is left unchanged because no write occurs (Requirement 16.2).
    """
    if not is_allowed_campaign_transition(current, requested):
        raise StateTransitionError(
            current_state=current.value,
            requested_state=requested.value,
            scope=CAMPAIGN_SCOPE,
        )


def is_allowed_day_transition(current: DayState, requested: DayState) -> bool:
    """Return whether ``current -> requested`` is a permitted day move.

    Pure lookup against :data:`DAY_TRANSITIONS`; performs no mutation
    (Requirement 16.3).
    """
    return requested in DAY_TRANSITIONS[current]


def assert_day_transition(current: DayState, requested: DayState) -> None:
    """Validate a day transition, raising on rejection.

    Raises :class:`StateTransitionError` (``scope="day"``) naming the current
    and requested state when the move is not permitted; the stored state is
    left unchanged because no write occurs (Requirement 16.4).
    """
    if not is_allowed_day_transition(current, requested):
        raise StateTransitionError(
            current_state=current.value,
            requested_state=requested.value,
            scope=DAY_SCOPE,
        )


def assert_day_retry(current: DayState) -> None:
    """Validate that a retry is permitted from the given day state.

    A retry is permitted only from FAILED, in which case the day transitions
    back to GENERATING (Requirement 16.5). A retry requested from any other
    state is rejected with an error indicating retry is permitted only from
    FAILED, preserving the current state (Requirement 16.6).
    """
    if current is not DayState.FAILED:
        raise StateTransitionError(
            current_state=current.value,
            requested_state=DayState.GENERATING.value,
            scope=DAY_SCOPE,
            message=(
                f"Retry is permitted only from Day_State '{DayState.FAILED.value}'; "
                f"current Day_State is '{current.value}'"
            ),
        )
