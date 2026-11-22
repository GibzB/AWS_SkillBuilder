#!/usr/bin/env python3
"""Assisted wish drafting: fill the create-wish form, you review and submit.

Opens https://builder.aws.com/create/wish in your saved, signed-in browser
profile and fills the title and description from a Markdown file in
``tools/builder_publish/wishes/``. It then lists every other field it found
(for example a category) so you can set them by hand.

This tool NEVER clicks anything: not Submit, not Create, not a dropdown. It only
types into the two text fields. You review the draft in the browser and press
Submit yourself, the same split as the article tool. A test fails if a click is
ever added to this module.

The form's markup is not public (it sits behind sign-in), so field detection is
heuristic. ``--inspect`` only lists the form's fields and saves a screenshot and
the page HTML, which is how the detection gets refined.

Usage:
    python tools/builder_publish/draft_wish.py                       # default wish
    python tools/builder_publish/draft_wish.py --wish create-limit-wait-time
    python tools/builder_publish/draft_wish.py --inspect
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harvest_badges import LOGIN_URL, PROFILE_DIR  # noqa: E402

CREATE_WISH_URL = "https://builder.aws.com/create/wish"
WISHES_DIR = Path(__file__).resolve().parent / "wishes"
DEFAULT_WISH = "streak-day-boundary"
OUT_DIR = Path(__file__).resolve().parent

# Every element a person could type into. Document order matters: it is the
# order the page reports, and the index we fill by.
FIELD_SELECTOR = (
    'input:not([type]), input[type="text"], textarea, '
    '[contenteditable="true"], [role="textbox"]'
)

_TITLE_HINT = re.compile(r"title|summary|name|headline", re.IGNORECASE)
_BODY_HINT = re.compile(r"descri|detail|wish|idea|tell|explain|what", re.IGNORECASE)
_SEARCH_HINT = re.compile(r"search", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"<[^>\n]{0,40}>|\bTODO\b|\bTBD\b", re.IGNORECASE)

_DESCRIBE_JS = r"""
(selector) => {
  return Array.from(document.querySelectorAll(selector)).map((el, index) => {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    return {
      index,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      placeholder: el.getAttribute('placeholder') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      name: el.getAttribute('name') || '',
      testid: el.getAttribute('data-testid') || '',
      editable: el.getAttribute('contenteditable') === 'true' || el.getAttribute('role') === 'textbox',
      visible,
      inChrome: !!el.closest('header, nav, [role="search"], [role="navigation"]'),
    };
  });
}
"""

_LISTING_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (el) => {
    const label = clean(el.getAttribute('aria-label'));
    const shown = clean(el.innerText);
    if (label && shown && label !== shown) return (label + ': ' + shown).slice(0, 80);
    return (label || shown).slice(0, 80);
  };
  const pick = (sel) => Array.from(document.querySelectorAll(sel))
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
    .map(text).filter(Boolean);
  return {
    selects: pick('select, [role="combobox"], [role="listbox"], [aria-haspopup="listbox"]'),
    buttons: pick('button, [role="button"]'),
    checkboxes: pick('input[type="checkbox"], [role="checkbox"], [role="radio"], input[type="radio"]'),
  };
}
"""


@dataclass(frozen=True)
class Wish:
    """A wish ready to type into the form."""

    title: str
    body: str


def load_wish(path: Path) -> Wish:
    """Read a wish file: first line ``# Title``, then the body."""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{path.name}: the first line must be '# <title>'")
    return Wish(title=lines[0][2:].strip(), body="\n".join(lines[1:]).strip())


def check_wish(wish: Wish) -> list[str]:
    """Problems to fix before drafting. Empty means fine.

    The form's real length limits are unknown, so long text only warns.
    """
    problems: list[str] = []
    if not wish.title:
        problems.append("the title is empty")
    if not wish.body:
        problems.append("the description is empty")
    if _PLACEHOLDER.search(f"{wish.title}\n{wish.body}"):
        problems.append("the text still contains a placeholder")
    if "\u2014" in wish.title + wish.body:
        problems.append("the text contains an em dash")
    if len(wish.title) > 120:
        problems.append("WARNING: the title is over 120 characters; the form may reject it")
    if len(wish.body) > 3000:
        problems.append("WARNING: the description is over 3000 characters; the form may reject it")
    return problems


def choose_fields(elements: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Pick the (title, description) elements from the page's text fields.

    Search boxes and anything in the page header or navigation are ignored. The
    title is the first field whose placeholder, label or name reads like a title.
    The description is the first remaining field that reads like a description or
    is a rich-text box; otherwise the next field after the title.
    """
    usable = [
        e for e in elements
        if e["visible"]
        and not e["inChrome"]
        and not _SEARCH_HINT.search(f"{e['placeholder']} {e['ariaLabel']} {e['name']}")
    ]

    def label(e: dict[str, Any]) -> str:
        return f"{e['placeholder']} {e['ariaLabel']} {e['name']} {e['testid']}"

    title = next((e for e in usable if _TITLE_HINT.search(label(e))), None)
    if title is None and usable:
        title = usable[0]
    rest = [e for e in usable if title is None or e["index"] != title["index"]]
    body = next((e for e in rest if _BODY_HINT.search(label(e))), None)
    if body is None:
        body = next((e for e in rest if e["editable"] or e["tag"] == "textarea"), None)
    if body is None and rest:
        body = rest[0]
    return (title["index"] if title else None, body["index"] if body else None)


def _type_into(page: Page, index: int, element: dict[str, Any], text: str) -> None:
    """Type ``text`` into one field without clicking elsewhere."""
    target = page.locator(FIELD_SELECTOR).nth(index)
    if element["editable"]:
        target.focus()
        page.keyboard.insert_text(text)
    else:
        target.fill(text)


def fill_wish(page: Page, wish: Wish) -> dict[str, Any]:
    """Fill the title and description and report what else is on the form."""
    page.goto(CREATE_WISH_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    elements: list[dict[str, Any]] = page.evaluate(_DESCRIBE_JS, FIELD_SELECTOR)
    title_index, body_index = choose_fields(elements)
    by_index = {e["index"]: e for e in elements}
    if title_index is not None:
        _type_into(page, title_index, by_index[title_index], wish.title)
    if body_index is not None:
        _type_into(page, body_index, by_index[body_index], wish.body)
    listing: dict[str, list[str]] = page.evaluate(_LISTING_JS)
    return {
        "title_filled": title_index is not None,
        "body_filled": body_index is not None,
        "fields_seen": len([e for e in elements if e["visible"] and not e["inChrome"]]),
        **listing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assisted wish drafting (never submits).")
    parser.add_argument("--wish", default=DEFAULT_WISH, help="File name in wishes/, without .md")
    parser.add_argument("--inspect", action="store_true", help="List the form fields only.")
    args = parser.parse_args(argv)

    wish = load_wish(WISHES_DIR / f"{args.wish}.md")
    problems = check_wish(wish)
    for problem in problems:
        print(f"  check: {problem}")
    if any(not p.startswith("WARNING") for p in problems):
        print("Fix those first, then run again.")
        return 2
    print(f"Wish: {wish.title} ({len(wish.title)} and {len(wish.body)} characters)")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to open the create-wish form...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        if args.inspect:
            page.goto(CREATE_WISH_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            elements = page.evaluate(_DESCRIBE_JS, FIELD_SELECTOR)
            for element in elements:
                print(f"  field: {element}")
            print(f"  extras: {page.evaluate(_LISTING_JS)}")
            (OUT_DIR / "wish_form.html").write_text(page.content(), encoding="utf-8")
        else:
            report = fill_wish(page, wish)
            print(f"\nTitle filled: {report['title_filled']}   Description filled: {report['body_filled']}")
            print(f"Dropdowns or selectors on the form: {report['selects'] or 'none seen'}")
            print(f"Buttons on the form: {report['buttons']}")
            if not (report["title_filled"] and report["body_filled"]):
                print("I could not identify both fields. Paste the text by hand:")
                print(f"\n{wish.title}\n\n{wish.body}\n")
        try:
            page.screenshot(path=str(OUT_DIR / "wish_form.png"), full_page=True)
        except Exception:  # noqa: BLE001 - the screenshot is a convenience
            pass
        print(
            "\nReview the draft in the browser, set any dropdown (such as the type of "
            "request) yourself, then YOU press Submit. Nothing was submitted."
        )
        try:
            input("Press Enter to close the browser... ")
        except (EOFError, KeyboardInterrupt):
            pass
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
