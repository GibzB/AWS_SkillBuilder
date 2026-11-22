"""Tests for the assisted wish drafting tool (it types, it never clicks)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "builder_publish"
sys.path.insert(0, str(TOOLS))

import draft_wish as dw  # noqa: E402

pytestmark = pytest.mark.unit


def _el(index: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": index, "tag": "input", "type": "text", "placeholder": "", "ariaLabel": "",
        "name": "", "testid": "", "editable": False, "visible": True, "inChrome": False,
    }
    base.update(kw)
    return base


# -- the wish files ------------------------------------------------------------------


def test_bundled_wishes_load_and_pass_the_checks() -> None:
    files = sorted((TOOLS / "wishes").glob("*.md"))
    assert {f.stem for f in files} >= {dw.DEFAULT_WISH, "create-limit-wait-time"}
    for path in files:
        wish = dw.load_wish(path)
        assert dw.check_wish(wish) == [], path.name
        assert 0 < len(wish.title) <= 120


def test_load_wish_requires_a_title_line(tmp_path: Path) -> None:
    bad = tmp_path / "w.md"
    bad.write_text("no heading here\nbody")
    with pytest.raises(ValueError, match="first line"):
        dw.load_wish(bad)


def test_check_wish_flags_problems() -> None:
    assert "the title is empty" in dw.check_wish(dw.Wish("", "body"))
    assert "the description is empty" in dw.check_wish(dw.Wish("t", ""))
    assert any("placeholder" in p for p in dw.check_wish(dw.Wish("t", "fill <this in>")))
    assert any("em dash" in p for p in dw.check_wish(dw.Wish("a \u2014 b", "body")))
    assert any(p.startswith("WARNING") for p in dw.check_wish(dw.Wish("t" * 121, "body")))


# -- picking fields --------------------------------------------------------------------


def test_search_box_and_header_fields_are_ignored() -> None:
    elements = [
        _el(0, placeholder="Search", inChrome=True),
        _el(1, placeholder="Search anything"),
        _el(2, placeholder="Title"),
        _el(3, tag="textarea", placeholder="Describe your wish"),
    ]
    assert dw.choose_fields(elements) == (2, 3)


def test_falls_back_to_order_when_labels_are_unhelpful() -> None:
    elements = [_el(0), _el(1, tag="textarea")]
    assert dw.choose_fields(elements) == (0, 1)


def test_rich_text_box_is_preferred_for_the_description() -> None:
    elements = [_el(0, placeholder="Wish title"), _el(1, tag="div", editable=True), _el(2)]
    assert dw.choose_fields(elements) == (0, 1)


def test_hidden_fields_are_never_chosen() -> None:
    elements = [_el(0, placeholder="Title", visible=False), _el(1, placeholder="Title")]
    assert dw.choose_fields(elements)[0] == 1


def test_no_fields_gives_none() -> None:
    assert dw.choose_fields([]) == (None, None)


# -- against a real (headless) browser -------------------------------------------------

_CLICK_COUNTER = "<script>window.__clicks = 0; document.addEventListener('click', () => { window.__clicks++; }, true);</script>"

_FORM_TEXTAREA = f"""
<header><input placeholder="Search"></header>
{_CLICK_COUNTER}
<main>
  <input placeholder="Title">
  <textarea placeholder="Describe your wish"></textarea>
  <select aria-label="Type of request"><option>Feature Request</option></select>
  <button>Submit</button>
</main>"""

_FORM_RICH = f"""
<header><input placeholder="Search"></header>
{_CLICK_COUNTER}
<main>
  <textarea placeholder="Title"></textarea>
  <div contenteditable="true" role="textbox" aria-label="Description" style="min-height:80px"></div>
  <button>Create wish</button>
</main>"""


def _run_in_browser(html: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any]]:
    sync_api = pytest.importorskip("playwright.sync_api")
    wish = dw.Wish("A title", "Line one\nLine two")
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            # fill_wish navigates to the real URL; serve our sample page instead.
            monkeypatch.setattr(page, "goto", lambda *a, **k: None)
            report = dw.fill_wish(page, wish)
            state = page.evaluate(
                """() => ({
                    clicks: window.__clicks,
                    title: (document.querySelector('input[placeholder="Title"], textarea[placeholder="Title"]') || {}).value,
                    search: document.querySelector('header input').value,
                    body: (document.querySelector('textarea[placeholder^="Describe"]') || document.querySelector('[contenteditable]')).value
                           ?? document.querySelector('[contenteditable]').innerText,
                })"""
            )
            browser.close()
    except Exception as error:  # noqa: BLE001 - no usable browser on this machine
        if "pytest" in type(error).__module__:
            raise
        pytest.skip(f"no usable browser: {error}")
    return report, state


def test_fills_a_textarea_form_without_a_single_click(monkeypatch: pytest.MonkeyPatch) -> None:
    report, state = _run_in_browser(_FORM_TEXTAREA, monkeypatch)
    assert report["title_filled"] and report["body_filled"]
    assert state["title"] == "A title"
    assert state["body"] == "Line one\nLine two"
    assert state["search"] == ""  # the search box is left alone
    assert state["clicks"] == 0  # Submit, dropdowns and everything else untouched
    assert any("Type of request" in item for item in report["selects"])
    assert "Submit" in report["buttons"]


def test_fills_a_rich_text_description_without_clicking(monkeypatch: pytest.MonkeyPatch) -> None:
    report, state = _run_in_browser(_FORM_RICH, monkeypatch)
    assert report["title_filled"] and report["body_filled"]
    assert state["title"] == "A title"
    assert "Line one" in state["body"] and "Line two" in state["body"]
    assert state["clicks"] == 0


# -- the promise, enforced -------------------------------------------------------------


def test_the_module_can_never_click_or_choose() -> None:
    tree = ast.parse((TOOLS / "draft_wish.py").read_text(encoding="utf-8"))
    banned = {"click", "dblclick", "check", "uncheck", "select_option", "press", "tap", "set_input_files"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in banned, f"draft_wish.py calls .{node.func.attr}()"
