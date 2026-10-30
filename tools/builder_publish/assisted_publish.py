#!/usr/bin/env python3
"""Assisted Builder Center publish (you log in and click Publish; tool fills).

LOCAL developer tool, not part of the deployed engine. It speeds up moving a
generated article into the Builder Center "Create article" editor, while
keeping you as the authenticated human and the one who approves going live.

What it does, for one campaign day:

1. Reads the article (title, description, body Markdown, tags) from the live
   DynamoDB table.
2. Opens Builder Center in a browser using your locally persisted login
   (you sign in once, manually; the tool never sees your credentials/cookies).
3. On the create-article page it fills Title and Description, and inserts the
   body into the rich-text editor by pasting the Markdown (the editor converts
   Markdown on paste). It then screenshots the result so you can verify the
   render.
4. It STOPS. You choose the Tags (fixed vocabulary) and click Publish yourself.

Security: no password/MFA/cookie handling, no auto-Publish. Matches the
project's design boundary (BuilderCenterPublisher stays unimplemented).

Usage:
    python tools/builder_publish/assisted_publish.py --day 1
    python tools/builder_publish/assisted_publish.py --day 1 --campaign aws-learning-21

Selectors were captured from the live page (see inspection_report.json):
    Title       textarea[placeholder="Title"]
    Description textarea[placeholder="Description"]
    Body editor [data-testid="draft-content"] (contenteditable rich text)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from playwright.sync_api import sync_playwright, Page, TimeoutError as PwTimeout

CREATE_URL = "https://builder.aws.com/create/content"
LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
REGION = "eu-west-1"

TITLE_SELECTOR = 'textarea[placeholder="Title"]'
DESCRIPTION_SELECTOR = 'textarea[placeholder="Description"]'
BODY_SELECTOR = '[data-testid="draft-content"]'


def _campaign_pk(campaign_id: str) -> str:
    return f"CAMPAIGN#{campaign_id}"


def _day_sk(day: int) -> str:
    return f"DAY#{day:04d}"


def load_article(table: str, campaign_id: str, day: int, region: str) -> dict[str, object]:
    """Load one day's article fields from DynamoDB."""
    ddb = boto3.client("dynamodb", region_name=region)
    resp = ddb.get_item(
        TableName=table,
        Key={"pk": {"S": _campaign_pk(campaign_id)}, "sk": {"S": _day_sk(day)}},
    )
    item = resp.get("Item")
    if not item:
        raise SystemExit(f"No article found for campaign={campaign_id} day={day}")
    tags_attr = item.get("tags", {})
    tags = tags_attr.get("SS") or [
        e["S"] for e in tags_attr.get("L", []) if "S" in e
    ]
    return {
        "title": item.get("title", {}).get("S", ""),
        "description": item.get("description", {}).get("S", ""),
        "body_markdown": item.get("body_markdown", {}).get("S", ""),
        "tags": tags,
        "day_state": item.get("day_state", {}).get("S", ""),
    }


def _fill_textarea(page: Page, selector: str, value: str, label: str) -> None:
    """Fill a textarea, waiting for it to be present."""
    page.wait_for_selector(selector, timeout=30000)
    page.fill(selector, value)
    print(f"  filled {label} ({len(value)} chars)")


def _insert_body(page: Page, markdown: str) -> None:
    """Insert the body Markdown into the rich-text editor via paste.

    ProseMirror-style editors convert Markdown on paste. We write the Markdown
    to the clipboard, focus the editor, and paste. If clipboard access is
    unavailable we fall back to typing, which also triggers live Markdown
    transforms in these editors.
    """
    page.wait_for_selector(BODY_SELECTOR, timeout=30000)
    editor = page.locator(BODY_SELECTOR).first
    editor.click()
    try:
        # Preferred: clipboard paste so the editor's markdown paste-handler runs.
        page.evaluate(
            "async (text) => { await navigator.clipboard.writeText(text); }",
            markdown,
        )
        modifier = "Meta" if sys.platform == "darwin" else "Control"
        page.keyboard.press(f"{modifier}+V")
        print("  pasted body into editor via clipboard")
    except Exception:  # noqa: BLE001 - fall back to typing
        editor.type(markdown, delay=0)
        print("  typed body into editor (clipboard unavailable)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assisted Builder Center publish")
    parser.add_argument("--day", type=int, required=True, help="Campaign day (1-based)")
    parser.add_argument("--campaign", default="aws-learning-21", help="Campaign id")
    parser.add_argument(
        "--table",
        default="aws-builder-aws-learning-21-campaign",
        help="DynamoDB table name",
    )
    parser.add_argument("--region", default=REGION)
    args = parser.parse_args(argv)

    article = load_article(args.table, args.campaign, args.day, args.region)
    print(f"\nArticle for {args.campaign} day {args.day}:")
    print(f"  title: {article['title']}")
    print(f"  tags (generated): {', '.join(article['tags'])}")  # type: ignore[arg-type]
    print(f"  body: {len(article['body_markdown'])} chars of Markdown")  # type: ignore[arg-type]

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            permissions=["clipboard-read", "clipboard-write"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Ensure we are logged in: load the site; if not signed in, let the user
        # sign in, then continue to the create page.
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nIf you are not already signed in, sign in now in the browser.")
        try:
            input("Press Enter when signed in and ready to open the editor... ")
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        print(f"Opening the create-article editor: {CREATE_URL}")
        page.goto(CREATE_URL, wait_until="domcontentloaded")

        try:
            _fill_textarea(page, TITLE_SELECTOR, str(article["title"]), "Title")
            _fill_textarea(
                page, DESCRIPTION_SELECTOR, str(article["description"]), "Description"
            )
            _insert_body(page, str(article["body_markdown"]))
        except PwTimeout as error:
            print(f"\nCould not find an expected field: {error}")
            print("The page layout may have changed; re-run the inspector.")
            context.close()
            return 2

        shot = OUT_DIR / f"assisted_day_{args.day:04d}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            print(f"\nScreenshot of the filled editor: {shot}")
        except Exception:  # noqa: BLE001 - best effort
            pass

        print("\n" + "=" * 68)
        print("REVIEW REQUIRED - the tool has stopped on purpose:")
        print("  1. Check the body rendered correctly (headings, code blocks).")
        print(f"  2. Choose Tags manually. Generated suggestions: "
              f"{', '.join(article['tags'])}")  # type: ignore[arg-type]
        print("  3. Click Publish yourself when you are satisfied.")
        print("=" * 68)
        try:
            input("\nPress Enter to close the browser (after you publish)... ")
        except (EOFError, KeyboardInterrupt):
            pass
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
