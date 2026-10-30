"""Property tests for notification content and failure isolation.

Feature: aws-builder-automation, Property 23
Feature: aws-builder-automation, Property 24

Validates: Requirements 10.1, 10.2, 10.3, 10.5.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.notify.notifier import (
    MAX_PUBLISH_ATTEMPTS,
    REASON_MAX,
    URL_UNAVAILABLE,
    NotificationContext,
    Notifier,
)
from tests.conftest import FakeSNSClient, FixedClock

pytestmark = pytest.mark.property

_text = st.text(max_size=60)


def _message(client: FakeSNSClient) -> dict[str, object]:
    assert client.calls
    result: dict[str, object] = json.loads(client.calls[0]["Message"])
    return result


# -- Property 23: content complete and bounded -------------------------


@settings(max_examples=100)
@given(
    campaign_id=st.text(min_size=1, max_size=40),
    day=st.integers(min_value=1, max_value=365),
    title=_text,
    url=_text,
)
def test_property_23_success_content(
    campaign_id: str, day: int, title: str, url: str
) -> None:
    """Success notifications carry every required field."""
    client = FakeSNSClient()
    Notifier(client, FixedClock()).notify_success(
        NotificationContext(
            campaign_id=campaign_id,
            day=day,
            topic_arn="arn:aws:sns:::topic",
            title=title,
            publication_url=url,
        )
    )
    msg = _message(client)
    assert msg["campaign_id"] == campaign_id
    assert msg["day"] == day
    assert msg["title"] == title
    assert msg["status"] == "PUBLISHED"
    assert isinstance(msg["timestamp"], str) and msg["timestamp"]
    # URL present when available, explicit marker otherwise.
    assert msg["publication_url"] == (url.strip() or URL_UNAVAILABLE)


@settings(max_examples=100)
@given(
    campaign_id=st.text(min_size=1, max_size=40),
    day=st.integers(min_value=1, max_value=365),
    stage=_text,
    reason=st.text(max_size=4000),
)
def test_property_23_failure_content_bounded(
    campaign_id: str, day: int, stage: str, reason: str
) -> None:
    """Failure notifications carry every required field; reason <= 1024."""
    client = FakeSNSClient()
    Notifier(client, FixedClock()).notify_failure(
        NotificationContext(
            campaign_id=campaign_id,
            day=day,
            topic_arn="arn:aws:sns:::topic",
            stage=stage,
            error_reason=reason,
            remediation="retry",
        )
    )
    msg = _message(client)
    assert msg["campaign_id"] == campaign_id
    assert msg["day"] == day
    assert msg["stage"] == stage
    assert msg["status"] == "FAILED"
    assert len(str(msg["reason"])) <= REASON_MAX
    assert str(msg["remediation"])
    assert isinstance(msg["timestamp"], str) and msg["timestamp"]


# -- Property 24: notification failures never corrupt a success --------


@settings(max_examples=50)
@given(fail_times=st.integers(min_value=0, max_value=10))
def test_property_24_publish_failures_are_isolated(fail_times: int) -> None:
    """A publish failure is retried at most 3 times and never raises."""
    client = FakeSNSClient(fail_times=fail_times)
    notifier = Notifier(client, FixedClock())
    ctx = NotificationContext(
        campaign_id="c1", day=1, topic_arn="arn:aws:sns:::topic", title="t"
    )
    # Must not raise regardless of how many publishes fail.
    notifier.notify_success(ctx)
    # At most MAX_PUBLISH_ATTEMPTS attempts to the topic.
    assert client.attempts <= MAX_PUBLISH_ATTEMPTS
