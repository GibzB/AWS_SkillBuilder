"""Unit tests for CampaignManager creation edges, lifecycle, and status.

Feature: aws-builder-automation

Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.4, 2.5, 2.6,
2.7, 13.1, 13.2, 13.3, 14.1, 14.4, 14.5, 15.5.
"""

from __future__ import annotations

import pytest

from app.campaign.manager import (
    DEFAULT_TIMEZONE,
    CampaignManager,
    CreateCampaignRequest,
)
from app.campaign.models import CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.errors import StateTransitionError, ValidationError

pytestmark = pytest.mark.unit


def _valid_request(**overrides: object) -> CreateCampaignRequest:
    base: dict[str, object] = {
        "name": "My AWS Journey",
        "total_days": 3,
        "start_date": "2026-01-01",
        "execution_time": "09:00",
        "topics": ("IAM", "S3", "EC2"),
    }
    base.update(overrides)
    return CreateCampaignRequest(**base)  # type: ignore[arg-type]


@pytest.fixture
def manager(store: CampaignStore, clock: object) -> CampaignManager:
    return CampaignManager(store, clock)  # type: ignore[arg-type]


# -- creation ----------------------------------------------------------


def test_create_persists_draft_with_timestamps_and_id(
    manager: CampaignManager,
) -> None:
    campaign = manager.create_campaign(_valid_request())
    assert campaign.campaign_state is CampaignState.DRAFT
    assert campaign.campaign_id  # unique id assigned (1.8)
    assert campaign.created_at == campaign.updated_at
    assert campaign.created_at.endswith("+00:00")  # ISO-8601 UTC (1.7)


def test_create_with_explicit_id_uses_it(manager: CampaignManager) -> None:
    """A supplied campaign_id is used verbatim (so Scheduler runs resolve it)."""
    campaign = manager.create_campaign(_valid_request(campaign_id="aws-learning-21"))
    assert campaign.campaign_id == "aws-learning-21"


def test_create_with_duplicate_explicit_id_rejected(
    manager: CampaignManager,
) -> None:
    manager.create_campaign(_valid_request(campaign_id="dup-id"))
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(_valid_request(campaign_id="dup-id"))
    assert "campaign_id" in exc.value.fields


def test_create_without_id_generates_unique(manager: CampaignManager) -> None:
    a = manager.create_campaign(_valid_request())
    b = manager.create_campaign(_valid_request())
    assert a.campaign_id != b.campaign_id  # (1.8)


def test_create_defaults_timezone(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    assert campaign.timezone == DEFAULT_TIMEZONE  # (1.2)


def test_create_writes_one_planned_day_per_topic(
    manager: CampaignManager, store: CampaignStore
) -> None:
    campaign = manager.create_campaign(_valid_request())
    days = store.query_days(campaign.campaign_id)
    assert [d.day for d in days] == [1, 2, 3]
    assert all(d.day_state is DayState.PLANNED for d in days)
    assert [d.topic for d in days] == ["IAM", "S3", "EC2"]


@pytest.mark.parametrize("bad_days", [0, -1, 366, 1000])
def test_create_rejects_out_of_range_total_days(
    manager: CampaignManager, bad_days: int
) -> None:
    topics = tuple(f"t{i}" for i in range(max(bad_days, 1)))
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(_valid_request(total_days=bad_days, topics=topics))
    assert "total_days" in exc.value.fields  # (1.3, 1.4)


def test_create_rejects_and_names_multiple_offending_fields(
    manager: CampaignManager,
) -> None:
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(
            _valid_request(name="", execution_time="25:99")
        )
    assert "name" in exc.value.fields
    assert "execution_time" in exc.value.fields  # (1.5, 1.6)


def test_create_rejects_oversized_name(manager: CampaignManager) -> None:
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(_valid_request(name="x" * 201))
    assert "name" in exc.value.fields  # (1.6)


def test_create_rejects_topic_count_mismatch(manager: CampaignManager) -> None:
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(_valid_request(total_days=3, topics=("only-one",)))
    assert "topics" in exc.value.fields  # (2.2 well-formedness)


def test_create_rejects_bad_start_date(manager: CampaignManager) -> None:
    with pytest.raises(ValidationError) as exc:
        manager.create_campaign(_valid_request(start_date="2026-13-40"))
    assert "start_date" in exc.value.fields


# -- approval / lifecycle ---------------------------------------------


def test_approve_moves_draft_to_ready(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    approved = manager.approve(campaign.campaign_id)
    assert approved.campaign_state is CampaignState.READY  # (2.4, 2.8)


def test_approve_rejects_non_draft(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    manager.approve(campaign.campaign_id)  # now READY
    with pytest.raises(StateTransitionError):
        manager.approve(campaign.campaign_id)  # (2.5)


def test_pause_and_resume(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    manager.approve(campaign.campaign_id)  # READY
    paused = manager.pause(campaign.campaign_id)
    assert paused.campaign_state is CampaignState.PAUSED  # (14.1)
    resumed = manager.resume(campaign.campaign_id)
    assert resumed.campaign_state is CampaignState.RUNNING  # (14.3)


def test_resume_rejects_non_paused(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    manager.approve(campaign.campaign_id)  # READY, not PAUSED
    with pytest.raises(StateTransitionError):
        manager.resume(campaign.campaign_id)  # (14.5)


# -- submit_topics -----------------------------------------------------


def test_submit_topics_replaces_sequence(
    manager: CampaignManager, store: CampaignStore
) -> None:
    campaign = manager.create_campaign(_valid_request())
    manager.submit_topics(campaign.campaign_id, ["A", "B", "C"])
    days = store.query_days(campaign.campaign_id)
    assert [d.topic for d in days] == ["A", "B", "C"]  # (2.6)


def test_submit_topics_rejects_length_mismatch(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    with pytest.raises(ValidationError):
        manager.submit_topics(campaign.campaign_id, ["A", "B"])  # (2.7)


def test_submit_topics_rejects_after_approval(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    manager.approve(campaign.campaign_id)
    with pytest.raises(StateTransitionError):
        manager.submit_topics(campaign.campaign_id, ["A", "B", "C"])


# -- status ------------------------------------------------------------


def test_status_unknown_campaign_raises(manager: CampaignManager) -> None:
    with pytest.raises(ValidationError) as exc:
        manager.status("does-not-exist")
    assert "campaign_id" in exc.value.fields  # (13.3)


def test_status_fresh_campaign_fields_present(manager: CampaignManager) -> None:
    campaign = manager.create_campaign(_valid_request())
    status = manager.status(campaign.campaign_id)
    assert status.completed_days == 0
    assert status.failed_days == 0
    assert status.current_day == 1  # lowest non-published day
    assert status.publication_urls == ()
    assert status.last_execution is None  # empty/null not omitted (13.2)
    assert status.next_execution is None
