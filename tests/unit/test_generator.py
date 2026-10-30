"""Unit tests for ContentGenerator edges and the versioned prompt asset.

Feature: aws-builder-automation

Validates: Requirements 4.5, 4.6, 4.7, 20.1, 20.2.
"""

from __future__ import annotations

import json

import pytest

from app.common.errors import GenerationError
from app.generation.generator import (
    MAX_OUTPUT_TOKENS,
    ContentGenerator,
    load_prompt_template,
)
from app.generation.models import GenerationSpec
from tests.conftest import (
    AlwaysTimeoutRunner,
    FakeBedrockClient,
    ImmediateTimeoutRunner,
)

pytestmark = pytest.mark.unit

_PROMPT = "title<={title_max} desc<={description_max} tags {tags_min}..{tags_max}"


def _spec() -> GenerationSpec:
    return GenerationSpec(campaign_id="c1", day=1, topic="IAM")


def _valid_response() -> str:
    return json.dumps(
        {
            "title": "Learning IAM",
            "description": "A short note on least privilege.",
            "body_markdown": "# IAM\n\nOne thing I learned...",
            "tags": ["aws", "iam"],
            "learning_takeaway": "Separate identity from permission.",
        }
    )


def test_timeout_is_classified_as_generation_failure() -> None:
    gen = ContentGenerator(
        client=FakeBedrockClient(_valid_response()),
        timeout_runner=AlwaysTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError) as exc:
        gen.generate(_spec())
    assert exc.value.timed_out is True
    assert exc.value.reason == "timeout"  # (4.7)


def test_bedrock_exception_is_generation_failure() -> None:
    class Boom:
        def invoke(self, prompt: str, max_tokens: int) -> str:
            raise RuntimeError("bedrock down")

    gen = ContentGenerator(
        client=Boom(),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError) as exc:
        gen.generate(_spec())
    assert exc.value.reason == "unavailable"  # (4.6)


def test_malformed_json_is_generation_failure() -> None:
    gen = ContentGenerator(
        client=FakeBedrockClient("not json at all"),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError) as exc:
        gen.generate(_spec())
    assert exc.value.reason == "parse_error"  # (4.6)


def test_too_many_tags_is_generation_failure() -> None:
    response = json.dumps(
        {
            "title": "T",
            "description": "D",
            "body_markdown": "B",
            "tags": ["a", "b", "c", "d", "e", "f"],  # 6 > 5
            "learning_takeaway": "L",
        }
    )
    gen = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError) as exc:
        gen.generate(_spec())
    assert exc.value.reason == "out_of_bounds"  # (4.6)


def test_fenced_json_response_is_tolerated() -> None:
    fenced = "```json\n" + _valid_response() + "\n```"
    gen = ContentGenerator(
        client=FakeBedrockClient(fenced),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    article = gen.generate(_spec())
    assert article.title == "Learning IAM"


@pytest.mark.parametrize(
    "wrapper",
    [
        # leading prose before clean JSON
        "Here is the article you requested:\n\n{json}",
        # leading prose before a fenced block
        "Sure! Here is the JSON:\n```json\n{json}\n```",
        # trailing prose after the JSON object
        "{json}\n\nI hope this helps with your learning journey!",
        # fenced with surrounding prose on both sides
        "Certainly:\n```\n{json}\n```\nLet me know if you need changes.",
        # no fence, just the object with trailing newline noise
        "{json}\n\n\n",
    ],
)
def test_json_recovered_from_surrounding_prose(wrapper: str) -> None:
    """Intermittent prod failures: model wraps/pads the JSON. Recover it.

    Regression for "Bedrock response was not valid JSON" on days where the
    model added a sentence before/after the JSON or placed the fence mid-text.
    """
    response = wrapper.replace("{json}", _valid_response())
    gen = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    article = gen.generate(_spec())
    assert article.title == "Learning IAM"
    assert article.tags == ("aws", "iam")


def test_json_with_inner_fenced_diagram_in_body() -> None:
    """Root cause of intermittent prod failures: body_markdown contains its own
    fenced code block, so the response has multiple ``` sequences. The parser
    must brace-match the outer object and treat inner ``` as string content.
    """
    body_with_diagram = (
        "# Amazon VPC\\n\\nHere is the layout:\\n\\n"
        "```text\\nVPC\\n |\\n +-- Subnet\\n```\\n\\nThat is the idea."
    )
    response = (
        "```json\n{\n"
        '  "title": "Learning IAM",\n'
        '  "description": "A short note.",\n'
        f'  "body_markdown": "{body_with_diagram}",\n'
        '  "tags": ["aws", "iam"],\n'
        '  "learning_takeaway": "Keep it simple."\n'
        "}\n```"
    )
    assert response.count("```") == 4  # outer fence + inner diagram fence
    gen = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    article = gen.generate(_spec())
    assert article.title == "Learning IAM"
    assert "```text" in article.body_markdown  # inner diagram preserved


def test_two_part_format_with_embedded_code_in_body() -> None:
    """Primary fix: two-part output (JSON header + ---BODY--- + raw Markdown).

    The body contains a CloudFormation JSON snippet with unescaped quotes - the
    exact content that broke JSON-embedded parsing in production. Because the
    body is outside the JSON, it parses cleanly.
    """
    response = (
        '{"title": "Learning IAM", "description": "A short note.", '
        '"tags": ["aws", "iam"], "learning_takeaway": "Keep it simple."}\n'
        "---BODY---\n"
        "# IAM\n\nHere is a CloudFormation example:\n\n"
        '```json\n{\n  "Resources": {\n    "Bucket": {"Type": "AWS::S3::Bucket"}\n  }\n}\n```\n\n'
        "That is the idea."
    )
    gen = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    article = gen.generate(_spec())
    assert article.title == "Learning IAM"
    assert article.tags == ("aws", "iam")
    # Raw body preserved verbatim, including the quote-heavy embedded JSON.
    assert '"Resources"' in article.body_markdown
    assert '"Type": "AWS::S3::Bucket"' in article.body_markdown


def test_two_part_empty_body_is_generation_failure() -> None:
    """A delimiter with no body after it is an empty-body generation failure."""
    response = (
        '{"title": "T", "description": "D", "tags": ["aws"], '
        '"learning_takeaway": "L"}\n---BODY---\n   '
    )
    gen = ContentGenerator(
        client=FakeBedrockClient(response),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError):
        gen.generate(_spec())


def test_truly_non_json_still_fails() -> None:
    """Genuinely non-JSON prose (no object) is still a generation failure."""
    gen = ContentGenerator(
        client=FakeBedrockClient("I'm sorry, I cannot help with that request."),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    with pytest.raises(GenerationError) as exc:
        gen.generate(_spec())
    assert exc.value.reason == "parse_error"


def test_bounded_max_tokens_is_passed_to_client() -> None:
    client = FakeBedrockClient(_valid_response())
    gen = ContentGenerator(
        client=client,
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    gen.generate(_spec())
    assert client.last_max_tokens == MAX_OUTPUT_TOKENS


def test_build_prompt_with_real_asset_handles_literal_json_braces() -> None:
    """Regression: the real prompt asset has literal JSON braces.

    build_prompt must substitute only the named placeholders and leave literal
    { } in the asset intact, rather than treating them as format fields (which
    raised KeyError in a live run).
    """
    gen = ContentGenerator(
        client=FakeBedrockClient(_valid_response()),
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=load_prompt_template(),  # the REAL packaged asset
    )
    prompt = gen.build_prompt(_spec())
    # Placeholders resolved, literal JSON contract braces preserved.
    assert "{title_max}" not in prompt
    assert "255" in prompt  # title_max substituted
    assert '"title"' in prompt  # literal JSON from the asset survived
    # And a full generate() call works end to end with the real asset.
    article = gen.generate(_spec())
    assert article.title == "Learning IAM"


def test_prompt_asset_contains_anti_fabrication_and_voice_rules() -> None:
    template = load_prompt_template()
    lowered = template.lower()
    assert "first person" in lowered  # (20.2)
    assert "certification" in lowered  # anti-fabrication (4.5)
    assert "mermaid" in lowered  # portable-diagram guidance (20.1)
    # Contains the format placeholders the generator fills.
    assert "{title_max}" in template
    assert "{description_max}" in template
    assert "{tags_max}" in template
