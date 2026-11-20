"""The one-command morning routine (read-only on Builder Center).

    python tools/builder_publish/engage.py daily

Opens ONE browser window with your saved login, then in order:

1. takes the badge snapshot and rewrites docs/badge-dashboard.md,
2. builds the reading list,
3. prints today's status: what is open, which streaks are at risk, the focus.

If Builder Center throttles the snapshot, the routine stops there (no more page
loads), tells you to wait, and still prints the status. Nothing is liked,
commented on, replied to or voted on; that is always you.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_OK = 0


def run(
    page: Any,
    snapshot: Callable[..., int],
    reading: Callable[[Any], Any],
    show_status: Callable[[], None],
    force: bool = False,
) -> int:
    """Run the routine against an already signed-in page.

    ``snapshot`` and ``reading`` are the real tools in production and fakes in
    tests. Returns the snapshot's exit code.
    """
    import badge_snapshot as snap

    code = snapshot(page, force=force)
    if code in (snap.EXIT_OK, snap.EXIT_SKIPPED):
        reading(page)
    elif code == snap.EXIT_THROTTLED:
        print("\nStopped before the reading list so Builder Center can cool down.")
    else:
        print("\nThe snapshot did not complete, so the reading list was skipped.")
    print()
    show_status()
    return code


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="engage.py daily", description=__doc__)
    parser.add_argument("--force", action="store_true", help="Snapshot even if already done today.")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    import badge_snapshot as snap
    import engage
    import reading_list
    from harvest_badges import LOGIN_URL, PROFILE_DIR

    def show_status() -> None:
        now = datetime.now().astimezone()
        print(engage.format_status(engage.build_status(now.date(), now)))

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to start the daily routine...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1
        code = run(page, snap.run_with_page, reading_list.run_with_page, show_status, args.force)
        context.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
