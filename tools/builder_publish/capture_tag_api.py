#!/usr/bin/env python3
"""Capture the Builder Center tag API traffic to learn the real endpoint(s).

The picker is server-side (api.builder.aws.com/cs/getRecommendedTags?pageSize=20
returns the 20 shown options). This logs the FULL bodies of api.builder.aws.com
responses and watches what fires when we type into the picker, so we can tell:
  - the response shape (fields, tag format, any total/paging info),
  - whether typing calls a different search endpoint (and its query param),
  - whether pageSize can be raised to fetch more.

View-only (does not create an article), so it is not rate-limited.
Writes tag_api_capture.json with each endpoint's URL and body.

Usage:
    AWS_PROFILE=... python tools/builder_publish/capture_tag_api.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

CREATE_URL = "https://builder.aws.com/create/content"
LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
CAP_PATH = OUT_DIR / "tag_api_capture.json"
TAGS_TRIGGER = 'text="Choose tags"'


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    calls: list[dict[str, object]] = []

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(resp: object) -> None:
            url = getattr(resp, "url", "")
            if "api.builder.aws.com" not in url:
                return
            entry: dict[str, object] = {
                "url": url,
                "status": getattr(resp, "status", None),
            }
            try:
                entry["body"] = resp.text()  # type: ignore[attr-defined]
            except Exception as error:  # noqa: BLE001
                entry["body_error"] = str(error)
            calls.append(entry)

        page.on("response", on_response)

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to capture tag API...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Open the picker and type several queries so any search endpoint fires.
        try:
            trigger = page.locator(TAGS_TRIGGER).first
            trigger.scroll_into_view_if_needed()
            trigger.click()
            page.wait_for_timeout(1000)
            for query in ["s3", "lambda", "secur", "network", "ec2"]:
                for ch in query:
                    page.keyboard.type(ch, delay=40)
                page.wait_for_timeout(900)
                for _ in query:
                    page.keyboard.press("Backspace")
                page.wait_for_timeout(400)
        except Exception as error:  # noqa: BLE001
            print("interaction error:", error)

        page.wait_for_timeout(1000)
        CAP_PATH.write_text(json.dumps(calls, indent=2), encoding="utf-8")

        # Summarize distinct endpoints (path + query keys).
        print(f"\nCaptured {len(calls)} api.builder.aws.com responses -> {CAP_PATH}")
        seen: set[str] = set()
        for c in calls:
            url = str(c["url"])
            # strip the value of query params for a compact signature
            base = url.split("?")[0]
            params = url.split("?")[1] if "?" in url else ""
            keys = ",".join(sorted(p.split("=")[0] for p in params.split("&") if p))
            sig = f"{base}?{keys}"
            if sig not in seen:
                seen.add(sig)
                body = str(c.get("body", ""))
                print(f"\n  {sig}  (status {c.get('status')})")
                print(f"    body preview: {body[:300]}")
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
