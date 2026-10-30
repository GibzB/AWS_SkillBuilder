"""Unit tests for publisher selection, MockPublisher, and the blocked stub.

Feature: aws-builder-automation

Validates: Requirements 6.1, 6.2, 6.3, 6.5, 12.3, 12.4.
"""

from __future__ import annotations

import inspect

import pytest

from app.common.errors import ConfigurationError, PublicationError
from app.generation.models import Article
from app.publishing.adapter import PublicationResult, PublishingAdapter
from app.publishing.builder_center import (
    BUILDER_CENTER_BLOCKER,
    BuilderCenterPublisher,
)
from app.publishing.mock import MockPublisher
from app.publishing.selection import (
    BUILDER_CENTER,
    DEFAULT_PUBLISHER_SELECTION,
    MOCK,
    select_publisher,
)

pytestmark = pytest.mark.unit


def _article() -> Article:
    return Article(
        campaign_id="c1",
        day=1,
        topic="Amazon S3",
        title="S3",
        description="d",
        body_markdown="b",
        tags=("aws",),
        learning_takeaway="t",
    )


# -- publish operation shape (Requirement 6.1) -------------------------


def test_publish_operation_takes_single_article() -> None:
    sig = inspect.signature(MockPublisher.publish_article)
    params = [p for p in sig.parameters if p != "self"]
    assert params == ["article"]  # exactly one input object (6.1)


# -- MockPublisher (Requirement 6.2) -----------------------------------


def test_mock_default_selection_is_mock() -> None:
    assert DEFAULT_PUBLISHER_SELECTION == MOCK


def test_mock_returns_success_without_network() -> None:
    result = MockPublisher().publish_article(_article())
    assert result.status == "SUCCESS"
    assert "mock.local" in result.publication_url  # clearly not a real link


# -- selection (Requirement 6.5) ---------------------------------------


def test_select_mock() -> None:
    adapter = select_publisher(MOCK)
    assert isinstance(adapter, MockPublisher)
    assert isinstance(adapter, PublishingAdapter)


def test_select_builder_center_explicit_only() -> None:
    adapter = select_publisher(BUILDER_CENTER)
    assert isinstance(adapter, BuilderCenterPublisher)


def test_empty_selection_is_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as exc:
        select_publisher("")
    assert "publisher_selection" in exc.value.fields  # (6.5)


def test_unknown_selection_is_configuration_error() -> None:
    with pytest.raises(ConfigurationError) as exc:
        select_publisher("browser_bot")
    assert "publisher_selection" in exc.value.fields


def test_selection_is_case_insensitive() -> None:
    assert isinstance(select_publisher("  Mock "), MockPublisher)


# -- BuilderCenterPublisher blocked stub (Requirement 6.3) -------------


def test_builder_center_publish_fails_safe() -> None:
    result = BuilderCenterPublisher().publish_article(_article())
    assert result.status == "FAILURE"
    assert result.ambiguous is False
    assert result.error_summary == BUILDER_CENTER_BLOCKER


def test_builder_center_verify_raises_rather_than_guessing() -> None:
    with pytest.raises(PublicationError):
        BuilderCenterPublisher().verify_published(_article())


def test_publication_result_success_and_failure_helpers() -> None:
    ok = PublicationResult.success("https://example.com/a")
    assert ok.is_success and ok.error_summary == ""
    bad = PublicationResult.failure("boom")
    assert not bad.is_success and bad.publication_url == ""
