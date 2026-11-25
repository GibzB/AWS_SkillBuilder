"""Generator tests for the news and roundup prompts and the diagram section."""

from __future__ import annotations

import json

import pytest

from app.diagrams.spec import ICON_CATALOG
from app.generation.generator import ContentGenerator, load_prompt_template
from app.generation.models import (
    GenerationSpec,
    NewsContext,
    RoundupContext,
    RoundupDaily,
    RoundupRegional,
)
from tests.conftest import FakeBedrockClient, ImmediateTimeoutRunner

pytestmark = pytest.mark.unit

_NEWS = NewsContext(
    title="AWS Lambda adds a thing",
    link="https://aws.amazon.com/about-aws/whats-new/2026/11/lambda-thing/",
    description_text="Lambda now does the thing for everyone.",
    published_iso="2026-11-24T10:00:00Z",
    service="lambda",
    related_fundamental="lambda",
)

_HEADER = (
    '{"title": "Lambda thing", "description": "Short.", "tags": ["lambda"], '
    '"learning_takeaway": "I learned it."}'
)
_DIAGRAM = '{"nodes": [{"id": "fn", "service": "lambda", "label": "Handler"}], "edges": []}'


def _gen(response: str) -> tuple[ContentGenerator, FakeBedrockClient]:
    client = FakeBedrockClient(response)
    return ContentGenerator(client=client, timeout_runner=ImmediateTimeoutRunner()), client


def _news_spec() -> GenerationSpec:
    return GenerationSpec(campaign_id="aws-news-30", day=3, topic=_NEWS.title, news=_NEWS)


def test_news_prompt_used_only_for_news_specs() -> None:
    gen, _ = _gen("")
    news_prompt = gen.build_prompt(_news_spec())
    plain_prompt = gen.build_prompt(GenerationSpec(campaign_id="c", day=1, topic="IAM"))
    assert "Prompt version: news_v1" in news_prompt
    assert "Prompt version: article_v2" in plain_prompt
    assert "news_v1" not in plain_prompt


def test_news_fields_and_icon_keys_appear_in_the_prompt() -> None:
    gen, _ = _gen("")
    prompt = gen.build_prompt(_news_spec())
    task = json.loads(prompt.split("# Task input")[1].split("```json")[1].split("```")[0])
    assert task["news"]["link"] == _NEWS.link
    assert task["news"]["description_text"] == _NEWS.description_text
    assert "{diagram_services}" not in prompt
    assert all(key in prompt for key in ("lambda", "step_functions", "on_prem"))
    assert "{title_max}" not in prompt and "{seo_title_max}" not in prompt
    assert len(ICON_CATALOG) > 40


def test_existing_prompt_is_unchanged_for_non_news_specs() -> None:
    gen, _ = _gen("")
    spec = GenerationSpec(campaign_id="c", day=2, topic="Amazon S3")
    template = load_prompt_template()
    for token, value in {
        "{title_max}": "255", "{description_max}": "512", "{seo_title_max}": "60",
        "{seo_description_max}": "160", "{tags_min}": "1", "{tags_max}": "5",
    }.items():
        template = template.replace(token, value)
    task = {
        "campaign_id": "c", "day": 2, "topic": "Amazon S3",
        "difficulty": "beginner_to_lower_intermediate", "voice": "first_person",
        "executed_in_aws": False,
        "article_requirements": {
            "title_max_chars": 255, "description_max_chars": 512, "max_tags": 5,
        },
    }
    expected = f"{template}\n\n# Task input\n\n```json\n{json.dumps(task)}\n```\n"
    assert gen.build_prompt(spec) == expected


def test_diagram_section_is_split_from_the_body() -> None:
    body = "Intro.\n\n![Architecture diagram](diagram.png)\n\nRead the announcement: [t](https://x/)"
    gen, _ = _gen(f"{_HEADER}\n---BODY---\n{body}\n---DIAGRAM---\n{_DIAGRAM}\n")
    article = gen.generate(_news_spec())
    assert article.body_markdown == body
    assert article.diagram_json == _DIAGRAM
    assert article.kind == "daily"


def test_missing_diagram_section_gives_empty_diagram_json() -> None:
    gen, _ = _gen(f"{_HEADER}\n---BODY---\nJust a body.")
    article = gen.generate(_news_spec())
    assert article.diagram_json == ""
    assert article.body_markdown == "Just a body."


def test_legacy_single_json_still_parses_without_diagram() -> None:
    legacy = json.dumps(
        {"title": "T", "description": "D", "tags": ["a"], "learning_takeaway": "L",
         "body_markdown": "# Body"}
    )
    gen, _ = _gen(legacy)
    article = gen.generate(GenerationSpec(campaign_id="c", day=1, topic="x"))
    assert article.diagram_json == "" and article.body_markdown == "# Body"


def test_revise_keeps_the_diagram_section_in_the_echo() -> None:
    gen, client = _gen(f"{_HEADER}\n---BODY---\nBody.\n---DIAGRAM---\n{_DIAGRAM}")
    draft = gen.generate(_news_spec())
    gen.revise(_news_spec(), draft, ["EM_DASH problem"])
    assert client.last_prompt is not None
    assert f"---DIAGRAM---\n{_DIAGRAM}\n=== PREVIOUS DRAFT END ===" in client.last_prompt


def test_revise_echo_unchanged_without_diagram() -> None:
    gen, client = _gen(f"{_HEADER}\n---BODY---\nBody.")
    spec = GenerationSpec(campaign_id="c", day=1, topic="x")
    draft = gen.generate(spec)
    gen.revise(spec, draft, ["a problem"])
    assert client.last_prompt is not None
    assert "Body.\n=== PREVIOUS DRAFT END ===" in client.last_prompt
    assert "DIAGRAM" not in client.last_prompt.split("# Revision required")[1]


def test_roundup_spec_uses_roundup_prompt_and_marks_article_kind() -> None:
    roundup = RoundupContext(
        week=2,
        week_start="2026-11-16",
        week_end="2026-11-22",
        dailies=(RoundupDaily("A", "https://cdn/day/0001.html", "https://aws/a/", "lambda"),),
        regional=(RoundupRegional("S3 in Cape Town", "https://aws/r/"),),
    )
    spec = GenerationSpec(campaign_id="c", day=2, topic="AWS news roundup week 2", roundup=roundup)
    gen, _ = _gen(f"{_HEADER}\n---BODY---\nWeek summary.")
    prompt = gen.build_prompt(spec)
    assert "Prompt version: roundup_v1" in prompt
    task = json.loads(prompt.split("# Task input")[1].split("```json")[1].split("```")[0])
    assert task["roundup"]["dailies"][0]["url"] == "https://cdn/day/0001.html"
    assert task["roundup"]["regional"][0]["link"] == "https://aws/r/"
    assert gen.generate(spec).kind == "roundup"


def test_prompt_assets_contain_no_em_dash_or_unfilled_tokens() -> None:
    gen, _ = _gen("")
    roundup = RoundupContext(1, "a", "b", ())
    for spec in (
        _news_spec(),
        GenerationSpec(campaign_id="c", day=1, topic="t", roundup=roundup),
    ):
        prompt = gen.build_prompt(spec)
        assert "\u2014" not in prompt
        for token in ("{title_max}", "{description_max}", "{tags_max}", "{tags_min}"):
            assert token not in prompt
