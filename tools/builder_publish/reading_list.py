#!/usr/bin/env python3
"""Daily reading list (READ-ONLY): recent Builder Center items worth your time.

Loads the home feed once with your saved login, scrolls to render more cards,
and writes up to 15 links to ~/.aws-builder-publish/engagement/reading_list.md.
You open them, read them, and like or comment only on what genuinely earned it.

This module never clicks anything: it only scrolls and reads the page. It has no
code that likes, comments, replies or votes, and a test
(tests/unit/test_engagement_safety.py) fails if that ever changes.

Usage:
    python tools/builder_publish/reading_list.py
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engagement_store  # noqa: E402
from harvest_badges import LOGIN_URL, PROFILE_DIR  # noqa: E402

HOME_URL = "https://builder.aws.com/"
OWN_HANDLE = "gibzb"
LIMIT = 15
SCROLLS = 6

# Words that mark an item as relevant to the AWS fundamentals this series covers.
_TOPIC_WORDS = (
    "aws", "amazon", "s3", "ec2", "vpc", "iam", "lambda", "dynamodb", "rds",
    "cloudwatch", "api gateway", "eventbridge", "sns", "sqs", "terraform",
    "cloudformation", "serverless", "security", "cost", "bedrock", "cloudfront",
    "route 53", "kms", "step functions", "beginner", "fundamentals",
)

# Reads card details from the page. Anchors to /content/<id>/<slug> are the
# articles; the closest card ancestor supplies the author and age text.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/content/"]')) {
    const title = norm(a.innerText);
    if (!title) continue;
    const card = a.closest('article, li, [role="article"], [role="listitem"], [class*="card"], [class*="Card"]')
      || a.parentElement;
    const cardText = norm(card ? card.innerText : '');
    const profile = card ? card.querySelector('a[href*="/profile"], a[href^="/@"]') : null;
    out.push({
      href: a.getAttribute('href') || '',
      title,
      cardText: cardText.slice(0, 600),
      author: profile ? norm(profile.innerText) : '',
      authorHref: profile ? (profile.getAttribute('href') || '') : '',
    });
  }
  return out;
}
"""


def normalize_url(href: str) -> str:
    """Absolute https URL without query or fragment."""
    parts = urlsplit(urljoin(HOME_URL, href))
    return f"https://{parts.netloc}{parts.path}".rstrip("/")


def age_hours(text: str) -> float | None:
    """Rough age in hours from text such as '3 hours ago' or 'just now'."""
    low = text.lower()
    if "just now" in low or "moments ago" in low:
        return 0.0
    match = re.search(r"(\d+)\s*(minute|min|hour|hr|day|week|month)s?\b", low)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    scale = {"minute": 1 / 60, "min": 1 / 60, "hour": 1, "hr": 1, "day": 24, "week": 168, "month": 720}
    return value * scale[unit]


def topic_score(text: str) -> int:
    """How many fundamentals keywords appear in the text."""
    low = text.lower()
    return sum(1 for word in _TOPIC_WORDS if re.search(rf"\b{re.escape(word)}\b", low))


def is_own(item: dict[str, Any], handle: str = OWN_HANDLE) -> bool:
    """True for the user's own posts, by author name, profile link or card text."""
    haystack = " ".join(str(item.get(k, "")) for k in ("author", "authorHref", "cardText")).lower()
    return handle.lower() in haystack


def select_items(
    raw: list[dict[str, Any]], handle: str = OWN_HANDLE, limit: int = LIMIT
) -> list[dict[str, Any]]:
    """Dedupe, drop own posts, and rank by topic relevance then recency."""
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for position, entry in enumerate(raw):
        url = normalize_url(str(entry.get("href", "")))
        if "/content/" not in url or url in seen or is_own(entry, handle):
            continue
        seen.add(url)
        text = f"{entry.get('title', '')} {entry.get('cardText', '')}"
        items.append({
            "title": str(entry.get("title", "")).strip(),
            "url": url,
            "author": str(entry.get("author", "")).strip(),
            "age": age_hours(str(entry.get("cardText", ""))),
            "score": topic_score(text),
            "position": position,
        })
    items.sort(key=lambda i: (-i["score"], i["age"] if i["age"] is not None else 9999.0, i["position"]))
    return items[:limit]


def _age_label(hours: float | None) -> str:
    if hours is None:
        return ""
    if hours < 1:
        return "under an hour old"
    if hours < 48:
        return f"about {round(hours)}h old"
    return f"about {round(hours / 24)} days old"


def render_markdown(items: list[dict[str, Any]], today: date) -> str:
    """The reading list as Markdown. No em dashes, no personal data."""
    lines = [
        f"# Reading list for {today.isoformat()}",
        "",
        "Read first. Like or comment only on what genuinely helped you, and say "
        "something specific in a comment (what you learned, what you would add). "
        "Comments that start a conversation also feed the Conversation Starter badge.",
        "",
    ]
    if not items:
        lines.append("No items were found. The feed may not have loaded; try again later.")
    for number, item in enumerate(items, 1):
        by = f" by {item['author']}" if item["author"] else ""
        age = _age_label(item["age"])
        extra = f" ({age})" if age else ""
        lines.append(f"{number}. [{item['title']}]({item['url']}){by}{extra}")
    lines += ["", "When you are done: python tools/builder_publish/engage.py --done like comment", ""]
    return "\n".join(lines)


def run_with_page(
    page: Page, base: Path | None = None, today: date | None = None
) -> list[dict[str, Any]]:
    """Load the feed once, read the cards and write the list. Clicks nothing."""
    today = today or date.today()
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    for _ in range(SCROLLS):
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(700)
    raw: list[dict[str, Any]] = page.evaluate(_EXTRACT_JS)
    items = select_items(raw)
    target = (base if base is not None else engagement_store.DATA_DIR) / "reading_list.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(items, today)
    target.write_text(text, encoding="utf-8")
    print(text)
    print(f"Saved to {target}")
    return items


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Write a read-only daily reading list.").parse_args(argv)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to build today's reading list...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1
        run_with_page(page)
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
