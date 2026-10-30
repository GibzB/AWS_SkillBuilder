"""Unit tests for Notifier SMS, URL marker, and secret redaction.

Feature: aws-builder-automation

Validates: Requirements 10.2, 10.4, 10.5, 10.6.
"""

from __future__ import annotations

import json

import pytest

from app.notify.notifier import (
    MAX_PUBLISH_ATTEMPTS,
    URL_UNAVAILABLE,
    NotificationContext,
    Notifier,
)
from tests.conftest import FakeSNSClient, FixedClock

pytestmark = pytest.mark.unit

_TOPIC = "arn:aws:sns:us-east-1:123456789012:notifications"


def _notifier(fail_times: int = 0) -> tuple[Notifier, FakeSNSClient]:
    client = FakeSNSClient(fail_times=fail_times)
    return Notifier(client, FixedClock()), client


def test_sms_published_when_phone_configured() -> None:
    notifier, client = _notifier()
    notifier.notify_success(
        NotificationContext(
            campaign_id="c1",
            day=1,
            topic_arn=_TOPIC,
            title="t",
            publication_url="https://example.com/a",
            sms_phone="+254700000000",
        )
    )
    destinations = [
        c.get("TopicArn") or c.get("PhoneNumber") for c in client.calls
    ]
    assert _TOPIC in destinations
    assert "+254700000000" in destinations  # (10.6)


def test_no_sms_when_phone_absent() -> None:
    notifier, client = _notifier()
    notifier.notify_success(
        NotificationContext(campaign_id="c1", day=1, topic_arn=_TOPIC, title="t")
    )
    assert all("PhoneNumber" not in c for c in client.calls)


def test_url_unavailable_marker() -> None:
    notifier, client = _notifier()
    notifier.notify_success(
        NotificationContext(
            campaign_id="c1", day=1, topic_arn=_TOPIC, title="t", publication_url=""
        )
    )
    msg = json.loads(client.calls[0]["Message"])
    assert msg["publication_url"] == URL_UNAVAILABLE  # (10.2)


def test_secret_in_reason_is_redacted() -> None:
    notifier, client = _notifier()
    notifier.notify_failure(
        NotificationContext(
            campaign_id="c1",
            day=1,
            topic_arn=_TOPIC,
            stage="publishing",
            error_reason="auth failed cookie=session=abc123secret; Path=/",
            remediation="re-authenticate",
        )
    )
    message = client.calls[0]["Message"]
    assert "abc123secret" not in message  # (10.4)
    assert "[REDACTED]" in message


def test_success_never_raises_when_all_publishes_fail() -> None:
    notifier, client = _notifier(fail_times=MAX_PUBLISH_ATTEMPTS + 2)
    # Should complete without raising (Requirement 10.5).
    notifier.notify_success(
        NotificationContext(campaign_id="c1", day=1, topic_arn=_TOPIC, title="t")
    )
    assert client.attempts == MAX_PUBLISH_ATTEMPTS  # bounded
    assert client.calls == []  # nothing succeeded


def test_retry_then_success_records_one_message() -> None:
    notifier, client = _notifier(fail_times=MAX_PUBLISH_ATTEMPTS - 1)
    notifier.notify_success(
        NotificationContext(campaign_id="c1", day=1, topic_arn=_TOPIC, title="t")
    )
    assert len(client.calls) == 1  # succeeded on the final attempt
