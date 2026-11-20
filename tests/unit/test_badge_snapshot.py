"""Tests for the daily snapshot: guard, throttle handling, history and dashboard."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import badge_snapshot as snap  # noqa: E402
import engagement_store as store  # noqa: E402

pytestmark = pytest.mark.unit

TODAY = date(2026, 11, 19)

CARDS: list[dict[str, Any]] = [
    {"title": "First Article", "description": "Publish your first article.", "state": "earned"},
    {
        "title": "30-Day Visit Streak",
        "description": "Visit Builder Center at least once a day for 30 consecutive days while signed in.",
        "state": "in_progress",
    },
    {
        "title": "7-Day Comment Streak",
        "description": "Leave at least one comment on any content each day for seven consecutive days.",
        "state": "not_started",
    },
]

POPUPS = {
    "30-Day Visit Streak": "30-Day Visit Streak\nVisit Builder Center...\n60%\n18 / 30 days",
    "7-Day Comment Streak": "7-Day Comment Streak\nLeave at least one comment...",
}


def _read(card: dict[str, Any]) -> str:
    return POPUPS[card["title"]]


# -- pure pieces -----------------------------------------------------------------


def test_collect_reads_popups_only_for_unearned_badges() -> None:
    asked: list[str] = []

    def read(card: dict[str, Any]) -> str:
        asked.append(card["title"])
        return _read(card)

    pauses: list[int] = []
    badges = snap.collect_badges(CARDS, read, lambda: pauses.append(1))
    assert asked == ["30-Day Visit Streak", "7-Day Comment Streak"]
    assert len(pauses) == 2
    visit = next(b for b in badges if b.title == "30-Day Visit Streak")
    assert (visit.current, visit.target, visit.unit, visit.percent) == (18, 30, "days", 60)


def test_missing_progress_falls_back_to_the_description_target() -> None:
    badges = snap.collect_badges(CARDS, _read, lambda: None)
    comment = next(b for b in badges if b.title == "7-Day Comment Streak")
    assert (comment.current, comment.target, comment.unit) == (None, 7, "days")


def test_throttle_stops_collection() -> None:
    def read(card: dict[str, Any]) -> str:
        raise snap.Throttled("slow down")

    with pytest.raises(snap.Throttled):
        snap.collect_badges(CARDS, read, lambda: None)


def test_once_a_day_guard() -> None:
    assert snap.should_run(None, TODAY, force=False)
    assert not snap.should_run(TODAY, TODAY, force=False)
    assert snap.should_run(TODAY, TODAY, force=True)
    assert snap.should_run(date(2026, 11, 18), TODAY, force=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Operation GetUserProfileByProfileId has exceeded allowed call rate", True),
        ("Failed to load the article", True),
        ("30-Day Visit Streak 18 / 30 days", False),
    ],
)
def test_throttle_detection(text: str, expected: bool) -> None:
    assert snap.looks_throttled(text) is expected


# -- full run with a fake page ----------------------------------------------------


class FakePage:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)

    def wait_for_timeout(self, _: int) -> None:
        return None

    def evaluate(self, _: str) -> list[dict[str, Any]]:
        return CARDS


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snap, "_dismiss_overlays", lambda page: None)
    monkeypatch.setattr(snap, "_wait_for_badges", lambda page: None)
    monkeypatch.setattr(snap, "_open_popup", lambda page, card: _read(card))
    monkeypatch.setattr(snap, "_close_popup", lambda page: None)


def test_run_writes_history_dashboard_and_records_visit(tmp_path: Path, patched: None) -> None:
    dashboard = tmp_path / "badge-dashboard.md"
    code = snap.run_with_page(FakePage(), base=tmp_path, dashboard=dashboard, today=TODAY)  # type: ignore[arg-type]
    assert code == snap.EXIT_OK
    assert len(store.load_history(tmp_path)) == 1
    text = dashboard.read_text(encoding="utf-8")
    assert "30-Day Visit Streak" in text and "18/30 days" in text
    assert "visit" in store.actions_done(TODAY, tmp_path)


def test_second_run_the_same_day_is_skipped(tmp_path: Path, patched: None) -> None:
    page = FakePage()
    dashboard = tmp_path / "d.md"
    snap.run_with_page(page, base=tmp_path, dashboard=dashboard, today=TODAY)  # type: ignore[arg-type]
    # The history record is stamped with the real clock, so align it with TODAY.
    import json

    path = tmp_path / "badge_history.jsonl"
    record = json.loads(path.read_text().splitlines()[0])
    record["taken_at"] = "2026-11-19T08:00:00+03:00"
    path.write_text(json.dumps(record) + "\n")

    page.visited.clear()
    code = snap.run_with_page(page, base=tmp_path, dashboard=dashboard, today=TODAY)  # type: ignore[arg-type]
    assert code == snap.EXIT_SKIPPED
    assert page.visited == []  # no page load when skipped


def test_throttle_saves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snap, "_dismiss_overlays", lambda page: None)
    monkeypatch.setattr(snap, "_wait_for_badges", lambda page: None)
    monkeypatch.setattr(snap, "_close_popup", lambda page: None)

    def boom(page: Any, card: dict[str, Any]) -> str:
        raise snap.Throttled("slow")

    monkeypatch.setattr(snap, "_open_popup", boom)
    dashboard = tmp_path / "d.md"
    code = snap.run_with_page(FakePage(), base=tmp_path, dashboard=dashboard, today=TODAY)  # type: ignore[arg-type]
    assert code == snap.EXIT_THROTTLED
    assert store.load_history(tmp_path) == []
    assert not dashboard.exists()
    assert store.actions_done(TODAY, tmp_path) == {}
