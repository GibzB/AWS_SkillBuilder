#!/usr/bin/env python3
"""Keep docs/campaign-progress.md in sync as days are drafted/published.

The checklist has three tracked stages per day (S3 / BC draft / BC published)
plus a posting-streak column and a streak banner at the top. Editing that table
by hand after every publish is easy to forget and easy to get wrong, so this
module does it mechanically.

Design:
- Pure helpers (``set_cell``, ``recompute_streak``, ``set_banner``) operate on
  the Markdown TEXT and are fully unit-testable with no filesystem or network.
- Thin wrappers (``mark_drafted``, ``mark_published``) read the file, apply the
  change, and write it back.

Streak model (matches the doc):
- The *posting* streak counts consecutive PUBLISHED days with no gap in day
  numbers. Publishing day N right after day N-1 was published continues the
  chain; publishing after a gap (or when the previous day is not published)
  starts a fresh chain at 🔥 1.
- Streak values are derived from the published column, so they can never drift
  out of sync with reality: ``recompute_streak`` rebuilds the whole column from
  which rows are marked published.

This tool only ever EDITS local Markdown. It never touches Builder Center.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "campaign-progress.md"
)

CHECK = "✅"
UNCHECK = "⬜"
FIRE = "🔥"
BROKEN = "· broken"

# Column indices in the table (0-based, between the leading/trailing pipes):
# | Day | Topic | S3 | BC draft | BC published | Streak | Notes |
COL_DAY = 0
COL_TOPIC = 1
COL_S3 = 2
COL_DRAFT = 3
COL_PUBLISHED = 4
COL_STREAK = 5
COL_NOTES = 6
N_COLS = 7


# ----------------------------- row parsing --------------------------------- #

def _is_data_row(line: str) -> bool:
    """A table data row starts with '|' and its first cell is an integer day."""
    if not line.lstrip().startswith("|"):
        return False
    cells = _split_row(line)
    if len(cells) < N_COLS:
        return False
    return cells[COL_DAY].strip().isdigit()


def _split_row(line: str) -> list[str]:
    """Split a Markdown table row into its cell strings (no surrounding pipes).

    Keeps interior spacing so we can rewrite a single cell while leaving the
    rest of the row byte-identical.
    """
    # Strip exactly one leading and trailing pipe, then split on '|'.
    s = line.rstrip("\n")
    first = s.index("|")
    last = s.rindex("|")
    inner = s[first + 1 : last]
    return inner.split("|")


def _join_row(cells: list[str], newline: str = "\n") -> str:
    return "|" + "|".join(cells) + "|" + newline


def _day_of(line: str) -> int:
    return int(_split_row(line)[COL_DAY].strip())


# ----------------------------- pure helpers -------------------------------- #

def set_cell(text: str, day: int, col: int, value: str) -> str:
    """Return ``text`` with row ``day``'s column ``col`` set to ``value``.

    The value is centered-padded with single spaces (" value ") to match the
    table's existing one-space cell padding. Raises ValueError if the day row
    is not found so a silent no-op can't hide a mistake.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _is_data_row(line) and _day_of(line) == day:
            nl = "\n" if line.endswith("\n") else ""
            cells = _split_row(line)
            cells[col] = f" {value} "
            lines[i] = _join_row(cells, nl)
            return "".join(lines)
    raise ValueError(f"day {day} row not found in table")


def _published_days(text: str) -> set[int]:
    """Days whose BC published cell contains the check mark."""
    out: set[int] = set()
    for line in text.splitlines():
        if _is_data_row(line):
            cells = _split_row(line)
            if CHECK in cells[COL_PUBLISHED]:
                out.add(_day_of(line))
    return out


def _pinned_broken_days(text: str) -> set[int]:
    """Days whose Streak cell is pinned to '· broken'.

    A published day can still belong to a PAST, severed chain (e.g. days 1-5
    before the day-6 gap). The published column alone can't express that, so a
    day may be explicitly pinned to '· broken' in its Streak cell. Such a day is
    treated as a hard reset boundary: the consecutive-run count never crosses
    it, and it is never itself rendered as a 🔥 day. Pins persist across
    recomputes because this reads the current Streak cell.
    """
    out: set[int] = set()
    for line in text.splitlines():
        if _is_data_row(line):
            cells = _split_row(line)
            if BROKEN in cells[COL_STREAK]:
                out.add(_day_of(line))
    return out


def _run_ending_at(day: int, published: set[int], pinned_broken: set[int]) -> int:
    """Length of the consecutive published run ending at ``day``.

    Walks backwards day-by-day while each day is published AND not pinned
    broken. A pinned-broken day stops the walk, so a severed prior chain cannot
    inflate the current streak.
    """
    run = 0
    d = day
    while d in published and d not in pinned_broken:
        run += 1
        d -= 1
    return run


def recompute_streak(text: str) -> str:
    """Rebuild the Streak column for every data row.

    A published, non-pinned day D gets 🔥 k where k is the length of the run of
    consecutive published, non-pinned days ending at D. A day that is not
    published, or is pinned broken, renders '· broken'. Pins (see
    ``_pinned_broken_days``) act as hard reset boundaries so a past severed
    chain never bleeds into the live streak.
    """
    published = _published_days(text)
    pinned = _pinned_broken_days(text)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not _is_data_row(line):
            continue
        day = _day_of(line)
        nl = "\n" if line.endswith("\n") else ""
        cells = _split_row(line)
        if day in published and day not in pinned:
            run = _run_ending_at(day, published, pinned)
            cells[COL_STREAK] = f" {FIRE} {run} "
        else:
            cells[COL_STREAK] = f" {BROKEN} "
        lines[i] = _join_row(cells, nl)
    return "".join(lines)


def current_streak(text: str) -> int:
    """Streak length of the highest-numbered live (published, non-pinned) day."""
    published = _published_days(text)
    pinned = _pinned_broken_days(text)
    live = published - pinned
    if not live:
        return 0
    top = max(live)
    return _run_ending_at(top, published, pinned)


_BANNER_RE = re.compile(
    r"^> ?🔥 \*\*Current posting streak:.*?$",
    re.MULTILINE,
)


def set_banner(text: str, streak: int) -> str:
    """Replace the top streak banner line with the given current streak.

    The banner is a single blockquote line beginning with '🔥 **Current posting
    streak:'. If it is not found, the text is returned unchanged (so the module
    degrades gracefully if the doc layout changes).
    """
    if streak <= 0:
        msg = (
            f"> {FIRE} **Current posting streak: Day 0** — publish a day to start "
            "a fresh chain at 🔥 Streak Day 1."
        )
    else:
        msg = (
            f"> {FIRE} **Current posting streak: Day {streak}** — keep publishing "
            "consecutive days to extend the chain."
        )
    if _BANNER_RE.search(text):
        return _BANNER_RE.sub(msg, text, count=1)
    return text


# ----------------------------- file wrappers ------------------------------- #

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def mark_drafted(day: int, path: Path = DOC_PATH) -> None:
    """Tick the BC draft box for ``day`` (does not touch streak/published)."""
    text = _read(path)
    text = set_cell(text, day, COL_DRAFT, CHECK)
    _write(path, text)


def pin_broken(day: int, path: Path = DOC_PATH) -> None:
    """Pin ``day``'s Streak cell to '· broken' (a hard chain-reset boundary).

    Use this to sever a prior published chain from the current one — e.g. days
    1-5 were published but belong to an old streak that ended at the day-6 gap.
    """
    text = _read(path)
    text = set_cell(text, day, COL_STREAK, BROKEN)
    _write(path, text)


def mark_published(day: int, path: Path = DOC_PATH) -> int:
    """Tick BC published for ``day``, recompute streaks, update the banner.

    Returns the current streak after the update so the caller can print it.
    """
    text = _read(path)
    text = set_cell(text, day, COL_PUBLISHED, CHECK)
    # Publishing implies the draft existed; keep the draft box consistent.
    text = set_cell(text, day, COL_DRAFT, CHECK)
    # Clear any pre-existing '· broken' pin on THIS day: a day we are publishing
    # is joining the live chain, so it must not act as a severance boundary.
    # (Projected future rows carry '· broken' until published; this removes that
    # before recompute so the day counts toward the streak.) We set a 🔥 0
    # placeholder that recompute immediately overwrites with the real run.
    text = set_cell(text, day, COL_STREAK, f"{FIRE} 0")
    text = recompute_streak(text)
    streak = current_streak(text)
    text = set_banner(text, streak)
    _write(path, text)
    return streak


# --------------------------------- CLI ------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update docs/campaign-progress.md streak/draft/published state."
    )
    parser.add_argument("--day", type=int, required=True, help="Campaign day number.")
    parser.add_argument(
        "--state",
        choices=["drafted", "published"],
        required=True,
        help="drafted = tick BC draft; published = tick BC published + bump streak.",
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=DOC_PATH,
        help="Path to the progress markdown (default: docs/campaign-progress.md).",
    )
    args = parser.parse_args(argv)

    if args.state == "drafted":
        mark_drafted(args.day, args.doc)
        print(f"Marked day {args.day} as drafted in {args.doc}.")
    else:
        streak = mark_published(args.day, args.doc)
        print(
            f"Marked day {args.day} as published; current posting streak is "
            f"{FIRE} {streak} in {args.doc}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
