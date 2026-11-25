"""Tests for the draw.io PNG export and the render helper (no draw.io needed)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from diagrams.export_png import INSTALL_GUIDANCE, export_png, find_drawio_cli  # noqa: E402
from diagrams.render import render_spec  # noqa: E402

pytestmark = pytest.mark.unit

SPEC = '{"nodes":[{"id":"a","service":"s3","label":"Bucket"}]}'


class FakeRunner:
    def __init__(self, returncode: int = 0, write_png: bool = True, raises: Exception | None = None) -> None:
        self.returncode = returncode
        self.write_png = write_png
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        if self.raises:
            raise self.raises
        if self.write_png and self.returncode == 0:
            Path(command[command.index("-o") + 1]).write_bytes(b"png")
        return subprocess.CompletedProcess(command, self.returncode, "", "boom\nlast line")


def test_find_cli_prefers_path() -> None:
    assert find_drawio_cli(which=lambda n: "/bin/drawio" if n == "drawio" else None, exists=lambda p: False) == "/bin/drawio"


def test_find_cli_mac_app_then_none() -> None:
    assert find_drawio_cli(which=lambda n: None, exists=lambda p: True) is not None
    assert find_drawio_cli(which=lambda n: None, exists=lambda p: False) is None


def test_export_builds_command_and_succeeds(tmp_path: Path) -> None:
    xml, png = tmp_path / "d.drawio", tmp_path / "d.png"
    xml.write_text("<mxfile/>")
    runner = FakeRunner()
    result = export_png(xml, png, runner=runner, timeout=7, cli="/x/drawio")
    assert result.ok and result.png_path == png
    command, kwargs = runner.calls[0]
    assert command == ["/x/drawio", "-x", "-f", "png", "-s", "2", "-b", "10", "-o", str(png), str(xml)]
    assert kwargs["timeout"] == 7


def test_missing_cli_keeps_drawio_and_guides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("diagrams.export_png.find_drawio_cli", lambda *a, **k: None)
    xml = tmp_path / "d.drawio"
    xml.write_text("<mxfile/>")
    runner = FakeRunner()
    result = export_png(xml, tmp_path / "d.png", runner=runner)
    assert not result.ok and result.png_path is None
    assert result.message == INSTALL_GUIDANCE
    assert runner.calls == []
    assert xml.exists()


@pytest.mark.parametrize(
    ("runner", "text"),
    [
        (FakeRunner(returncode=1), "last line"),
        (FakeRunner(raises=subprocess.TimeoutExpired("drawio", 5)), "timed out"),
        (FakeRunner(raises=OSError("nope")), "could not be started"),
        (FakeRunner(write_png=False), "no PNG"),
    ],
)
def test_failures_do_not_raise(tmp_path: Path, runner: FakeRunner, text: str) -> None:
    result = export_png(tmp_path / "d.drawio", tmp_path / "d.png", runner=runner, cli="/x/drawio")
    assert not result.ok and result.png_path is None
    assert text in result.message


def test_render_spec_writes_drawio_and_png(tmp_path: Path) -> None:
    result = render_spec(SPEC, "day-01", tmp_path, runner=FakeRunner(), cli="/x/drawio")
    assert result.drawio_path == tmp_path / "day-01.drawio"
    assert result.drawio_path.read_text().startswith("<mxfile")
    assert result.png_path == tmp_path / "day-01.png"


def test_render_spec_invalid_spec_writes_nothing(tmp_path: Path) -> None:
    result = render_spec("{not json", "day-01", tmp_path)
    assert result.drawio_path is None and result.png_path is None
    assert list(tmp_path.iterdir()) == []
