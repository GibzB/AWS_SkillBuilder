"""Topic_Planner: propose an ordered AWS learning-topic sequence.

Given a campaign total day count, the planner produces an ordered sequence of
one non-empty, unique topic per day (Requirements 2.1, 2.2). It draws from a
curated catalog of AWS fundamentals (the PRD 21-topic sequence as the seed) and
generalizes to *any* length between 1 and 365 by composing fundamental themes
with distinct facets, so it never relies on a hard-coded value tied to a fixed
day count (Requirement 15.4). Out-of-range counts are rejected with the
permitted range named (Requirement 2.3).

Uniqueness strategy:

* For lengths within the seed catalog, the first ``total_days`` seed topics are
  returned verbatim, preserving the intended fundamentals ordering.
* For lengths beyond the seed catalog, additional days are filled by pairing a
  base AWS theme with a distinct facet ("deep dive", "cost optimization",
  "security", ...). Each generated title embeds the day number, which
  guarantees global uniqueness regardless of how themes and facets combine,
  while remaining a real, learnable AWS subject rather than a placeholder.

The planner is pure and dependency-free: same input, same output.

Design reference: design.md "Topic_Planner"; Requirements 2.1, 2.2, 2.3, 15.4.
"""

from __future__ import annotations

from app.common.errors import ValidationError

__all__ = [
    "MIN_TOTAL_DAYS",
    "MAX_TOTAL_DAYS",
    "SEED_TOPICS",
    "TopicPlanner",
]

MIN_TOTAL_DAYS = 1
MAX_TOTAL_DAYS = 365

# Curated AWS fundamentals seed (PRD Section 8, 21-topic sequence). Order is
# intentional: it tells a coherent fundamentals-first learning story.
SEED_TOPICS: tuple[str, ...] = (
    "AWS account fundamentals and security mindset",
    "IAM users, roles and least privilege",
    "Amazon S3 fundamentals",
    "S3 lifecycle and storage optimization",
    "Amazon EC2 fundamentals",
    "EC2 security groups",
    "Amazon VPC fundamentals",
    "Subnets, route tables and internet access",
    "AWS Lambda",
    "Amazon API Gateway",
    "Amazon EventBridge",
    "Amazon CloudWatch",
    "AWS Systems Manager",
    "Amazon DynamoDB",
    "Amazon RDS fundamentals",
    "Amazon SNS",
    "Infrastructure as Code with Terraform and CloudFormation",
    "AWS security fundamentals",
    "AWS cost optimization",
    "Serverless architecture patterns",
    "Lessons learned: connecting the AWS fundamentals",
)

# Base themes used to compose additional topics beyond the seed catalog. These
# are broad, real AWS subject areas, not day-count-specific values.
_EXTENSION_THEMES: tuple[str, ...] = (
    "Amazon S3",
    "Amazon EC2",
    "Amazon VPC networking",
    "AWS IAM",
    "AWS Lambda",
    "Amazon API Gateway",
    "Amazon DynamoDB",
    "Amazon RDS",
    "Amazon CloudWatch",
    "AWS Systems Manager",
    "Amazon EventBridge",
    "Amazon SNS and messaging",
    "AWS security",
    "AWS cost management",
    "Serverless architecture",
    "Infrastructure as Code",
    "AWS observability",
    "AWS resilience and backups",
)

# Distinct facets that turn a base theme into a distinct, learnable day.
_EXTENSION_FACETS: tuple[str, ...] = (
    "a practical deep dive",
    "cost optimization",
    "a security walkthrough",
    "monitoring and alarms",
    "automation with Infrastructure as Code",
    "common pitfalls and how to avoid them",
    "least-privilege access patterns",
    "a hands-on beginner example",
)


class TopicPlanner:
    """Proposes an ordered, unique topic sequence for a campaign length.

    Pure and stateless; safe to share. The ``objective`` is accepted for
    interface compatibility and future tailoring but does not affect
    correctness of the well-formedness guarantees.
    """

    def propose(self, total_days: int, objective: str = "") -> list[str]:
        """Return ``total_days`` ordered, non-empty, unique AWS topics.

        Rejects ``total_days`` outside 1..365 with a :class:`ValidationError`
        naming the permitted range (Requirement 2.3). For any valid length the
        result satisfies: ``len(result) == total_days``, every entry is
        non-empty, and no two entries are equal (Requirements 2.1, 2.2, 15.4).
        """
        if total_days < MIN_TOTAL_DAYS or total_days > MAX_TOTAL_DAYS:
            raise ValidationError(
                message=(
                    f"total day count {total_days} is out of range; permitted "
                    f"range is {MIN_TOTAL_DAYS} to {MAX_TOTAL_DAYS} inclusive"
                ),
                fields=("total_days",),
            )

        if total_days <= len(SEED_TOPICS):
            return list(SEED_TOPICS[:total_days])

        topics: list[str] = list(SEED_TOPICS)
        # Fill remaining days by composing a theme with a facet. The day number
        # is embedded to guarantee uniqueness for any length up to the maximum.
        for day in range(len(SEED_TOPICS) + 1, total_days + 1):
            extension_index = day - len(SEED_TOPICS) - 1
            theme = _EXTENSION_THEMES[extension_index % len(_EXTENSION_THEMES)]
            facet = _EXTENSION_FACETS[
                (extension_index // len(_EXTENSION_THEMES)) % len(_EXTENSION_FACETS)
            ]
            topics.append(f"Day {day}: {theme} - {facet}")
        return topics
