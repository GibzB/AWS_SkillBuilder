"""Local storage for the daily engagement log and badge history.

Everything lives under ``~/.aws-builder-publish/engagement/``, outside the git
repository, because the repository is public and these files describe your own
activity. Nothing here talks to Builder Center or AWS.

Files:
    log/YYYY-MM-DD.json   which actions you recorded that day
    badge_history.jsonl   one JSON line per badge snapshot
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ACTIONS",
    "DATA_DIR",
    "record_action",
    "actions_done",
    "append_history",
    "load_history",
    "last_snapshot_date",
]

DATA_DIR = Path.home() / ".aws-builder-publish" / "engagement"

# What a day of engagement can contain. "visit" and "publish" are usually
# recorded by the tools themselves; like, comment, reply and vote are always
# recorded by you after you did them.
ACTIONS = ("visit", "like", "comment", "reply", "vote", "publish")


def _root(base: Path | None) -> Path:
    """The storage directory, looked up at call time so tests can redirect it."""
    return base if base is not None else DATA_DIR


def _log_path(day: date, base: Path) -> Path:
    return base / "log" / f"{day.isoformat()}.json"


def _read_log(day: date, base: Path) -> dict[str, str]:
    path = _log_path(day, base)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    actions = data.get("actions", {}) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in actions.items()}


def record_action(
    action: str,
    day: date | None = None,
    base: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Record that ``action`` happened on ``day``. Returns True if it is new.

    Idempotent: recording the same action twice keeps the first timestamp.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; choose from {', '.join(ACTIONS)}")
    base = _root(base)
    day = day or date.today()
    actions = _read_log(day, base)
    if action in actions:
        return False
    stamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    actions[action] = stamp
    path = _log_path(day, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"actions": actions}, indent=2), encoding="utf-8")
    return True


def actions_done(day: date | None = None, base: Path | None = None) -> dict[str, str]:
    """Actions recorded for ``day`` mapped to their timestamps."""
    return _read_log(day or date.today(), _root(base))


def append_history(record: dict[str, Any], base: Path | None = None) -> None:
    """Append one snapshot record (a JSON object) to the history file."""
    base = _root(base)
    base.mkdir(parents=True, exist_ok=True)
    with (base / "badge_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def load_history(base: Path | None = None) -> list[dict[str, Any]]:
    """All snapshot records, oldest first. Unreadable lines are skipped."""
    path = _root(base) / "badge_history.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def last_snapshot_date(base: Path | None = None) -> date | None:
    """The calendar date of the most recent snapshot, or None."""
    history = load_history(base)
    if not history:
        return None
    try:
        return datetime.fromisoformat(str(history[-1]["taken_at"])).date()
    except (KeyError, ValueError):
        return None
