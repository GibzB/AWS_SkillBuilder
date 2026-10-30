#!/usr/bin/env python3
"""Harvest ONLY genuinely-selectable Builder Center tags from the live picker.

Lesson learned: neither getRecommendedTags nor searchTags matches what the tags
picker actually renders as selectable. The picker is a Cloudscape autocomplete
whose dropdown (role="option" items) is the ONLY source of truth for what can
be selected (e.g. 'new-to-aws' is returned by the API but never rendered).

This drives the real picker: for each query fragment it focuses the "Search
Tags" input, types the fragment, waits for the dropdown, and records the
rendered option labels. Unioning across many fragments yields the set of tags
you can actually pick. That set is written to tags_selectable.json and used by
the tag chooser so suggestions are always selectable.

View-only; not rate-limited for article creation.

Usage:
    AWS_PROFILE=... python tools/builder_publish/harvest_selectable_tags.py
    AWS_PROFILE=... python tools/builder_publish/harvest_selectable_tags.py --quick
"""

from __future__ import annotations

import argparse
import json
import string
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://builder.aws.com/"
CREATE_URL = "https://builder.aws.com/create/content"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
OUT_PATH = OUT_DIR / "tags_selectable.json"

TAGS_TRIGGER = 'text="Choose tags"'
SEARCH_INPUT = 'input[placeholder="Search Tags"]'
OPTION_SELECTOR = '[role="option"]'

# AWS service / topic fragments worth sweeping in addition to a-z, 0-9.
_AWS_TERMS = [
    "aws", "amazon", "s3", "ec2", "vpc", "iam", "lambda", "dynamodb", "rds",
    "cloudwatch", "api", "gateway", "eventbridge", "sns", "sqs", "security",
    "cost", "serverless", "network", "subnet", "route", "terraform",
    "cloudformation", "cdk", "storage", "compute", "database", "monitoring",
    "account", "fundamentals", "beginner", "architecture", "devops",
    "automation", "systems", "manager", "backup", "encryption", "kms",
    "cloudfront", "ecs", "eks", "fargate", "container", "ai", "ml", "bedrock",
    "analytics", "glue", "redshift", "kinesis", "step", "functions", "amplify",
    "cognito", "identity", "access", "policy", "role", "privilege", "cloud",
    "data", "web", "app", "migration", "scaling", "availability", "region",
]


def _read_rendered_options(page: Page) -> list[str]:
    """Read the labels of currently-rendered, selectable dropdown options.

    Cloudscape renders each option as a role=option element; the visible label
    is its text. Virtualized duplicates are de-duped by the caller's set.
    Options marked aria-disabled=true are excluded.
    """
    js = r"""
    () => {
      const nodes = Array.from(document.querySelectorAll('[role="option"]'));
      const out = [];
      for (const el of nodes) {
        if (el.getAttribute('aria-disabled') === 'true') continue;
        const txt = (el.innerText || '').trim();
        if (txt) out.push(txt);
      }
      return out;
    }
    """
    labels: list[str] = page.evaluate(js)
    # Options can carry extra lines; keep the first line (the tag label).
    cleaned = []
    for label in labels:
        first = label.splitlines()[0].strip()
        if first:
            cleaned.append(first)
    return cleaned


def _open_picker(page: Page) -> None:
    page.wait_for_selector(TAGS_TRIGGER, timeout=30000)
    page.locator(TAGS_TRIGGER).first.scroll_into_view_if_needed()
    page.locator(TAGS_TRIGGER).first.click()
    page.wait_for_timeout(500)


def _clear_search(page: Page) -> None:
    """Fully clear the Search Tags input and confirm it is empty.

    Cloudscape re-renders the input, so a single Ctrl+A/Delete is unreliable.
    We select-all + delete, then verify the value is empty and retry, so stale
    text never bleeds from one query into the next.
    """
    box = page.locator(SEARCH_INPUT).first
    for _ in range(4):
        box.click()
        box.press("Meta+A" if _is_mac() else "Control+A")
        box.press("Delete")
        box.press("Backspace")
        page.wait_for_timeout(120)
        try:
            if (box.input_value() or "") == "":
                return
        except Exception:  # noqa: BLE001
            return


def _is_mac() -> bool:
    import sys

    return sys.platform == "darwin"


def _query(page: Page, fragment: str) -> list[str]:
    """Query ONE fragment: clear the box, type it, let the dropdown refresh.

    Each fragment is searched in isolation (the box is cleared first and after),
    so results reflect only this fragment rather than bundling across queries.
    Only options whose label actually contains the fragment are kept, which
    guards against the picker momentarily showing stale/default recommendations.
    """
    _clear_search(page)
    box = page.locator(SEARCH_INPUT).first
    box.click()
    box.type(fragment, delay=40)
    # Confirm the input holds exactly the fragment before reading the dropdown.
    for _ in range(5):
        page.wait_for_timeout(250)
        try:
            if (box.input_value() or "").strip().lower() == fragment.lower():
                break
        except Exception:  # noqa: BLE001
            break
    page.wait_for_timeout(500)
    rendered = _read_rendered_options(page)
    # Keep only options that actually match the fragment, so default
    # recommendations (shown when the filter hasn't applied) are not counted.
    frag = fragment.lower()
    matched = [t for t in rendered if frag in t.lower()]
    # Clear afterward so the next iteration starts clean.
    _clear_search(page)
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest selectable Builder Center tags")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Sweep only the AWS term list (skip a-z/0-9) for a faster run.",
    )
    args = parser.parse_args(argv)

    fragments = list(_AWS_TERMS)
    if not args.quick:
        fragments = list(string.ascii_lowercase) + list(string.digits) + _AWS_TERMS
    # De-dup fragments preserving order.
    seen_f: dict[str, None] = {}
    for f in fragments:
        seen_f.setdefault(f, None)
    fragments = list(seen_f.keys())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selectable: set[str] = set()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to harvest selectable tags...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        try:
            _open_picker(page)
        except Exception as error:  # noqa: BLE001
            print(f"Could not open the tags picker: {error}")
            context.close()
            return 2

        print(f"Sweeping {len(fragments)} fragments through the live picker...")
        for i, frag in enumerate(fragments, 1):
            try:
                opts = _query(page, frag)
                before = len(selectable)
                selectable.update(opts)
                gained = len(selectable) - before
                if gained:
                    print(f"  [{i}/{len(fragments)}] {frag!r}: +{gained} (total {len(selectable)})")
            except Exception:  # noqa: BLE001 - keep sweeping on any single failure
                continue
        context.close()

    tags = sorted(selectable)
    OUT_PATH.write_text(json.dumps(tags, indent=2), encoding="utf-8")
    print(f"\nHarvested {len(tags)} SELECTABLE tags -> {OUT_PATH}")
    if tags:
        print("sample:", ", ".join(tags[:40]))
    # Sanity: confirm the known-bad tag is absent.
    print("contains 'new-to-aws' (should be False if truly non-selectable):",
          "new-to-aws" in selectable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
