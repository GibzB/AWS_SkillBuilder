"""Property tests for correlation IDs and secret redaction.

Feature: aws-builder-automation, Property 15
Feature: aws-builder-automation, Property 16
Feature: aws-builder-automation, Property 17

Validates: Requirements 8.1, 8.3, 10.4, 17.1, 17.4, 17.5.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.common.context import ExecutionContext
from app.common.logging import StructuredLogger
from app.common.redaction import REDACTED_PLACEHOLDER, redact
from tests.conftest import FixedClock

pytestmark = pytest.mark.property

# Secret-bearing key names that must always be redacted.
_secret_keys = st.sampled_from(
    ["password", "cookie", "access_token", "session_token", "authorization", "secret"]
)
# Secret values drawn from a hex alphabet and long enough that they cannot be a
# substring of any key name or fixed placeholder text; this keeps the
# "value never appears" assertions meaningful (a match would be a real leak).
_secret_values = st.text(alphabet="0123456789abcdef", min_size=16, max_size=40)


# -- Property 16: redaction removes excluded values + records an event --


@settings(max_examples=200)
@given(key=_secret_keys, value=_secret_values)
def test_property_16_sensitive_key_redacted(key: str, value: str) -> None:
    result = redact({key: value, "safe": "ok"})
    payload = result.payload
    assert isinstance(payload, dict)
    assert payload[key] == REDACTED_PLACEHOLDER
    assert payload["safe"] == "ok"
    assert result.redacted
    # A valueless event is recorded, and the value never appears in it.
    assert any(e.path == key for e in result.events)
    for event in result.events:
        assert value not in event.path
        assert value not in event.reason


@settings(max_examples=200)
@given(
    key=_secret_keys,
    value=_secret_values,
    depth=st.integers(min_value=0, max_value=4),
)
def test_property_15_secret_never_survives_nesting(
    key: str, value: str, depth: int
) -> None:
    """A secret nested anywhere is removed from the serialized output."""
    payload: dict[str, object] = {key: value}
    for _ in range(depth):
        payload = {"nested": payload}
    serialized = json.dumps(redact(payload).payload)
    assert value not in serialized
    assert REDACTED_PLACEHOLDER in serialized


# -- Property 17: correlation ID unique + attached everywhere ----------


@settings(max_examples=100)
@given(messages=st.lists(st.text(max_size=30), min_size=1, max_size=5))
def test_property_17_correlation_id_on_every_record(
    messages: list[str],
) -> None:
    context = ExecutionContext.start()
    lines: list[str] = []
    logger = StructuredLogger(context, FixedClock(), sink=lines.append)
    for message in messages:
        logger.info(message)
    assert lines
    for line in lines:
        record = json.loads(line)
        assert record["correlation_id"] == context.correlation_id


def test_property_17_ids_unique_per_execution() -> None:
    ids = {ExecutionContext.start().correlation_id for _ in range(500)}
    assert len(ids) == 500  # unique per execution


@settings(max_examples=100)
@given(secret=_secret_values)
def test_property_15_logged_secret_is_redacted(secret: str) -> None:
    """A secret passed through the logger never reaches the sink in cleartext."""
    context = ExecutionContext.start()
    lines: list[str] = []
    logger = StructuredLogger(context, FixedClock(), sink=lines.append)
    logger.info("auth attempt", password=secret)
    assert lines
    # The secret value never appears in any emitted line (record or event).
    assert all(secret not in line for line in lines)
    # The record line shows the password field redacted to the placeholder.
    records = [json.loads(line) for line in lines]
    assert any(r.get("password") == REDACTED_PLACEHOLDER for r in records)
