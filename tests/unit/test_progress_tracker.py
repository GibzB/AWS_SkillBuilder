"""Unit tests for the campaign-progress streak/draft/published updater.

These exercise the pure Markdown-editing helpers in
``tools/builder_publish/progress_tracker.py`` against a small synthetic table,
so the streak math and cell edits are verified without touching the real doc.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the local tool importable (it lives outside the app package).
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools" / "builder_publish"
sys.path.insert(0, str(TOOLS_DIR))

import progress_tracker as pt  # noqa: E402


BANNER = (
    "> 🔥 **Current posting streak: Day 0** — next publish (day 6) starts a "
    "fresh chain at 🔥 Streak Day 1."
)

HEADER = (
    "| Day | Topic | S3 | BC draft | BC published | Streak | Notes |\n"
    "|----:|-------|:--:|:--------:|:------------:|:------:|-------|\n"
)


def _row(day: int, drafted: bool, published: bool, streak: str, note: str = "") -> str:
    d = pt.CHECK if drafted else pt.UNCHECK
    p = pt.CHECK if published else pt.UNCHECK
    return f"| {day} | Topic {day} | ✅ | {d} | {p} | {streak} | {note} |\n"


def _doc(rows: str) -> str:
    return BANNER + "\n\nintro text\n\n" + HEADER + rows + "\nfooter text\n"


def test_set_cell_updates_only_target_row_and_column() -> None:
    doc = _doc(_row(6, False, False, pt.BROKEN) + _row(7, False, False, pt.BROKEN))
    out = pt.set_cell(doc, 6, pt.COL_DRAFT, pt.CHECK)
    lines = [ln for ln in out.splitlines() if ln.startswith("| 6 ")]
    assert pt.CHECK in pt._split_row(lines[0])[pt.COL_DRAFT]
    # Row 7 untouched.
    row7 = [ln for ln in out.splitlines() if ln.startswith("| 7 ")][0]
    assert pt.UNCHECK in pt._split_row(row7)[pt.COL_DRAFT]


def test_set_cell_missing_day_raises() -> None:
    doc = _doc(_row(6, False, False, pt.BROKEN))
    with pytest.raises(ValueError):
        pt.set_cell(doc, 99, pt.COL_DRAFT, pt.CHECK)


def test_recompute_streak_fresh_chain_from_single_publish() -> None:
    # Days 1-5 not published (broken), day 6 published -> fresh streak 1.
    rows = "".join(
        _row(d, True, d <= 5, pt.BROKEN) for d in range(1, 7)
    )
    # Mark day 6 published explicitly (the helper above set published for d<=5;
    # rebuild with the real intent: 1-5 are the OLD broken chain, 6 is live).
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, True, True, "placeholder")
    doc = _doc(rows)
    out = pt.recompute_streak(doc)
    row6 = [ln for ln in out.splitlines() if ln.startswith("| 6 ")][0]
    assert f"{pt.FIRE} 1" in pt._split_row(row6)[pt.COL_STREAK]
    # A non-published day is marked broken.
    row3 = [ln for ln in out.splitlines() if ln.startswith("| 3 ")][0]
    assert pt.BROKEN in pt._split_row(row3)[pt.COL_STREAK]


def test_recompute_streak_counts_consecutive_run() -> None:
    # Days 6,7,8 published consecutively -> 1,2,3.
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, True, True, "x") + _row(7, True, True, "x") + _row(8, True, True, "x")
    doc = _doc(rows)
    out = pt.recompute_streak(doc)
    got = {}
    for ln in out.splitlines():
        if ln.startswith(("| 6 ", "| 7 ", "| 8 ")):
            day = int(pt._split_row(ln)[pt.COL_DAY].strip())
            got[day] = pt._split_row(ln)[pt.COL_STREAK].strip()
    assert got[6] == f"{pt.FIRE} 1"
    assert got[7] == f"{pt.FIRE} 2"
    assert got[8] == f"{pt.FIRE} 3"


def test_recompute_streak_gap_restarts_chain() -> None:
    # Published 6,7 then skip 8 then publish 9 -> 9 restarts at 1.
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, True, True, "x") + _row(7, True, True, "x")
    rows += _row(8, True, False, pt.BROKEN) + _row(9, True, True, "x")
    doc = _doc(rows)
    out = pt.recompute_streak(doc)
    row7 = [ln for ln in out.splitlines() if ln.startswith("| 7 ")][0]
    row9 = [ln for ln in out.splitlines() if ln.startswith("| 9 ")][0]
    assert f"{pt.FIRE} 2" in pt._split_row(row7)[pt.COL_STREAK]
    assert f"{pt.FIRE} 1" in pt._split_row(row9)[pt.COL_STREAK]


def test_current_streak_tracks_highest_published_day() -> None:
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, True, True, "x") + _row(7, True, True, "x")
    doc = pt.recompute_streak(_doc(rows))
    assert pt.current_streak(doc) == 2


def test_current_streak_zero_when_nothing_published() -> None:
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    assert pt.current_streak(_doc(rows)) == 0


def test_set_banner_reflects_streak() -> None:
    doc = _doc(_row(6, True, True, f"{pt.FIRE} 1"))
    out = pt.set_banner(doc, 1)
    assert "Current posting streak: Day 1" in out
    assert "Day 0" not in out.splitlines()[0]


def test_mark_published_end_to_end(tmp_path: Path) -> None:
    # Realistic fixture: 1-5 drafted+published-but-broken-old, 6 pending.
    rows = "".join(_row(d, True, False, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, False, False, f"{pt.FIRE} 1", "NEXT")
    doc_file = tmp_path / "campaign-progress.md"
    doc_file.write_text(_doc(rows), encoding="utf-8")

    streak = pt.mark_published(6, path=doc_file)
    assert streak == 1

    text = doc_file.read_text(encoding="utf-8")
    row6 = [ln for ln in text.splitlines() if ln.startswith("| 6 ")][0]
    cells = pt._split_row(row6)
    assert pt.CHECK in cells[pt.COL_DRAFT]       # draft implied by publish
    assert pt.CHECK in cells[pt.COL_PUBLISHED]
    assert f"{pt.FIRE} 1" in cells[pt.COL_STREAK]
    assert "Current posting streak: Day 1" in text


def test_pinned_broken_day_severs_prior_published_chain() -> None:
    # Real-doc shape: days 1-5 PUBLISHED but pinned '· broken' (old streak),
    # day 6 published live -> must be 🔥 1, not 🔥 6.
    rows = "".join(_row(d, True, True, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, True, True, f"{pt.FIRE} 1")
    doc = _doc(rows)
    out = pt.recompute_streak(doc)
    row6 = [ln for ln in out.splitlines() if ln.startswith("| 6 ")][0]
    assert f"{pt.FIRE} 1" in pt._split_row(row6)[pt.COL_STREAK]
    # Pinned days stay broken even though published.
    row5 = [ln for ln in out.splitlines() if ln.startswith("| 5 ")][0]
    assert pt.BROKEN in pt._split_row(row5)[pt.COL_STREAK]
    assert pt.current_streak(out) == 1


def test_live_chain_extends_past_pinned_prefix() -> None:
    # 1-5 pinned broken (published), 6,7,8 live -> 1,2,3; current = 3.
    rows = "".join(_row(d, True, True, pt.BROKEN) for d in range(1, 6))
    rows += (
        _row(6, True, True, f"{pt.FIRE} 1")
        + _row(7, True, True, f"{pt.FIRE} 2")
        + _row(8, True, True, f"{pt.FIRE} 3")
    )
    out = pt.recompute_streak(_doc(rows))
    assert pt.current_streak(out) == 3


def test_mark_published_on_real_doc_shape(tmp_path: Path) -> None:
    # Days 1-5 published + pinned broken; day 6 pending with 🔥 1 planned.
    rows = "".join(_row(d, True, True, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, False, False, f"{pt.FIRE} 1", "NEXT")
    doc_file = tmp_path / "campaign-progress.md"
    doc_file.write_text(_doc(rows), encoding="utf-8")

    streak = pt.mark_published(6, path=doc_file)
    assert streak == 1  # NOT 6 — the pinned prefix is severed

    text = doc_file.read_text(encoding="utf-8")
    assert "Current posting streak: Day 1" in text


def test_sequential_publish_builds_streak_despite_projected_broken_cells(
    tmp_path: Path,
) -> None:
    # Days 1-5 published+pinned (old chain). Days 6,7 start as PROJECTED future
    # rows carrying '· broken'. Publishing 6 then 7 must yield 🔥 1 then 🔥 2 —
    # the projected '· broken' on day 7 must not sever the live chain.
    rows = "".join(_row(d, True, True, pt.BROKEN) for d in range(1, 6))
    rows += _row(6, False, False, pt.BROKEN) + _row(7, False, False, pt.BROKEN)
    doc_file = tmp_path / "campaign-progress.md"
    doc_file.write_text(_doc(rows), encoding="utf-8")

    assert pt.mark_published(6, path=doc_file) == 1
    assert pt.mark_published(7, path=doc_file) == 2

    text = doc_file.read_text(encoding="utf-8")
    row7 = [ln for ln in text.splitlines() if ln.startswith("| 7 ")][0]
    assert f"{pt.FIRE} 2" in pt._split_row(row7)[pt.COL_STREAK]
    assert "Current posting streak: Day 2" in text


def test_mark_drafted_only_ticks_draft(tmp_path: Path) -> None:
    rows = _row(6, False, False, f"{pt.FIRE} 1")
    doc_file = tmp_path / "campaign-progress.md"
    doc_file.write_text(_doc(rows), encoding="utf-8")

    pt.mark_drafted(6, path=doc_file)
    text = doc_file.read_text(encoding="utf-8")
    cells = pt._split_row([ln for ln in text.splitlines() if ln.startswith("| 6 ")][0])
    assert pt.CHECK in cells[pt.COL_DRAFT]
    assert pt.UNCHECK in cells[pt.COL_PUBLISHED]  # not published yet
