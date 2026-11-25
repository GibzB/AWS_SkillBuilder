"""Tests for the --news and --roundup modes of tools/bootstrap_campaign.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "bootstrap_campaign.py"
_spec = importlib.util.spec_from_file_location("bootstrap_campaign_news", _TOOL)
assert _spec is not None and _spec.loader is not None
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


class _Payload:
    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")


class _FakeLambda:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke(self, FunctionName: str, Payload: bytes) -> dict[str, Any]:  # noqa: N803
        self.calls.append((FunctionName, json.loads(Payload.decode("utf-8"))))
        return {"Payload": _Payload('{"statusCode": 200}')}


class _FakeSts:
    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": "000000000000"}


def _wire(monkeypatch: pytest.MonkeyPatch) -> _FakeLambda:
    lam = _FakeLambda()

    def client(service: str, **_: Any) -> Any:
        if service == "lambda":
            return lam
        if service == "sts":
            return _FakeSts()
        return object()

    monkeypatch.setattr(bootstrap.boto3, "client", client)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _s: None)
    return lam


def test_news_topics_are_placeholders() -> None:
    topics = bootstrap._news_topics(30)
    assert len(topics) == 30
    assert topics[0].startswith("News day 1: topic chosen from the AWS What's New feed")
    assert all("\u2014" not in t for t in topics)


def test_roundup_event_shape() -> None:
    assert bootstrap._roundup_event("aws-news-30", None) == {
        "source": "eventbridge-scheduler",
        "campaign_id": "aws-news-30",
        "kind": "weekly_roundup",
    }
    assert bootstrap._roundup_event("aws-news-30", 2)["week"] == 2


def test_news_creates_and_approves_without_generating(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lam = _wire(monkeypatch)
    monkeypatch.setattr("sys.argv", ["bootstrap", "--news", "--email", "x"])
    assert bootstrap.main() == 0
    assert [c[0] for c in lam.calls] == ["aws-builder-aws-news-30-orchestrator"] * 2
    create = lam.calls[0][1]
    assert create["action"] == "create"
    payload = create["payload"]
    assert payload["campaign_id"] == "aws-news-30"
    assert payload["name"] == "30 Days of AWS News"
    assert payload["total_days"] == 30
    assert payload["content_style"] == "news"
    assert len(payload["topics"]) == 30
    assert lam.calls[1][1]["action"] == "approve"
    assert "No days generated" in capsys.readouterr().out


def test_roundup_invokes_weekly_event(monkeypatch: pytest.MonkeyPatch) -> None:
    lam = _wire(monkeypatch)
    monkeypatch.setattr("sys.argv", ["bootstrap", "--roundup", "--week", "3"])
    assert bootstrap.main() == 0
    assert lam.calls == [
        (
            "aws-builder-aws-news-30-orchestrator",
            {
                "source": "eventbridge-scheduler",
                "campaign_id": "aws-news-30",
                "kind": "weekly_roundup",
                "week": 3,
            },
        )
    ]


def test_week_requires_roundup(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch)
    monkeypatch.setattr("sys.argv", ["bootstrap", "--week", "1"])
    with pytest.raises(SystemExit) as exc:
        bootstrap.main()
    assert exc.value.code == 2


def test_defaults_without_news_flags_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    lam = _wire(monkeypatch)
    monkeypatch.setattr(bootstrap, "_day_item", lambda *a, **k: {"day_state": {"S": "PUBLISHED"}})
    monkeypatch.setattr("sys.argv", ["bootstrap", "--skip-create", "--days", "2"])
    assert bootstrap.main() == 0
    assert lam.calls == []  # both days already published, function name is the old campaign's
