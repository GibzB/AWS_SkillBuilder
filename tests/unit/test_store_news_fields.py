"""Round-trip tests for the news fields and the WEEK# rows in the store."""

from __future__ import annotations

import pytest

from dataclasses import replace
from typing import Any

from app.campaign.models import CampaignDay, DayState, week_sk
from app.campaign.store import CampaignStore
from tests.conftest import FakeDynamoDBClient

pytestmark = pytest.mark.unit


def _day(day: int = 1, **extra: Any) -> CampaignDay:
    base = CampaignDay(
        campaign_id="aws-news-30",
        day=day,
        day_state=DayState.PLANNED,
        topic="t",
        updated_at="2026-01-01T00:00:00Z",
    )
    return replace(base, **extra)


def test_week_sk_is_zero_padded() -> None:
    assert week_sk(3) == "WEEK#0003"


def test_day_with_source_fields_round_trips(store: CampaignStore) -> None:
    day = _day(
        source_guid="g1",
        source_link="https://aws.amazon.com/x/",
        source_service="lambda",
        source_date="2026-11-24T10:00:00Z",
        source_text="The announcement text.",
        diagram_json='{"nodes": []}',
    )
    store.put_day(day)
    assert store.get_day("aws-news-30", 1) == day


def test_legacy_item_without_news_fields_loads_with_empty_strings(
    store: CampaignStore, fake_client: FakeDynamoDBClient
) -> None:
    store.put_day(_day())
    item = fake_client.get_item(Key={"pk": {"S": "CAMPAIGN#aws-news-30"}, "sk": {"S": "DAY#0001"}})["Item"]
    assert not any(k.startswith("source_") or k == "diagram_json" for k in item)
    loaded = store.get_day("aws-news-30", 1)
    assert loaded is not None
    assert loaded.source_guid == "" and loaded.diagram_json == ""


def test_week_rows_do_not_appear_in_query_days(store: CampaignStore) -> None:
    store.put_day(_day(1))
    store.put_week(_day(1, source_text="roundup"))
    store.put_week(_day(2))
    assert [d.day for d in store.query_days("aws-news-30")] == [1]
    weeks = store.query_weeks("aws-news-30")
    assert [w.day for w in weeks] == [1, 2]
    assert weeks[0].source_text == "roundup"


def test_get_week_and_missing_week(store: CampaignStore) -> None:
    assert store.get_week("aws-news-30", 4) is None
    store.put_week(_day(4))
    week = store.get_week("aws-news-30", 4)
    assert week is not None and week.day == 4
    assert store.get_day("aws-news-30", 4) is None
