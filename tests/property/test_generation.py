"""Property tests for Bedrock generation parsing and request building.

Feature: aws-builder-automation, Property 8
Feature: aws-builder-automation, Property 9

Validates: Requirements 4.2, 4.3, 4.4, 4.6.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.common.errors import GenerationError
from app.generation.generator import ContentGenerator
from app.generation.models import (
    DESCRIPTION_MAX,
    DIFFICULTY,
    TAGS_MAX,
    TITLE_MAX,
    VOICE,
    GenerationSpec,
)
from tests.conftest import FakeBedrockClient, ImmediateTimeoutRunner

pytestmark = pytest.mark.property

_PROMPT = "PROMPT title<={title_max} desc<={description_max} tags {tags_min}..{tags_max}"


def _generator(response: str) -> tuple[ContentGenerator, FakeBedrockClient]:
    client = FakeBedrockClient(response)
    gen = ContentGenerator(
        client=client,
        timeout_runner=ImmediateTimeoutRunner(),
        prompt_template=_PROMPT,
    )
    return gen, client


def _spec(**overrides: object) -> GenerationSpec:
    base: dict[str, object] = {"campaign_id": "c1", "day": 1, "topic": "IAM"}
    base.update(overrides)
    return GenerationSpec(**base)  # type: ignore[arg-type]


# -- Property 8: bounded article or generation failure -----------------

_valid_title = st.text(min_size=1, max_size=TITLE_MAX).filter(lambda s: s.strip())
_valid_desc = st.text(min_size=1, max_size=DESCRIPTION_MAX).filter(lambda s: s.strip())
_valid_body = st.text(min_size=1, max_size=400).filter(lambda s: s.strip())
_valid_tags = st.lists(
    st.text(min_size=1, max_size=20).filter(lambda s: s.strip()),
    min_size=1,
    max_size=TAGS_MAX,
)


@settings(max_examples=200)
@given(
    title=_valid_title,
    description=_valid_desc,
    body=_valid_body,
    tags=_valid_tags,
    takeaway=_valid_body,
)
def test_property_8_valid_response_parses_within_bounds(
    title: str, description: str, body: str, tags: list[str], takeaway: str
) -> None:
    """A well-formed response yields an Article respecting every bound."""
    response = json.dumps(
        {
            "title": title,
            "description": description,
            "body_markdown": body,
            "tags": tags,
            "learning_takeaway": takeaway,
        }
    )
    gen, _ = _generator(response)
    article = gen.generate(_spec())
    assert 1 <= len(article.title) <= TITLE_MAX
    assert 1 <= len(article.description) <= DESCRIPTION_MAX
    assert article.body_markdown.strip()
    assert 1 <= len(article.tags) <= TAGS_MAX
    assert article.learning_takeaway.strip()


@settings(max_examples=200)
@given(raw=st.text(max_size=200))
def test_property_8_arbitrary_text_never_produces_bad_article(raw: str) -> None:
    """Arbitrary model text yields either a bounded Article or a failure."""
    gen, _ = _generator(raw)
    try:
        article = gen.generate(_spec())
    except GenerationError:
        return  # classified as generation failure; no article produced
    assert 1 <= len(article.title) <= TITLE_MAX
    assert 1 <= len(article.description) <= DESCRIPTION_MAX
    assert 1 <= len(article.tags) <= TAGS_MAX


@settings(max_examples=100)
@given(
    title=_valid_title,
    description=_valid_desc,
    body=_valid_body,
    tags=_valid_tags,
    takeaway=_valid_body,
)
def test_property_8_roundtrip_preserves_fields(
    title: str, description: str, body: str, tags: list[str], takeaway: str
) -> None:
    """Serializing a valid article then parsing preserves all fields."""
    response = json.dumps(
        {
            "title": title,
            "description": description,
            "body_markdown": body,
            "tags": tags,
            "learning_takeaway": takeaway,
        }
    )
    gen, _ = _generator(response)
    article = gen.generate(_spec(campaign_id="camp", day=7, topic="S3"))
    assert article.campaign_id == "camp"
    assert article.day == 7
    assert article.topic == "S3"
    assert article.title == title
    assert article.description == description
    assert article.body_markdown == body
    assert article.tags == tuple(tags)
    assert article.learning_takeaway == takeaway


# -- Property 9: request carries the required bounded specification ----


# Identifiers/topics drawn from printable characters that survive JSON encoding
# verbatim (no control chars, quotes, or backslashes to escape). This is the
# realistic domain for campaign IDs and topic strings.
_plain_text = st.text(
    alphabet=st.characters(
        min_codepoint=32,
        max_codepoint=126,
        blacklist_characters='"\\',
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip())


@settings(max_examples=100)
@given(
    campaign_id=_plain_text,
    day=st.integers(min_value=1, max_value=365),
    topic=_plain_text,
)
def test_property_9_request_carries_required_spec(
    campaign_id: str, day: int, topic: str
) -> None:
    """The prompt sent to Bedrock includes every required field."""
    valid = json.dumps(
        {
            "title": "T",
            "description": "D",
            "body_markdown": "B",
            "tags": ["aws"],
            "learning_takeaway": "L",
        }
    )
    gen, client = _generator(valid)
    gen.generate(_spec(campaign_id=campaign_id, day=day, topic=topic))
    prompt = client.last_prompt or ""
    assert campaign_id in prompt
    assert f'"day": {day}' in prompt
    assert topic in prompt
    assert DIFFICULTY in prompt
    assert VOICE in prompt
    assert str(TITLE_MAX) in prompt
    assert str(DESCRIPTION_MAX) in prompt
    assert str(TAGS_MAX) in prompt
    assert '"executed_in_aws": false' in prompt
