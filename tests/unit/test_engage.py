"""Tests for the daily engagement log and status view."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import engage  # noqa: E402
import engagement_store as store  # noqa: E402
from badge_progress import BadgeProgress  # noqa: E402

pytestmark = pytest.mark.unit

THURSDAY = date(2026, 11, 19)  # a Thursday
EAT = timezone(timedelta(hours=3))


def _now(hour: int, day: date = THURSDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 30, tzinfo=EAT)


BADGES = [
    BadgeProgress("7-Day Like Streak", "Leave at least one like on any content each day for seven consecutive days.", "earned"),
    BadgeProgress("30-Day Like Streak", "Leave at least one like on any content each day for 30 consecutive days.", "in_progress", 3, 30, "days", 10),
    BadgeProgress("7-Day Comment Streak", "Leave at least one comment on any content each day for seven consecutive days.", "not_started"),
    BadgeProgress("30-Day Visit Streak", "Visit Builder Center at least once a day for 30 consecutive days while signed in.", "in_progress", 18, 30, "days", 60),
]


# -- the store ---------------------------------------------------------------------


def test_record_action_is_idempotent_and_validated(tmp_path: Path) -> None:
    assert store.record_action("like", THURSDAY, tmp_path) is True
    assert store.record_action("like", THURSDAY, tmp_path) is False
    assert list(store.actions_done(THURSDAY, tmp_path)) == ["like"]
    with pytest.raises(ValueError):
        store.record_action("spam", THURSDAY, tmp_path)


def test_actions_are_kept_per_day(tmp_path: Path) -> None:
    store.record_action("like", THURSDAY, tmp_path)
    assert store.actions_done(THURSDAY + timedelta(days=1), tmp_path) == {}


def test_history_round_trip_and_bad_lines_are_skipped(tmp_path: Path) -> None:
    store.append_history({"taken_at": "2026-11-19T08:00:00+03:00", "badges": []}, tmp_path)
    (tmp_path / "badge_history.jsonl").open("a").write("not json\n")
    assert len(store.load_history(tmp_path)) == 1
    assert store.last_snapshot_date(tmp_path) == THURSDAY


def test_default_directory_is_resolved_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store.record_action("visit", THURSDAY)
    assert (tmp_path / "log" / "2026-11-19.json").exists()


# -- status ------------------------------------------------------------------------


def test_status_lists_open_actions_and_protected_streaks(tmp_path: Path) -> None:
    store.record_action("visit", THURSDAY, tmp_path)
    status = engage.build_status(THURSDAY, _now(10), tmp_path, BADGES)
    assert status.done == ["visit"]
    assert status.open_daily == ["like", "comment"]
    comment_line = next(line for line in status.at_risk if line.startswith("comment"))
    assert "7-Day Comment Streak" in comment_line
    like_line = next(line for line in status.at_risk if line.startswith("like"))
    assert "30-Day Like Streak" in like_line and "7-Day Like Streak" not in like_line
    assert status.warnings == []


def test_late_evening_warning(tmp_path: Path) -> None:
    status = engage.build_status(THURSDAY, _now(20), tmp_path, BADGES)
    assert any("before 21:00" in w for w in status.warnings)


def test_no_warning_when_everything_is_done(tmp_path: Path) -> None:
    for action in ("visit", "like", "comment"):
        store.record_action(action, THURSDAY, tmp_path)
    status = engage.build_status(THURSDAY, _now(22), tmp_path, BADGES)
    assert status.open_daily == [] and status.warnings == [] and status.at_risk == []


def test_weekly_vote_counts_within_the_same_week_only(tmp_path: Path) -> None:
    monday = THURSDAY - timedelta(days=3)
    store.record_action("vote", monday, tmp_path)
    store.record_action("publish", THURSDAY, tmp_path)
    assert engage.build_status(THURSDAY, _now(9), tmp_path, BADGES).weekly_open == []

    last_week = THURSDAY - timedelta(days=7)
    other = tmp_path / "other"
    store.record_action("vote", last_week, other)
    status = engage.build_status(THURSDAY, _now(9), other, BADGES)
    assert status.weekly_open == ["publish an article this week", "vote on one wish this week"]


def test_weekly_warning_escalates_from_friday(tmp_path: Path) -> None:
    friday = THURSDAY + timedelta(days=1)
    status = engage.build_status(friday, _now(9, friday), tmp_path, BADGES)
    assert any("Weekly item still open" in w for w in status.warnings)
    assert not engage.build_status(THURSDAY, _now(9), tmp_path, BADGES).warnings


def test_status_text_has_focus_and_log_hint(tmp_path: Path) -> None:
    text = engage.format_status(engage.build_status(THURSDAY, _now(9), tmp_path, BADGES))
    assert "Focus (from the latest badge snapshot)" in text
    assert "7-Day Comment Streak" in text
    assert "--done like comment" in text
    assert "\u2014" not in text


# -- command line ------------------------------------------------------------------


def test_cli_done_records_and_shows_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    assert engage.main(["--done", "like", "comment"]) == 0
    out = capsys.readouterr().out
    assert "recorded: like" in out and "recorded: comment" in out
    assert set(store.actions_done(base=tmp_path)) == {"like", "comment"}
    engage.main(["--done", "like"])
    assert "already recorded: like" in capsys.readouterr().out


def test_publishing_through_the_real_checklist_logs_a_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import progress_tracker as pt

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    doc = tmp_path / "doc.md"
    row = "| 6 | T | ✅ | ✅ | ⬜ | · broken | |\n"
    doc.write_text(
        "> 🔥 **Current posting streak: Day 0** x\n\n"
        "| Day | Topic | S3 | BC draft | BC published | Streak | Notes |\n"
        "|----:|---|:--:|:--:|:--:|:--:|---|\n" + row
    )
    pt.mark_published(6, path=doc)
    assert store.actions_done(base=tmp_path) == {}  # a copy never writes the log
    monkeypatch.setattr(pt, "DOC_PATH", doc)
    pt.mark_published(6, path=doc)
    assert "publish" in store.actions_done(base=tmp_path)
