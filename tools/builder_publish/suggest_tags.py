#!/usr/bin/env python3
"""Bedrock-backed tag search-term suggester for Builder Center.

LOCAL developer helper. Given an article (its topic, title, and body), it asks
Amazon Bedrock (the same Nova Micro inference profile the engine uses) to
suggest short AWS-oriented search terms to TYPE INTO the Builder Center tags
picker. It does NOT auto-select tags: the picker uses a fixed slug vocabulary
we cannot fully enumerate, so the human still chooses from the live dropdown.
These suggestions just tell you what to type to surface the right options.

Output is a small list of lowercase, hyphenated terms (matching the picker's
slug style, e.g. "aws-amplify", "advanced-networking") plus plain service
words ("s3", "vpc") that the picker's search will match.

Usage (standalone test):
    AWS_PROFILE=... python tools/builder_publish/suggest_tags.py --day 1
    AWS_PROFILE=... python tools/builder_publish/suggest_tags.py --day 3

This reuses app.generation.generator.BedrockClient-compatible invocation.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import boto3

REGION = "eu-west-1"
MODEL_ID = "eu.amazon.nova-micro-v1:0"
MAX_TAG_SUGGESTIONS = 6


def _invoke_bedrock(prompt: str, region: str, model_id: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 200, "temperature": 0.2},
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


def _build_prompt(topic: str, title: str, body_excerpt: str) -> str:
    """Prompt Bedrock for picker search terms, constrained to the slug style."""
    return (
        "You help tag an AWS Builder Center article. The tags field is a picker "
        "with a fixed vocabulary of lowercase, hyphenated slugs (examples: "
        "aws-amplify, advanced-networking, aws-cdk, ai-agents, serverless, "
        "security). I will TYPE your suggestions into the picker's search box to "
        "find matching tags, so give concise, likely-to-exist terms.\n\n"
        "Return ONLY a JSON array of 3 to 6 short search terms (lowercase, "
        "hyphen-or-single-word, no sentences). Prefer concrete AWS service names "
        "and well-known topic slugs relevant to the article.\n\n"
        f"Article topic: {topic}\n"
        f"Title: {title}\n"
        f"Body excerpt: {body_excerpt[:600]}\n\n"
        'Example output: ["s3", "storage", "aws-backup", "security"]\n'
        "Your JSON array:"
    )


def _parse_terms(raw: str) -> list[str]:
    """Extract a JSON array of terms from the model text, tolerantly."""
    text = raw.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            arr = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            arr = []
    else:
        arr = []
    terms: list[str] = []
    for item in arr:
        if isinstance(item, str) and item.strip():
            terms.append(item.strip().lower())
    # De-dup, cap.
    seen: dict[str, None] = {}
    for t in terms:
        seen.setdefault(t, None)
    return list(seen.keys())[:MAX_TAG_SUGGESTIONS]


def suggest_tag_terms(
    topic: str,
    title: str,
    body_markdown: str,
    region: str = REGION,
    model_id: str = MODEL_ID,
) -> list[str]:
    """Return suggested picker search terms for an article (Bedrock-backed).

    Falls back to a deterministic term derived from the topic if the model
    response cannot be parsed, so the caller always gets something usable.
    """
    prompt = _build_prompt(topic, title, body_markdown)
    try:
        raw = _invoke_bedrock(prompt, region, model_id)
        terms = _parse_terms(raw)
    except Exception:  # noqa: BLE001 - fall back below
        terms = []
    if not terms:
        # Deterministic fallback: a couple of words from the topic.
        words = [w.lower() for w in topic.split() if len(w) > 2][:3]
        terms = words or ["aws"]
    return terms


def _load_article(table: str, campaign_id: str, day: int, region: str) -> dict[str, Any]:
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
        "tags": item.get("tags", {}).get("SS", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bedrock tag search-term suggester")
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--campaign", default="aws-learning-21")
    parser.add_argument("--table", default="aws-builder-aws-learning-21-campaign")
    parser.add_argument("--region", default=REGION)
    parser.add_argument("--model", default=MODEL_ID)
    args = parser.parse_args(argv)

    article = _load_article(args.table, args.campaign, args.day, args.region)
    if not article["title"]:
        raise SystemExit(f"No article for day {args.day}")

    print(f"\nDay {args.day}: {article['title']}")
    print(f"  topic: {article['topic']}")
    print(f"  generated tags (freeform): {', '.join(article['tags'])}")
    terms = suggest_tag_terms(
        article["topic"], article["title"], article["body_markdown"],
        args.region, args.model,
    )
    print(f"\n  Bedrock-suggested picker search terms: {', '.join(terms)}")
    print("  (Type these into the Builder Center tags picker to find matches.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
