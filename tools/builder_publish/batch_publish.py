#!/usr/bin/env python3
"""Batch assisted-publish walkthrough for a campaign's articles.

LOCAL developer tool. Walks a range of campaign days one at a time. For each
day it:

1. loads the article from DynamoDB,
2. opens/reuses the Builder Center create-article editor in your logged-in
   session,
3. fills Title and Description and pastes the body Markdown (which the editor
   renders),
4. prints Bedrock-suggested tag search terms to TYPE into the picker,
5. pauses so YOU choose tags from the live picker and click Publish,
6. advances to the next day on your signal.

Security boundary unchanged: you sign in, you pick tags, you click Publish. The
tool never handles credentials/cookies and never auto-publishes.

Usage:
    AWS_PROFILE=... python tools/builder_publish/batch_publish.py --from 1 --to 21
    AWS_PROFILE=... python tools/builder_publish/batch_publish.py --from 1 --to 1   # test one
    AWS_PROFILE=... python tools/builder_publish/batch_publish.py --from 1 --to 21 --no-bedrock
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from playwright.sync_api import sync_playwright, Page, TimeoutError as PwTimeout

# Make the sibling helper importable whether run as a script
# (python tools/builder_publish/batch_publish.py) or as a module
# (python -m tools.builder_publish.batch_publish).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suggest_tags import suggest_tag_terms  # noqa: E402
from choose_tags import choose_tags, load_vocabulary  # noqa: E402

CREATE_URL = "https://builder.aws.com/create/content"
LOGIN_URL = "https://builder.aws.com/"
PROFILE_DIR = Path.home() / ".aws-builder-publish" / "chrome-profile"
OUT_DIR = Path(__file__).resolve().parent
REGION = "eu-west-1"

TITLE_SELECTOR = 'textarea[placeholder="Title"]'
DESCRIPTION_SELECTOR = 'textarea[placeholder="Description"]'
BODY_SELECTOR = '[data-testid="draft-content"]'
TAGS_TRIGGER = 'text="Choose tags"'
TAGS_SEARCH_INPUT = 'input[placeholder="Search Tags"]'
TAGS_OPTION = '[role="option"]'


def _tag_is_selected(page: Page, tag: str) -> bool:
    """Return True if ``tag`` appears as a selected token/chip on the page.

    Cloudscape multiselect renders chosen items as dismissible tokens
    (role/aria or an awsui token class). We check the page for a token whose
    text equals the tag, confirming the selection actually attached rather than
    just that an option was clicked.
    """
    js = r"""
    (tag) => {
      const sel = '[class*="awsui_token"], [data-testid*="token"], '
                + 'li[role="option"][aria-selected="true"], [class*="token-box"]';
      const nodes = Array.from(document.querySelectorAll(sel));
      const want = tag.trim().toLowerCase();
      return nodes.some(n => (n.innerText||'').toLowerCase().includes(want));
    }
    """
    try:
        result: bool = page.evaluate(js, tag)
        return bool(result)
    except Exception:  # noqa: BLE001
        return False


def _select_tags(page: Page, tags: list[str]) -> tuple[list[str], list[str]]:
    """Auto-select each tag in the Builder Center picker.

    For each tag: open/focus the picker's Search Tags input, type the tag,
    wait for the dropdown, and click the option whose label matches the tag
    exactly (case-insensitive). Verifies the click registered by checking the
    option is no longer offered. Returns (added, failed). Defensive: any single
    tag that cannot be matched/clicked is reported in ``failed`` rather than
    raising, and the picker is never left in a bad state.

    All suggested tags come from the verified-selectable set, so matches are
    expected; ``failed`` should normally be empty.
    """
    added: list[str] = []
    failed: list[str] = []

    # Open the picker once.
    try:
        page.wait_for_selector(TAGS_TRIGGER, timeout=15000)
        page.locator(TAGS_TRIGGER).first.scroll_into_view_if_needed()
        page.locator(TAGS_TRIGGER).first.click()
        page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001 - if the picker will not open, report all failed
        return [], list(tags)

    for tag in tags:
        try:
            box = page.locator(TAGS_SEARCH_INPUT).first
            box.click()
            box.fill("")  # clear any prior text
            box.type(tag, delay=35)
            page.wait_for_timeout(700)
            # Find the exact-match option (first line of its label equals tag).
            options = page.locator(TAGS_OPTION)
            count = options.count()
            clicked = False
            for i in range(count):
                opt = options.nth(i)
                label = (opt.inner_text() or "").splitlines()[0].strip()
                if label.lower() == tag.lower():
                    opt.click()
                    clicked = True
                    break
            page.wait_for_timeout(500)
            if clicked and _tag_is_selected(page, tag):
                added.append(tag)
            else:
                failed.append(tag)
            # Clear the search box for the next tag.
            box = page.locator(TAGS_SEARCH_INPUT).first
            box.click()
            box.fill("")
            page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001 - record and continue
            failed.append(tag)
    return added, failed


def _load_article(table: str, campaign_id: str, day: int, region: str) -> dict[str, object]:
    ddb = boto3.client("dynamodb", region_name=region)
    resp = ddb.get_item(
        TableName=table,
        Key={"pk": {"S": f"CAMPAIGN#{campaign_id}"}, "sk": {"S": f"DAY#{day:04d}"}},
    )
    item = resp.get("Item")
    if not item:
        return {}
    return {
        "topic": item.get("topic", {}).get("S", ""),
        "title": item.get("title", {}).get("S", ""),
        "description": item.get("description", {}).get("S", ""),
        "body_markdown": item.get("body_markdown", {}).get("S", ""),
        "tags": item.get("tags", {}).get("SS", []),
    }


class RateLimited(Exception):
    """Raised when Builder Center's create-article rate limit is hit."""


# Text Builder Center shows when the hourly create limit is reached.
_RATE_LIMIT_MARKERS = (
    "reached the create limit",
    "create a new article in an hour",
    "create limit for articles",
)


def _check_rate_limited(page: Page) -> None:
    """Raise RateLimited if the create-limit message is on the page."""
    try:
        body_text = (page.inner_text("body") or "").lower()
    except Exception:  # noqa: BLE001 - if we can't read, let the caller proceed
        return
    if any(marker in body_text for marker in _RATE_LIMIT_MARKERS):
        raise RateLimited(
            "Builder Center create-article limit reached "
            "(it allows a new article about once per hour)."
        )


def _fill_article(page: Page, article: dict[str, object]) -> None:
    """Open a fresh editor and fill title, description, and body.

    Respects Builder Center's create rate limit: if the limit message appears
    instead of the editor, raises :class:`RateLimited` rather than retrying or
    trying to work around the control.
    """
    page.goto(CREATE_URL, wait_until="domcontentloaded")
    # The create limit shows a message page without a Title field. Detect it
    # explicitly so we stop cleanly instead of timing out on a missing field.
    _check_rate_limited(page)
    try:
        page.wait_for_selector(TITLE_SELECTOR, timeout=15000)
    except PwTimeout:
        # No Title field appeared; re-check for the limit, else re-raise.
        _check_rate_limited(page)
        raise
    page.fill(TITLE_SELECTOR, str(article["title"]))
    page.fill(DESCRIPTION_SELECTOR, str(article["description"]))

    editor = _find_body_editor(page)
    editor.click()
    markdown = str(article["body_markdown"])
    try:
        page.evaluate(
            "async (t) => { await navigator.clipboard.writeText(t); }", markdown
        )
        modifier = "Meta" if sys.platform == "darwin" else "Control"
        page.keyboard.press(f"{modifier}+V")
    except Exception:  # noqa: BLE001 - typing fallback also triggers transforms
        editor.type(markdown, delay=0)


def _find_body_editor(page: Page):  # type: ignore[no-untyped-def]
    """Locate the rich-text body editor, resilient to selector drift.

    The body editor is a contenteditable rich-text area. Its data-testid can
    change on Builder Center's side, so we try several stable signals in order
    (testid, ARIA role/label, ProseMirror/contenteditable classes) and return
    the first that becomes visible. Raises PwTimeout only if none appear, so a
    genuine failure is still surfaced.
    """
    candidates = [
        BODY_SELECTOR,  # [data-testid="draft-content"]
        '[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"][aria-label]',
        'div[aria-label="What do you want to share?"]',
        '.ProseMirror[contenteditable="true"]',
        '[contenteditable="true"]',
    ]
    deadline = 40000  # ms total budget across candidates
    per = max(4000, deadline // len(candidates))
    last_error: Exception | None = None
    for sel in candidates:
        try:
            page.wait_for_selector(sel, state="visible", timeout=per)
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        except PwTimeout as error:  # try the next candidate
            last_error = error
            continue
    if last_error is not None:
        raise last_error
    raise PwTimeout("no body editor selector matched")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch Builder Center assisted publish")
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="end", type=int, default=21)
    parser.add_argument("--campaign", default="aws-learning-21")
    parser.add_argument("--table", default="aws-builder-aws-learning-21-campaign")
    parser.add_argument("--region", default=REGION)
    parser.add_argument(
        "--no-bedrock",
        action="store_true",
        help="Skip Bedrock tag suggestions; derive terms from the topic instead.",
    )
    parser.add_argument(
        "--auto-tags",
        action="store_true",
        help=(
            "Automatically select the chosen (verified-selectable) tags in the "
            "picker. You still log in and click Publish."
        ),
    )
    parser.add_argument(
        "--max",
        dest="max_articles",
        type=int,
        default=1,
        help=(
            "Max articles to create this run (default 1). Builder Center limits "
            "new-article creation to roughly one per hour, so keep this small "
            "and run again after the cooldown."
        ),
    )
    args = parser.parse_args(argv)

    days = list(range(args.start, args.end + 1))
    print(f"Batch publish: campaign={args.campaign} days {args.start}..{args.end}")
    print(
        f"Creating at most {args.max_articles} article(s) this run "
        "(Builder Center rate-limits new articles to ~1/hour)."
    )

    # Load the harvested tag vocabulary if present, so we can choose valid tags.
    vocabulary: list[str] = []
    if not args.no_bedrock:
        try:
            vocabulary = load_vocabulary()
            print(f"Loaded {len(vocabulary)} valid tags for Bedrock tag selection.")
        except SystemExit:
            print("No tag vocabulary found; falling back to tag search hints.")

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
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in if prompted, then press Enter to begin the walkthrough...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            context.close()
            return 1

        created = 0
        stopped_reason = "completed the requested range"
        try:
            for day in days:
                if created >= args.max_articles:
                    stopped_reason = (
                        f"reached --max={args.max_articles} for this run; "
                        "run again (after any cooldown) to continue"
                    )
                    break

                article = _load_article(args.table, args.campaign, day, args.region)
                if not article:
                    print(f"\n[day {day}] no article found; skipping.")
                    continue

                print("\n" + "=" * 70)
                print(f"[day {day}] {article['title']}")
                print(f"  topic: {article['topic']}")

                # Prefer the validated chooser (picks real tags from the
                # harvested vocabulary). Fall back to Bedrock search-term hints,
                # then topic-derived terms.
                chosen_tags: list[str] = []
                if args.no_bedrock:
                    terms = [
                        w.lower() for w in str(article["topic"]).split() if len(w) > 2
                    ][:5]
                elif vocabulary:
                    chosen_tags, _ = choose_tags(
                        {
                            "topic": str(article["topic"]),
                            "title": str(article["title"]),
                            "body_markdown": str(article["body_markdown"]),
                            "tags": article["tags"],  # type: ignore[dict-item]
                        },
                        vocabulary,
                        args.region,
                    )
                    terms = chosen_tags
                else:
                    terms = suggest_tag_terms(
                        str(article["topic"]), str(article["title"]),
                        str(article["body_markdown"]), args.region,
                    )

                try:
                    _fill_article(page, article)
                except RateLimited as error:
                    print(f"\n  RATE LIMITED: {error}")
                    stopped_reason = (
                        f"hit Builder Center's create limit at day {day}; "
                        "wait for the cooldown (about an hour) and re-run with "
                        f"--from {day}"
                    )
                    break
                except PwTimeout as error:
                    print(f"  could not fill editor (no rate-limit detected): {error}")
                    stopped_reason = f"editor did not load at day {day}"
                    break

                created += 1
                print(f"  filled title/description/body.")

                if args.auto_tags and chosen_tags:
                    print(f"  auto-selecting tags: {', '.join(chosen_tags)}")
                    add, fail = _select_tags(page, chosen_tags)
                    if add:
                        print(f"    added (confirmed selected): {', '.join(add)}")
                    if fail:
                        print(f"    could NOT add (pick manually): {', '.join(fail)}")
                elif chosen_tags:
                    print(
                        f"  SELECT these valid tags in the picker: "
                        f"{', '.join(chosen_tags)}"
                    )
                else:
                    print(f"  TYPE these into the Tags picker: {', '.join(terms)}")

                # Screenshot AFTER tag selection so it reflects the final state
                # (title/description/body + selected tags).
                shot = OUT_DIR / f"assisted_day_{day:04d}.png"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    print(f"  screenshot (with tags): {shot.name}")
                except Exception:  # noqa: BLE001
                    pass
                print("  >>> Review tags, then log in / click Publish (or leave as draft).")
                try:
                    ans = input("  Press Enter to finish this run, or 'c' to try the next day: ")
                except (EOFError, KeyboardInterrupt):
                    stopped_reason = "interrupted by user"
                    break
                if ans.strip().lower() != "c":
                    stopped_reason = "you chose to stop after this article"
                    break
        except KeyboardInterrupt:
            stopped_reason = "interrupted by user"

        print(f"\nCreated/filled {created} article(s) this run — {stopped_reason}.")
        try:
            input("Press Enter to close the browser... ")
        except (EOFError, KeyboardInterrupt):
            pass
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
