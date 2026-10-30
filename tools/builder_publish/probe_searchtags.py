#!/usr/bin/env python3
"""Probe searchTags to find the ACTUALLY selectable tag set.

getRecommendedTags returns a broad taxonomy, but some of those tags are not
offered by the picker's live search (e.g. 'new-to-aws' exists in the list but
cannot be selected). The picker uses /cs/searchTags?fragment=<q>&limit=<n>.
This probes it with real queries and reports exactly what it returns, so we can
build the tag chooser against the SELECTABLE set rather than the broad taxonomy.

View-only; not rate-limited. Writes searchtags_probe.json.

Usage:
    AWS_PROFILE=... python tools/builder_publish/probe_searchtags.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://builder.aws.com/"
CREATE_URL = "https://builder.aws.com/create/content"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
PROBE_PATH = OUT_DIR / "searchtags_probe.json"

PROBE_JS = r"""
async (queries) => {
  const base = "https://api.builder.aws.com/cs/searchTags";
  const out = [];
  for (const q of queries) {
    const entry = { query: q };
    try {
      const url = base + "?fragment=" + encodeURIComponent(q) + "&limit=50";
      const r = await fetch(url, { headers: { accept: "application/json" }, credentials: "include" });
      entry.status = r.status;
      const text = await r.text();
      let j = null;
      try { j = JSON.parse(text); } catch (e) {}
      if (j) {
        entry.keys = Object.keys(j);
        // Try the known/likely list fields.
        const list = j.tagsNameList || j.tagList || j.tags || j.results || [];
        entry.count = list.length;
        entry.tags = list.slice(0, 50);
      } else {
        entry.raw = text.slice(0, 300);
      }
    } catch (e) {
      entry.error = String(e);
    }
    out.push(entry);
  }
  return out;
}
"""

QUERIES = ["new", "new-to-aws", "beginner", "s3", "security", "cost", "serverless", "vpc"]


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
        print("\nSign in if prompted, then press Enter to probe searchTags...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        results = page.evaluate(PROBE_JS, QUERIES)
        context.close()

    PROBE_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nsearchTags probe -> {PROBE_PATH}\n")
    for r in results:
        print(f"  query={r['query']!r} status={r.get('status')} "
              f"keys={r.get('keys')} count={r.get('count')}")
        tags = r.get("tags") or []
        if tags:
            print(f"    tags: {', '.join(str(t) for t in tags[:20])}")
        if r.get("raw"):
            print(f"    raw: {r['raw']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
