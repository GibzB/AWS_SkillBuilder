#!/usr/bin/env python3
"""Daily badge snapshot (READ-ONLY): read progress, update history and dashboard.

Opens your profile Badges tab with your saved login, opens each not-yet-earned
badge's popup to read its progress ("18 / 30 days"), then:

* appends the snapshot to ~/.aws-builder-publish/engagement/badge_history.jsonl,
* rewrites docs/badge-dashboard.md (progress bars, estimated dates, changes),
* records today's "visit", because a signed-in page load is a real visit.

The only clicks are on badge buttons, the popup's close button, the Badges tab
and "Load more". Nothing is liked, commented on, replied to or voted on; a test
(tests/unit/test_engagement_safety.py) enforces that.

Builder Center throttles profile lookups, so this runs at most once a day (use
--force to override), pauses between popups, and stops at the first throttle
message instead of retrying.

Usage:
    python tools/builder_publish/badge_snapshot.py
    python tools/builder_publish/badge_snapshot.py --force
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engagement_store  # noqa: E402
from badge_progress import (  # noqa: E402
    BadgeProgress,
    badge_from_record,
    diff_snapshots,
    parse_progress_text,
    render_dashboard,
    target_from_description,
)
from badge_progress import badge_to_record  # noqa: E402
from harvest_badges import (  # noqa: E402
    _EXTRACT_JS,
    BADGES_URL,
    LOGIN_URL,
    PROFILE_DIR,
    _dismiss_overlays,
    _wait_for_badges,
)

DASHBOARD_PATH = Path(__file__).resolve().parents[2] / "docs" / "badge-dashboard.md"
PAUSE_MS = 1500

THROTTLE_MARKERS = ("exceeded allowed call rate", "failed to load")
_POPUP_SELECTORS = ('[role="dialog"]', '[aria-modal="true"]', '[class*="modal"]')

EXIT_OK, EXIT_INTERRUPTED, EXIT_SKIPPED, EXIT_THROTTLED = 0, 1, 2, 3


class Throttled(Exception):
    """Builder Center refused the request because of its profile-lookup throttle."""


def looks_throttled(text: str) -> bool:
    """True if page text contains Builder Center's throttle message."""
    low = text.lower()
    return any(marker in low for marker in THROTTLE_MARKERS)


def should_run(last: date | None, today: date, force: bool) -> bool:
    """One snapshot per calendar day unless forced."""
    return force or last != today


def collect_badges(
    cards: list[dict[str, Any]],
    read_popup: Callable[[dict[str, Any]], str],
    pause: Callable[[], None],
) -> list[BadgeProgress]:
    """Build BadgeProgress for every card, reading popups for unearned ones.

    ``read_popup`` returns the popup text for a card and may raise
    :class:`Throttled`, which stops the run (nothing is retried).
    """
    badges: list[BadgeProgress] = []
    for card in cards:
        title = str(card.get("title", ""))
        description = str(card.get("description", ""))
        state = str(card.get("state", "unknown"))
        if state == "earned":
            badges.append(BadgeProgress(title, description, "earned"))
            continue
        text = read_popup(card)
        current, target, unit, percent = parse_progress_text(text)
        if target is None:
            target, unit = target_from_description(description)
        elif not unit:
            unit = target_from_description(description)[1]
        if percent is None and current is not None and target:
            percent = round(100 * current / target)
        badges.append(BadgeProgress(title, description, state, current, target, unit, percent))
        pause()
    return badges


# --------------------------------------------------------------------------- #
# Browser side (thin; all decisions are above)
# --------------------------------------------------------------------------- #

def _badge_button(page: Page, title: str):  # type: ignore[no-untyped-def]
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    return page.locator(f'[role="tabpanel"][id*="badges"] button:has(img[alt="{safe}"])').first


def _popup_text(page: Page) -> str:
    for selector in _POPUP_SELECTORS:
        loc = page.locator(selector)
        if loc.count() and loc.first.is_visible():
            return str(loc.first.inner_text())
    return ""


def _open_popup(page: Page, card: dict[str, Any]) -> str:
    """Click one badge and return its popup text."""
    button = _badge_button(page, str(card.get("title", "")))
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(900)
    text = _popup_text(page)
    page_text = str(page.evaluate("() => document.body.innerText || ''"))
    if looks_throttled(text) or (not text and looks_throttled(page_text)):
        raise Throttled(text or "throttle message on page")
    return text


def _close_popup(page: Page) -> None:
    """Close the popup with its own close button, or Escape as a fallback."""
    close = page.locator('[role="dialog"] button[aria-label*="lose"], [aria-modal="true"] button[aria-label*="lose"]')
    if close.count() and close.first.is_visible():
        close.first.click()
    else:
        page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def run_with_page(
    page: Page,
    base: Path | None = None,
    dashboard: Path = DASHBOARD_PATH,
    force: bool = False,
    today: date | None = None,
) -> int:
    """Take one snapshot using an already signed-in page. Returns an exit code."""
    today = today or date.today()
    if not should_run(engagement_store.last_snapshot_date(base), today, force):
        print("A snapshot was already taken today. Use --force to take another.")
        return EXIT_SKIPPED

    page.goto(BADGES_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    _dismiss_overlays(page)
    _wait_for_badges(page)
    cards: list[dict[str, Any]] = page.evaluate(_EXTRACT_JS)
    if not cards:
        print("No badges were found on the page. Is the Badges tab loaded and are you signed in?")
        return EXIT_INTERRUPTED

    def read(card: dict[str, Any]) -> str:
        text = _open_popup(page, card)
        _close_popup(page)
        return text

    try:
        badges = collect_badges(cards, read, lambda: page.wait_for_timeout(PAUSE_MS))
    except Throttled:
        print(
            "Builder Center is throttling profile lookups. Nothing was saved. "
            "Wait about 30 minutes and run again."
        )
        return EXIT_THROTTLED

    history = engagement_store.load_history(base)
    previous = [badge_from_record(r) for r in history[-1]["badges"]] if history else []
    changes = diff_snapshots(previous, badges) if previous else []

    record = {
        "taken_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "badges": [badge_to_record(b) for b in badges],
    }
    engagement_store.append_history(record, base)
    dashboard.write_text(render_dashboard(badges, changes, today), encoding="utf-8")
    engagement_store.record_action("visit", today, base)

    earned = sum(1 for b in badges if b.state == "earned")
    print(f"Snapshot saved: {earned} earned, {len(badges) - earned} to go. Dashboard: {dashboard}")
    for change in changes:
        print(f"  {change.kind}: {change.title} {change.before} -> {change.after}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily read-only badge snapshot.")
    parser.add_argument("--force", action="store_true", help="Run even if already done today.")
    args = parser.parse_args(argv)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to take today's badge snapshot...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return EXIT_INTERRUPTED
        code = run_with_page(page, force=args.force)
        context.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
