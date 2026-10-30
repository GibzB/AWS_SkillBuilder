"""Unit tests for the pre-deploy cost-estimate gate.

Feature: aws-builder-automation

Validates: Requirements 18.4, 18.5.
"""

from __future__ import annotations

import pytest

from app.common.cost_estimate import (
    COST_CEILING_USD,
    assert_under_budget,
    estimate_campaign_cost,
    main,
)
from app.common.errors import ConfigurationError

pytestmark = pytest.mark.unit


def test_mock_21_day_is_under_budget() -> None:
    breakdown = estimate_campaign_cost(21, "mock")
    assert breakdown.total < COST_CEILING_USD  # (18.4)
    assert_under_budget(breakdown)  # does not raise


def test_projection_at_or_above_ceiling_blocks() -> None:
    # A very long campaign with browser automation exceeds the ceiling.
    breakdown = estimate_campaign_cost(365, "builder_center", sms_enabled=True)
    assert breakdown.total >= COST_CEILING_USD
    with pytest.raises(ConfigurationError) as exc:
        assert_under_budget(breakdown)
    assert "cost_estimate" in exc.value.fields
    assert f"{breakdown.total:.2f}" in exc.value.message  # names projected total


def test_cli_ok_for_mock(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--days", "21", "--publisher", "mock"])
    assert code == 0
    assert "under budget" in capsys.readouterr().out


def test_cli_blocks_when_over(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--days", "365", "--publisher", "builder_center", "--sms"])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_zero_days_rejected() -> None:
    with pytest.raises(ConfigurationError):
        estimate_campaign_cost(0)
