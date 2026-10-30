"""Timezone-aware schedule evaluation for daily campaign execution.

EventBridge Scheduler triggers the Orchestrator at the configured execution
time, evaluated in the campaign timezone (default Africa/Nairobi). This module
provides the pure logic that (1) computes the correct UTC trigger instant for a
given campaign day from the configured execution time, start date, and timezone
(Requirements 11.2, 23, 1.2), and (2) decides whether an arriving trigger
should invoke the Orchestrator based on Campaign_State: only READY or RUNNING
campaigns execute; any other state records a skipped trigger and does not
execute, and a PAUSED campaign never executes (Requirements 11.3, 14.2).

The logic is pure and deterministic (no wall-clock, no I/O), so it is fully
property-testable. Timezone conversion uses the standard library
``zoneinfo``.

Design reference: design.md "Scheduling"; Requirements 11.1, 11.2, 11.3, 14.2,
1.2, 23.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.campaign.models import CampaignState

__all__ = [
    "DEFAULT_TIMEZONE",
    "EXECUTING_STATES",
    "ScheduleDecision",
    "compute_trigger_utc",
    "evaluate_trigger",
]

DEFAULT_TIMEZONE = "Africa/Nairobi"

# A trigger executes only when the campaign is READY or RUNNING (Requirement
# 11.1, 11.3). Every other state (including PAUSED, Requirement 14.2) is skipped.
EXECUTING_STATES: frozenset[CampaignState] = frozenset(
    {CampaignState.READY, CampaignState.RUNNING}
)


@dataclass(frozen=True)
class ScheduleDecision:
    """The outcome of evaluating an arriving trigger.

    ``should_execute`` is True only for READY/RUNNING campaigns. When False,
    ``skipped_reason`` names why the trigger was skipped so the caller can
    record it (Requirement 11.3).
    """

    should_execute: bool
    campaign_state: CampaignState
    skipped_reason: str = ""


def _resolve_zone(tz_name: str) -> ZoneInfo:
    """Resolve an IANA timezone name, defaulting to Africa/Nairobi.

    An empty or unknown timezone falls back to the default so scheduling never
    fails closed on a misconfigured value; configuration validation catches bad
    values earlier at deploy time.
    """
    name = tz_name.strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone falls back to default
        return ZoneInfo(DEFAULT_TIMEZONE)


def compute_trigger_utc(
    start_date: str, execution_time: str, tz_name: str, day: int
) -> datetime:
    """Return the UTC instant a given campaign day should trigger.

    Day ``d`` triggers at ``execution_time`` on ``start_date + (d - 1)`` days,
    interpreted in the campaign timezone, then converted to UTC (Requirement
    11.2). ``day`` is 1-based: day 1 is the start date itself. Raises
    :class:`ValueError` on malformed ``start_date``/``execution_time``.
    """
    if day < 1:
        raise ValueError(f"day must be >= 1, got {day}")
    zone = _resolve_zone(tz_name)
    year, month, day_of_month = (int(p) for p in start_date.split("-"))
    hour, minute = (int(p) for p in execution_time.split(":"))
    local_start = datetime(
        year, month, day_of_month, hour, minute, tzinfo=zone
    )
    local_trigger = local_start + timedelta(days=day - 1)
    return local_trigger.astimezone(timezone.utc)


def evaluate_trigger(campaign_state: CampaignState) -> ScheduleDecision:
    """Decide whether an arriving trigger should invoke the Orchestrator.

    Executes only for READY or RUNNING; PAUSED and every other state are
    skipped without execution, with a reason recorded (Requirements 11.3,
    14.2).
    """
    if campaign_state in EXECUTING_STATES:
        return ScheduleDecision(
            should_execute=True, campaign_state=campaign_state
        )
    return ScheduleDecision(
        should_execute=False,
        campaign_state=campaign_state,
        skipped_reason=(
            f"campaign is in '{campaign_state.value}'; triggers execute only "
            "for READY or RUNNING"
        ),
    )
