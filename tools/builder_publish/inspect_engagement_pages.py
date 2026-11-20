#!/usr/bin/env python3
"""Capture the real markup the engagement tooling will read (READ-ONLY).

Run once, by you, with your saved Builder Center login. It does exactly two
things and nothing else:

1. Opens the profile Badges tab and clicks ONE not-yet-earned badge so its
   popup (title, description, progress bar, "18 / 30 days", "60%") is on screen,
   then saves the popup's text and HTML.
2. Loads the home feed once and saves its text and HTML.

Nothing is liked, commented on, replied to or voted on. The saved files are
test fixtures: the parsing code is written against what Builder Center really
renders instead of guesses. Before they are committed, ``redact`` removes
anything that looks like an email address or a long token.

Usage:
    python tools/builder_publish/inspect_engagement_pages.py
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HOME_URL = "https://builder.aws.com/"
BADGES_URL = "https://builder.aws.com/profile?tab=badges"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# Not-yet-earned badges use these asset suffixes (see harvest_badges.py).
_GREY_BADGE_BUTTON = (
    '[role="tabpanel"][id*="badges"] button:has(img[src*="_NotStarted"]), '
    '[role="tabpanel"][id*="badges"] button:has(img[src*="_InProgress"])'
)
_POPUP_CANDIDATES = ('[role="dialog"]', '[aria-modal="true"]', '[class*="modal"]')

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")


def redact(text: str) -> str:
    """Remove email addresses and long opaque tokens from captured markup."""
    return _LONG_TOKEN.sub("[token]", _EMAIL.sub("[email]", text))


def _popup_text_and_html(page: Page) -> tuple[str, str]:
    for selector in _POPUP_CANDIDATES:
        loc = page.locator(selector)
        if loc.count() and loc.first.is_visible():
            return loc.first.inner_text(), loc.first.evaluate("e => e.outerHTML")
    return "", ""


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to capture the two pages...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        # 1. One grey badge popup.
        page.goto(BADGES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        tab = page.get_by_role("tab", name="Badges")
        if tab.count():
            tab.first.click()
            page.wait_for_timeout(1500)
        grey = page.locator(_GREY_BADGE_BUTTON)
        print(f"grey badge buttons found: {grey.count()}")
        if grey.count():
            grey.first.click()
            page.wait_for_timeout(1200)
            text, html = _popup_text_and_html(page)
            if not text:
                # Fall back to the whole panel so the popup is still captured.
                text = page.evaluate("() => document.body.innerText || ''")
                html = page.evaluate("() => document.body.outerHTML")
                print("no dialog element matched; saved the full page instead")
            (FIXTURES / "badge_popup_real.txt").write_text(redact(text), encoding="utf-8")
            (FIXTURES / "badge_popup_real.html").write_text(redact(html), encoding="utf-8")
            print(f"popup text:\n{text[:400]}")
        else:
            print("no grey badge found; is the Badges tab loaded?")

        # 2. The home feed, once.
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        feed_text = page.evaluate("() => document.body.innerText || ''")
        feed_html = page.evaluate(
            "() => (document.querySelector('main') || document.body).outerHTML"
        )
        (FIXTURES / "feed_real.txt").write_text(redact(feed_text), encoding="utf-8")
        (FIXTURES / "feed_real.html").write_text(redact(feed_html), encoding="utf-8")
        print(f"feed captured: {len(feed_text)} characters of text")
        context.close()

    print(f"\nSaved fixtures to {FIXTURES}. Tell me when it is done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
