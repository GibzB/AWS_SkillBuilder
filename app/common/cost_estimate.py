"""Pre-deployment cost-estimate gate for a campaign.

Before deployment the engine must project a total cost strictly below USD 5.00
for a 21-day campaign and fail deployment (naming the projected total) if the
estimate reaches USD 5.00 or more (Requirements 18.4, 18.5). This module
computes a conservative, order-of-magnitude estimate for the automation itself
on the MockPublisher / API-only path and exposes a gate that raises when the
projection is not under budget.

The estimate is intentionally simple and transparent: a small per-day cost for
Lambda, DynamoDB, Bedrock (small model), SNS email, and CloudWatch, plus an
optional per-day SMS cost when SMS is enabled, and a per-day browser-automation
cost only when a non-mock publisher that requires it is selected. Figures are
conservative constants documented in design.md "Cost Estimate"; they can be
tuned without changing the gate logic.

Run as a module to gate a deploy:

    python -m app.common.cost_estimate --days 21 --publisher mock

Design reference: design.md "Cost Estimate"; Requirements 18.4, 18.5.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from app.common.errors import ConfigurationError

__all__ = [
    "COST_CEILING_USD",
    "CostBreakdown",
    "estimate_campaign_cost",
    "assert_under_budget",
    "main",
]

# Deployment fails if the projected total reaches this ceiling (Requirement 18.5).
COST_CEILING_USD = 5.00

# Conservative per-day estimates (USD) for the MockPublisher / API-only path.
_PER_DAY_LAMBDA = 0.0005
_PER_DAY_DYNAMODB = 0.002
_PER_DAY_BEDROCK = 0.005
_PER_DAY_SNS_EMAIL = 0.0001
_PER_DAY_CLOUDWATCH = 0.003

# Optional add-ons.
_PER_DAY_SMS = 0.02  # a few cents per SMS if enabled
# Browser automation (only if a real publisher requiring it is ever selected).
_PER_DAY_BROWSER_AUTOMATION = 0.05


@dataclass(frozen=True)
class CostBreakdown:
    """A transparent per-service cost projection for a campaign."""

    days: int
    lambda_cost: float
    dynamodb_cost: float
    bedrock_cost: float
    sns_cost: float
    cloudwatch_cost: float
    browser_automation_cost: float

    @property
    def total(self) -> float:
        """The projected total cost across all services."""
        return round(
            self.lambda_cost
            + self.dynamodb_cost
            + self.bedrock_cost
            + self.sns_cost
            + self.cloudwatch_cost
            + self.browser_automation_cost,
            4,
        )


def estimate_campaign_cost(
    days: int, publisher_selection: str = "mock", sms_enabled: bool = False
) -> CostBreakdown:
    """Return a conservative cost projection for a ``days``-day campaign.

    ``publisher_selection`` other than ``mock`` that requires browser
    automation adds a per-day automation cost; the mock/API-only path does not.
    ``sms_enabled`` adds a per-day SMS cost to the SNS line.
    """
    if days < 1:
        raise ConfigurationError(
            message=f"days must be >= 1, got {days}", fields=("days",)
        )
    sns_per_day = _PER_DAY_SNS_EMAIL + (_PER_DAY_SMS if sms_enabled else 0.0)
    # Only a real publisher relying on headless-browser automation adds this;
    # the mock/API-only default does not (Cost Estimate section).
    browser_per_day = (
        _PER_DAY_BROWSER_AUTOMATION
        if publisher_selection.strip().casefold() == "builder_center"
        else 0.0
    )
    return CostBreakdown(
        days=days,
        lambda_cost=round(_PER_DAY_LAMBDA * days, 4),
        dynamodb_cost=round(_PER_DAY_DYNAMODB * days, 4),
        bedrock_cost=round(_PER_DAY_BEDROCK * days, 4),
        sns_cost=round(sns_per_day * days, 4),
        cloudwatch_cost=round(_PER_DAY_CLOUDWATCH * days, 4),
        browser_automation_cost=round(browser_per_day * days, 4),
    )


def assert_under_budget(breakdown: CostBreakdown) -> None:
    """Raise if the projected total reaches the ceiling (Requirement 18.5).

    Deployment must fail when the projection is >= USD 5.00, and the error
    names the projected total so the operator sees exactly why.
    """
    if breakdown.total >= COST_CEILING_USD:
        raise ConfigurationError(
            message=(
                f"projected {breakdown.days}-day cost USD {breakdown.total:.2f} "
                f"reaches or exceeds the USD {COST_CEILING_USD:.2f} ceiling; "
                "deployment blocked"
            ),
            fields=("cost_estimate",),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry: estimate and gate. Returns 0 under budget, 1 otherwise."""
    parser = argparse.ArgumentParser(description="Campaign cost-estimate gate")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--publisher", default="mock")
    parser.add_argument("--sms", action="store_true")
    args = parser.parse_args(argv)

    breakdown = estimate_campaign_cost(args.days, args.publisher, args.sms)
    print(
        f"Projected {breakdown.days}-day cost: USD {breakdown.total:.2f} "
        f"(ceiling USD {COST_CEILING_USD:.2f})"
    )
    try:
        assert_under_budget(breakdown)
    except ConfigurationError as error:
        print(f"BLOCKED: {error.message}")
        return 1
    print("OK: projected cost is under budget.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI shim
    raise SystemExit(main())
