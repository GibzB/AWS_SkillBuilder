"""Property test for deterministic validation soundness.

Feature: aws-builder-automation, Property 6

Validates: Requirements 5.1 to 5.12, 20.3, 20.4.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.campaign.models import DayState
from app.generation.models import Article
from app.validation.validator import ContentValidator

pytestmark = pytest.mark.property

_APPROVED_TOPIC = "Amazon S3 fundamentals"


def _valid_article(**overrides: object) -> Article:
    base: dict[str, object] = {
        "campaign_id": "c1",
        "day": 1,
        "topic": _APPROVED_TOPIC,
        "title": "What I learned about Amazon S3",
        "description": "A short, honest note on S3 storage basics.",
        "body_markdown": (
            "# Amazon S3\n\nOne thing I learned about S3 is how buckets "
            "organize objects.\n\n```text\nclient -> S3 bucket\n```\n"
        ),
        "tags": ("aws", "s3"),
        "learning_takeaway": "S3 buckets are simple once you separate storage from access.",
        "executed_in_aws": False,
    }
    base.update(overrides)
    return Article(**base)  # type: ignore[arg-type]


@settings(max_examples=100)
@given(
    title_words=st.lists(
        st.text(alphabet="abcdefghijklmnop ", min_size=1, max_size=8).filter(
            lambda s: s.strip()
        ),
        min_size=1,
        max_size=6,
    )
)
def test_property_6_valid_article_always_passes(title_words: list[str]) -> None:
    """A well-formed, on-topic article passes all gates."""
    title = "Amazon S3: " + " ".join(title_words)
    article = _valid_article(title=title[:255])
    result = ContentValidator().validate(article, _APPROVED_TOPIC, DayState.PLANNED)
    assert result.is_valid
    assert result.violations == ()


def _codes(article: Article, state: DayState | None = DayState.PLANNED) -> set[str]:
    result = ContentValidator().validate(article, _APPROVED_TOPIC, state)
    return {v.code for v in result.violations}


@settings(max_examples=50)
@given(pad=st.integers(min_value=1, max_value=50))
def test_property_6_each_gate_triggers_its_code(pad: int) -> None:
    """Each targeted defect produces its specific violation code."""
    assert "TITLE_MISSING" in _codes(_valid_article(title="   "))
    assert "TITLE_TOO_LONG" in _codes(_valid_article(title="A" * (256 + pad)))
    assert "DESCRIPTION_MISSING" in _codes(_valid_article(description=""))
    assert "DESCRIPTION_TOO_LONG" in _codes(
        _valid_article(description="D" * (513 + pad))
    )
    assert "BODY_EMPTY" in _codes(_valid_article(body_markdown="   "))
    assert "TAG_COUNT_INVALID" in _codes(_valid_article(tags=()))
    assert "TAG_COUNT_INVALID" in _codes(
        _valid_article(tags=tuple(f"t{i}" for i in range(6)))
    )
    assert "PLACEHOLDER_PRESENT" in _codes(
        _valid_article(body_markdown="# S3\n\nTODO write this section about S3")
    )
    assert "FABRICATED_EXECUTION" in _codes(
        _valid_article(
            body_markdown="# S3\n\nI deployed this S3 bucket in my prod account."
        )
    )
    assert "FABRICATED_EXPERIENCE" in _codes(
        _valid_article(
            body_markdown="# S3\n\nAs an AWS-certified architect I know S3 well."
        )
    )
    assert "TOPIC_MISMATCH" in _codes(_valid_article(topic="Amazon EC2 fundamentals"))
    assert "NON_PORTABLE_DIAGRAM" in _codes(
        _valid_article(
            body_markdown="# S3\n\n```mermaid\ngraph TD; A-->B;\n```\nAbout S3."
        )
    )
    assert "DUPLICATE_PUBLICATION" in _codes(
        _valid_article(), state=DayState.PUBLISHED
    )
