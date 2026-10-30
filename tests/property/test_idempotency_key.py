"""Property test for deterministic, injective Idempotency_Key derivation.

Feature: aws-builder-automation, Property 4

Validates: Requirements 9.1.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.idempotency import derive_idempotency_key

pytestmark = pytest.mark.property

_campaign_ids = st.text(min_size=1, max_size=64)
_days = st.integers(min_value=1, max_value=365)


@settings(max_examples=200)
@given(campaign_id=_campaign_ids, day=_days)
def test_property_4_deterministic(campaign_id: str, day: int) -> None:
    """Identical inputs always produce identical keys."""
    assert derive_idempotency_key(campaign_id, day) == derive_idempotency_key(
        campaign_id, day
    )


@settings(max_examples=300)
@given(
    campaign_a=_campaign_ids,
    day_a=_days,
    campaign_b=_campaign_ids,
    day_b=_days,
)
def test_property_4_injective(
    campaign_a: str, day_a: int, campaign_b: str, day_b: int
) -> None:
    """Different (campaign_id, day) pairs produce different keys."""
    key_a = derive_idempotency_key(campaign_a, day_a)
    key_b = derive_idempotency_key(campaign_b, day_b)
    if (campaign_a, day_a) != (campaign_b, day_b):
        assert key_a != key_b
    else:
        assert key_a == key_b
