"""Unit tests for weighted news topic selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.news.feed import FeedItem
from app.news.selection import is_related_day, is_related_item, select_item, service_of

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

_RELATED_SLUGS = [
    "aws-lambda", "amazon-s3", "amazon-dynamodb", "aws-cloudformation",
    "amazon-cloudwatch", "amazon-sqs", "amazon-sns", "amazon-cloudfront",
    "aws-systems-manager", "amazon-ec2", "amazon-rds", "amazon-api-gateway",
]


def _item(
    guid: str,
    title: str,
    slug: str = "",
    age_days: float = 1,
    *,
    published: str | None = None,
) -> FeedItem:
    when = published or (NOW - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cats = (f"general:products/{slug}",) if slug else ()
    return FeedItem(guid, title, "text", when, cats, f"https://aws.amazon.com/{guid}/", slug)


def _rich_feed() -> list[FeedItem]:
    items: list[FeedItem] = []
    for i in range(48):
        slug = _RELATED_SLUGS[i % len(_RELATED_SLUGS)]
        items.append(_item(f"rel-{i}", f"{slug} news {i}", slug, age_days=0.3 + i * 0.3))
    for i in range(48):
        items.append(_item(f"oth-{i}", f"Thing {i} gets a feature", f"amazon-other-{i}", age_days=0.4 + i * 0.3))
    return items


def test_regional_item_is_never_chosen() -> None:
    items = [
        _item("r", "Amazon S3 is now available in the Africa (Cape Town) Region", "amazon-s3"),
        _item("ok", "AWS Lambda adds a thing", "aws-lambda", age_days=5),
    ]
    for day in range(1, 31):
        chosen = select_item(items, day=day, used_guids=set(), previous_service="", now=NOW)
        assert chosen is not None and chosen.guid == "ok"


def test_none_when_only_regional_items_exist() -> None:
    items = [_item("r", "Amazon S3 is now available in the Asia Pacific (Jakarta) Region", "amazon-s3")]
    assert select_item(items, day=1, used_guids=set(), previous_service="", now=NOW) is None


def test_used_guid_is_skipped() -> None:
    items = [_item("a", "AWS Lambda adds A", "aws-lambda", 1), _item("b", "AWS Lambda adds B", "aws-lambda", 2)]
    chosen = select_item(items, day=2, used_guids={"a"}, previous_service="", now=NOW)
    assert chosen is not None and chosen.guid == "b"


def test_prefers_a_different_service_than_yesterday() -> None:
    items = [
        _item("same", "AWS Lambda adds A", "aws-lambda", 1),
        _item("diff", "Amazon S3 adds B", "amazon-s3", 3),
    ]
    day = 2  # (2 * 13) % 20 = 6 -> related day
    assert is_related_day(day)
    chosen = select_item(items, day=day, used_guids=set(), previous_service="lambda", now=NOW)
    assert chosen is not None and chosen.guid == "diff"


def test_no_same_service_on_consecutive_days_on_rich_feed() -> None:
    items = _rich_feed()
    used: set[str] = set()
    previous = ""
    for day in range(1, 31):
        chosen = select_item(items, day=day, used_guids=used, previous_service=previous, now=NOW)
        assert chosen is not None
        assert service_of(chosen) != previous
        used.add(chosen.guid)
        previous = service_of(chosen)


def test_related_ratio_over_thirty_days_is_between_55_and_75_percent() -> None:
    items = _rich_feed()
    used: set[str] = set()
    previous = ""
    related = 0
    for day in range(1, 31):
        chosen = select_item(items, day=day, used_guids=used, previous_service=previous, now=NOW)
        assert chosen is not None
        related += is_related_item(chosen)
        used.add(chosen.guid)
        previous = service_of(chosen)
    assert 0.55 <= related / 30 <= 0.75


def test_day_weight_is_65_percent_per_twenty_days() -> None:
    assert sum(is_related_day(d) for d in range(1, 21)) == 13


def test_fallback_to_the_other_side_when_wanted_side_is_empty() -> None:
    items = [_item("o", "Thing gets a feature", "amazon-other", 2)]
    day = 2  # related day, but only an unrelated item exists
    chosen = select_item(items, day=day, used_guids=set(), previous_service="", now=NOW)
    assert chosen is not None and chosen.guid == "o"


def test_same_service_allowed_when_it_is_the_only_wanted_item() -> None:
    items = [_item("only", "AWS Lambda adds A", "aws-lambda", 1)]
    chosen = select_item(items, day=2, used_guids=set(), previous_service="lambda", now=NOW)
    assert chosen is not None and chosen.guid == "only"


def test_old_items_are_used_only_as_the_last_resort() -> None:
    old = _item("old", "AWS Lambda adds old thing", "aws-lambda", age_days=40)
    fresh = _item("fresh", "Amazon Foo adds thing", "amazon-foo", age_days=2)
    chosen = select_item([old, fresh], day=2, used_guids=set(), previous_service="", now=NOW)
    assert chosen is not None and chosen.guid == "fresh"
    chosen = select_item([old], day=2, used_guids=set(), previous_service="", now=NOW)
    assert chosen is not None and chosen.guid == "old"


def test_selection_is_deterministic_and_newest_first() -> None:
    items = _rich_feed()
    first = select_item(items, day=5, used_guids=set(), previous_service="", now=NOW)
    second = select_item(list(reversed(items)), day=5, used_guids=set(), previous_service="", now=NOW)
    assert first == second


def test_related_matching_uses_slug_and_title() -> None:
    assert is_related_item(_item("a", "Anything", "aws-iam-identity-center"))
    assert is_related_item(_item("b", "Amazon RDS for Oracle adds x", "amazon-rds-for-oracle"))
    assert is_related_item(_item("c", "Amazon CloudWatch extends PromQL"))
    assert not is_related_item(_item("d", "Amazon SageMaker HyperPod adds x", "amazon-sagemaker"))
    assert not is_related_item(_item("e", "AWS Quick adds plugins"))
