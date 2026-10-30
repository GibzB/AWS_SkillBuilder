#!/usr/bin/env python3
"""Probe the Builder Center tag endpoint directly from your logged-in session.

We know the picker calls:
    https://api.builder.aws.com/cs/getRecommendedTags?pageSize=20

This runs fetch() INSIDE your authenticated page (so it uses your session
cookies; the script never handles them) to learn:
  - what getRecommendedTags actually returns (shape, how many, slug field),
  - whether pageSize can be raised to pull many/all tags,
  - whether a search/query parameter is supported (q=, query=, search=, prefix=),
  - whether a sibling search endpoint exists (searchTags, tags, etc.).

View-only; not rate-limited. Writes probe_tag_endpoint.json.

Usage:
    AWS_PROFILE=... python tools/builder_publish/probe_tag_endpoint.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://builder.aws.com/"
CREATE_URL = "https://builder.aws.com/create/content"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
PROBE_PATH = OUT_DIR / "probe_tag_endpoint.json"

# fetch() runs in the page origin (builder.aws.com) so the api.builder.aws.com
# call carries the same credentials the app uses. Each candidate is tried; we
# record status, length, and a body preview.
PROBE_JS = r"""
async () => {
  const base = "https://api.builder.aws.com/cs";
  const candidates = [
    "/getRecommendedTags?pageSize=20",
    "/getRecommendedTags?pageSize=500",
    "/getRecommendedTags?pageSize=1000",
    "/getRecommendedTags?pageSize=100&query=s3",
    "/getRecommendedTags?pageSize=100&q=s3",
    "/getRecommendedTags?pageSize=100&search=s3",
    "/getRecommendedTags?pageSize=100&prefix=s3",
    "/getRecommendedTags?pageSize=100&filter=s3",
    "/searchTags?pageSize=100&query=s3",
    "/tags?pageSize=100",
    "/getTags?pageSize=100",
    "/v2/tags?pageSize=100",
  ];
  const out = [];
  for (const path of candidates) {
    const entry = { path };
    try {
      const r = await fetch(base + path, { headers: { "accept": "application/json" }, credentials: "include" });
      entry.status = r.status;
      const text = await r.text();
      entry.length = text.length;
      entry.preview = text.slice(0, 1200);
    } catch (e) {
      entry.error = String(e);
    }
    out.push(entry);
  }
  return out;
}
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1200, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to probe the tag endpoint...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        # Be on an app page so fetch runs in the right origin with session.
        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        results = page.evaluate(PROBE_JS)
        PROBE_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

        print(f"\nProbe results -> {PROBE_PATH}\n")
        for r in results:
            status = r.get("status", r.get("error", "?"))
            length = r.get("length", "-")
            print(f"  {r['path']}")
            print(f"    status={status} len={length}")
            prev = str(r.get("preview", "")).replace("\n", " ")
            if prev:
                print(f"    preview: {prev[:160]}")
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
