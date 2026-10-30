#!/usr/bin/env python3
"""Harvest the full Builder Center tag vocabulary from your logged-in session.

Findings from probing the API:
  - GET /cs/getRecommendedTags?pageSize=N  -> {"tagList":[...]} and the list
    grows with pageSize (pageSize=1000 returns thousands of bytes of slugs).
  - GET /cs/searchTags?fragment=<q>&limit=<n> -> per-query search (requires
    'fragment' and 'limit').

Strategy: pull a large recommended list, then sweep searchTags across a-z,
0-9, and AWS service/topic terms, unioning everything into one de-duplicated
slug vocabulary. All calls run via fetch() inside your authenticated page, so
they use your session (no credential handling here). View-only; not
rate-limited for article creation.

Writes tags_vocabulary.json (the full list) for the Bedrock tag chooser.

Usage:
    AWS_PROFILE=... python tools/builder_publish/harvest_tags.py
"""

from __future__ import annotations

import json
import string
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://builder.aws.com/"
CREATE_URL = "https://builder.aws.com/create/content"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
VOCAB_PATH = OUT_DIR / "tags_vocabulary.json"

# Search fragments: every letter, digit, 2-letter combos are overkill; letters
# + digits + common AWS terms give wide coverage of the searchTags index.
_AWS_TERMS = [
    "aws", "amazon", "s3", "ec2", "vpc", "iam", "lambda", "dynamodb", "rds",
    "cloudwatch", "api", "gateway", "eventbridge", "sns", "sqs", "security",
    "cost", "serverless", "network", "subnet", "route", "terraform",
    "cloudformation", "cdk", "storage", "compute", "database", "monitoring",
    "account", "fundamentals", "beginner", "architecture", "devops",
    "automation", "systems", "manager", "backup", "encryption", "kms",
    "cloudfront", "ecs", "eks", "fargate", "container", "kubernetes", "docker",
    "ai", "ml", "bedrock", "sagemaker", "analytics", "athena", "glue",
    "redshift", "kinesis", "step", "functions", "amplify", "cognito",
    "identity", "access", "policy", "role", "least", "privilege",
]

HARVEST_JS = r"""
async (fragments) => {
  const base = "https://api.builder.aws.com/cs";
  const tags = new Set();
  const diag = { recommended: 0, searchHits: 0, searchErrors: 0, searchShape: null };

  // 1) Large recommended list.
  try {
    const r = await fetch(base + "/getRecommendedTags?pageSize=5000",
      { headers: { accept: "application/json" }, credentials: "include" });
    if (r.ok) {
      const j = await r.json();
      const list = j.tagList || j.tags || [];
      list.forEach(t => tags.add(typeof t === "string" ? t : (t.tag || t.name || t.id)));
      diag.recommended = list.length;
    }
  } catch (e) { diag.recommendedError = String(e); }

  // 2) searchTags sweep (requires fragment + limit).
  for (const frag of fragments) {
    try {
      const r = await fetch(base + "/searchTags?fragment=" + encodeURIComponent(frag) + "&limit=100",
        { headers: { accept: "application/json" }, credentials: "include" });
      if (!r.ok) { diag.searchErrors++; continue; }
      const j = await r.json();
      if (diag.searchShape === null) diag.searchShape = Object.keys(j);
      const list = j.tagList || j.tags || j.results || j.items || [];
      list.forEach(t => tags.add(typeof t === "string" ? t : (t.tag || t.name || t.id)));
      diag.searchHits += list.length;
    } catch (e) { diag.searchErrors++; }
  }

  return { tags: Array.from(tags).filter(Boolean).sort(), diag };
}
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fragments = list(string.ascii_lowercase) + list(string.digits) + _AWS_TERMS

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1200, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to harvest all tags...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        print(f"Harvesting (recommended list + {len(fragments)} search fragments)...")
        result = page.evaluate(HARVEST_JS, fragments)
        context.close()

    tags = result.get("tags", [])
    diag = result.get("diag", {})
    VOCAB_PATH.write_text(json.dumps(tags, indent=2), encoding="utf-8")
    print(f"\nHarvested {len(tags)} distinct tags -> {VOCAB_PATH}")
    print("diagnostics:", json.dumps(diag))
    if tags:
        print("sample:", ", ".join(tags[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
