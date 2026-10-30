#!/usr/bin/env python3
"""Inspect what the tags picker ACTUALLY renders as selectable options.

The searchTags API returns 'new-to-aws', yet it is not selectable in the
picker. So the picker renders a different/filtered set than the raw API. This
tool lets YOU drive the picker: you open it and type a query, then press Enter
here, and it captures the real, rendered dropdown options (what you can click)
plus their DOM so we can see how the picker decides what is selectable.

Run it, then in the browser: go to the create page, open the Tags picker, type
a query (e.g. 'new'), and come back to press Enter to capture. Repeat for a few
queries; type 'done' to finish.

View-only; not rate-limited. Writes picker_options.json.

Usage:
    AWS_PROFILE=... python tools/builder_publish/inspect_picker_options.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://builder.aws.com/"
CREATE_URL = "https://builder.aws.com/create/content"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
OUT_PATH = OUT_DIR / "picker_options.json"


def _capture(page: Page) -> dict[str, object]:
    """Capture every plausible dropdown option element and its state."""
    js = r"""
    () => {
      const sel = '[role="option"], li[role="option"], [data-testid*="option"], '
                + 'ul[role="listbox"] li, [class*="option"]';
      const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 80);
      const opt = nodes.map(el => ({
        text: (el.innerText || '').trim().slice(0, 80),
        role: el.getAttribute('role'),
        ariaDisabled: el.getAttribute('aria-disabled'),
        ariaSelected: el.getAttribute('aria-selected'),
        dataTestId: el.getAttribute('data-testid') || el.getAttribute('data-test-id'),
        className: (el.getAttribute('class') || '').slice(0, 100),
        clickable: !(el.getAttribute('aria-disabled') === 'true'),
      }));
      // Also grab the current value of any visible combobox/search input.
      const inputs = Array.from(document.querySelectorAll('input'))
        .filter(i => i.offsetParent !== null)
        .map(i => ({ value: i.value, placeholder: i.placeholder, aria: i.getAttribute('aria-label') }));
      return { optionCount: opt.length, options: opt, visibleInputs: inputs.slice(0, 8) };
    }
    """
    result: dict[str, object] = page.evaluate(js)
    return result


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    captures: list[dict[str, object]] = []

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        print("\nIn the browser: open the Tags picker and type a query.")
        print("Then here, type a label for what you typed (e.g. 'new') and")
        print("press Enter to capture the rendered options. Type 'done' to end.\n")

        while True:
            try:
                label = input("capture label (or 'done'): ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if label.lower() == "done":
                break
            snap = _capture(page)
            snap["label"] = label
            captures.append(snap)
            opts = snap.get("options", [])
            print(f"  captured {snap.get('optionCount')} options")
            for o in opts[:25]:  # type: ignore[index]
                mark = "" if o.get("clickable") else "  [DISABLED]"
                print(f"    - {o.get('text')!r}{mark}")

        OUT_PATH.write_text(json.dumps(captures, indent=2), encoding="utf-8")
        print(f"\nWrote {OUT_PATH}")
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
