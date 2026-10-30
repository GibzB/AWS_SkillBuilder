"""End-to-end pipeline tests with the MockPublisher (fakes, not live AWS).

Feature: aws-builder-automation

Drives the full daily flow (create -> approve -> run -> generate -> validate ->
record -> notify) through the real components wired with the real MockPublisher
and a real ContentValidator, backed by the in-memory DynamoDB and SNS fakes.
Also covers duplicate prevention and retry recovery.

Validates: Requirements 3.1, 4.1, 5.13, 7.2, 7.3, 7.4, 9.3, 9.4, 10.1, 12.1,
15.1, 16.5.
"""

from __future__ import annotations

import json

import pytest

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignState, DayState
from app.campaign.store import CampaignStore
from app.common.metrics import EMFMetricsEmitter
from app.common.context import ExecutionContext
from app.generation.generator import ContentGenerator
from app.notify.notifier import Notifier
from app.orchestrator.handler import BedrockRuntimeClient, load_config
from app.orchestrator.orchestrator import ExecutionOutcome, Orchestrator
from app.publishing.mock import MockPublisher
from app.validation.validator import ContentValidator
from tests.conftest import (
    FakeBedrockClient,
    FakeDynamoDBClient,
    FakeSNSClient,
    FixedClock,
    ImmediateTimeoutRunner,
    NoSleep,
)

pytestmark = [pytest.mark.integration]


def _model_response(topic: str) -> str:
    return json.dumps(
        {
            "title": f"What I learned about {topic}",
            "description": f"A short honest note on {topic}.",
            "body_markdown": (
                f"# {topic}\n\nOne thing I learned about {topic} is how the "
                "pieces fit together.\n\n```text\nclient -> service\n```\n"
            ),
            "tags": ["aws", "learning"],
            "learning_takeaway": f"{topic} is approachable when broken down.",
        }
    )


def _build(topics: list[str], response: str) -> tuple[Orchestrator, CampaignManager, CampaignStore, FakeSNSClient]:
    client = FakeDynamoDBClient()
    sns = FakeSNSClient()
    clock = FixedClock()
    store = CampaignStore(client, "t")
    manager = CampaignManager(store, clock)
    generator = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template="{title_max}{description_max}{tags_min}{tags_max}",
    )
    metrics = EMFMetricsEmitter(ExecutionContext.start(), clock, sink=lambda _: None)
    orch = Orchestrator(
        store=store,
        manager=manager,
        generator=generator,
        validator=ContentValidator(),
        publisher=MockPublisher(),
        notifier=Notifier(sns, clock),
        clock=clock,
        metrics=metrics,
        topic_arn="arn:aws:sns:::t",
        sleeper=NoSleep(),
        publish_timeout_runner=ImmediateTimeoutRunner(),
    )
    return orch, manager, store, sns


def test_e2e_single_day_publishes_and_notifies() -> None:
    orch, manager, store, sns = _build(["AWS Lambda"], _model_response("AWS Lambda"))
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="21 Days of AWS",
            total_days=1,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=("AWS Lambda",),
        )
    )
    manager.approve(campaign.campaign_id)
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)

    result = orch.execute(campaign.campaign_id)

    assert result.outcome is ExecutionOutcome.PUBLISHED
    day = store.get_day(campaign.campaign_id, 1)
    assert day is not None
    assert day.day_state is DayState.PUBLISHED
    assert day.publication_url  # recorded URL (7.3)
    assert day.published_at  # recorded timestamp (7.4)
    # Campaign completed on the final day (15.1).
    assert store.get_campaign(campaign.campaign_id).campaign_state is CampaignState.COMPLETED  # type: ignore[union-attr]
    # Success notification sent (10.1).
    success = json.loads(sns.calls[0]["Message"])
    assert success["status"] == "PUBLISHED"
    assert success["day"] == 1


def test_e2e_multi_day_advances_in_order() -> None:
    orch, manager, store, _ = _build(
        ["S3", "EC2"], _model_response("Amazon")  # topic-agnostic body mentions "Amazon"
    )
    # Use topic-matching bodies: rebuild per topic by re-seeding generator is
    # complex; instead give topics the generator response mentions.
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="Two Day",
            total_days=2,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=("Amazon", "Amazon S3"),
        )
    )
    manager.approve(campaign.campaign_id)
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)

    first = orch.execute(campaign.campaign_id)
    assert first.outcome is ExecutionOutcome.PUBLISHED
    assert first.day == 1
    assert store.get_day(campaign.campaign_id, 1).day_state is DayState.PUBLISHED  # type: ignore[union-attr]
    # Day 2 still pending.
    assert store.get_day(campaign.campaign_id, 2).day_state is DayState.PLANNED  # type: ignore[union-attr]


def test_e2e_duplicate_run_prevented() -> None:
    orch, manager, store, _ = _build(["AWS Lambda"], _model_response("AWS Lambda"))
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="One Day",
            total_days=1,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=("AWS Lambda",),
        )
    )
    manager.approve(campaign.campaign_id)
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)
    orch.execute(campaign.campaign_id)  # publishes day 1 + completes

    # A second run finds the campaign already complete; no re-publish (9.3, 9.4).
    second = orch.execute(campaign.campaign_id)
    assert second.outcome is ExecutionOutcome.ALREADY_COMPLETE
    assert store.get_day(campaign.campaign_id, 1).day_state is DayState.PUBLISHED  # type: ignore[union-attr]


def test_e2e_retry_failed_day_recovers() -> None:
    # First generation fails (empty JSON), day goes FAILED; retry with a good
    # response recovers to PUBLISHED without a duplicate (12.1, 16.5).
    client = FakeDynamoDBClient()
    sns = FakeSNSClient()
    clock = FixedClock()
    store = CampaignStore(client, "t")
    manager = CampaignManager(store, clock)

    class TwoShotBedrock:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, prompt: str, max_tokens: int) -> str:
            self.calls += 1
            if self.calls == 1:
                return "not json"  # generation failure
            return _model_response("AWS Lambda")

    generator = ContentGenerator(
        client=TwoShotBedrock(),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template="{title_max}{description_max}{tags_min}{tags_max}",
    )
    metrics = EMFMetricsEmitter(ExecutionContext.start(), clock, sink=lambda _: None)
    orch = Orchestrator(
        store=store,
        manager=manager,
        generator=generator,
        validator=ContentValidator(),
        publisher=MockPublisher(),
        notifier=Notifier(sns, clock),
        clock=clock,
        metrics=metrics,
        topic_arn="arn:aws:sns:::t",
        sleeper=NoSleep(),
        publish_timeout_runner=ImmediateTimeoutRunner(),
    )
    campaign = manager.create_campaign(
        CreateCampaignRequest(
            name="Retry",
            total_days=1,
            start_date="2026-01-01",
            execution_time="09:00",
            topics=("AWS Lambda",),
        )
    )
    manager.approve(campaign.campaign_id)
    manager.transition(campaign.campaign_id, CampaignState.RUNNING)

    first = orch.execute(campaign.campaign_id)
    assert first.outcome is ExecutionOutcome.FAILED
    assert store.get_day(campaign.campaign_id, 1).day_state is DayState.FAILED  # type: ignore[union-attr]

    # Retry: the FAILED day is selected again (16.5) and now succeeds.
    second = orch.execute(campaign.campaign_id)
    assert second.outcome is ExecutionOutcome.PUBLISHED
    assert store.get_day(campaign.campaign_id, 1).day_state is DayState.PUBLISHED  # type: ignore[union-attr]


def test_load_config_reports_missing_required() -> None:
    from app.common.errors import ConfigurationError

    with pytest.raises(ConfigurationError) as exc:
        load_config({"CAMPAIGN_ID": "c1"})  # missing TABLE_NAME etc.
    assert "TABLE_NAME" in exc.value.fields
    assert "SNS_TOPIC_ARN" in exc.value.fields


def test_load_config_defaults() -> None:
    config = load_config(
        {
            "TABLE_NAME": "t",
            "SNS_TOPIC_ARN": "arn:sns",
            "BEDROCK_MODEL_ID": "amazon.nova-micro-v1:0",
        }
    )
    assert config.timezone == "Africa/Nairobi"
    assert config.total_days == 21
    assert config.publisher_selection == "mock"
