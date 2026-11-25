"""Safety scan for diagram_attach.py (its own copy of the AST approach).

``test_engagement_safety.py`` bans ``set_input_files`` for the reading tools, so
the image upload lives in its own module with its own rules: one click, only in
``attach_png``, no engagement or publish words in selectors, no HTTP client.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[2] / "tools" / "builder_publish" / "diagram_attach.py"
FORBIDDEN = re.compile(
    r"\b(likes?|liked|unlike|comments?|repl(?:y|ies|ied)|votes?|voted|upvote|"
    r"react(?:s|ed|ions?)?|thumbs?|publish\w*|submit|post)\b",
    re.IGNORECASE,
)
HTTP_MODULES = ("requests", "httpx", "aiohttp", "urllib3", "urllib.request", "http.client", "socket")
PLAYWRIGHT = {"locator", "get_by_role", "get_by_text", "get_by_label", "get_by_placeholder",
              "get_by_test_id", "query_selector", "wait_for_selector", "click", "filter"}


def _calls() -> list[tuple[ast.Call, str]]:
    found: list[tuple[ast.Call, str]] = []

    def visit(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            if isinstance(child, ast.Call):
                found.append((child, name))
            visit(child, name)

    visit(ast.parse(SOURCE.read_text(encoding="utf-8")), "<module>")
    return found


def test_single_click_inside_attach_png() -> None:
    clicks = [fn for call, fn in _calls() if isinstance(call.func, ast.Attribute) and call.func.attr == "click"]
    assert clicks == ["attach_png"]


def test_only_set_input_files_may_write_and_only_there() -> None:
    risky = {"fill", "type", "check", "uncheck", "select_option", "dblclick", "press"}
    for call, fn in _calls():
        if isinstance(call.func, ast.Attribute):
            assert call.func.attr not in risky, f"{call.func.attr}() in {fn}()"
            if call.func.attr == "set_input_files":
                assert fn == "attach_png"


def test_selector_constants_name_no_forbidden_control() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    selectors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.endswith("SELECTORS") or isinstance(t, ast.Name) and t.id.endswith("SELECTOR")
            for t in node.targets
        ):
            selectors += [c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert selectors
    for text in selectors:
        assert not FORBIDDEN.search(text), text


def test_playwright_call_strings_are_clean() -> None:
    for call, _ in _calls():
        if isinstance(call.func, ast.Attribute) and call.func.attr in PLAYWRIGHT:
            for arg in [*call.args, *[k.value for k in call.keywords]]:
                for c in ast.walk(arg):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        assert not FORBIDDEN.search(c.value)


def test_no_http_clients() -> None:
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        )
        for name in names:
            assert not any(name == m or name.startswith(m + ".") for m in HTTP_MODULES)


def test_the_scan_would_catch_a_publish_selector() -> None:
    assert FORBIDDEN.search('button[aria-label="Publish"]')
    assert FORBIDDEN.search('[data-testid="like-button"]')
    assert not FORBIDDEN.search('button[aria-label="Image"]')
