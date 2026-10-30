# Builder Center assisted-publish tooling (local only)

Local developer tools for working with AWS Builder Center. **Not part of the
deployed engine.** These respect Builder Center's signed-in workflow: *you* log
in manually; the tooling never stores, reads, or transmits your password, MFA,
or session cookies.

## Security boundary

- You sign in yourself in a real browser window.
- The browser profile persists locally under `~/.aws-builder-publish/` so you
  don't re-login every run. That directory is **never committed or uploaded**
  (see `.gitignore`).
- No credential storage, no cookie harvesting, no headless auth bypass. The
  engine's `BuilderCenterPublisher` remains intentionally unimplemented; this
  tooling assists a human-driven publish rather than automating credentials.

## Prerequisites

```bash
source .venv/bin/activate
pip install playwright
python -m playwright install chromium   # bundled Chromium (isolated profile)
```

Uses Playwright's bundled Chromium with its own isolated profile — it does not
reuse or conflict with your everyday Google Chrome.

## Step 1 — Inspect the create-article page

```bash
python tools/builder_publish/inspect_builder_center.py
```

1. A Chromium window opens at https://builder.aws.com/.
2. Sign in with your AWS Builder ID (password + MFA).
3. Navigate to the page where you create/write a new article.
4. Return to the terminal and press Enter.

The tool writes `inspection_report.json` (form fields, editors, buttons,
iframes) and `inspection_page.png`. We use that report to design the assisted
form-fill around the real DOM — selectors, whether the body is a rich-text
editor or textarea, whether anything sits in an iframe, etc.

## Step 2 — Assisted publish

```bash
AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/assisted_publish.py --day 1
```

For the chosen campaign day the tool:

1. Reads the article (title, description, body Markdown, tags) from DynamoDB.
2. Opens Builder Center using your persisted login (sign in first if prompted).
3. Navigates to the create-article editor and fills **Title** and
   **Description**, then inserts the **Body** by pasting the Markdown into the
   rich-text editor (which converts Markdown on paste).
4. Screenshots the filled editor (`assisted_day_NNNN.png`) and **stops**.

You then:

- verify the body rendered (headings, code blocks, lists);
- choose **Tags** from Builder Center's picker (fixed vocabulary — the tool
  prints the generated suggestions to guide you);
- click **Publish** yourself.

The real field selectors (from the inspection):

| Field | Selector |
| --- | --- |
| Title | `textarea[placeholder="Title"]` (0/255 chars) |
| Description | `textarea[placeholder="Description"]` (0/512 chars) |
| Body | `[data-testid="draft-content"]` (contenteditable rich text) |
| Tags | "Choose tags" picker, 5 max (manual) |
| Publish | button labelled "Publish" (manual) |

Options: `--campaign <id>` (default `aws-learning-21`), `--table <name>`,
`--region <region>` (default `eu-west-1`).

## Step 3 — Batch walkthrough (all days)

Walks a range of days one at a time: fills each article, prints
Bedrock-suggested tag search terms, pauses for you to pick tags + Publish, then
advances.

```bash
# test one day first
AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/batch_publish.py --from 1 --to 1

# then the whole campaign
AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/batch_publish.py --from 1 --to 21
```

For each day it fills Title/Description/Body, screenshots, and prints tag
search terms to TYPE into the picker. You choose tags from the live picker and
click Publish, then press Enter to advance (or `q` to stop). Add `--no-bedrock`
to derive search terms from the topic instead of calling Bedrock.

### Tags

The picker uses a fixed slug vocabulary (e.g. `aws-amplify`,
`advanced-networking`) that cannot be fully enumerated from the UI, so tags are
chosen by you. `suggest_tags.py` uses Bedrock to propose search terms to type
into the picker; the picker's live search then shows the real, selectable tags.

```bash
AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/suggest_tags.py --day 3
```

`capture_tags.py` captures a sample of the picker's vocabulary to
`tags_vocabulary.json` (partial — the picker windows results).
