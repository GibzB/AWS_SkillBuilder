"""Tests for the read-only reading list."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import reading_list as rl  # noqa: E402

pytestmark = pytest.mark.unit

TODAY = date(2026, 11, 19)


def _raw(title: str, slug: str, card: str = "", author: str = "Someone") -> dict[str, Any]:
    return {
        "href": f"/content/abc123/{slug}?ref=feed#top",
        "title": title,
        "cardText": card or title,
        "author": author,
        "authorHref": "/profile/other",
    }


def test_normalize_url_strips_query_and_fragment() -> None:
    assert rl.normalize_url("/content/abc/slug?x=1#y") == "https://builder.aws.com/content/abc/slug"
    assert rl.normalize_url("https://builder.aws.com/content/abc/slug/") == "https://builder.aws.com/content/abc/slug"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("posted 3 hours ago", 3.0), ("2 days ago", 48.0), ("just now", 0.0), ("no age here", None)],
)
def test_age_hours(text: str, expected: float | None) -> None:
    assert rl.age_hours(text) == expected


def test_own_posts_are_excluded() -> None:
    raw = [
        _raw("Mine", "mine", author="GibzB"),
        {**_raw("Also mine", "mine2"), "authorHref": "/profile/@gibzb"},
        _raw("Theirs", "theirs"),
    ]
    assert [i["title"] for i in rl.select_items(raw)] == ["Theirs"]


def test_duplicates_and_non_article_links_are_dropped() -> None:
    raw = [
        _raw("One", "same"),
        _raw("One again", "same"),
        {"href": "/events/foo", "title": "Event", "cardText": "Event", "author": "", "authorHref": ""},
    ]
    assert [i["title"] for i in rl.select_items(raw)] == ["One"]


def test_ranking_prefers_aws_topics_then_recency() -> None:
    raw = [
        _raw("Cooking tips", "cook", card="Cooking tips 1 hours ago"),
        _raw("AWS Lambda basics", "lambda", card="AWS Lambda basics 30 hours ago"),
        _raw("IAM and S3 security on AWS", "iam", card="IAM and S3 security on AWS 5 hours ago"),
    ]
    assert [i["title"] for i in rl.select_items(raw)] == [
        "IAM and S3 security on AWS", "AWS Lambda basics", "Cooking tips",
    ]


def test_limit_is_respected() -> None:
    raw = [_raw(f"AWS item {n}", f"item-{n}") for n in range(40)]
    assert len(rl.select_items(raw)) == rl.LIMIT


def test_markdown_has_links_and_guidance_without_em_dashes() -> None:
    items = rl.select_items([_raw("AWS Lambda basics", "lambda", card="AWS Lambda basics 3 hours ago")])
    text = rl.render_markdown(items, TODAY)
    assert "[AWS Lambda basics](https://builder.aws.com/content/abc123/lambda)" in text
    assert "about 3h old" in text and "Conversation Starter" in text
    assert "\u2014" not in text


def test_empty_feed_message() -> None:
    assert "No items were found" in rl.render_markdown([], TODAY)


class FakeMouse:
    def __init__(self) -> None:
        self.scrolled = 0

    def wheel(self, _x: int, _y: int) -> None:
        self.scrolled += 1


class FakePage:
    def __init__(self, raw: list[dict[str, Any]]) -> None:
        self.raw = raw
        self.mouse = FakeMouse()
        self.visited: list[str] = []

    def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)

    def wait_for_timeout(self, _: int) -> None:
        return None

    def evaluate(self, _: str) -> list[dict[str, Any]]:
        return self.raw


def test_run_loads_the_feed_once_scrolls_and_writes_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage([_raw("AWS Lambda basics", "lambda")])
    items = rl.run_with_page(page, tmp_path, TODAY)  # type: ignore[arg-type]
    assert page.visited == [rl.HOME_URL]
    assert page.mouse.scrolled == rl.SCROLLS
    assert len(items) == 1
    assert "AWS Lambda basics" in (tmp_path / "reading_list.md").read_text(encoding="utf-8")


def test_extraction_script_against_sample_markup() -> None:
    """Run the real in-page script on sample HTML (skipped without a browser)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    html = """
    <ul>
      <li><a href="/profile/jane">Jane</a> 3 hours ago
          <a href="/content/aaa/lambda-basics?x=1">AWS Lambda basics</a></li>
      <li><a href="/profile/gibzb">GibzB</a>
          <a href="/content/bbb/my-post">My own post</a></li>
      <li><a href="/about">About</a></li>
    </ul>"""
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            raw = page.evaluate(rl._EXTRACT_JS)
            browser.close()
    except Exception as error:  # noqa: BLE001 - no browser installed on this machine
        pytest.skip(f"no usable browser: {error}")
    titles = [i["title"] for i in rl.select_items(raw)]
    assert titles == ["AWS Lambda basics"]
