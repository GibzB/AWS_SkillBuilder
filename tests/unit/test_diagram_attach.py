"""Tests for diagram_attach with a fake Playwright page."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

import diagram_attach as da  # noqa: E402

pytestmark = pytest.mark.unit

PNG = Path("/tmp/day-01.png")


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page, self.selector = page, selector

    @property
    def first(self) -> FakeLocator:
        return self

    @property
    def last(self) -> FakeLocator:
        return self

    def count(self) -> int:
        return 1 if self.selector in self.page.present else 0

    def click(self) -> None:
        self.page.clicks.append(self.selector)

    def set_input_files(self, path: str) -> None:
        if self.page.upload_error:
            raise RuntimeError("upload broke")
        self.page.uploads.append(path)


class FakePage:
    def __init__(self, present: set[str], upload_error: bool = False) -> None:
        self.present, self.upload_error = present, upload_error
        self.clicks: list[str] = []
        self.uploads: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def wait_for_timeout(self, ms: int) -> None:
        return None


def test_split_at_marker() -> None:
    body = f"Intro\n\n{da.MARKER}\n\n## Hands on\nrun it\n"
    assert da.split_body_at_marker(body) == ("Intro", "## Hands on\nrun it")


@pytest.mark.parametrize("body", ["no marker here", f"{da.MARKER}\nx\n{da.MARKER}"])
def test_split_needs_exactly_one_marker(body: str) -> None:
    assert da.split_body_at_marker(body) is None


def test_strip_marker() -> None:
    assert da.strip_marker(f"a\n{da.MARKER}\nb") == "a\nb\n"


def test_attach_clicks_only_the_image_button(capsys: pytest.CaptureFixture[str]) -> None:
    page: Any = FakePage({da.IMAGE_BUTTON_SELECTORS[1], da.FILE_INPUT_SELECTOR})
    assert da.attach_png(page, PNG) is True
    assert page.clicks == [da.IMAGE_BUTTON_SELECTORS[1]]
    assert page.uploads == [str(PNG)]


def test_missing_button_prints_path(capsys: pytest.CaptureFixture[str]) -> None:
    page: Any = FakePage(set())
    assert da.attach_png(page, PNG) is False
    assert page.clicks == [] and page.uploads == []
    assert str(PNG) in capsys.readouterr().out


def test_upload_error_prints_path(capsys: pytest.CaptureFixture[str]) -> None:
    page: Any = FakePage({da.IMAGE_BUTTON_SELECTORS[0]}, upload_error=True)
    assert da.attach_png(page, PNG) is False
    out = capsys.readouterr().out
    assert "upload failed" in out and str(PNG) in out
