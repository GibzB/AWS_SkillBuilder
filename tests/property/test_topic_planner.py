"""Property test for well-formed topic plans of any valid length.

Feature: aws-builder-automation, Property 14

Validates: Requirements 2.1, 2.2, 15.4.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.common.errors import ValidationError
from app.topics.planner import MAX_TOTAL_DAYS, MIN_TOTAL_DAYS, TopicPlanner

pytestmark = pytest.mark.property


@settings(max_examples=200)
@given(total_days=st.integers(min_value=MIN_TOTAL_DAYS, max_value=MAX_TOTAL_DAYS))
def test_property_14_topic_plan_well_formed(total_days: int) -> None:
    """For any valid length: correct length, non-empty, unique topics."""
    topics = TopicPlanner().propose(total_days)
    assert len(topics) == total_days
    assert all(topic.strip() for topic in topics)
    assert len(set(topics)) == total_days


@settings(max_examples=100)
@given(
    total_days=st.integers().filter(
        lambda n: n < MIN_TOTAL_DAYS or n > MAX_TOTAL_DAYS
    )
)
def test_property_14_out_of_range_rejected(total_days: int) -> None:
    """Out-of-range lengths are rejected naming total_days."""
    with pytest.raises(ValidationError) as exc:
        TopicPlanner().propose(total_days)
    assert "total_days" in exc.value.fields
