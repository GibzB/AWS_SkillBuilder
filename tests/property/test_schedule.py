"""Property tests for timezone-aware schedule evaluation.

Feature: aws-builder-automation, Property 25
Feature: aws-builder-automation, Property 26

Validates: Requirements 11.2, 11.3, 14.2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.models import CampaignState
from app.orchestrator.schedule import (
    EXECUTING_STATES,
    compute_trigger_utc,
    evaluate_trigger,
)

pytestmark = pytest.mark.property


# -- Property 25: timezone-aware schedule evaluation -------------------


@settings(max_examples=200)
@given(
    hour=st.integers(min_value=0, max_value=23),
    minute=st.integers(min_value=0, max_value=59),
    day=st.integers(min_value=1, max_value=90),
)
def test_property_25_trigger_matches_local_wall_clock(
    hour: int, minute: int, day: int
) -> None:
    """The UTC trigger corresponds to the configured local wall-clock time."""
    tz = "Africa/Nairobi"
    start = "2026-03-01"
    trigger = compute_trigger_utc(start, f"{hour:02d}:{minute:02d}", tz, day)
    # Convert back to the campaign timezone; the local time must match.
    local = trigger.astimezone(ZoneInfo(tz))
    assert local.hour == hour
    assert local.minute == minute
    assert trigger.tzinfo == timezone.utc


def test_property_25_nairobi_is_utc_plus_3() -> None:
    """Africa/Nairobi is UTC+3, so 09:00 local is 06:00 UTC."""
    trigger = compute_trigger_utc("2026-01-01", "09:00", "Africa/Nairobi", 1)
    assert trigger == datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)


@settings(max_examples=100)
@given(day=st.integers(min_value=1, max_value=365))
def test_property_25_day_advances_by_24h_local(day: int) -> None:
    """Day N triggers exactly (N-1) days after day 1 at the same local time."""
    tz = "Africa/Nairobi"
    first = compute_trigger_utc("2026-01-01", "09:00", tz, 1)
    nth = compute_trigger_utc("2026-01-01", "09:00", tz, day)
    assert (nth - first).days == day - 1


# -- Property 26: paused / ineligible campaigns do not execute ---------


@settings(max_examples=100)
@given(state=st.sampled_from(list(CampaignState)))
def test_property_26_only_ready_or_running_execute(
    state: CampaignState,
) -> None:
    """Triggers execute only for READY/RUNNING; others are skipped."""
    decision = evaluate_trigger(state)
    if state in EXECUTING_STATES:
        assert decision.should_execute is True
        assert decision.skipped_reason == ""
    else:
        assert decision.should_execute is False
        assert decision.skipped_reason  # reason recorded


def test_property_26_paused_never_executes() -> None:
    """A PAUSED campaign never executes (Requirement 14.2)."""
    decision = evaluate_trigger(CampaignState.PAUSED)
    assert decision.should_execute is False
