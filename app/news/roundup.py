"""Weekly roundup for the news campaign.

Once a week (Sunday) the runner summarises the dailies published that week and
lists the regional-availability announcements, which are never daily topics. The
roundup lives in its own ``WEEK#NN`` row, so it never changes ``total_days`` or
day completion, and it publishes through the same publisher as the dailies (the
S3 key is ``week/NN.html``).

Behaviour:

* a week already PUBLISHED returns ``ALREADY_DONE`` (idempotent re-runs);
* a week with no published dailies returns ``NO_DAILIES`` (no failure, no
  notification);
* any generation, validation or publishing failure is stored as a FAILED week
  row and notified, so a manual re-run works.

Week numbering: ``(today - start_date).days // 7 + 1`` in the campaign time zone
unless a week is given. With no explicit week the window is the seven days
ending on the run day (Monday to Sunday on a Sunday run). An explicit week uses
the canonical window that ends on the Nth Sunday on or after ``start_date``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from app.campaign.models import Campaign, CampaignDay, DayState
from app.campaign.store import CampaignStore
from app.common.clock import Clock
from app.diagrams.normalise import normalise_diagram
from app.common.errors import EngineError, GenerationError
from app.generation.generator import ContentGenerator
from app.generation.models import (
    Article,
    GenerationSpec,
    RoundupContext,
    RoundupDaily,
    RoundupRegional,
)
from app.news.feed import FeedItem, NewsSource, is_regional
from app.notify.notifier import NotificationContext, Notifier
from app.publishing.adapter import PublishingAdapter
from app.validation.validator import ContentValidator, ValidationResult

__all__ = ["RoundupOutcome", "RoundupResult", "RoundupRunner", "week_window"]

ERROR_SUMMARY_MAX = 500


class RoundupOutcome(str, Enum):
    PUBLISHED = "PUBLISHED"
    NO_DAILIES = "NO_DAILIES"
    ALREADY_DONE = "ALREADY_DONE"
    FAILED = "FAILED"
    NO_CAMPAIGN = "NO_CAMPAIGN"


@dataclass(frozen=True)
class RoundupResult:
    outcome: RoundupOutcome
    week: int | None = None
    publication_url: str = ""
    error_summary: str = ""


def week_window(start: date, today: date, week: int | None) -> tuple[int, date, date]:
    """Return ``(week number, first day, last day)`` of the roundup window."""
    if week is None:
        number = max((today - start).days // 7 + 1, 1)
        return number, today - timedelta(days=6), today
    first_sunday = start + timedelta(days=(6 - start.weekday()) % 7)
    end = first_sunday + timedelta(days=7 * (week - 1))
    return week, end - timedelta(days=6), end


def _local_date(iso: str, tz: ZoneInfo) -> date | None:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date()


class RoundupRunner:
    """Builds, validates, publishes and records one weekly roundup."""

    def __init__(
        self,
        store: CampaignStore,
        generator: ContentGenerator,
        validator: ContentValidator,
        publisher: PublishingAdapter,
        notifier: Notifier,
        clock: Clock,
        news_source: NewsSource | None,
        topic_arn: str,
    ) -> None:
        self._store = store
        self._generator = generator
        self._validator = validator
        self._publisher = publisher
        self._notifier = notifier
        self._clock = clock
        self._news_source = news_source
        self._topic_arn = topic_arn

    def run(self, campaign_id: str, week: int | None = None) -> RoundupResult:
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            return RoundupResult(RoundupOutcome.NO_CAMPAIGN)
        tz = ZoneInfo(campaign.timezone or "UTC")
        today = self._clock.now().astimezone(tz).date()
        number, first, last = week_window(date.fromisoformat(campaign.start_date), today, week)

        existing = self._store.get_week(campaign_id, number)
        if existing is not None and existing.day_state is DayState.PUBLISHED:
            return RoundupResult(
                RoundupOutcome.ALREADY_DONE, number, existing.publication_url
            )

        dailies = self._published_dailies(campaign_id, first, last, tz)
        if not dailies:
            return RoundupResult(RoundupOutcome.NO_DAILIES, number)

        context = RoundupContext(
            week=number,
            week_start=first.isoformat(),
            week_end=last.isoformat(),
            dailies=tuple(
                RoundupDaily(d.title, d.publication_url, d.source_link, d.source_service)
                for d in dailies
            ),
            regional=self._regional(first, last, tz),
        )
        topic = f"AWS news roundup week {number}"
        spec = GenerationSpec(
            campaign_id=campaign_id, day=number, topic=topic, roundup=context
        )
        try:
            article, result = self._generate_valid(spec, topic, context)
        except GenerationError as error:
            return self._fail(campaign, number, topic, "generation", error.message)
        if not result.is_valid:
            reason = "; ".join(v.message for v in result.violations)
            return self._fail(campaign, number, topic, "validation", reason)

        try:
            published = self._publisher.publish_article(article)
        except EngineError as error:
            return self._fail(campaign, number, topic, "publishing", error.message)
        if not published.is_success:
            return self._fail(
                campaign, number, topic, "publishing",
                published.error_summary or "publication failed",
            )

        now = self._clock.now_iso()
        self._store.put_week(
            CampaignDay(
                campaign_id=campaign_id,
                day=number,
                day_state=DayState.PUBLISHED,
                topic=topic,
                title=article.title,
                description=article.description,
                body_markdown=article.body_markdown,
                tags=article.tags,
                publication_url=published.publication_url,
                published_at=now,
                diagram_json=article.diagram_json,
                idempotency_key=f"{campaign_id}#week#{number}",
                updated_at=now,
            )
        )
        self._notifier.notify_success(
            NotificationContext(
                campaign_id=campaign_id,
                day=number,
                week=number,
                topic_arn=self._topic_arn,
                campaign_name=campaign.name,
                title=article.title,
                publication_url=published.publication_url,
            )
        )
        return RoundupResult(RoundupOutcome.PUBLISHED, number, published.publication_url)

    # ------------------------------------------------------------------

    def _published_dailies(
        self, campaign_id: str, first: date, last: date, tz: ZoneInfo
    ) -> list[CampaignDay]:
        found: list[CampaignDay] = []
        for day in self._store.query_days(campaign_id):
            if day.day_state is not DayState.PUBLISHED or not day.published_at:
                continue
            when = _local_date(day.published_at, tz)
            if when is not None and first <= when <= last:
                found.append(day)
        return found

    def _regional(self, first: date, last: date, tz: ZoneInfo) -> tuple[RoundupRegional, ...]:
        if self._news_source is None:
            return ()
        try:
            items: list[FeedItem] = self._news_source.fetch()
        except GenerationError:
            return ()  # the roundup does not need the feed to be useful
        found: list[RoundupRegional] = []
        for item in items:
            when = _local_date(item.published_iso, tz)
            if is_regional(item) and when is not None and first <= when <= last:
                found.append(RoundupRegional(item.title, item.link))
        return tuple(found)

    def _generate_valid(
        self, spec: GenerationSpec, topic: str, context: RoundupContext
    ) -> tuple[Article, ValidationResult]:
        """Generate, validate and give the generator one repair attempt."""
        article = normalise_diagram(self._generator.generate(spec))
        result = self._validator.validate(article, topic, None, roundup=context)
        if result.is_valid:
            return article, result
        revise = getattr(self._generator, "revise", None)
        if not callable(revise):
            return article, result
        try:
            revised = normalise_diagram(
                revise(spec, article, [v.message for v in result.violations])
            )
        except GenerationError:
            return article, result
        revised_result = self._validator.validate(revised, topic, None, roundup=context)
        if revised_result.is_valid:
            return revised, revised_result
        return article, result

    def _fail(
        self, campaign: Campaign, week: int, topic: str, stage: str, reason: str
    ) -> RoundupResult:
        summary = reason[:ERROR_SUMMARY_MAX]
        now = self._clock.now_iso()
        self._store.put_week(
            CampaignDay(
                campaign_id=campaign.campaign_id,
                day=week,
                day_state=DayState.FAILED,
                topic=topic,
                last_error=summary,
                updated_at=now,
            )
        )
        self._notifier.notify_failure(
            NotificationContext(
                campaign_id=campaign.campaign_id,
                day=week,
                week=week,
                topic_arn=self._topic_arn,
                campaign_name=campaign.name,
                stage=stage,
                error_reason=summary,
                remediation="Re-run the weekly roundup for this week.",
            )
        )
        return RoundupResult(RoundupOutcome.FAILED, week, error_summary=summary)
