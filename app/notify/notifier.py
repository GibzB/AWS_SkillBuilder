"""Notifier: publish success and failure notifications through Amazon SNS.

The Notifier turns a campaign-day outcome into an SNS notification
(Requirement 10). A success notification carries the campaign id, day, article
title, status PUBLISHED, an ISO-8601 UTC timestamp, and the Builder Center URL
when available (an explicit "unavailable" marker otherwise). A failure
notification carries the campaign id, day, stage, status FAILED, a sanitized
error reason (<= 1024 chars), and a remediation action (Requirements 10.1,
10.2, 10.3).

Two safety guarantees are built in:

* Secret exclusion: every field, including the sanitized reason, is passed
  through :func:`app.common.redaction.redact` before it can reach SNS, so
  passwords, cookies, tokens, session headers, and personal auth details are
  removed (Requirement 10.4).
* Notification-failure isolation: SNS publish is retried up to 3 times, and a
  notification failure never propagates to the caller, so an already-successful
  publication can never be flipped to FAILED by a notification problem
  (Requirement 10.5). :meth:`notify_success` and :meth:`notify_failure` never
  raise.

The SNS client and clock are injected, so the Notifier is testable without real
AWS. SMS is optional and only sent when a phone number is configured
(Requirement 10.6).

Design reference: design.md "Notifier" and "Notifications"; Requirements 10.1
to 10.6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.common.clock import Clock
from app.common.redaction import redact

__all__ = [
    "REASON_MAX",
    "MAX_PUBLISH_ATTEMPTS",
    "URL_UNAVAILABLE",
    "SNSClient",
    "NotificationContext",
    "Notifier",
]

# Maximum length of the sanitized failure reason (Requirement 10.3).
REASON_MAX = 1024

# Bounded SNS publish retries (Requirement 10.5).
MAX_PUBLISH_ATTEMPTS = 3

# Explicit marker used when a success notification has no Builder Center URL
# (Requirement 10.2).
URL_UNAVAILABLE = "unavailable"


class SNSClient(Protocol):
    """Minimal structural type for the injected boto3 SNS client.

    Only ``publish`` is used. The real ``boto3.client("sns")`` satisfies this,
    as does an in-memory fake in tests.
    """

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        """Publish a message to a topic or phone number."""
        ...


@dataclass(frozen=True)
class NotificationContext:
    """The inputs needed to render one notification.

    ``topic_arn`` is the SNS topic for email (and any topic subscribers);
    ``sms_phone`` enables an additional SMS publish when non-empty. The other
    fields populate the success/failure message bodies.
    """

    campaign_id: str
    day: int
    topic_arn: str
    campaign_name: str = ""
    title: str = ""
    stage: str = ""
    publication_url: str = ""
    error_reason: str = ""
    remediation: str = ""
    sms_phone: str = ""


class Notifier:
    """Publishes success/failure notifications; never raises to the caller."""

    def __init__(self, sns_client: SNSClient, clock: Clock) -> None:
        self._sns = sns_client
        self._clock = clock

    def notify_success(self, ctx: NotificationContext) -> None:
        """Publish a PUBLISHED success notification (Requirements 10.1, 10.2).

        Includes every required field; the Builder Center URL is included when
        available and replaced by an explicit unavailable marker otherwise.
        Never raises: a publish failure is retried and then swallowed so it
        cannot affect the publication outcome (Requirement 10.5).
        """
        url = ctx.publication_url.strip() or URL_UNAVAILABLE
        # Actionable guidance: tell the reader exactly how to push this day to
        # Builder Center (fills the editor + auto-selects tags; they log in and
        # click Publish). Keeps the notification self-explanatory day to day.
        next_action = (
            f"To post Day {ctx.day} to Builder Center, run: "
            f"python tools/builder_publish/batch_publish.py "
            f"--from {ctx.day} --to {ctx.day} --auto-tags"
        )
        payload = {
            "type": "SUCCESS",
            "status": "PUBLISHED",
            "campaign_id": ctx.campaign_id,
            "campaign_name": ctx.campaign_name,
            "day": ctx.day,
            "title": ctx.title,
            "publication_url": url,
            "timestamp": self._clock.now_iso(),
            "next_action": next_action,
        }
        subject = f"AWS Builder Automation - Day {ctx.day} ready to post"
        self._safe_publish(ctx, subject, payload)

    def notify_failure(self, ctx: NotificationContext) -> None:
        """Publish a FAILED failure notification (Requirements 10.3, 10.4).

        Includes the campaign id, day, stage, status FAILED, a sanitized reason
        truncated to 1024 characters, and a remediation action. Never raises
        (Requirement 10.5).
        """
        reason = ctx.error_reason[:REASON_MAX]
        payload = {
            "type": "FAILURE",
            "status": "FAILED",
            "campaign_id": ctx.campaign_id,
            "campaign_name": ctx.campaign_name,
            "day": ctx.day,
            "stage": ctx.stage,
            "reason": reason,
            "remediation": ctx.remediation or "Review the logs and retry the day.",
            "timestamp": self._clock.now_iso(),
        }
        subject = f"AWS Builder Automation - Day {ctx.day} Failed"
        self._safe_publish(ctx, subject, payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _safe_publish(
        self, ctx: NotificationContext, subject: str, payload: dict[str, object]
    ) -> None:
        """Redact, then publish to the topic and optional SMS; never raise.

        Redaction runs first so no secret can reach SNS (Requirement 10.4).
        Each destination is retried up to :data:`MAX_PUBLISH_ATTEMPTS` times;
        if all attempts fail the error is swallowed so a notification failure
        never corrupts a successful publication (Requirement 10.5).
        """
        sanitized = redact(payload).payload
        message = json.dumps(sanitized, separators=(",", ":"), sort_keys=True)

        # Email / topic subscribers.
        self._publish_with_retry(
            TopicArn=ctx.topic_arn, Subject=subject[:100], Message=message
        )

        # Optional SMS to a configured phone number (Requirement 10.6).
        phone = ctx.sms_phone.strip()
        if phone:
            self._publish_with_retry(PhoneNumber=phone, Message=message)

    def _publish_with_retry(self, **kwargs: Any) -> bool:
        """Attempt an SNS publish up to the bounded retry limit; never raise.

        Returns True on success and False if every attempt failed. Failures are
        intentionally swallowed (Requirement 10.5).
        """
        for _ in range(MAX_PUBLISH_ATTEMPTS):
            try:
                self._sns.publish(**kwargs)
                return True
            except Exception:  # noqa: BLE001 - notification failures are isolated
                continue
        return False
