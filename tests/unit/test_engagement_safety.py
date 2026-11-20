"""Safety test: the browser tools may read Builder Center but never engage with it.

The engagement tooling reads badge progress and the feed. Every like, comment,
reply and vote must stay a human action. This test reads the source of each
browser-driving module and fails if it could do otherwise:

* a string passed to a Playwright call names a like, comment, reply, vote or
  reaction control,
* ``.click()`` appears outside an allowlisted function,
* text is typed or form fields are filled,
* a key other than Escape is pressed,
* an HTTP client is imported (no direct calls to Builder Center APIs).

If you extend these tools, extend the allowlist deliberately, in review.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "builder_publish"

# Functions that may contain a click, per module. The reading list has none.
ALLOWED_CLICKS: dict[str, set[str]] = {
    "badge_snapshot.py": {"_open_popup", "_close_popup"},
    "reading_list.py": set(),
    "harvest_badges.py": {"_dismiss_overlays", "_activate_badges_tab", "_load_all_badges"},
}

FORBIDDEN_WORDS = re.compile(
    r"\b(likes?|liked|unlike|comments?|repl(?:y|ies|ied)|votes?|voted|upvote|"
    r"react(?:s|ed|ions?)?|thumbs?)\b",
    re.IGNORECASE,
)

PLAYWRIGHT_CALLS = {
    "locator", "get_by_role", "get_by_text", "get_by_label", "get_by_placeholder",
    "get_by_test_id", "get_by_alt_text", "get_by_title", "query_selector",
    "query_selector_all", "wait_for_selector", "click", "dblclick", "hover",
    "filter", "has_text",
}
TYPING_CALLS = {"fill", "type", "dblclick", "check", "uncheck", "select_option", "set_input_files"}
# Network clients. urllib.parse only splits strings, so it is fine.
HTTP_MODULES = ("requests", "httpx", "aiohttp", "urllib3", "urllib.request", "http.client", "socket")


def _tree(name: str) -> ast.Module:
    return ast.parse((TOOLS / name).read_text(encoding="utf-8"))


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _calls_with_function(tree: ast.Module) -> list[tuple[ast.Call, str]]:
    """Every call paired with the name of the function that contains it."""
    found: list[tuple[ast.Call, str]] = []

    def visit(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            if isinstance(child, ast.Call):
                found.append((child, name))
            visit(child, name)

    visit(tree, "<module>")
    return found


@pytest.mark.parametrize("module", sorted(ALLOWED_CLICKS))
def test_no_engagement_controls_in_selectors(module: str) -> None:
    for call, _ in _calls_with_function(_tree(module)):
        if isinstance(call.func, ast.Attribute) and call.func.attr in PLAYWRIGHT_CALLS:
            for text in (s for arg in [*call.args, *[k.value for k in call.keywords]] for s in _strings(arg)):
                assert not FORBIDDEN_WORDS.search(text), (
                    f"{module}: {call.func.attr}({text!r}) targets an engagement control"
                )


@pytest.mark.parametrize("module", sorted(ALLOWED_CLICKS))
def test_clicks_only_in_allowlisted_functions(module: str) -> None:
    for call, function in _calls_with_function(_tree(module)):
        if isinstance(call.func, ast.Attribute) and call.func.attr == "click":
            assert function in ALLOWED_CLICKS[module], (
                f"{module}: click() inside {function}() is not allowlisted"
            )


@pytest.mark.parametrize("module", sorted(ALLOWED_CLICKS))
def test_nothing_is_typed_or_filled(module: str) -> None:
    for call, function in _calls_with_function(_tree(module)):
        if isinstance(call.func, ast.Attribute) and call.func.attr in TYPING_CALLS:
            pytest.fail(f"{module}: {call.func.attr}() in {function}() would type into the page")


@pytest.mark.parametrize("module", sorted(ALLOWED_CLICKS))
def test_only_escape_may_be_pressed(module: str) -> None:
    for call, _ in _calls_with_function(_tree(module)):
        if isinstance(call.func, ast.Attribute) and call.func.attr == "press":
            assert [s for a in call.args for s in _strings(a)] == ["Escape"]


@pytest.mark.parametrize("module", sorted(ALLOWED_CLICKS))
def test_no_http_clients_are_imported(module: str) -> None:
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not any(name == m or name.startswith(m + ".") for m in HTTP_MODULES), (
                f"{module} imports a network client: {name}"
            )


def test_the_scanner_would_catch_a_violation() -> None:
    """Guard the guard: a module that clicks a Like button must be flagged."""
    bad = ast.parse('def go(page):\n    page.get_by_role("button", name="Like").click()\n')
    flagged = False
    for call, _ in _calls_with_function(bad):
        if isinstance(call.func, ast.Attribute) and call.func.attr in PLAYWRIGHT_CALLS:
            flagged |= any(
                FORBIDDEN_WORDS.search(s) for arg in [*call.args, *[k.value for k in call.keywords]] for s in _strings(arg)
            )
    assert flagged
