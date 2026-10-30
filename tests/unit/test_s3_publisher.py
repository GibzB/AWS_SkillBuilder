"""Unit tests for the S3Publisher and S3 publisher selection.

Feature: aws-builder-automation

Validates: Requirements 6.1, 6.5, 6.6, 6.8.
"""

from __future__ import annotations

import pytest

from app.common.errors import ConfigurationError
from app.generation.models import Article
from app.publishing.adapter import PublishingAdapter
from app.publishing.s3_publisher import S3Publisher, render_article_html
from app.publishing.selection import (
    PublisherConfig,
    select_publisher,
)
from tests.conftest import FakeS3Client

pytestmark = pytest.mark.unit

_BASE_URL = "https://d123.cloudfront.net"
_BUCKET = "aws-builder-articles"


def _article(day: int = 1) -> Article:
    return Article(
        campaign_id="aws-learning-21",
        day=day,
        topic="Amazon S3",
        title="Learning Amazon S3",
        description="A short, honest note on S3.",
        body_markdown="# S3\n\nOne thing I learned about S3.\n\n```text\nclient -> S3\n```",
        tags=("aws", "s3"),
        learning_takeaway="S3 is simple once you separate storage from access.",
    )


def test_publish_writes_html_and_returns_cloudfront_url() -> None:
    s3 = FakeS3Client()
    result = S3Publisher(s3, _BUCKET, _BASE_URL).publish_article(_article(7))
    assert result.status == "SUCCESS"
    # Deterministic, zero-padded key; HTTPS CloudFront URL.
    assert result.publication_url == f"{_BASE_URL}/day/0007.html"
    stored = s3.objects[(_BUCKET, "day/0007.html")]
    assert stored["ContentType"].startswith("text/html")
    body = stored["Body"].decode("utf-8")
    assert "<title>Learning Amazon S3</title>" in body
    assert "One thing I learned about S3" in body


def test_base_url_trailing_slash_normalized() -> None:
    s3 = FakeS3Client()
    result = S3Publisher(s3, _BUCKET, _BASE_URL + "/").publish_article(_article(1))
    assert result.publication_url == f"{_BASE_URL}/day/0001.html"


def test_s3_failure_returns_sanitized_failure_result() -> None:
    s3 = FakeS3Client(fail=True)
    result = S3Publisher(s3, _BUCKET, _BASE_URL).publish_article(_article())
    assert result.status == "FAILURE"
    assert "S3" in result.error_summary
    # No secret/internal detail, just the error class.
    assert "RuntimeError" in result.error_summary


def test_rendered_html_escapes_metadata_and_renders_markdown() -> None:
    article = Article(
        campaign_id="c", day=1, topic="t",
        title="S3 & <Security>",  # must be escaped
        description="Quotes \"here\" & there",
        body_markdown="# Heading\n\nSome **bold** text.",
        tags=("a&b",),
        learning_takeaway="Done.",
    )
    out = render_article_html(article)
    assert "S3 &amp; &lt;Security&gt;" in out  # title escaped
    assert "<h1>Heading</h1>" in out  # markdown rendered
    assert "<strong>bold</strong>" in out
    assert "a&amp;b" in out  # tag escaped


def test_code_block_in_body_rendered_as_pre() -> None:
    out = render_article_html(_article())
    assert "<pre>" in out  # fenced code becomes <pre>
    assert "client -&gt; S3" in out  # content escaped inside pre


# -- selection --------------------------------------------------------


def test_select_s3_requires_full_config() -> None:
    with pytest.raises(ConfigurationError) as exc:
        select_publisher("s3", PublisherConfig())  # nothing supplied
    assert "s3_client" in exc.value.fields
    assert "s3_bucket" in exc.value.fields
    assert "s3_base_url" in exc.value.fields


def test_select_s3_with_config_builds_publisher() -> None:
    cfg = PublisherConfig(
        s3_client=FakeS3Client(), s3_bucket=_BUCKET, s3_base_url=_BASE_URL
    )
    adapter = select_publisher("s3", cfg)
    assert isinstance(adapter, S3Publisher)
    assert isinstance(adapter, PublishingAdapter)


def test_select_s3_partial_config_names_missing() -> None:
    cfg = PublisherConfig(s3_client=FakeS3Client())  # bucket + url missing
    with pytest.raises(ConfigurationError) as exc:
        select_publisher("s3", cfg)
    assert "s3_bucket" in exc.value.fields
    assert "s3_base_url" in exc.value.fields
    assert "s3_client" not in exc.value.fields  # the one that WAS provided


def test_mock_and_builder_center_still_work_without_config() -> None:
    from app.publishing.builder_center import BuilderCenterPublisher
    from app.publishing.mock import MockPublisher

    assert isinstance(select_publisher("mock"), MockPublisher)
    assert isinstance(select_publisher("builder_center"), BuilderCenterPublisher)
