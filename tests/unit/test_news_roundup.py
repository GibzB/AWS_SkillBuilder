"""Tests for the weekly roundup runner and the handler routing."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignDay, CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.errors import GenerationError
from app.generation.models import Article, GenerationSpec
from app.news.feed import FeedItem
from app.news.roundup import RoundupOutcome, RoundupRunner, week_window
from app.notify.notifier import Notifier
from app.orchestrator.handler import load_config, route_event
from app.publishing.adapter import PublicationResult
from app.publishing.s3_publisher import S3Publisher
from app.validation.validator import ContentValidator
from tests.conftest import FakeDynamoDBClient, FakeS3Client, FakeSNSClient, FixedClock

pytestmark = pytest.mark.unit

START = "2026-11-09"  # a Monday
SUNDAY_WEEK2 = datetime(2026, 11, 22, 15, 0, 0, tzinfo=timezone.utc)  # 18:00 in Nairobi


def _setup(
    state: CampaignState = CampaignState.RUNNING,
) -> tuple[CampaignStore, str]:
    store = CampaignStore(FakeDynamoDBClient(), "t")
    manager = CampaignManager(store, FixedClock())
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="News", total_days=30, start_date=START, execution_time="09:00",
            topics=tuple(f"n{n}" for n in range(1, 31)), content_style="news",
            campaign_id="aws-news-30",
        )
    )
    manager.approve(campaign.campaign_id)
    if state is CampaignState.RUNNING:
        manager.transition(campaign.campaign_id, CampaignState.RUNNING)
    return store, campaign.campaign_id


def _daily(store: CampaignStore, cid: str, n: int, published_at: str, title: str | None = None) -> None:
    store.put_day(
        CampaignDay(
            campaign_id=cid, day=n, day_state=DayState.PUBLISHED, topic=f"t{n}",
            title=title or f"Daily {n}", publication_url=f"https://cdn.example/day/{n:04d}.html",
            published_at=published_at, updated_at=published_at,
            source_link=f"https://aws.amazon.com/new/{n}/", source_service="lambda",
        )
    )


def _feed_item(guid: str, title: str, published: str) -> FeedItem:
    return FeedItem(guid, title, "text", published, (), f"https://aws.amazon.com/r/{guid}/")


class Source:
    def __init__(self, items: list[FeedItem] | Exception) -> None:
        self._items = items
        self.calls = 0

    def fetch(self) -> list[FeedItem]:
        self.calls += 1
        if isinstance(self._items, Exception):
            raise self._items
        return self._items


class Generator:
    """Writes a roundup that links every daily and regional item it was given."""

    def __init__(self, error: Exception | None = None, link_all: bool = True) -> None:
        self.specs: list[GenerationSpec] = []
        self._error = error
        self._link_all = link_all

    def generate(self, spec: GenerationSpec) -> Article:
        self.specs.append(spec)
        if self._error is not None:
            raise self._error
        assert spec.roundup is not None
        lines = ["This week in AWS news I covered a few items."]
        if self._link_all:
            lines += [f"[{d.title}]({d.url})" for d in spec.roundup.dailies]
        if spec.roundup.regional:
            lines += ["## Regional availability"]
            lines += [f"- [{r.title}]({r.link})" for r in spec.roundup.regional]
        return Article(
            campaign_id=spec.campaign_id, day=spec.day, topic=spec.topic,
            title=f"AWS news roundup: week {spec.day}", description="The week in AWS news.",
            body_markdown="\n\n".join(lines), tags=("aws",), learning_takeaway="A busy week.",
            kind="roundup",
        )

    def revise(self, spec: GenerationSpec, draft: Article, problems: list[str]) -> Article:
        return self.generate(spec)


class Publisher:
    def __init__(self, fail: bool = False) -> None:
        self.articles: list[Article] = []
        self._fail = fail

    def publish_article(self, article: Article) -> PublicationResult:
        self.articles.append(article)
        if self._fail:
            return PublicationResult.failure("s3 down")
        return PublicationResult.success(f"https://cdn.example/week/{article.day:02d}.html")


def _runner(
    store: CampaignStore,
    generator: Any,
    publisher: Any,
    source: Source | None,
    sns: FakeSNSClient,
    moment: datetime = SUNDAY_WEEK2,
) -> RoundupRunner:
    return RoundupRunner(
        store=store, generator=generator, validator=ContentValidator(require_cli_example=True),
        publisher=publisher, notifier=Notifier(sns, FixedClock(moment)),
        clock=FixedClock(moment), news_source=source, topic_arn="arn:aws:sns:::t",
    )


def _week2_store() -> tuple[CampaignStore, str]:
    store, cid = _setup()
    _daily(store, cid, 1, "2026-11-14T10:00:00Z")  # Saturday of week 1: outside
    _daily(store, cid, 2, "2026-11-16T10:00:00Z", "Monday item")
    _daily(store, cid, 3, "2026-11-22T20:30:00Z", "Sunday late item")  # 23:30 Nairobi: inside
    _daily(store, cid, 4, "2026-11-22T21:30:00Z", "Next day in Nairobi")  # 00:30 Monday: outside
    return store, cid


def test_week_window_default_and_explicit() -> None:
    start = date(2026, 11, 9)
    assert week_window(start, date(2026, 11, 22), None) == (2, date(2026, 11, 16), date(2026, 11, 22))
    assert week_window(start, date(2026, 11, 15), None)[0] == 1
    assert week_window(start, date(2026, 11, 22), 2) == (2, date(2026, 11, 16), date(2026, 11, 22))
    # A Wednesday start: week 1 ends on the first Sunday.
    wed = date(2026, 11, 25)
    assert week_window(wed, date(2026, 11, 29), None)[0] == 1
    assert week_window(wed, date(2026, 12, 6), None)[0] == 2
    assert week_window(wed, date(2026, 12, 6), 2)[2] == date(2026, 12, 6)
    assert week_window(wed, date(2026, 12, 6), 1)[2] == date(2026, 11, 29)


def test_window_selects_only_the_weeks_published_dailies() -> None:
    store, cid = _week2_store()
    gen, pub, sns = Generator(), Publisher(), FakeSNSClient()
    result = _runner(store, gen, pub, Source([]), sns).run(cid)
    assert result.outcome is RoundupOutcome.PUBLISHED and result.week == 2
    assert result.publication_url == "https://cdn.example/week/02.html"
    context = gen.specs[0].roundup
    assert context is not None
    assert [d.title for d in context.dailies] == ["Monday item", "Sunday late item"]
    assert (context.week_start, context.week_end) == ("2026-11-16", "2026-11-22")
    assert context.dailies[0].source_link == "https://aws.amazon.com/new/2/"
    week = store.get_week(cid, 2)
    assert week is not None and week.day_state is DayState.PUBLISHED
    assert week.publication_url == result.publication_url and week.published_at
    assert len(sns.calls) == 1


def test_roundup_does_not_touch_days_or_the_campaign_state() -> None:
    store, cid = _week2_store()
    before = store.get_campaign(cid)
    days_before = store.query_days(cid)
    _runner(store, Generator(), Publisher(), Source([]), FakeSNSClient()).run(cid)
    assert store.query_days(cid) == days_before
    assert store.get_campaign(cid) == before


def test_zero_dailies_returns_no_dailies_without_side_effects() -> None:
    store, cid = _setup()
    gen, pub, sns = Generator(), Publisher(), FakeSNSClient()
    result = _runner(store, gen, pub, Source([]), sns).run(cid)
    assert result.outcome is RoundupOutcome.NO_DAILIES and result.week == 2
    assert gen.specs == [] and pub.articles == [] and sns.calls == []
    assert store.get_week(cid, 2) is None


def test_regional_items_appear_only_in_the_roundup() -> None:
    store, cid = _week2_store()
    items = [
        _feed_item("in", "Amazon S3 is now available in the Africa (Cape Town) Region", "2026-11-18T10:00:00Z"),
        _feed_item("old", "Amazon EC2 is now available in the Asia Pacific (Jakarta) Region", "2026-11-10T10:00:00Z"),
        _feed_item("news", "AWS Lambda adds a thing", "2026-11-18T11:00:00Z"),
    ]
    gen = Generator()
    result = _runner(store, gen, Publisher(), Source(items), FakeSNSClient()).run(cid)
    assert result.outcome is RoundupOutcome.PUBLISHED
    context = gen.specs[0].roundup
    assert context is not None
    assert [r.link for r in context.regional] == ["https://aws.amazon.com/r/in/"]


def test_feed_outage_does_not_block_the_roundup() -> None:
    store, cid = _week2_store()
    source = Source(GenerationError("down", reason="unavailable"))
    result = _runner(store, Generator(), Publisher(), source, FakeSNSClient()).run(cid)
    assert result.outcome is RoundupOutcome.PUBLISHED
    assert source.calls == 1


def test_repeated_run_is_idempotent() -> None:
    store, cid = _week2_store()
    gen, pub, sns = Generator(), Publisher(), FakeSNSClient()
    runner = _runner(store, gen, pub, Source([]), sns)
    first = runner.run(cid)
    second = runner.run(cid)
    assert first.outcome is RoundupOutcome.PUBLISHED
    assert second.outcome is RoundupOutcome.ALREADY_DONE
    assert second.publication_url == first.publication_url
    assert len(gen.specs) == 1 and len(pub.articles) == 1 and len(sns.calls) == 1


def test_generation_failure_leaves_a_failed_row_and_a_rerun_works() -> None:
    store, cid = _week2_store()
    sns = FakeSNSClient()
    bad = Generator(error=GenerationError("model down", reason="unavailable"))
    result = _runner(store, bad, Publisher(), Source([]), sns).run(cid)
    assert result.outcome is RoundupOutcome.FAILED and "model down" in result.error_summary
    week = store.get_week(cid, 2)
    assert week is not None and week.day_state is DayState.FAILED and "model down" in week.last_error
    assert len(sns.calls) == 1 and "Week 2" in sns.calls[0]["Subject"]

    again = _runner(store, Generator(), Publisher(), Source([]), sns).run(cid)
    assert again.outcome is RoundupOutcome.PUBLISHED
    week = store.get_week(cid, 2)
    assert week is not None and week.day_state is DayState.PUBLISHED


def test_validation_failure_fails_the_week() -> None:
    store, cid = _week2_store()
    result = _runner(
        store, Generator(link_all=False), Publisher(), Source([]), FakeSNSClient()
    ).run(cid)
    assert result.outcome is RoundupOutcome.FAILED
    assert "does not link" in result.error_summary
    week = store.get_week(cid, 2)
    assert week is not None and week.day_state is DayState.FAILED


def test_publish_failure_fails_the_week() -> None:
    store, cid = _week2_store()
    result = _runner(
        store, Generator(), Publisher(fail=True), Source([]), FakeSNSClient()
    ).run(cid)
    assert result.outcome is RoundupOutcome.FAILED and "s3 down" in result.error_summary


def test_explicit_week_uses_that_weeks_window() -> None:
    store, cid = _week2_store()
    gen = Generator()
    moment = datetime(2026, 11, 25, 9, 0, 0, tzinfo=timezone.utc)  # a Wednesday
    result = _runner(store, gen, Publisher(), Source([]), FakeSNSClient(), moment).run(cid, week=1)
    assert result.outcome is RoundupOutcome.PUBLISHED and result.week == 1
    context = gen.specs[0].roundup
    assert context is not None and [d.title for d in context.dailies] == ["Daily 1"]


def test_unknown_campaign() -> None:
    store, _ = _setup()
    result = _runner(store, Generator(), Publisher(), None, FakeSNSClient()).run("nope")
    assert result.outcome is RoundupOutcome.NO_CAMPAIGN


def test_roundup_is_written_to_the_week_key_by_the_s3_publisher() -> None:
    store, cid = _week2_store()
    s3 = FakeS3Client()
    publisher = S3Publisher(s3, "bucket", "https://cdn.example")
    result = _runner(store, Generator(), publisher, Source([]), FakeSNSClient()).run(cid)
    assert result.publication_url == "https://cdn.example/week/02.html"
    assert ("bucket", "week/02.html") in s3.objects


# ------------------------------ handler routing ------------------------------


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def run(self, campaign_id: str, week: int | None = None) -> Any:
        self.calls.append((campaign_id, week))
        return SimpleNamespace(outcome=RoundupOutcome.PUBLISHED, week=week or 2)


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, campaign_id: str) -> Any:
        self.calls.append(campaign_id)
        return SimpleNamespace(outcome=SimpleNamespace(value="PUBLISHED"), day=5)


def _route(event: dict[str, Any], store: CampaignStore) -> tuple[dict[str, Any], FakeRunner, FakeOrchestrator]:
    config = load_config(
        {"TABLE_NAME": "t", "SNS_TOPIC_ARN": "arn", "BEDROCK_MODEL_ID": "m", "CAMPAIGN_ID": "aws-news-30"}
    )
    runner, orch = FakeRunner(), FakeOrchestrator()
    response = route_event(event, config, orch, None, store, runner)  # type: ignore[arg-type]
    return response, runner, orch


def test_handler_routes_weekly_roundup_before_the_daily_path() -> None:
    store, _ = _setup()
    event = {"source": "eventbridge-scheduler", "campaign_id": "aws-news-30", "kind": "weekly_roundup"}
    response, runner, orch = _route(event, store)
    assert response["statusCode"] == 200 and '"week": 2' in response["body"]
    assert runner.calls == [("aws-news-30", None)] and orch.calls == []


def test_handler_passes_an_explicit_week_and_defaults_the_campaign() -> None:
    store, _ = _setup()
    response, runner, _ = _route({"kind": "weekly_roundup", "week": 3}, store)
    assert response["statusCode"] == 200
    assert runner.calls == [("aws-news-30", 3)]


@pytest.mark.parametrize("week", [0, -1, "2", True, 1.5])
def test_handler_rejects_a_bad_week(week: object) -> None:
    store, _ = _setup()
    response, runner, _ = _route({"kind": "weekly_roundup", "week": week}, store)
    assert response["statusCode"] == 400 and runner.calls == []


def test_handler_skips_a_roundup_for_a_paused_campaign() -> None:
    store, cid = _setup()
    CampaignManager(store, FixedClock()).transition(cid, CampaignState.PAUSED)
    response, runner, _ = _route({"kind": "weekly_roundup"}, store)
    assert response["statusCode"] == 200 and "skipped" in response["body"]
    assert runner.calls == []


def test_handler_roundup_for_missing_campaign_is_404() -> None:
    response, runner, _ = _route(
        {"kind": "weekly_roundup", "campaign_id": "ghost"}, CampaignStore(FakeDynamoDBClient(), "t")
    )
    assert response["statusCode"] == 404 and runner.calls == []


def test_plain_scheduler_event_still_runs_the_daily() -> None:
    store, _ = _setup()
    event = {"source": "eventbridge-scheduler", "campaign_id": "aws-news-30"}
    response, runner, orch = _route(event, store)
    assert response["statusCode"] == 200 and '"day": 5' in response["body"]
    assert orch.calls == ["aws-news-30"] and runner.calls == []
