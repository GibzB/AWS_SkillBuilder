"""Tests for lengthening a campaign with tools/bootstrap_campaign.py --extend-to."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import DayState
from app.campaign.store import CampaignStore
from tests.conftest import FakeDynamoDBClient, FixedClock

pytestmark = pytest.mark.unit

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "bootstrap_campaign.py"
_spec = importlib.util.spec_from_file_location("bootstrap_campaign", _TOOL)
assert _spec is not None and _spec.loader is not None
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


def _campaign() -> tuple[FakeDynamoDBClient, CampaignStore, str]:
    client = FakeDynamoDBClient()
    store = CampaignStore(client, "t")
    manager = CampaignManager(store, FixedClock())
    created = manager.create_campaign(
        CreateCampaignRequest(
            name="Test",
            total_days=3,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=("A", "B", "C"),
        )
    )
    manager.approve(created.campaign_id)
    return client, store, created.campaign_id


def test_extend_replaces_planned_tail_and_appends_new_days() -> None:
    client, store, cid = _campaign()
    bootstrap._extend(client, "t", cid, 6, ["C-new", "D", "E", "F"])

    campaign = store.get_campaign(cid)
    assert campaign is not None and campaign.total_days == 6
    topics = {d.day: d.topic for d in store.query_days(cid)}
    assert topics == {1: "A", 2: "B", 3: "C-new", 4: "D", 5: "E", 6: "F"}
    assert all(d.day_state is DayState.PLANNED for d in store.query_days(cid))
    # Appended days carry a deterministic idempotency key from the start.
    assert all(d.idempotency_key for d in store.query_days(cid) if d.day >= 4)


def test_extend_refuses_to_touch_a_published_day() -> None:
    client, store, cid = _campaign()
    day3 = store.get_day(cid, 3)
    assert day3 is not None
    store.put_day(replace(day3, day_state=DayState.PUBLISHED))
    with pytest.raises(SystemExit, match="only PLANNED"):
        bootstrap._extend(client, "t", cid, 5, ["C-new", "D", "E"])
    campaign = store.get_campaign(cid)
    assert campaign is not None and campaign.total_days == 3  # nothing changed


def test_extend_cannot_shrink_or_leave_a_gap() -> None:
    client, _, cid = _campaign()
    with pytest.raises(SystemExit, match="shrink"):
        bootstrap._extend(client, "t", cid, 2, ["B"])
    with pytest.raises(SystemExit, match="every new day"):
        bootstrap._extend(client, "t", cid, 9, ["Z"])
