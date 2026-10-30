#!/usr/bin/env python3
"""Choose valid Builder Center tags for an article using Bedrock.

Takes an article and the harvested tag vocabulary (tags_vocabulary.json, ~2672
real tags) and returns up to 5 tags SELECTED VERBATIM from that list. Because
the picker only accepts tags that exist, and the vocabulary is mixed-format
(slugs, CamelCase, spaces, acronyms), the model must pick exact strings - it
may not normalize or invent. Two safeguards enforce this:

1. Pre-filter: the full vocabulary is narrowed to a relevant candidate shortlist
   by keyword overlap with the article, so the prompt stays small/cheap and the
   model chooses from plausible options.
2. Post-validate: every tag the model returns is checked against the real
   vocabulary (exact, then case-insensitive) and anything not present is
   dropped. The result is guaranteed to contain only valid, selectable tags.

Usage:
    AWS_PROFILE=... python tools/builder_publish/choose_tags.py --day 1
    AWS_PROFILE=... python tools/builder_publish/choose_tags.py --day 3 --show-candidates
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import boto3

REGION = "eu-west-1"
MODEL_ID = "eu.amazon.nova-micro-v1:0"
OUT_DIR = Path(__file__).resolve().parent
# Prefer the SELECTABLE vocabulary (read from the picker's rendered options, so
# every tag is genuinely pickable). Fall back to the broad API-harvested list
# only if the selectable one is missing.
SELECTABLE_PATH = OUT_DIR / "tags_selectable.json"
VOCAB_PATH = OUT_DIR / "tags_vocabulary.json"
MAX_TAGS = 5
CANDIDATE_LIMIT = 80  # shortlist size sent to the model


def load_vocabulary() -> list[str]:
    """Load the selectable tag vocabulary.

    Uses tags_selectable.json (harvested from the picker's rendered options, so
    every entry is genuinely selectable) when present; otherwise falls back to
    the broad tags_vocabulary.json (API-harvested, may contain non-selectable
    tags like 'new-to-aws').
    """
    if SELECTABLE_PATH.exists():
        return json.loads(SELECTABLE_PATH.read_text(encoding="utf-8"))
    if VOCAB_PATH.exists():
        return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    raise SystemExit(
        f"No tag vocabulary found. Run harvest_selectable_tags.py to build "
        f"{SELECTABLE_PATH.name}."
    )


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens of length >= 2 from text."""
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 2}


# Broad, high-value tags confirmed present in the selectable set, used to pad
# the shortlist for abstract topics so the model has good general options.
# (Plain 'aws'/'cloud' are NOT selectable in the picker, so they are excluded.)
_BROAD_FALLBACK_TAGS = (
    "serverless", "security", "architecture", "networking", "devops",
    "storage", "compute", "cost-optimization", "ai",
)


def shortlist_candidates(
    vocabulary: list[str], article: dict[str, str], limit: int = CANDIDATE_LIMIT
) -> list[str]:
    """Narrow the vocabulary to tags relevant to the article.

    Scores each tag by (a) whole-token overlap with the article and (b) partial
    containment (so "network" matches "advanced-networking", "iam" matches
    "aws-identity-and-access-management"). Broad, high-value tags known to be
    selectable are always appended so abstract topics still get good general
    options. This keeps the Bedrock prompt focused while ensuring enough
    relevant candidates are present to choose a full set of 4-5.
    """
    article_text = " ".join(
        [
            article.get("topic", ""),
            article.get("title", ""),
            " ".join(article.get("tags", [])),  # type: ignore[arg-type]
            article.get("body_markdown", "")[:600],
        ]
    ).lower()
    article_tokens = {t for t in re.split(r"[^a-z0-9]+", article_text) if len(t) >= 3}

    scored: list[tuple[int, str]] = []
    for tag in vocabulary:
        tag_tokens = _tokens(tag)
        if not tag_tokens:
            continue
        # Whole-token overlap.
        overlap = len(tag_tokens & article_tokens)
        # Partial containment: any article token that appears inside a tag
        # token, or any tag token that appears in the article text.
        partial = 0
        for tt in tag_tokens:
            if len(tt) >= 4 and tt in article_text:
                partial += 1
            else:
                for at in article_tokens:
                    if len(at) >= 4 and (at in tt or tt in at):
                        partial += 1
                        break
        score = overlap * 2 + partial
        if score:
            scored.append((score, tag))
    # Prefer higher score, then shorter/cleaner tags.
    scored.sort(key=lambda s: (-s[0], len(s[1])))
    candidates = [tag for _, tag in scored[:limit]]

    # Always append broad, selectable fallbacks (de-duped, order preserved).
    for base in _BROAD_FALLBACK_TAGS:
        if base in vocabulary and base not in candidates:
            candidates.append(base)
    return candidates


def _invoke_bedrock(prompt: str, region: str, model_id: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 150, "temperature": 0.1},
    }
    resp = client.invoke_model(
        modelId=model_id,
        body=json.dumps(body).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    parts = payload.get("output", {}).get("message", {}).get("content", [])
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _build_prompt(article: dict[str, str], candidates: list[str]) -> str:
    return (
        "Choose the best tags for this AWS article. You MUST pick ONLY from the "
        "provided ALLOWED TAGS list, copying each chosen tag EXACTLY as written "
        "(same spelling, case, spaces, and punctuation). Do not invent, "
        "normalize, or modify tags. Choose 4 to 5 tags: include the specific "
        "AWS services the article covers AND one or two broader topic tags "
        "(for example security, architecture, serverless) so the article is "
        "well categorized. Prefer a full set of 5 when good options exist.\n\n"
        f"Article title: {article.get('title','')}\n"
        f"Article topic: {article.get('topic','')}\n"
        f"Body excerpt: {article.get('body_markdown','')[:400]}\n\n"
        "ALLOWED TAGS (choose only from these, verbatim):\n"
        + "\n".join(f"- {t}" for t in candidates)
        + "\n\nReturn ONLY a JSON array of the chosen tags, e.g. "
        '["amazon-s3","security"]. Each must appear in ALLOWED TAGS exactly.'
    )


def _parse_array(raw: str) -> list[str]:
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [str(x) for x in arr if isinstance(x, str)]


def choose_tags(
    article: dict[str, str],
    vocabulary: list[str],
    region: str = REGION,
    model_id: str = MODEL_ID,
) -> tuple[list[str], list[str]]:
    """Return (chosen_valid_tags, candidate_shortlist).

    The chosen tags are validated against the vocabulary (exact, then
    case-insensitive) so only real, selectable tags are returned. Falls back to
    the top candidates if the model returns nothing usable.
    """
    candidates = shortlist_candidates(vocabulary, article)
    if not candidates:
        return [], []

    prompt = _build_prompt(article, candidates)
    try:
        raw = _invoke_bedrock(prompt, region, model_id)
        picked = _parse_array(raw)
    except Exception:  # noqa: BLE001 - fall back to top candidates
        picked = []

    # Validate against the real vocabulary.
    vocab_exact = set(vocabulary)
    vocab_lower = {t.lower(): t for t in vocabulary}
    valid: list[str] = []
    for tag in picked:
        if tag in vocab_exact:
            valid.append(tag)
        elif tag.lower() in vocab_lower:
            valid.append(vocab_lower[tag.lower()])  # correct to canonical form
    # De-dup preserving order; cap at MAX_TAGS.
    seen: dict[str, None] = {}
    for t in valid:
        seen.setdefault(t, None)
    chosen = list(seen.keys())[:MAX_TAGS]

    if not chosen:
        chosen = candidates[:MAX_TAGS]  # deterministic fallback, still valid
    return chosen, candidates


def _load_article(table: str, campaign_id: str, day: int, region: str) -> dict[str, str]:
    ddb = boto3.client("dynamodb", region_name=region)
    resp = ddb.get_item(
        TableName=table,
        Key={"pk": {"S": f"CAMPAIGN#{campaign_id}"}, "sk": {"S": f"DAY#{day:04d}"}},
    )
    item = resp.get("Item") or {}
    return {
        "topic": item.get("topic", {}).get("S", ""),
        "title": item.get("title", {}).get("S", ""),
        "body_markdown": item.get("body_markdown", {}).get("S", ""),
        "tags": item.get("tags", {}).get("SS", []),  # type: ignore[dict-item]
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bedrock tag chooser (valid tags only)")
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--campaign", default="aws-learning-21")
    parser.add_argument("--table", default="aws-builder-aws-learning-21-campaign")
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--show-candidates", action="store_true")
    args = parser.parse_args(argv)

    vocabulary = load_vocabulary()
    article = _load_article(args.table, args.campaign, args.day, args.region)
    if not article["title"]:
        raise SystemExit(f"No article for day {args.day}")

    chosen, candidates = choose_tags(article, vocabulary, args.region)
    print(f"\nDay {args.day}: {article['title']}")
    print(f"  topic: {article['topic']}")
    print(f"  vocabulary size: {len(vocabulary)}")
    if args.show_candidates:
        print(f"  candidate shortlist ({len(candidates)}): {', '.join(candidates)}")
    print(f"\n  CHOSEN TAGS (valid, pick these in the picker): {', '.join(chosen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
