#!/usr/bin/env python3
"""Diagnose HOW the Builder Center tags picker serves options.

Determines whether the picker filters a full client-side list or fetches
options from a backend as you type. This decides the harvest strategy:
- client-side full list  -> read it all from the DOM / JS state
- server-side search      -> capture the API responses while typing queries

It opens the create page (viewing only, not creating a new article), captures
network responses that look tag-related, opens the picker, types a few letters,
and records what the DOM shows. Writes diagnose_tags.json.

Usage:
    AWS_PROFILE=... python tools/builder_publish/diagnose_tags.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

CREATE_URL = "https://builder.aws.com/create/content"
LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
DIAG_PATH = OUT_DIR / "diagnose_tags.json"
TAGS_TRIGGER = 'text="Choose tags"'


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    captured_responses: list[dict[str, object]] = []

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(resp: object) -> None:
            try:
                url = resp.url  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                return
            low = url.lower()
            # Heuristic: responses that likely carry tag data.
            if any(k in low for k in ("tag", "taxonom", "topic", "label", "graphql", "/api/")):
                entry: dict[str, object] = {"url": url}
                try:
                    ct = resp.headers.get("content-type", "")  # type: ignore[attr-defined]
                    if "json" in ct:
                        body = resp.json()  # type: ignore[attr-defined]
                        text = json.dumps(body)
                        entry["json_len"] = len(text)
                        # Keep a short preview; full body may be large.
                        entry["preview"] = text[:800]
                        # Heuristic: does it mention many slug-like tokens?
                        entry["hyphen_tokens"] = text.count('-"') + text.count('-\\"')
                except Exception:  # noqa: BLE001
                    pass
                captured_responses.append(entry)

        page.on("response", on_response)

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to diagnose the picker...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        dom_report: dict[str, object] = {}
        try:
            trigger = page.locator(TAGS_TRIGGER).first
            trigger.scroll_into_view_if_needed()
            trigger.click()
            page.wait_for_timeout(800)
            # How many options with no filter?
            dom_report["options_empty_filter"] = page.eval_on_selector_all(
                '[role="option"]', "els => els.length"
            )
            # Type a single letter and see how counts change.
            for letter in ["a", "s", "e"]:
                page.keyboard.type(letter, delay=30)
                page.wait_for_timeout(600)
                dom_report[f"options_after_{letter!r}"] = page.eval_on_selector_all(
                    '[role="option"]', "els => els.length"
                )
                page.keyboard.press("Backspace")
                page.wait_for_timeout(300)
        except Exception as error:  # noqa: BLE001
            dom_report["error"] = str(error)

        result = {
            "dom": dom_report,
            "tag_related_responses": captured_responses[:20],
            "response_count": len(captured_responses),
        }
        DIAG_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nDiagnosis written to {DIAG_PATH}")
        print("DOM option counts:", json.dumps(dom_report))
        print("tag-related network responses captured:", len(captured_responses))
        for r in captured_responses[:8]:
            print("  -", str(r.get("url"))[:110])
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
