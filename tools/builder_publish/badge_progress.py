#!/usr/bin/env python3
"""Badge progress core: parse, classify, estimate, diff and render (pure logic).

Builder Center shows each not-yet-earned badge with a popup such as
"Visit Builder Center at least once a day for 30 consecutive days ... 60% ...
18 / 30 days". This module turns that text into numbers and answers the daily
questions: what does each badge need from me, when will it land if I keep going,
what changed since the last snapshot, and did a streak reset.

Everything here is pure (no browser, no network, no clock unless passed in), so
it is fully unit-tested. The Playwright code in ``badge_snapshot.py`` only
fetches text and hands it to these functions.

Boundary: this module describes and tracks. It never performs likes, comments,
replies or votes; those are always done by the user.

Usage (prints a table from a saved snapshot):
    python tools/builder_publish/badge_progress.py --snapshot snapshot.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "BadgeProgress",
    "Playbook",
    "Change",
    "Priority",
    "parse_progress_text",
    "target_from_description",
    "classify",
    "estimate_finish",
    "diff_snapshots",
    "priorities",
    "progress_bar",
    "render_dashboard",
    "badge_from_record",
]

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_FRACTION = re.compile(r"(\d+)\s*/\s*(\d+)\s*(day|week)?s?", re.IGNORECASE)
_PERCENT = re.compile(r"(\d{1,3})\s*%")
_TARGET = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+consecutive\s+(day|week)s?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BadgeProgress:
    """One badge as last seen on the profile.

    ``state`` is earned, in_progress, not_started or unknown. ``current`` and
    ``target`` are in ``unit`` (days or weeks) when Builder Center reports them.
    """

    title: str
    description: str
    state: str
    current: int | None = None
    target: int | None = None
    unit: str = ""
    percent: int | None = None


@dataclass(frozen=True)
class Playbook:
    """What a badge needs from the user.

    ``kind`` is streak (repeat an action), impact (other people must react, so no
    date can be promised) or oneoff. ``action`` is visit, like, comment, publish
    or vote for streaks and None otherwise. ``period`` is daily or weekly.
    """

    kind: str
    action: str | None = None
    period: str | None = None


@dataclass(frozen=True)
class Change:
    """A difference between two snapshots for one badge."""

    kind: str  # earned, progress, reset, new
    title: str
    before: str
    after: str


@dataclass(frozen=True)
class Priority:
    """A suggested focus for today."""

    title: str
    action: str
    reason: str


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_progress_text(text: str) -> tuple[int | None, int | None, str, int | None]:
    """Return (current, target, unit, percent) found in popup text.

    Missing parts come back as None / "". A fraction without a unit keeps the
    unit empty so the caller can fall back to the description.
    """
    current = target = None
    unit = ""
    fraction = _FRACTION.search(text)
    if fraction:
        current, target = int(fraction.group(1)), int(fraction.group(2))
        unit = (fraction.group(3) or "").lower()
        if unit:
            unit += "s"
    percent_match = _PERCENT.search(text)
    percent = int(percent_match.group(1)) if percent_match else None
    if percent is None and current is not None and target:
        percent = round(100 * current / target)
    return current, target, unit, percent


def target_from_description(description: str) -> tuple[int | None, str]:
    """Read "7 consecutive days" / "four consecutive weeks" from a description."""
    match = _TARGET.search(description)
    if not match:
        return None, ""
    raw = match.group(1).lower()
    value = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return value, match.group(2).lower() + "s"


# --------------------------------------------------------------------------- #
# Classification and estimates
# --------------------------------------------------------------------------- #

def classify(badge: BadgeProgress) -> Playbook:
    """Map a badge to the action it needs, using its description."""
    text = badge.description.lower().strip()
    if text.startswith("get "):
        return Playbook("impact")
    weekly = "weekly" in text or "each week" in text or "consecutive weeks" in text
    period = "weekly" if weekly else "daily"
    if text.startswith("visit"):
        return Playbook("streak", "visit", period)
    if "consecutive" in text or weekly:
        if "like" in text:
            return Playbook("streak", "like", period)
        if "comment" in text:
            return Playbook("streak", "comment", period)
        if text.startswith("publish"):
            return Playbook("streak", "publish", period)
        if text.startswith("vote"):
            return Playbook("streak", "vote", period)
    return Playbook("oneoff")


def _resolved_target(badge: BadgeProgress) -> tuple[int | None, str]:
    if badge.target is not None:
        return badge.target, badge.unit or target_from_description(badge.description)[1]
    return target_from_description(badge.description)


def estimate_finish(badge: BadgeProgress, today: date) -> date | None:
    """Date the badge lands if the user keeps the streak unbroken, else None.

    Only streak badges get a date. Impact badges depend on other people, and
    earned badges are done. A badge with no reported progress counts from zero.
    """
    if badge.state == "earned" or classify(badge).kind != "streak":
        return None
    target, unit = _resolved_target(badge)
    if target is None:
        return None
    remaining = max(target - (badge.current or 0), 0)
    step = 7 if unit == "weeks" else 1
    return today + timedelta(days=remaining * step)


def progress_bar(percent: int | None, width: int = 10) -> str:
    """A small text bar such as ``######---- 60%``."""
    if percent is None:
        return "-" * width + " n/a"
    filled = max(0, min(width, round(width * percent / 100)))
    return "#" * filled + "-" * (width - filled) + f" {percent}%"


# --------------------------------------------------------------------------- #
# Snapshot comparison and priorities
# --------------------------------------------------------------------------- #

def _describe(badge: BadgeProgress) -> str:
    if badge.state == "earned":
        return "earned"
    if badge.current is not None and badge.target is not None:
        return f"{badge.current}/{badge.target} {badge.unit}".strip()
    return badge.state


def diff_snapshots(
    previous: list[BadgeProgress], current: list[BadgeProgress]
) -> list[Change]:
    """List what changed between two snapshots, including streak resets."""
    before = {b.title: b for b in previous}
    changes: list[Change] = []
    for badge in current:
        old = before.get(badge.title)
        if old is None:
            changes.append(Change("new", badge.title, "", _describe(badge)))
        elif old.state != "earned" and badge.state == "earned":
            changes.append(Change("earned", badge.title, _describe(old), "earned"))
        elif (
            old.current is not None
            and badge.current is not None
            and badge.current < old.current
            and classify(badge).kind == "streak"
        ):
            changes.append(Change("reset", badge.title, _describe(old), _describe(badge)))
        elif old.current != badge.current and badge.current is not None:
            changes.append(Change("progress", badge.title, _describe(old), _describe(badge)))
    return changes


def priorities(badges: list[BadgeProgress], limit: int = 3) -> list[Priority]:
    """Suggest what to focus on today.

    First the gaps: an action whose shortest streak badge is still unearned (for
    example comments while the 7-Day Like Streak is already earned), because the
    longer streaks for that action cannot finish before the short one does. Then
    the streaks closest to landing.
    """
    streaks = [
        (b, classify(b)) for b in badges
        if b.state != "earned" and classify(b).kind == "streak"
    ]

    # Shortest daily streak badge per action, earned or not.
    shortest: dict[str, BadgeProgress] = {}
    for badge in badges:
        play = classify(badge)
        if play.kind != "streak" or play.period != "daily" or not play.action:
            continue
        size = _resolved_target(badge)[0] or 10_000
        known = shortest.get(play.action)
        if known is None or size < (_resolved_target(known)[0] or 10_000):
            shortest[play.action] = badge
    earned_actions = sorted(a for a, b in shortest.items() if b.state == "earned")

    result: list[Priority] = []
    if earned_actions:
        for action, badge in sorted(shortest.items()):
            if badge.state != "earned":
                result.append(Priority(
                    badge.title, action,
                    f"your {earned_actions[0]} streak is earned but {action} is not; "
                    f"do one {action} every day until this lands",
                ))
    today = date(2000, 1, 1)
    closest = sorted(
        (b for b, _ in streaks if (b.current or 0) > 0),
        key=lambda b: (estimate_finish(b, today) or date.max),
    )
    listed = {p.title for p in result}
    for badge in closest:
        if len(result) >= limit:
            break
        if badge.title in listed:
            continue
        action = classify(badge).action or ""
        finish_in = ((estimate_finish(badge, today) or today) - today).days
        result.append(Priority(
            badge.title, action,
            f"{_describe(badge)}; keep the chain unbroken ({finish_in} days to go)",
        ))
    return result[:limit]


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

_NEEDS = {
    ("visit", "daily"): "visit daily",
    ("like", "daily"): "like something daily",
    ("comment", "daily"): "comment daily",
    ("publish", "weekly"): "publish weekly",
    ("vote", "weekly"): "vote on a wish weekly",
}


def _needs(badge: BadgeProgress) -> str:
    play = classify(badge)
    if play.kind == "impact":
        return "other people must react"
    if play.kind == "streak":
        return _NEEDS.get((play.action or "", play.period or ""), "repeat the action")
    return "one-off"


def render_dashboard(
    badges: list[BadgeProgress], changes: list[Change], today: date
) -> str:
    """Render docs/badge-dashboard.md. Plain text, no personal data."""
    earned = [b for b in badges if b.state == "earned"]
    open_ = [b for b in badges if b.state != "earned"]
    lines = [
        "# Builder Center badge dashboard",
        "",
        f"Updated {today.isoformat()} by `tools/builder_publish/badge_snapshot.py`. "
        "Do not edit by hand; it is rewritten on every snapshot.",
        "",
        f"**{len(earned)} earned, {len(open_)} to go** ({len(badges)} total).",
        "",
        "## Today's priorities",
        "",
    ]
    picks = priorities(badges)
    lines += (
        [f"{i}. **{p.title}** ({p.action}): {p.reason}" for i, p in enumerate(picks, 1)]
        or ["Nothing urgent. Keep the daily habit going."]
    )
    lines += ["", "## Changes since the last snapshot", ""]
    lines += (
        [f"- {c.kind}: **{c.title}** {c.before} -> {c.after}".replace("  ", " ") for c in changes]
        or ["- No change."]
    )
    lines += [
        "",
        "## Still to earn",
        "",
        "| Badge | Needs | Progress | Estimated finish |",
        "|---|---|---|---|",
    ]
    for badge in open_:
        finish = estimate_finish(badge, today)
        when = finish.isoformat() if finish else "depends on others"
        lines.append(
            f"| {badge.title} | {_needs(badge)} | "
            f"{progress_bar(badge.percent)} {_describe(badge)} | {when} |"
        )
    lines += ["", "## Earned", ""]
    lines += [f"- {b.title}" for b in earned] or ["- None yet."]
    lines += [
        "",
        "## Boundary",
        "",
        "These tools read and record. Every like, comment, reply and vote is done "
        "by you, on content you actually read.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #

def badge_from_record(record: dict[str, Any]) -> BadgeProgress:
    """Rebuild a BadgeProgress from a stored JSON record."""
    return BadgeProgress(
        title=str(record.get("title", "")),
        description=str(record.get("description", "")),
        state=str(record.get("state", "unknown")),
        current=record.get("current"),
        target=record.get("target"),
        unit=str(record.get("unit", "")),
        percent=record.get("percent"),
    )


def badge_to_record(badge: BadgeProgress) -> dict[str, Any]:
    """The JSON form stored in the history file."""
    return asdict(badge)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show badge progress from a snapshot.")
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    data = json.loads(args.snapshot.read_text(encoding="utf-8"))
    badges = [badge_from_record(r) for r in data["badges"]]
    today = date.today()
    for badge in badges:
        finish = estimate_finish(badge, today)
        print(
            f"{badge.title:<34} {_describe(badge):<12} "
            f"{progress_bar(badge.percent):<16} {finish.isoformat() if finish else '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
