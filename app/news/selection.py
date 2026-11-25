"""Weighted daily topic selection for the news campaign.

Pure and deterministic: no randomness, no I/O. About 65% of days favour an
announcement about one of the 26 services already covered in the fundamentals
campaign ("related" days); the rest favour any other service. Regional
availability items are never a daily topic (they belong to the weekly roundup).

The service keyword table below was built from the product slugs in a live
sample of the feed (``general:products/<slug>``, for example ``amazon-s3``,
``aws-lambda``, ``aws-identity-and-access-management``) plus the same
``amazon-`` / ``aws-`` naming pattern for the services that sample did not
contain. Many real items carry no product category at all, so the title is
matched as well.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.news.feed import FeedItem, is_regional

__all__ = [
    "MAX_AGE_DAYS",
    "FUNDAMENTAL_KEYS",
    "fundamental_of",
    "is_related_day",
    "is_related_item",
    "service_of",
    "select_item",
]

MAX_AGE_DAYS = 21

# service key -> (slug prefixes, title regex). A slug matches when it equals a
# prefix or starts with ``prefix + "-"`` (so ``amazon-rds`` covers
# ``amazon-rds-for-oracle`` and ``aws-iam`` covers ``aws-iam-identity-center``).
_SERVICES: dict[str, tuple[tuple[str, ...], str]] = {
    "iam": (
        ("aws-iam", "aws-identity-and-access-management"),
        r"\bIAM\b|\bidentity and access management\b",
    ),
    "s3": (("amazon-s3", "amazon-simple-storage-service"), r"\bAmazon S3\b|\bS3\b"),
    "ec2": (("amazon-ec2", "amazon-elastic-compute-cloud"), r"\bEC2\b"),
    "vpc": (("amazon-vpc", "amazon-virtual-private-cloud"), r"\bVPC\b"),
    "lambda": (("aws-lambda",), r"\bLambda\b"),
    "api-gateway": (("amazon-api-gateway",), r"\bAPI Gateway\b"),
    "eventbridge": (("amazon-eventbridge",), r"\bEventBridge\b"),
    "cloudwatch": (("amazon-cloudwatch",), r"\bCloudWatch\b"),
    "systems-manager": (("aws-systems-manager",), r"\bSystems Manager\b"),
    "dynamodb": (("amazon-dynamodb",), r"\bDynamoDB\b"),
    "rds": (("amazon-rds", "amazon-aurora"), r"\bRDS\b|\bAurora\b"),
    "sns": (("amazon-sns", "amazon-simple-notification-service"), r"\bSNS\b"),
    "sqs": (("amazon-sqs", "amazon-simple-queue-service"), r"\bSQS\b"),
    "step-functions": (("aws-step-functions",), r"\bStep Functions\b"),
    "cloudfront": (("amazon-cloudfront",), r"\bCloudFront\b"),
    "kms": (("aws-kms", "aws-key-management-service"), r"\bKMS\b"),
    "route-53": (("amazon-route-53",), r"\bRoute 53\b"),
    "cloudformation": (("aws-cloudformation",), r"\bCloudFormation\b"),
    "terraform": ((), r"\bTerraform\b"),
    "security": (
        ("amazon-guardduty", "aws-config", "aws-security-hub"),
        r"\bGuardDuty\b|\bAWS Config\b|\bSecurity Hub\b",
    ),
    "cost": (
        ("cloud-financial-management", "aws-budgets", "aws-cost-explorer", "cost-usage-reports"),
        r"\bBudgets\b|\bCost Explorer\b|\bBilling and Cost Management\b",
    ),
}
FUNDAMENTAL_KEYS = frozenset(_SERVICES)
_TITLE_RES = {key: re.compile(pattern) for key, (_, pattern) in _SERVICES.items()}

_PRODUCT_PREFIX = "general:products/"
_GENERIC_SLUGS = frozenset({"aiml", "aws-govcloud-us"})


def _product_slugs(item: FeedItem) -> list[str]:
    return [c[len(_PRODUCT_PREFIX) :] for c in item.categories if c.startswith(_PRODUCT_PREFIX)]


def fundamental_of(item: FeedItem) -> str:
    """Return the fundamentals service key the item is about, or ``""``."""
    slugs = _product_slugs(item)
    for key, (prefixes, _) in _SERVICES.items():
        for slug in slugs:
            if any(slug == p or slug.startswith(p + "-") for p in prefixes):
                return key
    for key, pattern in _TITLE_RES.items():
        if pattern.search(item.title):
            return key
    return ""


def is_related_item(item: FeedItem) -> bool:
    """True when the item is about one of the 26 published fundamentals."""
    return fundamental_of(item) != ""


def service_of(item: FeedItem) -> str:
    """A stable service label used to avoid the same service on consecutive days."""
    key = fundamental_of(item)
    if key:
        return key
    if item.service_slug:
        return item.service_slug
    words = re.findall(r"[A-Za-z0-9]+", item.title.lower())
    return "-".join(words[:2]) if words else item.guid


def is_related_day(day: int) -> bool:
    """65% of days (13 in every 20) are related days, with no RNG."""
    return (day * 13) % 20 < 13


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def select_item(
    items: list[FeedItem],
    *,
    day: int,
    used_guids: set[str] | frozenset[str],
    previous_service: str,
    now: datetime,
) -> FeedItem | None:
    """Pick the announcement for ``day``, or ``None`` when nothing is eligible.

    Newest first. Regional items and used guids are always excluded. The ladder
    is: wanted side with a different service than ``previous_service``; wanted
    side, any service; the other side; any unused non-regional item regardless
    of age.
    """
    unused = [i for i in items if i.guid not in used_guids and not is_regional(i)]
    unused.sort(key=lambda i: (i.published_iso, i.guid), reverse=True)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    fresh = [i for i in unused if (p := _parse_iso(i.published_iso)) is not None and p >= cutoff]

    want_related = is_related_day(day)
    wanted = [i for i in fresh if is_related_item(i) is want_related]
    other = [i for i in fresh if is_related_item(i) is not want_related]

    for pool, differ in ((wanted, True), (wanted, False), (other, True), (other, False)):
        for item in pool:
            if not differ or service_of(item) != previous_service:
                return item
    return unused[0] if unused else None
