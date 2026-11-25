"""Orchestrator tests for news mode (feed pick, retry reuse, diagram handling)."""

from __future__ import annotations

from typing import Any

import pytest

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.errors import GenerationError
from app.generation.models import Article, GenerationSpec
from app.news.feed import FeedItem
from app.notify.notifier import Notifier
from app.orchestrator.orchestrator import ExecutionOutcome, Orchestrator
from app.publishing.adapter import PublicationResult
from app.validation.validator import ValidationResult
from tests.conftest import (
    FakeDynamoDBClient,
    FakeSNSClient,
    FixedClock,
    ImmediateTimeoutRunner,
    NoSleep,
    RecordingMetrics,
)

pytestmark = pytest.mark.unit

MARKER = "![Architecture diagram](diagram.png)"
GOOD_DIAGRAM = '{"nodes": [{"id": "fn", "service": "lambda", "label": "Handler"}], "edges": []}'


def _item(guid: str, title: str, slug: str, published: str) -> FeedItem:
    return FeedItem(
        guid, title, f"Text about {title}.", published, (f"general:products/{slug}",),
        f"https://aws.amazon.com/whats-new/{guid}/", slug,
    )


ITEMS = [
    _item("g-lambda", "AWS Lambda adds a thing", "aws-lambda", "2025-12-31T10:00:00Z"),
    _item("g-s3", "Amazon S3 adds a thing", "amazon-s3", "2025-12-30T10:00:00Z"),
    _item("g-other", "Amazon Foo adds a thing", "amazon-foo", "2025-12-29T10:00:00Z"),
]
REGIONAL = _item(
    "g-reg", "Amazon S3 is now available in the Africa (Cape Town) Region", "amazon-s3",
    "2025-12-31T12:00:00Z",
)


class FakeSource:
    def __init__(self, items: list[FeedItem] | Exception) -> None:
        self._items = items
        self.calls = 0

    def fetch(self) -> list[FeedItem]:
        self.calls += 1
        if isinstance(self._items, Exception):
            raise self._items
        return list(self._items)


class NewsGenerator:
    def __init__(self, body: str = "Body.", diagram: str = "", error: Exception | None = None) -> None:
        self.specs: list[GenerationSpec] = []
        self._body, self._diagram, self._error = body, diagram, error

    def generate(self, spec: GenerationSpec) -> Article:
        self.specs.append(spec)
        if self._error is not None:
            raise self._error
        return Article(
            campaign_id=spec.campaign_id, day=spec.day, topic=spec.topic,
            title=f"About {spec.topic}", description="Short.", body_markdown=self._body,
            tags=("aws",), learning_takeaway="Learned.", diagram_json=self._diagram,
        )


class RecordingValidator:
    def __init__(self) -> None:
        self.sources: list[Any] = []

    def validate(
        self, article: object, approved_topic: str, existing: object, *, source: Any = None
    ) -> ValidationResult:
        self.sources.append(source)
        return ValidationResult(is_valid=True, violations=())


class RecordingPublisher:
    def __init__(self) -> None:
        self.articles: list[Article] = []

    def publish_article(self, article: Article) -> PublicationResult:
        self.articles.append(article)
        return PublicationResult.success(f"https://cdn/day/{article.day}")


def _setup(
    days: int = 2, style: str = "news"
) -> tuple[CampaignStore, CampaignManager, str]:
    store = CampaignStore(FakeDynamoDBClient(), "t")
    manager = CampaignManager(store, FixedClock())
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="News", total_days=days, start_date="2026-01-01", execution_time="09:00",
            topics=tuple(f"News day {n}: topic chosen from the feed" for n in range(1, days + 1)),
            content_style=style,
        )
    )
    manager.approve(campaign.campaign_id)
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)
    return store, manager, campaign.campaign_id


def _orch(
    store: CampaignStore,
    manager: CampaignManager,
    generator: Any,
    source: FakeSource | None,
    validator: Any = None,
    publisher: Any = None,
) -> Orchestrator:
    return Orchestrator(
        store=store, manager=manager, generator=generator,
        validator=validator or RecordingValidator(),  # type: ignore[arg-type]
        publisher=publisher or RecordingPublisher(),
        notifier=Notifier(FakeSNSClient(), FixedClock()),
        clock=FixedClock(), metrics=RecordingMetrics(), topic_arn="arn:aws:sns:::t",
        sleeper=NoSleep(), publish_timeout_runner=ImmediateTimeoutRunner(),
        news_source=source,
    )


def test_news_day_picks_stores_and_publishes() -> None:
    store, manager, cid = _setup()
    gen, validator = NewsGenerator(), RecordingValidator()
    result = _orch(store, manager, gen, FakeSource(ITEMS), validator).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    day = store.get_day(cid, 1)
    assert day is not None and day.day_state is DayState.PUBLISHED
    assert day.source_guid and day.source_link.startswith("https://aws.amazon.com/whats-new/")
    assert day.topic == gen.specs[0].topic
    assert gen.specs[0].news is not None and gen.specs[0].news.link == day.source_link
    assert validator.sources[0] is gen.specs[0].news
    assert day.source_text.startswith("Text about")


def test_second_day_uses_a_different_item() -> None:
    store, manager, cid = _setup()
    orch = _orch(store, manager, NewsGenerator(), FakeSource(ITEMS))
    orch.execute(cid)
    orch.execute(cid)
    first, second = store.get_day(cid, 1), store.get_day(cid, 2)
    assert first and second and first.source_guid != second.source_guid
    assert first.source_service != second.source_service


def test_retry_after_failure_reuses_the_stored_item_without_fetching() -> None:
    store, manager, cid = _setup(days=1)
    source = FakeSource(ITEMS)
    failing = NewsGenerator(error=GenerationError("model down", reason="unavailable"))
    assert _orch(store, manager, failing, source).execute(cid).outcome is ExecutionOutcome.FAILED
    failed = store.get_day(cid, 1)
    assert failed is not None and failed.day_state is DayState.FAILED and failed.source_guid
    assert source.calls == 1

    ok = NewsGenerator()
    offline = FakeSource(GenerationError("feed down", reason="unavailable"))
    result = _orch(store, manager, ok, offline).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert offline.calls == 0
    assert ok.specs[0].news is not None and ok.specs[0].news.link == failed.source_link
    assert ok.specs[0].topic == failed.topic


def test_feed_outage_fails_the_day_and_leaves_it_retryable() -> None:
    store, manager, cid = _setup(days=1)
    broken = FakeSource(GenerationError("news feed returned HTTP 503", reason="unavailable"))
    gen = NewsGenerator()
    result = _orch(store, manager, gen, broken).execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert "503" in result.error_summary
    day = store.get_day(cid, 1)
    assert day is not None and day.day_state is DayState.FAILED and day.source_guid == ""
    assert gen.specs == []
    retry = _orch(store, manager, gen, FakeSource(ITEMS)).execute(cid)
    assert retry.outcome is ExecutionOutcome.PUBLISHED


def test_no_eligible_item_fails_the_day() -> None:
    store, manager, cid = _setup(days=1)
    result = _orch(store, manager, NewsGenerator(), FakeSource([REGIONAL])).execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED
    assert "no eligible" in result.error_summary
    day = store.get_day(cid, 1)
    assert day is not None and day.day_state is DayState.FAILED


def test_missing_news_source_fails_the_day() -> None:
    store, manager, cid = _setup(days=1)
    result = _orch(store, manager, NewsGenerator(), None).execute(cid)
    assert result.outcome is ExecutionOutcome.FAILED


def test_invalid_diagram_is_dropped_and_the_article_still_publishes() -> None:
    store, manager, cid = _setup(days=1)
    body = f"Intro.\n\n{MARKER}\n\nMore text."
    publisher = RecordingPublisher()
    gen = NewsGenerator(body=body, diagram="not json at all")
    result = _orch(store, manager, gen, FakeSource(ITEMS), publisher=publisher).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert publisher.articles[0].diagram_json == ""
    assert "diagram.png" not in publisher.articles[0].body_markdown
    day = store.get_day(cid, 1)
    assert day is not None and day.diagram_json == "" and "diagram.png" not in day.body_markdown


def test_valid_diagram_is_kept_and_persisted() -> None:
    store, manager, cid = _setup(days=1)
    body = f"Intro.\n\n{MARKER}\n\nMore text."
    gen = NewsGenerator(body=body, diagram=GOOD_DIAGRAM)
    publisher = RecordingPublisher()
    _orch(store, manager, gen, FakeSource(ITEMS), publisher=publisher).execute(cid)
    day = store.get_day(cid, 1)
    assert day is not None and day.diagram_json == GOOD_DIAGRAM and MARKER in day.body_markdown
    assert publisher.articles[0].diagram_json == GOOD_DIAGRAM


def test_spec_without_marker_is_dropped() -> None:
    store, manager, cid = _setup(days=1)
    gen = NewsGenerator(body="No marker here.", diagram=GOOD_DIAGRAM)
    publisher = RecordingPublisher()
    _orch(store, manager, gen, FakeSource(ITEMS), publisher=publisher).execute(cid)
    assert publisher.articles[0].diagram_json == ""
    assert publisher.articles[0].body_markdown == "No marker here."


def test_non_news_campaign_never_calls_the_feed() -> None:
    store, manager, cid = _setup(days=1, style="")
    source = FakeSource(GenerationError("must not be called"))
    gen = NewsGenerator()

    class PlainValidator:
        def validate(self, article: object, approved_topic: str, existing: object) -> ValidationResult:
            return ValidationResult(is_valid=True, violations=())

    result = _orch(store, manager, gen, source, validator=PlainValidator()).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert source.calls == 0
    assert gen.specs[0].news is None
    day = store.get_day(cid, 1)
    assert day is not None and day.source_guid == ""


class DiagramOnlyValidator:
    """Reports DIAGRAM_MISSING when the article has no diagram, nothing else."""

    def validate(
        self, article: Article, approved_topic: str, existing: object, *, source: Any = None
    ) -> ValidationResult:
        from app.validation.validator import DIAGRAM_MISSING, ValidationViolation

        if article.diagram_json:
            return ValidationResult(is_valid=True, violations=())
        violation = ValidationViolation(DIAGRAM_MISSING, "body_markdown", "add a diagram")
        return ValidationResult(is_valid=False, violations=(violation,))


class RevisingNewsGenerator(NewsGenerator):
    def __init__(self, revised_diagram: str) -> None:
        super().__init__(body="No diagram yet.")
        self.revisions = 0
        self._revised_diagram = revised_diagram

    def revise(self, spec: GenerationSpec, draft: Article, problems: list[str]) -> Article:
        from dataclasses import replace

        self.revisions += 1
        if not self._revised_diagram:
            return draft
        return replace(
            draft,
            body_markdown=f"Intro.\n\n{MARKER}\n\nMore.",
            diagram_json=self._revised_diagram,
        )


def test_missing_diagram_triggers_repair_that_adds_it() -> None:
    store, manager, cid = _setup(days=1)
    gen, publisher = RevisingNewsGenerator(GOOD_DIAGRAM), RecordingPublisher()
    result = _orch(
        store, manager, gen, FakeSource(ITEMS), DiagramOnlyValidator(), publisher
    ).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert gen.revisions == 1
    assert publisher.articles[0].diagram_json == GOOD_DIAGRAM


def test_missing_diagram_after_repair_still_publishes() -> None:
    store, manager, cid = _setup(days=1)
    gen, publisher = RevisingNewsGenerator(""), RecordingPublisher()
    result = _orch(
        store, manager, gen, FakeSource(ITEMS), DiagramOnlyValidator(), publisher
    ).execute(cid)
    assert result.outcome is ExecutionOutcome.PUBLISHED
    assert gen.revisions == 1
    assert publisher.articles[0].diagram_json == ""
