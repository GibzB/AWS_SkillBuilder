"""Unit tests for ContentValidator edge cases.

Feature: aws-builder-automation

Validates: Requirements 5.13, 5.14, 20.1, 20.4.
"""

from __future__ import annotations

import pytest

from app.campaign.models import DayState
from app.generation.models import Article
from app.validation.validator import ContentValidator

pytestmark = pytest.mark.unit

_APPROVED = "AWS Lambda"


def _article(**overrides: object) -> Article:
    base: dict[str, object] = {
        "campaign_id": "c1",
        "day": 3,
        "topic": _APPROVED,
        "title": "Getting started with AWS Lambda",
        "description": "A short note on Lambda basics.",
        "body_markdown": "# AWS Lambda\n\nOne thing I learned about Lambda is events.",
        "tags": ("aws", "lambda"),
        "learning_takeaway": "Lambda runs code without managing servers.",
        "executed_in_aws": False,
    }
    base.update(overrides)
    return Article(**base)  # type: ignore[arg-type]


def test_valid_article_passes() -> None:
    result = ContentValidator().validate(_article(), _APPROVED, DayState.PLANNED)
    assert result.is_valid


@pytest.mark.parametrize(
    "body",
    [
        "# AWS Lambda\n\nIn this example, I created a function to see how it works.",
        "# AWS Lambda\n\nI ran the invoke command in this walkthrough to test it.",
        "# AWS Lambda\n\nOne thing I learned: I set up a trigger in the example below.",
        "# AWS Lambda\n\nMy first steps with Lambda taught me how events flow.",
    ],
)
def test_legitimate_learning_prose_not_flagged_as_fabricated(body: str) -> None:
    """Tutorial first-person prose must not trip FABRICATED_EXECUTION (5.10).

    Regression for a live run where the validator rejected authentic
    learning-journey language describing example steps.
    """
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    codes = {v.code for v in result.violations}
    assert "FABRICATED_EXECUTION" not in codes


@pytest.mark.parametrize(
    "body",
    [
        "# AWS Lambda\n\nI deployed this function in my prod account.",
        "# AWS Lambda\n\nI actually launched a cluster and it handled traffic.",
        "# AWS Lambda\n\nI provisioned this in my production environment.",
    ],
)
def test_real_execution_claims_still_rejected(body: str) -> None:
    """Genuine claims of real deployment are still rejected (5.10)."""
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    codes = {v.code for v in result.violations}
    assert "FABRICATED_EXECUTION" in codes


def test_markdown_wrapper_fence_rejected() -> None:
    """A ```markdown wrapper fence breaks rendering and must be rejected.

    Regression for days 4/19/21: the model wrapped an example in a ```markdown
    fence with nested ```json inside, which garbled the published article.
    """
    body = (
        "# AWS Lambda\n\nHere is an example:\n\n"
        "```markdown\n# Example\n\n```json\n{\"a\": 1}\n```\n```\n\n## Done\nText."
    )
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    assert "MALFORMED_FENCE" in {v.code for v in result.violations}
    assert not result.is_valid


def test_nested_same_type_fences_rejected() -> None:
    body = (
        "# Title\n\n```text\nouter\n```json\n{\"nested\": true}\n```\n```\n"
    )
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    assert "MALFORMED_FENCE" in {v.code for v in result.violations}


def test_multiple_separate_code_blocks_allowed() -> None:
    """Several well-formed, non-nested code blocks are fine (e.g. day 17)."""
    body = (
        "# AWS Lambda\n\nFirst example:\n\n```json\n{\"a\": 1}\n```\n\n"
        "Second example:\n\n```yaml\nkey: value\n```\n\n"
        "Third:\n\n```text\nclient -> service\n```\n\nDone."
    )
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    assert "MALFORMED_FENCE" not in {v.code for v in result.violations}
    assert result.is_valid


def test_portable_ascii_diagram_not_flagged() -> None:
    body = (
        "# AWS Lambda\n\nHere is the flow for Lambda:\n\n"
        "```text\nevent -> Lambda -> response\n```\n"
    )
    result = ContentValidator().validate(
        _article(body_markdown=body), _APPROVED, DayState.PLANNED
    )
    assert result.is_valid


def test_multiple_violations_all_reported() -> None:
    article = _article(
        title="",  # TITLE_MISSING
        tags=(),  # TAG_COUNT_INVALID
        body_markdown="# L\n\nTODO finish this Lambda section",  # PLACEHOLDER_PRESENT
    )
    result = ContentValidator().validate(article, _APPROVED, DayState.PLANNED)
    codes = {v.code for v in result.violations}
    assert {"TITLE_MISSING", "TAG_COUNT_INVALID", "PLACEHOLDER_PRESENT"} <= codes
    assert not result.is_valid


def test_duplicate_only_when_published() -> None:
    validator = ContentValidator()
    for state in (DayState.PLANNED, DayState.FAILED, DayState.GENERATED, None):
        result = validator.validate(_article(), _APPROVED, state)
        assert "DUPLICATE_PUBLICATION" not in {v.code for v in result.violations}
    published = validator.validate(_article(), _APPROVED, DayState.PUBLISHED)
    assert "DUPLICATE_PUBLICATION" in {v.code for v in published.violations}


def test_every_violation_has_safe_message() -> None:
    article = _article(title="", description="")
    result = ContentValidator().validate(article, _APPROVED, DayState.PLANNED)
    for violation in result.violations:
        assert violation.code
        assert violation.field
        assert violation.message  # non-empty, safe to notify (5.14)
