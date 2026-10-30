"""Property tests for the Campaign and Day state machines.

Feature: aws-builder-automation, Property 1
Feature: aws-builder-automation, Property 2

Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.models import CampaignState, DayState
from app.campaign.state import (
    CAMPAIGN_TRANSITIONS,
    DAY_TRANSITIONS,
    assert_campaign_transition,
    assert_day_retry,
    assert_day_transition,
    is_allowed_campaign_transition,
    is_allowed_day_transition,
)
from app.common.errors import StateTransitionError

pytestmark = pytest.mark.property

_campaign_states = st.sampled_from(list(CampaignState))
_day_states = st.sampled_from(list(DayState))


@settings(max_examples=200)
@given(current=_campaign_states, requested=_campaign_states)
def test_property_1_campaign_transitions_only_allowed(
    current: CampaignState, requested: CampaignState
) -> None:
    """Property 1: campaign machine admits only table-listed transitions."""
    allowed = requested in CAMPAIGN_TRANSITIONS[current]
    assert is_allowed_campaign_transition(current, requested) is allowed
    if allowed:
        assert_campaign_transition(current, requested)  # no raise
    else:
        with pytest.raises(StateTransitionError) as exc:
            assert_campaign_transition(current, requested)
        assert exc.value.current_state == current.value
        assert exc.value.requested_state == requested.value
        assert exc.value.scope == "campaign"


@settings(max_examples=200)
@given(current=_day_states, requested=_day_states)
def test_property_2_day_transitions_only_allowed(
    current: DayState, requested: DayState
) -> None:
    """Property 2: day machine admits only table-listed transitions."""
    allowed = requested in DAY_TRANSITIONS[current]
    assert is_allowed_day_transition(current, requested) is allowed
    if allowed:
        assert_day_transition(current, requested)  # no raise
    else:
        with pytest.raises(StateTransitionError) as exc:
            assert_day_transition(current, requested)
        assert exc.value.current_state == current.value
        assert exc.value.requested_state == requested.value
        assert exc.value.scope == "day"


@settings(max_examples=100)
@given(current=_day_states)
def test_property_2_retry_only_from_failed(current: DayState) -> None:
    """Retry is permitted only from FAILED (Requirements 16.5, 16.6)."""
    if current is DayState.FAILED:
        assert_day_retry(current)  # no raise
    else:
        with pytest.raises(StateTransitionError):
            assert_day_retry(current)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    """COMPLETED and PUBLISHED are terminal."""
    assert CAMPAIGN_TRANSITIONS[CampaignState.COMPLETED] == frozenset()
    assert DAY_TRANSITIONS[DayState.PUBLISHED] == frozenset()
