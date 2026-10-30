"""Property test: completion is monotonic in total_days and never hard-coded.

Feature: aws-builder-automation, Property 13

Validates: Requirements 3.6, 7.7, 15.1, 15.2, 15.3, 15.4.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignState
from app.campaign.store import CampaignStore
from app.topics.planner import TopicPlanner
from tests.conftest import FakeDynamoDBClient, FixedClock

pytestmark = pytest.mark.property


def _running_campaign(total_days: int) -> tuple[CampaignManager, str]:
    """Create a campaign and drive it to RUNNING for completion testing."""
    manager = CampaignManager(
        CampaignStore(FakeDynamoDBClient(), "t"), FixedClock()
    )
    topics = TopicPlanner().propose(total_days)
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="Test",
            total_days=total_days,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=tuple(topics),
        )
    )
    manager.approve(campaign.campaign_id)  # -> READY
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)
    return manager, campaign.campaign_id


@settings(max_examples=100, deadline=None)
@given(
    total_days=st.integers(min_value=1, max_value=40),
    published_day=st.integers(min_value=1, max_value=40),
)
def test_property_13_completion_monotonic(
    total_days: int, published_day: int
) -> None:
    """COMPLETED iff published_day >= total_days, compared to config value."""
    manager, campaign_id = _running_campaign(total_days)
    result = manager.complete_if_final(campaign_id, published_day)
    if published_day >= total_days:
        assert result.campaign_state is CampaignState.COMPLETED
    else:
        assert result.campaign_state is CampaignState.RUNNING
