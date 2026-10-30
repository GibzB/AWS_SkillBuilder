"""Property and unit tests for the Command_API.

Feature: aws-builder-automation, Property 18
Feature: aws-builder-automation, Property 19

Validates: Requirements 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 13.3, 13.4, 14.6.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.manager import CampaignManager, CreateCampaignRequest
from app.campaign.models import CampaignState
from app.campaign.store import CampaignStore
from app.notify.notifier import Notifier
from app.orchestrator.command_api import (
    CommandAPI,
    CommandRequest,
    InMemoryNonceStore,
)
from app.orchestrator.orchestrator import ExecutionOutcome, ExecutionResult, Orchestrator
from app.publishing.adapter import PublicationResult
from tests.conftest import (
    FakeDynamoDBClient,
    FakeSNSClient,
    FixedClock,
    ImmediateTimeoutRunner,
    NoSleep,
    RecordingMetrics,
)

pytestmark = [pytest.mark.unit]


# ----------------------------------------------------------------------
# Spies that record whether any downstream effect happened
# ----------------------------------------------------------------------


class SpyManager:
    """Records calls; used to prove no downstream effect on rejected requests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_campaign(self, request: object) -> object:
        self.calls.append("create")
        raise AssertionError("downstream must not be invoked")

    def approve(self, campaign_id: str) -> object:
        self.calls.append("approve")
        raise AssertionError("downstream must not be invoked")

    def pause(self, campaign_id: str) -> object:
        self.calls.append("pause")
        raise AssertionError("downstream must not be invoked")

    def resume(self, campaign_id: str) -> object:
        self.calls.append("resume")
        raise AssertionError("downstream must not be invoked")

    def status(self, campaign_id: str) -> object:
        self.calls.append("status")
        raise AssertionError("downstream must not be invoked")


class SpyOrchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, campaign_id: str) -> ExecutionResult:
        self.calls.append("execute")
        raise AssertionError("downstream must not be invoked")


def _spy_api() -> tuple[CommandAPI, SpyManager, SpyOrchestrator]:
    manager = SpyManager()
    orch = SpyOrchestrator()
    api = CommandAPI(manager, orch, FixedClock())  # type: ignore[arg-type]
    return api, manager, orch


_valid_create_payload = {
    "name": "C",
    "total_days": 1,
    "start_date": "2026-01-01",
    "execution_time": "09:00",
    "topics": ["IAM"],
}


# ----------------------------------------------------------------------
# Property 18: authentication required before any downstream effect
# ----------------------------------------------------------------------


@settings(max_examples=100)
@given(
    action=st.sampled_from(
        ["create", "approve", "run", "retry", "pause", "resume", "status"]
    )
)
def test_property_18_unauthenticated_rejected(action: str) -> None:
    api, manager, orch = _spy_api()
    resp = api.handle(
        CommandRequest(
            action=action,
            authenticated=False,
            campaign_id="c1",
            nonce="n1",
            timestamp="2026-01-01T09:00:00+00:00",
            payload=_valid_create_payload,
        )
    )
    assert resp.status_code == 401
    assert not resp.ok
    assert manager.calls == []  # no downstream effect
    assert orch.calls == []


# ----------------------------------------------------------------------
# Property 19: invalid payload rejected before any downstream effect
# ----------------------------------------------------------------------


@settings(max_examples=50)
@given(missing=st.sampled_from(["name", "total_days", "start_date", "topics"]))
def test_property_19_invalid_create_payload_rejected(missing: str) -> None:
    api, manager, orch = _spy_api()
    payload = {k: v for k, v in _valid_create_payload.items() if k != missing}
    resp = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="n-unique",
            timestamp="2026-01-01T09:00:00+00:00",
            payload=payload,
        )
    )
    assert resp.status_code == 400
    assert manager.calls == []  # rejected before downstream (11.9)


def test_property_19_missing_campaign_id_rejected() -> None:
    api, manager, orch = _spy_api()
    resp = api.handle(
        CommandRequest(
            action="approve",
            authenticated=True,
            campaign_id="   ",
            nonce="n1",
            timestamp="2026-01-01T09:00:00+00:00",
        )
    )
    assert resp.status_code == 400
    assert manager.calls == []


# ----------------------------------------------------------------------
# Replay protection
# ----------------------------------------------------------------------


def _real_api() -> tuple[CommandAPI, CampaignStore, CampaignManager]:
    client = FakeDynamoDBClient()
    store = CampaignStore(client, "t")
    clock = FixedClock()
    manager = CampaignManager(store, clock)
    orch = Orchestrator(
        store=store,
        manager=manager,
        generator=_NullGen(),  # type: ignore[arg-type]
        validator=_NullValidator(),  # type: ignore[arg-type]
        publisher=_NullPub(),
        notifier=Notifier(FakeSNSClient(), clock),
        clock=clock,
        metrics=RecordingMetrics(),
        topic_arn="arn:aws:sns:::t",
        sleeper=NoSleep(),
        publish_timeout_runner=ImmediateTimeoutRunner(),
    )
    api = CommandAPI(manager, orch, clock, InMemoryNonceStore())
    return api, store, manager


class _NullGen:
    def generate(self, spec: object) -> object:
        raise AssertionError("not used")


class _NullValidator:
    def validate(self, a: object, t: str, e: object) -> object:
        raise AssertionError("not used")


class _NullPub:
    def publish_article(self, a: object) -> PublicationResult:
        raise AssertionError("not used")


def test_replay_duplicate_nonce_rejected() -> None:
    api, _, _ = _real_api()
    first = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="dup",
            timestamp="2026-01-01T09:00:00+00:00",
            payload=_valid_create_payload,
        )
    )
    assert first.ok
    second = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="dup",  # reused
            timestamp="2026-01-01T09:00:00+00:00",
            payload=_valid_create_payload,
        )
    )
    assert second.status_code == 409  # duplicate rejected


def test_stale_timestamp_rejected() -> None:
    api, _, _ = _real_api()
    resp = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="n-stale",
            timestamp="2020-01-01T00:00:00+00:00",  # far outside window
            payload=_valid_create_payload,
        )
    )
    assert resp.status_code == 409


# ----------------------------------------------------------------------
# Routing happy paths
# ----------------------------------------------------------------------


def test_create_then_status() -> None:
    api, store, manager = _real_api()
    created = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="n1",
            timestamp="2026-01-01T09:00:00+00:00",
            payload=_valid_create_payload,
        )
    )
    assert created.status_code == 201
    campaign_id = created.body["campaign_id"]
    status = api.handle(
        CommandRequest(action="status", authenticated=True, campaign_id=campaign_id)
    )
    assert status.ok
    assert status.body["state"] == CampaignState.DRAFT.value
    assert status.body["current_day"] == 1


def test_status_unknown_campaign_is_404() -> None:
    api, _, _ = _real_api()
    resp = api.handle(
        CommandRequest(action="status", authenticated=True, campaign_id="missing")
    )
    assert resp.status_code == 404  # (13.3)


def test_run_with_no_eligible_day_rejected() -> None:
    api, store, manager = _real_api()
    created = api.handle(
        CommandRequest(
            action="create",
            authenticated=True,
            nonce="n1",
            timestamp="2026-01-01T09:00:00+00:00",
            payload=_valid_create_payload,
        )
    )
    cid = created.body["campaign_id"]
    manager.approve(cid)
    # Mark the only day PUBLISHED so no eligible day remains, then run.
    manager.transition(cid, CampaignState.RUNNING)
    store.mark_published(cid, 1, "u", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
    resp = api.handle(
        CommandRequest(
            action="run",
            authenticated=True,
            campaign_id=cid,
            nonce="n-run",
            timestamp="2026-01-01T09:00:00+00:00",
        )
    )
    assert resp.status_code == 409  # no eligible day (11.5)
