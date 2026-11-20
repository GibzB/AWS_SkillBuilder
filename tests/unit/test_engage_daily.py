"""Tests for the one-command routine: ordering and throttle handling."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import badge_snapshot as snap  # noqa: E402
import engage  # noqa: E402
import engage_daily  # noqa: E402

pytestmark = pytest.mark.unit


def _run(snapshot_code: int) -> tuple[int, list[str]]:
    calls: list[str] = []

    def snapshot(page: Any, force: bool = False) -> int:
        calls.append(f"snapshot(force={force})")
        return snapshot_code

    code = engage_daily.run(
        object(),
        snapshot,
        lambda page: calls.append("reading"),
        lambda: calls.append("status"),
        force=True,
    )
    return code, calls


def test_success_runs_snapshot_then_reading_list_then_status() -> None:
    code, calls = _run(snap.EXIT_OK)
    assert code == snap.EXIT_OK
    assert calls == ["snapshot(force=True)", "reading", "status"]


def test_an_already_done_snapshot_still_builds_the_reading_list() -> None:
    _, calls = _run(snap.EXIT_SKIPPED)
    assert calls == ["snapshot(force=True)", "reading", "status"]


def test_throttle_stops_before_the_reading_list_but_still_shows_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, calls = _run(snap.EXIT_THROTTLED)
    assert code == snap.EXIT_THROTTLED
    assert calls == ["snapshot(force=True)", "status"]
    assert "cool down" in capsys.readouterr().out


def test_a_failed_snapshot_skips_the_reading_list() -> None:
    _, calls = _run(snap.EXIT_INTERRUPTED)
    assert calls == ["snapshot(force=True)", "status"]


def test_engage_dispatches_daily_and_reminders(monkeypatch: pytest.MonkeyPatch) -> None:
    import engage_reminders

    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(engage_daily, "main", lambda argv: seen.setdefault("daily", argv) and 0)
    monkeypatch.setattr(engage_reminders, "main", lambda argv: seen.setdefault("rem", argv) and 0)
    engage.main(["daily", "--force"])
    engage.main(["reminders", "install", "--at", "20:00"])
    assert seen == {"daily": ["--force"], "rem": ["install", "--at", "20:00"]}
