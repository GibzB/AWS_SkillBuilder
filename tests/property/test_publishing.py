"""Property tests for the publishing adapter contract.

Feature: aws-builder-automation, Property 10
Feature: aws-builder-automation, Property 11

Validates: Requirements 6.4, 6.6, 6.7, 6.8.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.generation.models import Article
from app.publishing.adapter import (
    PUBLICATION_URL_MAX,
    PublicationResult,
    PublishingAdapter,
)
from app.publishing.mock import MockPublisher

pytestmark = pytest.mark.property


def _article(campaign_id: str = "c1", day: int = 1) -> Article:
    return Article(
        campaign_id=campaign_id,
        day=day,
        topic="Amazon S3",
        title="S3",
        description="d",
        body_markdown="b",
        tags=("aws",),
        learning_takeaway="t",
    )


# -- Property 10: PublicationResult honors its output contract ---------


@settings(max_examples=200)
@given(url=st.text(min_size=0, max_size=PUBLICATION_URL_MAX))
def test_property_10_success_contract(url: str) -> None:
    """A success result carries no error summary; URL within bounds."""
    result = PublicationResult.success(publication_url=url)
    assert result.status == "SUCCESS"
    assert result.is_success
    assert result.error_summary == ""
    assert len(result.publication_url) <= PUBLICATION_URL_MAX


@settings(max_examples=200)
@given(summary=st.text(min_size=1, max_size=500), ambiguous=st.booleans())
def test_property_10_failure_contract(summary: str, ambiguous: bool) -> None:
    """A failure result carries a summary and no success flag."""
    result = PublicationResult.failure(summary, ambiguous=ambiguous)
    assert result.status == "FAILURE"
    assert not result.is_success
    assert result.error_summary == summary
    assert result.ambiguous is ambiguous


@settings(max_examples=100)
@given(campaign_id=st.text(min_size=1, max_size=40), day=st.integers(1, 365))
def test_property_10_mock_result_is_valid(campaign_id: str, day: int) -> None:
    """MockPublisher always returns a contract-valid success result."""
    result = MockPublisher().publish_article(_article(campaign_id, day))
    assert result.status == "SUCCESS"
    assert 1 <= len(result.publication_url) <= PUBLICATION_URL_MAX
    assert result.error_summary == ""
    assert result.ambiguous is False


# -- Property 11: orchestrator depends only on the shared contract -----


class RecordingAdapter:
    """A stand-in adapter exposing only the shared publish operation."""

    def __init__(self, result: PublicationResult) -> None:
        self._result = result
        self.calls: list[Article] = []

    def publish_article(self, article: Article) -> PublicationResult:
        self.calls.append(article)
        return self._result


@settings(max_examples=100)
@given(
    status=st.sampled_from(["SUCCESS", "FAILURE"]),
    url=st.text(max_size=50),
    summary=st.text(max_size=50),
)
def test_property_11_behavior_depends_only_on_contract(
    status: str, url: str, summary: str
) -> None:
    """Any adapter satisfying the Protocol is usable via the shared contract."""
    result = PublicationResult(
        status=status,  # type: ignore[arg-type]
        publication_url=url,
        error_summary=summary,
    )
    adapter: PublishingAdapter = RecordingAdapter(result)
    assert isinstance(adapter, PublishingAdapter)  # runtime_checkable Protocol
    article = _article()
    returned = adapter.publish_article(article)
    # The consumer sees only status/url/error_summary/ambiguous.
    assert returned.status == status
    assert returned.publication_url == url
    assert returned.error_summary == summary
