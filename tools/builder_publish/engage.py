#!/usr/bin/env python3
"""Daily engagement log and status.

You do the engagement; this records it and tells you what is still open. It
never likes, comments, replies or votes for you, and it never talks to Builder
Center on its own (the snapshot and reading-list tools do that, read-only).

Record what you did:
    python tools/builder_publish/engage.py --done like comment
Show what is open today and which streaks are at risk:
    python tools/builder_publish/engage.py
Evening reminder (macOS notification):
    python tools/builder_publish/engage.py reminders install --at 20:00

Actions: visit, like, comment, reply, vote, publish. "visit" is recorded by the
badge snapshot and "publish" by the publish tracker; the rest are yours.

Builder Center's day boundary (local time or UTC) is not documented. Nairobi is
UTC+3, so log your daily actions before 21:00 local time to be safe either way.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engagement_store  # noqa: E402
from badge_progress import BadgeProgress, badge_from_record, classify, priorities  # noqa: E402

__all__ = ["Status", "build_status", "format_status", "load_latest_badges", "main"]

# Actions that feed daily streak badges, and the habit that supports them.
DAILY_STREAK_ACTIONS = ("visit", "like", "comment")
OPTIONAL_DAILY = ("reply",)
WEEKLY_ACTIONS = ("publish", "vote")
LATE_HOUR = 20  # from this hour on, open daily actions are called out
SAFE_HOUR = 21  # log before this local hour, whatever the platform's day boundary

_WEEKLY_TEXT = {
    "publish": "publish an article this week",
    "vote": "vote on one wish this week",
}


@dataclass(frozen=True)
class Status:
    """Everything the status view shows for one day."""

    day: date
    done: list[str]
    open_daily: list[str]
    optional_open: list[str]
    at_risk: list[str]
    weekly_open: list[str]
    warnings: list[str] = field(default_factory=list)
    focus: list[str] = field(default_factory=list)


def load_latest_badges(base: Path | None = None) -> list[BadgeProgress]:
    """Badges from the most recent snapshot, or an empty list."""
    history = engagement_store.load_history(base)
    if not history:
        return []
    return [badge_from_record(r) for r in history[-1].get("badges", [])]


def _done_this_week(action: str, day: date, base: Path | None) -> bool:
    monday = day - timedelta(days=day.weekday())
    return any(
        action in engagement_store.actions_done(monday + timedelta(days=i), base)
        for i in range((day - monday).days + 1)
    )


def build_status(
    day: date,
    now: datetime,
    base: Path | None = None,
    badges: list[BadgeProgress] | None = None,
) -> Status:
    """Work out what is done, open and at risk for ``day``."""
    badges = load_latest_badges(base) if badges is None else badges
    done_map = engagement_store.actions_done(day, base)
    done = [a for a in engagement_store.ACTIONS if a in done_map]
    open_daily = [a for a in DAILY_STREAK_ACTIONS if a not in done_map]
    optional_open = [a for a in OPTIONAL_DAILY if a not in done_map]

    at_risk: list[str] = []
    for action in open_daily:
        titles = sorted(
            b.title for b in badges
            if b.state != "earned"
            and classify(b).kind == "streak"
            and classify(b).period == "daily"
            and classify(b).action == action
        )
        if titles:
            at_risk.append(f"{action}: protects {', '.join(titles)}")
        else:
            at_risk.append(f"{action}: keeps the {action} streak alive")

    weekly_open = [
        _WEEKLY_TEXT[a] for a in WEEKLY_ACTIONS if not _done_this_week(a, day, base)
    ]

    warnings: list[str] = []
    if open_daily and now.hour >= LATE_HOUR:
        warnings.append(
            f"It is {now:%H:%M} and {', '.join(open_daily)} is still open. "
            f"Log it before {SAFE_HOUR}:00 so the day counts either way."
        )
    if weekly_open and day.weekday() >= 4:
        warnings.append(f"Weekly item still open on a {day:%A}: {'; '.join(weekly_open)}.")

    focus = [f"{p.title}: {p.reason}" for p in priorities(badges)]
    return Status(day, done, open_daily, optional_open, at_risk, weekly_open, warnings, focus)


def format_status(status: Status) -> str:
    """Plain-text status view."""
    lines = [f"Engagement status for {status.day.isoformat()} ({status.day:%A})", ""]
    lines.append(f"Done today:  {', '.join(status.done) or 'nothing yet'}")
    lines.append(f"Open today:  {', '.join(status.open_daily) or 'all daily streak actions done'}")
    if status.optional_open:
        lines.append(f"Worth doing: {', '.join(status.optional_open)} (answer replies to your comments)")
    if status.at_risk:
        lines += ["", "At risk until logged:"] + [f"  - {item}" for item in status.at_risk]
    if status.weekly_open:
        lines += ["", "This week:"] + [f"  - {item}" for item in status.weekly_open]
    if status.warnings:
        lines += [""] + [f"Heads up: {w}" for w in status.warnings]
    if status.focus:
        lines += ["", "Focus (from the latest badge snapshot):"] + [f"  - {f}" for f in status.focus]
    lines += ["", "Log what you did with:  python tools/builder_publish/engage.py --done like comment"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args_in = sys.argv[1:] if argv is None else argv
    # Sub-commands with their own options are handed to their modules.
    if args_in and args_in[0] == "reminders":
        import engage_reminders

        return engage_reminders.main(args_in[1:])
    if args_in and args_in[0] == "daily":
        import engage_daily

        return engage_daily.main(args_in[1:])

    parser = argparse.ArgumentParser(
        description="Daily engagement log and status. Also: 'reminders' and 'daily' "
        "sub-commands (run them with --help)."
    )
    parser.add_argument(
        "--done", nargs="+", metavar="ACTION", choices=engagement_store.ACTIONS,
        help="Record actions you did today: " + ", ".join(engagement_store.ACTIONS),
    )
    parser.add_argument("command", nargs="?", choices=["status"], help="Show status (default).")
    args = parser.parse_args(args_in)

    if args.done:
        for action in args.done:
            new = engagement_store.record_action(action)
            print(f"{'recorded' if new else 'already recorded'}: {action}")
        print()
    now = datetime.now().astimezone()
    print(format_status(build_status(now.date(), now)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
