"""Tests for the evening reminder: decision logic, plist and launchctl calls."""

from __future__ import annotations

import plistlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import engage_reminders as rem  # noqa: E402
import engagement_store as store  # noqa: E402

pytestmark = pytest.mark.unit

DAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone(timedelta(hours=3)))


class Recorder:
    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, command: list[str]) -> int:
        self.calls.append(command)
        return self.code


def test_missing_actions_follow_the_log(tmp_path: Path) -> None:
    assert rem.missing_actions(DAY, tmp_path) == ["like", "comment"]
    store.record_action("like", DAY, tmp_path)
    assert rem.missing_actions(DAY, tmp_path) == ["comment"]
    store.record_action("comment", DAY, tmp_path)
    assert rem.missing_actions(DAY, tmp_path) == []


def test_check_notifies_only_when_something_is_open(tmp_path: Path) -> None:
    runner = Recorder()
    rem.check(NOW, tmp_path, runner)
    assert len(runner.calls) == 1 and runner.calls[0][0] == "osascript"
    assert "like, comment" in runner.calls[0][2]

    for action in ("like", "comment"):
        store.record_action(action, DAY, tmp_path)
    quiet = Recorder()
    rem.check(NOW, tmp_path, quiet)
    assert quiet.calls == []


def test_dry_run_check_never_notifies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = Recorder()
    rem.check(NOW, tmp_path, runner, dry_run=True)
    assert runner.calls == [] and "[dry run]" in capsys.readouterr().out


def test_applescript_escapes_quotes() -> None:
    script = rem.applescript('say "hi"', 'path\\to "x"')
    assert script == 'display notification "path\\\\to \\"x\\"" with title "say \\"hi\\""'


@pytest.mark.parametrize(("value", "expected"), [("20:00", (20, 0)), ("7:05", (7, 5)), ("23:59", (23, 59))])
def test_parse_time(value: str, expected: tuple[int, int]) -> None:
    assert rem.parse_time(value) == expected


@pytest.mark.parametrize("value", ["24:00", "20:60", "8pm", ""])
def test_parse_time_rejects_bad_values(value: str) -> None:
    with pytest.raises(ValueError):
        rem.parse_time(value)


def test_plist_schedules_the_check() -> None:
    data = plistlib.loads(rem.build_plist("20:30", python="/venv/bin/python", script=Path("/x/engage.py")))
    assert data["Label"] == rem.LABEL
    assert data["ProgramArguments"] == ["/venv/bin/python", "/x/engage.py", "reminders", "check"]
    assert data["StartCalendarInterval"] == {"Hour": 20, "Minute": 30}
    assert data["RunAtLoad"] is False


def test_install_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = Recorder()
    target = tmp_path / "agent.plist"
    assert rem.install("20:00", True, runner, target) == 0
    assert runner.calls == [] and not target.exists()
    assert "com.awsbuilder.engage" in capsys.readouterr().out


def test_install_writes_the_agent_and_loads_it(tmp_path: Path) -> None:
    runner = Recorder()
    target = tmp_path / "LaunchAgents" / "agent.plist"
    assert rem.install("20:00", False, runner, target) == 0
    assert target.exists()
    verbs = [call[1] for call in runner.calls]
    assert verbs == ["bootout", "bootstrap"]  # replaces any older copy first


def test_install_reports_a_launchctl_failure(tmp_path: Path) -> None:
    code = rem.install("20:00", False, Recorder(code=5), tmp_path / "a.plist")
    assert code == 5


def test_uninstall_removes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "a.plist"
    target.write_text("x")
    runner = Recorder()
    rem.uninstall(runner, target)
    assert not target.exists() and runner.calls[0][1] == "bootout"
