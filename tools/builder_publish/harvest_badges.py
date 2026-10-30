#!/usr/bin/env python3
"""Harvest Builder Center badges — earned and not-yet-earned — with their text.

Assisted pattern (same as the tag harvester): launch a persistent Chrome
context so your existing login is reused, wait for you to confirm you are
signed in, then navigate to the profile Badges tab and scrape every badge
card's text (name, description, earned/locked state, and any progress/date).

The Badges tab is a client-rendered grid of cards. We don't know the exact
component markup ahead of time and it may change, so the scraper is defensive:

1. It waits for badge-like content to render.
2. It walks the DOM for card-shaped containers and extracts their visible text,
   an image/icon alt or aria-label, and whether the card looks "locked"
   (dimmed / aria-disabled / a lock affordance) vs "earned".
3. It ALSO writes a raw snapshot (full rendered text + outerHTML of the badges
   region + a screenshot) so nothing is lost even if the structured parse
   misses a field. You can eyeball the raw dump and we can tighten selectors.

View-only; does not create content, so it is not subject to the article
create-rate limit (be mindful of the profile-API throttle — one run is fine).

Usage:
    AWS_PROFILE=... python tools/builder_publish/harvest_badges.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://builder.aws.com/"
BADGES_URL = "https://builder.aws.com/profile?tab=badges"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
OUT_JSON = OUT_DIR / "badges.json"
OUT_RAW_TEXT = OUT_DIR / "badges_raw.txt"
OUT_RAW_HTML = OUT_DIR / "badges_raw.html"
OUT_PNG = OUT_DIR / "badges_page.png"


# JavaScript run in the page to extract badge cards structurally.
#
# The Builder Center badges grid renders each badge as a <button> that carries:
#   - aria-label / title  -> the badge DESCRIPTION (what earns it)
#   - an <img alt="...">   -> the badge NAME
#   - an <img src="...">   -> encodes state in the filename:
#         *_Complete.svg            -> earned
#         *_InProgress_(Light|Dark) -> not yet earned (in progress)
#         *_Locked* / otherwise     -> locked / unknown
# There is no visible text, which is why a text-based heuristic found nothing.
_EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // Scope to the active badges tab panel; fall back to main/body.
  const root =
      document.querySelector('[role="tabpanel"][id*="badges"]') ||
      document.querySelector('[role="tabpanel"]:not([hidden])') ||
      document.querySelector('main') ||
      document.body;

  // Each badge is a button containing a badge image.
  const buttons = Array.from(root.querySelectorAll('button'))
      .filter(b => b.querySelector('img') &&
                   !/load more/i.test(norm(b.innerText)) &&
                   (b.getAttribute('aria-label') || b.getAttribute('title')));

  const seen = new Set();
  const cards = [];

  for (const b of buttons) {
    const description = norm(b.getAttribute('aria-label') || b.getAttribute('title') || '');
    const img = b.querySelector('img[alt]') || b.querySelector('img');
    const title = img ? norm(img.getAttribute('alt') || '') : '';
    const src = img ? (img.getAttribute('src') || '') : '';

    const key = (title + '|' + description).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);

    // State from the SVG asset filename.
    let state = 'unknown';
    if (/_complete/i.test(src)) state = 'earned';
    else if (/_inprogress|_in_progress/i.test(src)) state = 'in_progress';
    else if (/_notstarted|_not_started/i.test(src)) state = 'not_started';
    else if (/_locked/i.test(src)) state = 'locked';

    cards.push({ title, description, state, iconSrc: src });
  }
  return cards;
}
"""


# Count badge buttons currently rendered (used to know when "Load more" stops).
_COUNT_JS = r"""
() => {
  const root =
      document.querySelector('[role="tabpanel"][id*="badges"]') ||
      document.querySelector('[role="tabpanel"]:not([hidden])') ||
      document.querySelector('main') ||
      document.body;
  return Array.from(root.querySelectorAll('button'))
      .filter(b => b.querySelector('img') && !/load more/i.test((b.innerText||'')))
      .length;
}
"""


def _dismiss_overlays(page: Page) -> None:
    """Best-effort dismissal of cookie/consent banners that cover content."""
    for label in ["Accept all", "Accept All", "Accept", "I agree", "Got it", "Close"]:
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass


def _activate_badges_tab(page: Page) -> None:
    """Click the profile 'Badges' tab so its panel actually renders.

    The ?tab=badges URL loads the profile shell but Builder Center only mounts
    the tab's content after the tab control is clicked. We try the tab role
    first, then a plain text/link fallback.
    """
    page.wait_for_timeout(1000)
    clickers = [
        lambda: page.get_by_role("tab", name="Badges"),
        lambda: page.get_by_role("link", name="Badges"),
        lambda: page.get_by_text("Badges", exact=True),
        lambda: page.locator('a[href*="tab=badges"]'),
        lambda: page.locator('[role="tab"]:has-text("Badges")'),
    ]
    for make in clickers:
        try:
            loc = make()
            if loc.count() and loc.first.is_visible():
                loc.first.scroll_into_view_if_needed()
                loc.first.click()
                page.wait_for_timeout(1500)
                return
        except Exception:  # noqa: BLE001
            continue


def _wait_for_badges(page: Page) -> None:
    """Give the client-rendered badges grid time to populate."""
    page.wait_for_load_state("domcontentloaded")
    _activate_badges_tab(page)
    # Try to wait for any badge-ish content; fall back to a fixed settle time.
    for _ in range(24):
        try:
            txt = page.evaluate("() => document.body.innerText || ''")
        except Exception:  # noqa: BLE001
            txt = ""
        low = txt.lower()
        # Look for badge-specific wording that only appears once the panel mounts.
        if any(k in low for k in ("earned", "to earn", "locked", "badge", "streak", "achievement")):
            break
        page.wait_for_timeout(500)
    # Scroll to force any lazy/virtualized cards to render.
    try:
        for _ in range(8):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(350)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception:  # noqa: BLE001
        pass
    _load_all_badges(page)


def _load_all_badges(page: Page) -> None:
    """Click 'Load more' until the full badge set is rendered.

    The grid paginates (10 at a time). We click the Load more button, wait for
    the badge count to grow, and stop when it no longer increases or the button
    disappears. Bounded so a stuck button can't loop forever.
    """
    try:
        prev = page.evaluate(_COUNT_JS)
    except Exception:  # noqa: BLE001
        prev = 0
    for _ in range(30):
        try:
            btn = page.get_by_role("button", name="Load more")
            if not btn.count() or not btn.first.is_visible():
                break
            btn.first.scroll_into_view_if_needed()
            btn.first.click()
        except Exception:  # noqa: BLE001
            break
        # Wait for the count to increase.
        grew = False
        for _ in range(12):
            page.wait_for_timeout(300)
            try:
                now = page.evaluate(_COUNT_JS)
            except Exception:  # noqa: BLE001
                now = prev
            if now > prev:
                prev = now
                grew = True
                break
        if not grew:
            break
    # Scroll back to top so a full-page screenshot starts cleanly.
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to harvest your badges...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(BADGES_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        _dismiss_overlays(page)
        _wait_for_badges(page)

        # Structured extraction.
        try:
            cards = page.evaluate(_EXTRACT_JS)
        except Exception as error:  # noqa: BLE001
            print(f"Structured extraction failed: {error}")
            cards = []

        # Raw snapshots so nothing is lost regardless of parsing accuracy.
        # Scope to the active tab panel when present; else fall back to body.
        try:
            body_text = page.evaluate(
                "() => { const p = document.querySelector('[role=\"tabpanel\"]:not([hidden])') "
                "|| document.querySelector('[role=\"tabpanel\"]') "
                "|| document.querySelector('main') || document.body; "
                "return (p && p.innerText) || document.body.innerText || ''; }"
            )
        except Exception:  # noqa: BLE001
            body_text = ""
        try:
            region_html = page.evaluate(
                "() => { const p = document.querySelector('[role=\"tabpanel\"]:not([hidden])') "
                "|| document.querySelector('[role=\"tabpanel\"]') "
                "|| document.querySelector('main') || document.body; "
                "return p ? p.outerHTML : ''; }"
            )
        except Exception:  # noqa: BLE001
            region_html = ""

        try:
            page.screenshot(path=str(OUT_PNG), full_page=True)
        except Exception:  # noqa: BLE001
            pass

        context.close()

    # Split by state for a readable summary.
    earned = [c for c in cards if c.get("state") == "earned"]
    in_progress = [c for c in cards if c.get("state") == "in_progress"]
    not_started = [c for c in cards if c.get("state") == "not_started"]
    locked = [c for c in cards if c.get("state") == "locked"]
    unknown = [c for c in cards if c.get("state") == "unknown"]

    result = {
        "source": BADGES_URL,
        "count": len(cards),
        "earned": earned,
        "in_progress": in_progress,
        "not_started": not_started,
        "locked": locked,
        "unknown": unknown,
        "all": cards,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    OUT_RAW_TEXT.write_text(body_text, encoding="utf-8")
    OUT_RAW_HTML.write_text(region_html, encoding="utf-8")

    print(f"\nParsed {len(cards)} badges "
          f"(earned={len(earned)}, in_progress={len(in_progress)}, "
          f"not_started={len(not_started)}, locked={len(locked)}, "
          f"unknown={len(unknown)}).")
    print(f"  structured -> {OUT_JSON}")
    print(f"  raw text   -> {OUT_RAW_TEXT}")
    print(f"  raw html   -> {OUT_RAW_HTML}")
    print(f"  screenshot -> {OUT_PNG}")

    def _preview(title: str, items: list[dict]) -> None:
        if not items:
            return
        print(f"\n{title}:")
        for c in items:
            name = c.get("title") or "(untitled)"
            desc = c.get("description") or ""
            print(f"  - {name}" + (f" — {desc}" if desc else ""))

    _preview("EARNED", earned)
    _preview("IN PROGRESS", in_progress)
    _preview("NOT STARTED", not_started)
    _preview("LOCKED", locked)
    _preview("UNCLASSIFIED (check raw dump)", unknown)

    if not cards:
        print("\nNo cards parsed. Open badges_raw.txt / badges_page.png — the "
              "markup may differ; share them and I'll tighten the selectors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
