#!/usr/bin/env python3
"""Capture the Builder Center tag vocabulary from the create-article picker.

LOCAL developer tool. Opens the create-article page in your logged-in session,
opens the "Choose tags" dropdown, and records the options it offers. Because
the picker is a Cloudscape combobox that filters as you type, this tries both
the initial option list and a set of seed queries (one per generated tag plus
common AWS terms) to surface as many real, selectable tags as possible.

The result is written to tags_vocabulary.json so the batch publisher can map
each article's generated tags to the closest valid Builder Center tags.

Usage:
    AWS_PROFILE=... python tools/builder_publish/capture_tags.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

CREATE_URL = "https://builder.aws.com/create/content"
LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
VOCAB_PATH = OUT_DIR / "tags_vocabulary.json"

# The tags field is a Cloudscape combobox labelled "Choose tags".
TAGS_TRIGGER = 'text="Choose tags"'

# Seed queries to type into the filter to surface options: generated tags from
# our campaign plus common AWS service/topic terms.
SEED_QUERIES = [
    "", "aws", "s3", "ec2", "vpc", "iam", "lambda", "dynamodb", "rds",
    "cloudwatch", "api", "eventbridge", "sns", "security", "cost",
    "serverless", "networking", "terraform", "cloudformation", "storage",
    "compute", "database", "monitoring", "account", "fundamentals",
    "beginner", "architecture", "devops", "automation", "systems manager",
]


def _open_tags_dropdown(page: Page) -> None:
    """Scroll to and open the tags combobox."""
    page.wait_for_selector(TAGS_TRIGGER, timeout=30000)
    page.locator(TAGS_TRIGGER).first.scroll_into_view_if_needed()
    page.locator(TAGS_TRIGGER).first.click()
    page.wait_for_timeout(600)


def _read_options(page: Page) -> list[str]:
    """Read currently-visible dropdown option labels."""
    # Cloudscape renders options as [role=option]; fall back to listbox items.
    opts = page.eval_on_selector_all(
        '[role="option"], [data-testid="tags"] li, ul[role="listbox"] li',
        "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)",
    )
    return [o for o in opts if o]


def _type_filter(page: Page, query: str) -> None:
    """Type a filter query into the focused combobox input."""
    # Clear any existing text, then type the query.
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    if query:
        page.keyboard.type(query, delay=20)
    page.wait_for_timeout(500)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    found: set[str] = set()
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to capture tags...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        page.goto(CREATE_URL, wait_until="domcontentloaded")
        try:
            _open_tags_dropdown(page)
        except Exception as error:  # noqa: BLE001
            print(f"Could not open the tags dropdown: {error}")
            context.close()
            return 2

        for query in SEED_QUERIES:
            try:
                if query:
                    _type_filter(page, query)
                for label in _read_options(page):
                    found.add(label)
            except Exception:  # noqa: BLE001 - keep going across queries
                continue
        context.close()

    vocab = sorted(found)
    VOCAB_PATH.write_text(json.dumps(vocab, indent=2), encoding="utf-8")
    print(f"\nCaptured {len(vocab)} distinct tag options -> {VOCAB_PATH}")
    if vocab:
        print("Sample:", ", ".join(vocab[:30]))
    else:
        print("No options captured. The picker markup may differ; we can fall")
        print("back to manual tag selection in the batch tool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
