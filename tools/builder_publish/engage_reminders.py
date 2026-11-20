"""Evening macOS reminder for the daily engagement habit.

A launchd agent runs ``engage.py reminders check`` at a time you choose (default
20:00). The check shows a macOS notification only when a like or a comment is
still unlogged for today, so a finished day stays quiet.

Commands (via engage.py):
    reminders install --at 20:00 [--dry-run]   write and load the launchd agent
    reminders uninstall                         unload and remove it
    reminders test                              show a sample notification now
    reminders check [--dry-run]                 what launchd runs each evening

``install`` changes your system (a file in ~/Library/LaunchAgents), so run it
yourself, and use --dry-run first to see exactly what would be written.
"""

from __future__ import annotations

import argparse
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engagement_store  # noqa: E402

LABEL = "com.awsbuilder.engage"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ENGAGE_SCRIPT = Path(__file__).resolve().parent / "engage.py"
REMINDER_ACTIONS = ("like", "comment")  # the ones only you can do
SAFE_HOUR = 21

Runner = Callable[[list[str]], int]


def _run(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def missing_actions(day: date, base: Path | None = None) -> list[str]:
    """Reminder actions not yet logged for ``day``."""
    done = engagement_store.actions_done(day, base)
    return [a for a in REMINDER_ACTIONS if a not in done]


def message_for(missing: list[str]) -> tuple[str, str]:
    """Notification title and body for the open actions."""
    title = "Builder Center streaks"
    body = (
        f"Still open today: {', '.join(missing)}. Do it and log it before "
        f"{SAFE_HOUR}:00 with: engage.py --done {' '.join(missing)}"
    )
    return title, body


def applescript(title: str, body: str) -> str:
    """AppleScript that shows a notification, with quotes safely escaped."""
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    return f'display notification "{esc(body)}" with title "{esc(title)}"'


def notify(title: str, body: str, runner: Runner = _run) -> int:
    """Show a macOS notification."""
    return runner(["osascript", "-e", applescript(title, body)])


def parse_time(value: str) -> tuple[int, int]:
    """Parse HH:MM (24 hour) into (hour, minute)."""
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value.strip())
    if not match:
        raise ValueError(f"time must be HH:MM in 24-hour form, got {value!r}")
    return int(match.group(1)), int(match.group(2))


def build_plist(at: str, python: str = sys.executable, script: Path = ENGAGE_SCRIPT) -> bytes:
    """The launchd agent definition as plist XML."""
    hour, minute = parse_time(at)
    log = engagement_store.DATA_DIR / "reminder.log"
    definition = {
        "Label": LABEL,
        "ProgramArguments": [python, str(script), "reminders", "check"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    }
    return plistlib.dumps(definition)


def check(
    now: datetime | None = None,
    base: Path | None = None,
    runner: Runner = _run,
    dry_run: bool = False,
) -> int:
    """Notify if a like or comment is unlogged today. Returns 0 either way."""
    now = now or datetime.now().astimezone()
    missing = missing_actions(now.date(), base)
    if not missing:
        print("Nothing open today; no reminder.")
        return 0
    title, body = message_for(missing)
    if dry_run:
        print(f"[dry run] {title}: {body}")
        return 0
    notify(title, body, runner)
    return 0


def install(at: str, dry_run: bool, runner: Runner = _run, path: Path = PLIST_PATH) -> int:
    """Write the launchd agent and load it (or just show it with --dry-run)."""
    payload = build_plist(at)
    if dry_run:
        print(f"[dry run] would write {path} and load it with launchctl:\n")
        print(payload.decode("utf-8"))
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    uid = str(_uid())
    runner(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])  # replace any old copy
    code = runner(["launchctl", "bootstrap", f"gui/{uid}", str(path)])
    if code != 0:
        print(f"launchctl returned {code}. The file is at {path}; try: launchctl load {path}")
        return code
    print(f"Installed. A check runs every day at {at} and notifies only if like or comment is open.")
    return 0


def uninstall(runner: Runner = _run, path: Path = PLIST_PATH) -> int:
    """Unload and remove the launchd agent."""
    runner(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"])
    if path.exists():
        path.unlink()
    print("Reminder removed.")
    return 0


def _uid() -> int:
    import os

    return os.getuid()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="engage.py reminders", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    install_p = sub.add_parser("install", help="Install the evening reminder.")
    install_p.add_argument("--at", default="20:00", help="Local time HH:MM (default 20:00).")
    install_p.add_argument("--dry-run", action="store_true", help="Show the agent without installing.")
    sub.add_parser("uninstall", help="Remove the evening reminder.")
    sub.add_parser("test", help="Show a sample notification now.")
    check_p = sub.add_parser("check", help="Run the evening check once.")
    check_p.add_argument("--dry-run", action="store_true", help="Print instead of notifying.")
    args = parser.parse_args(argv)

    if args.action == "install":
        try:
            return install(args.at, args.dry_run)
        except ValueError as error:
            parser.error(str(error))
    if args.action == "uninstall":
        return uninstall()
    if args.action == "test":
        title, body = message_for(list(REMINDER_ACTIONS))
        return notify(title, body)
    return check(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
