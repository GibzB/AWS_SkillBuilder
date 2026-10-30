"""Shared test fixtures: an in-memory DynamoDB fake and a fixed clock.

These fakes let the campaign engine be tested without real AWS. The
:class:`FakeDynamoDBClient` emulates only the operations the store uses
(``get_item``, ``put_item``, ``update_item``, ``query``) in the low-level
attribute-value wire format, including ``ConditionExpression`` evaluation for
the specific expressions the store issues, and raises a botocore-compatible
``ConditionalCheckFailedException`` so the store's error classification path is
exercised exactly as in production.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.campaign.store import CampaignStore
from app.common.clock import to_iso8601_utc

Item = dict[str, dict[str, Any]]


class ConditionalCheckFailedException(Exception):
    """Botocore-compatible conditional-check failure.

    The store recognises this both by class name and by the ``response``
    payload shape, mirroring boto3's ``ClientError``.
    """

    def __init__(self) -> None:
        super().__init__("The conditional request failed")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def _key_tuple(key: Item) -> tuple[str, str]:
    return (key["pk"]["S"], key["sk"]["S"])


class FakeDynamoDBClient:
    """Minimal in-memory DynamoDB emulating the store's access patterns."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Item] = {}

    # -- get -----------------------------------------------------------
    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        key = _key_tuple(kwargs["Key"])
        item = self._items.get(key)
        return {"Item": dict(item)} if item is not None else {}

    # -- put -----------------------------------------------------------
    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        item: Item = kwargs["Item"]
        self._items[(item["pk"]["S"], item["sk"]["S"])] = dict(item)
        return {}

    # -- update --------------------------------------------------------
    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        key = _key_tuple(kwargs["Key"])
        item = self._items.get(key, {"pk": kwargs["Key"]["pk"], "sk": kwargs["Key"]["sk"]})
        item = dict(item)
        values: Item = kwargs.get("ExpressionAttributeValues", {})

        condition = kwargs.get("ConditionExpression")
        if condition is not None and not self._eval_condition(item, condition, values):
            raise ConditionalCheckFailedException()

        update_expr: str = kwargs["UpdateExpression"]
        self._apply_update(item, update_expr, values)
        self._items[key] = item

        if kwargs.get("ReturnValues") == "UPDATED_NEW":
            # Only attempt_count is read back by the store.
            attr = item.get("attempt_count")
            return {"Attributes": {"attempt_count": attr}} if attr else {"Attributes": {}}
        return {}

    # -- query ---------------------------------------------------------
    def query(self, **kwargs: Any) -> dict[str, Any]:
        values: Item = kwargs["ExpressionAttributeValues"]
        pk = values[":pk"]["S"]
        prefix = values[":day_prefix"]["S"]
        matches = [
            dict(item)
            for (item_pk, item_sk), item in self._items.items()
            if item_pk == pk and item_sk.startswith(prefix)
        ]
        matches.sort(key=lambda it: it["sk"]["S"])
        return {"Items": matches}

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _eval_condition(item: Item, condition: str, values: Item) -> bool:
        """Evaluate the two condition shapes the store issues."""
        condition = condition.strip()
        # "day_state = :expected" or "campaign_state = :expected"
        if " = :expected" in condition:
            attr = condition.split(" = ")[0].strip()
            current = item.get(attr, {}).get("S")
            return bool(current == values[":expected"]["S"])
        # "day_state <> :published"
        if " <> :published" in condition:
            attr = condition.split(" <> ")[0].strip()
            current = item.get(attr, {}).get("S")
            return bool(current != values[":published"]["S"])
        raise AssertionError(f"unsupported ConditionExpression: {condition!r}")

    @staticmethod
    def _apply_update(item: Item, expr: str, values: Item) -> None:
        """Apply the SET / ADD expressions the store issues."""
        expr = expr.strip()
        add_part = ""
        set_part = ""
        if expr.startswith("ADD "):
            # "ADD attempt_count :one SET updated_at = :updated_at"
            rest = expr[len("ADD ") :]
            if " SET " in rest:
                add_part, set_part = rest.split(" SET ", 1)
            else:
                add_part = rest
        elif expr.startswith("SET "):
            set_part = expr[len("SET ") :]

        if add_part:
            attr, placeholder = add_part.split()
            increment = int(values[placeholder]["N"])
            current = int(item.get(attr, {}).get("N", "0"))
            item[attr] = {"N": str(current + increment)}

        if set_part:
            for assignment in set_part.split(","):
                target, placeholder = (s.strip() for s in assignment.split("="))
                item[target] = dict(values[placeholder.strip()])


class FixedClock:
    """A deterministic clock returning a fixed instant."""

    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._moment

    def now_iso(self) -> str:
        return to_iso8601_utc(self._moment)


@pytest.fixture
def fake_client() -> FakeDynamoDBClient:
    return FakeDynamoDBClient()


@pytest.fixture
def store(fake_client: FakeDynamoDBClient) -> CampaignStore:
    return CampaignStore(fake_client, table_name="test-table")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


class FakeBedrockClient:
    """Returns canned model text; records the last prompt it received."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_prompt: str | None = None
        self.last_max_tokens: int | None = None

    def invoke(self, prompt: str, max_tokens: int) -> str:
        self.last_prompt = prompt
        self.last_max_tokens = max_tokens
        return self._response


class ImmediateTimeoutRunner:
    """Runs the callable inline with no real timeout (deterministic tests)."""

    def run(self, seconds: float, func: Any) -> Any:
        return func()


class AlwaysTimeoutRunner:
    """Always raises TimeoutError to exercise the timeout classification path."""

    def run(self, seconds: float, func: Any) -> Any:
        raise TimeoutError(f"forced timeout after {seconds:.0f}s")


class FakeSNSClient:
    """Records published messages; can fail a configurable number of times.

    ``fail_times`` publishes raise before succeeding, letting tests exercise
    the Notifier's bounded-retry and failure-isolation behavior.
    """

    def __init__(self, fail_times: int = 0) -> None:
        self._fail_times = fail_times
        self.calls: list[dict[str, Any]] = []
        self.attempts = 0

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise RuntimeError("simulated SNS publish failure")
        self.calls.append(kwargs)
        return {"MessageId": f"msg-{self.attempts}"}


class RecordingMetrics:
    """Captures emitted metric names and values for assertions."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, int]] = []

    def emit(self, name: Any, value: int = 1, **dimensions: str) -> None:
        self.emitted.append((getattr(name, "value", str(name)), value))

    def count(self, metric_name: str) -> int:
        return sum(v for n, v in self.emitted if n == metric_name)


class NoSleep:
    """Sleeper that records requested backoff delays without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeS3Client:
    """In-memory S3 fake recording put_object calls; can fail on demand."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("simulated S3 failure")
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = dict(kwargs)
        return {"ETag": '"fake-etag"'}
