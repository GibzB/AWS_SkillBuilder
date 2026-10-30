#!/usr/bin/env python3
"""Interactive Builder Center inspector (you log in; the tool observes).

This is a LOCAL developer tool, not part of the deployed engine. It is used to
learn the real DOM structure of the AWS Builder Center "create article" page so
we can design an assisted-publish helper around what actually exists.

Security posture (deliberate, matches the project's design):

* YOU sign in manually in a real browser window. The script never types, reads,
  stores, or transmits your password, MFA code, or session cookies.
* The browser profile is persisted locally under ~/.aws-builder-publish/ ONLY,
  so you don't have to log in every run. That directory stays on your machine;
  it is never committed (see .gitignore) or uploaded anywhere.
* This script does not post anything. It observes the page and writes a
  structured report of form fields, editors, and buttons for us to review.

Usage:
    python tools/builder_publish/inspect_builder_center.py

Then: sign in when the browser opens, navigate to the create-article page
(or let the script try a best-effort navigation), and press Enter in the
terminal when you are on the article editor. The tool writes a report to
tools/builder_publish/inspection_report.json and a screenshot alongside it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
REPORT_PATH = OUT_DIR / "inspection_report.json"
SCREENSHOT_PATH = OUT_DIR / "inspection_page.png"


def _inspect_page(page: Page) -> dict[str, object]:
    """Collect a structured snapshot of the current page's input surface.

    Runs entirely in the page via one evaluate() call: enumerates inputs,
    textareas, contenteditable/rich-text editors, selects, and buttons with
    their identifying attributes so we can map article fields to selectors.
    """
    script = r"""
    () => {
      const described = (el) => ({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || null,
        id: el.id || null,
        name: el.getAttribute('name') || null,
        placeholder: el.getAttribute('placeholder') || null,
        ariaLabel: el.getAttribute('aria-label') || null,
        dataTestId: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || null,
        role: el.getAttribute('role') || null,
        contentEditable: el.getAttribute('contenteditable') || null,
        className: (el.getAttribute('class') || '').slice(0, 120) || null,
        text: (el.innerText || '').trim().slice(0, 60) || null,
      });
      const pick = (sel) => Array.from(document.querySelectorAll(sel)).slice(0, 40).map(described);
      return {
        url: location.href,
        title: document.title,
        inputs: pick('input'),
        textareas: pick('textarea'),
        selects: pick('select'),
        editors: pick('[contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor, .cm-content'),
        buttons: pick('button, [role="button"], input[type="submit"]'),
        iframes: Array.from(document.querySelectorAll('iframe')).map(f => ({
          src: f.getAttribute('src') || null, id: f.id || null,
          title: f.getAttribute('title') || null,
        })),
      };
    }
    """
    result: dict[str, object] = page.evaluate(script)
    return result


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # Use Playwright's bundled Chromium with its OWN isolated profile, not
        # system Chrome. Reusing the system Chrome profile conflicts with an
        # already-running Chrome (locked profile) and hangs the launch. An
        # isolated persistent profile keeps your login between runs without
        # touching your everyday browser. Stored locally only.
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        print(f"\nOpening {LOGIN_URL}")
        print("A Chrome window will open. Sign in with your AWS Builder ID")
        print("(password + MFA). The script never sees your credentials.\n")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print("Steps:")
        print("  1. Sign in in the browser window.")
        print("  2. Navigate to the page where you CREATE/WRITE a new article.")
        print("  3. Come back here and press Enter to capture that page.\n")
        try:
            input("Press Enter once you are on the article-editor page... ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted before capture.")
            context.close()
            return 1

        print(f"\nCapturing: {page.url}")
        report = _inspect_page(page)

        # Also capture any same-origin iframes' editor surfaces, since rich
        # editors are sometimes inside an iframe.
        frame_reports = []
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_reports.append(
                    {
                        "frame_url": frame.url,
                        "snapshot": frame.evaluate(
                            "() => ({inputs: document.querySelectorAll('input').length,"
                            " editors: document.querySelectorAll('[contenteditable=\"true\"],"
                            " [role=\"textbox\"], .ProseMirror, .ql-editor').length})"
                        ),
                    }
                )
            except Exception:  # noqa: BLE001 - cross-origin frames are expected to fail
                frame_reports.append({"frame_url": frame.url, "snapshot": "inaccessible"})
        report["child_frames"] = frame_reports

        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        except Exception:  # noqa: BLE001 - screenshot is best-effort
            pass

        print(f"\nReport written to: {REPORT_PATH}")
        print(f"Screenshot:        {SCREENSHOT_PATH}")
        print("\nReview complete. Press Enter to close the browser.")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
