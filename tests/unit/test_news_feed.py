"""Unit tests for the What's New feed client and parser (no network)."""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Any

import pytest

from app.common.errors import GenerationError
from app.news.feed import ACCEPT, USER_AGENT, FeedItem, fetch_feed, is_regional, parse_feed

pytestmark = pytest.mark.unit

_SAMPLE = (Path(__file__).parent.parent / "fixtures" / "whats_new_sample.xml").read_text(
    encoding="utf-8"
)


class _Response:
    def __init__(self, body: str, status: int = 200, content_type: str = "application/xml") -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body


class _Opener:
    def __init__(self, response: _Response | Exception) -> None:
        self._response = response
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _item(title: str) -> FeedItem:
    return FeedItem("g", title, "", "", (), "https://x/")


def test_fetch_sends_browser_user_agent_and_rss_accept_header() -> None:
    opener = _Opener(_Response(_SAMPLE))
    items = fetch_feed(opener=opener, timeout=7)
    request = opener.requests[0]
    assert request.get_header("User-agent") == USER_AGENT
    assert "Mozilla/5.0" in USER_AGENT
    assert request.get_header("Accept") == ACCEPT
    assert ACCEPT.startswith("application/rss+xml")
    assert opener.timeouts == [7]
    assert len(items) == 7


def test_parse_extracts_fields_and_strips_html() -> None:
    items = {i.guid: i for i in parse_feed(_SAMPLE)}
    lam = items["guid-lambda-1"]
    assert lam.title.startswith("AWS Lambda now supports")
    assert "<" not in lam.description_text and "&lt;" not in lam.description_text
    assert "Use the larger batch size" in lam.description_text
    assert lam.published_iso == "2026-11-24T10:00:00Z"
    assert lam.service_slug == "aws-lambda"
    assert lam.link.endswith("/lambda-sqs-batch/")
    assert "marketing:marchitecture/serverless" in lam.categories
    assert items["guid-bedrock-1"].description_text.count("&") == 1


def test_generic_product_slug_is_skipped() -> None:
    bedrock = {i.guid: i for i in parse_feed(_SAMPLE)}["guid-bedrock-1"]
    assert bedrock.service_slug == "amazon-bedrock"


def test_item_without_category_is_kept_with_empty_slug() -> None:
    item = {i.guid: i for i in parse_feed(_SAMPLE)}["guid-nocat-1"]
    assert item.categories == ()
    assert item.service_slug == ""


def test_items_missing_link_are_skipped() -> None:
    assert "guid-incomplete-1" not in {i.guid for i in parse_feed(_SAMPLE)}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Amazon OpenSearch Serverless is now available in the Africa (Cape Town) Region", True),
        ("Amazon CloudFront is now available in the AWS European Sovereign Cloud", True),
        ("Amazon Cognito extends multi-Region replication to additional AWS Regions", True),
        ("Service X expands to 3 new Regions", True),
        ("Demand Center now available in AWS Partner Central", False),
        ("AWS Lambda now supports a batch size of up to 10,000 messages", False),
        ("Amazon Connect Customer expands automated evaluations to 45 new languages", False),
    ],
)
def test_is_regional(title: str, expected: bool) -> None:
    assert is_regional(_item(title)) is expected


def test_sample_has_exactly_one_regional_item() -> None:
    regional = [i.guid for i in parse_feed(_SAMPLE) if is_regional(i)]
    assert regional == ["guid-regional-1"]


def test_html_404_body_is_rejected() -> None:
    opener = _Opener(_Response("<!DOCTYPE html><html><body>404</body></html>", content_type="text/html"))
    with pytest.raises(GenerationError) as caught:
        fetch_feed(opener=opener)
    assert caught.value.reason == "unavailable"
    assert "HTML" in caught.value.message


def test_html_body_with_xml_content_type_is_rejected() -> None:
    opener = _Opener(_Response("<html><body>oops</body></html>", content_type="application/xml"))
    with pytest.raises(GenerationError):
        fetch_feed(opener=opener)


def test_non_200_is_rejected() -> None:
    with pytest.raises(GenerationError) as caught:
        fetch_feed(opener=_Opener(_Response("", status=503)))
    assert "503" in caught.value.message


def test_network_error_is_unavailable() -> None:
    with pytest.raises(GenerationError) as caught:
        fetch_feed(opener=_Opener(TimeoutError("slow")))
    assert caught.value.reason == "unavailable"


def test_malformed_xml_is_rejected() -> None:
    with pytest.raises(GenerationError):
        fetch_feed(opener=_Opener(_Response("<?xml version='1.0'?><rss><channel><item>")))
