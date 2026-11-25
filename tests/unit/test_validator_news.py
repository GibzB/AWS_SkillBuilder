"""Validator tests for the news campaign: source rules and the CLI disclaimer path."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.generation.models import (
    Article,
    NewsContext,
    RoundupContext,
    RoundupDaily,
    RoundupRegional,
)
from app.validation.validator import ContentValidator

pytestmark = pytest.mark.unit

LINK = "https://aws.amazon.com/about-aws/whats-new/2026/11/lambda-thing/"
SOURCE_TEXT = (
    "Starting today Lambda event source mappings for Amazon SQS accept much larger "
    "batches so that high volume queues need far fewer invocations overall."
)
NEWS = NewsContext(title="AWS Lambda adds a thing", link=LINK, description_text=SOURCE_TEXT)
DISCLAIMER = (
    "> **Validate before you run:** confirm these commands and any prerequisites "
    "against the current AWS documentation, because this feature is new."
)
GOOD_CMD = "aws lambda list-functions"
NEW_CMD = "aws lambda frobnicate-widgets --widget-id w1"
MARKER = "![Architecture diagram](diagram.png)"
SPEC = '{"nodes": [{"id": "fn", "service": "lambda", "label": "Handler"}], "edges": []}'
LAST = f"Read the announcement: [AWS Lambda adds a thing]({LINK})"


def _body(cmd: str | None = GOOD_CMD, disclaimer: bool = False, last: str = LAST, extra: str = "") -> str:
    parts = ["Lambda gets a handy change, and I wrote down why it matters to me.", extra]
    if cmd is not None:
        parts.append("## Try it yourself")
        if disclaimer:
            parts.append(DISCLAIMER)
        parts.append(f"```bash\n{cmd}\n```")
    parts.append(last)
    return "\n\n".join(p for p in parts if p)


def _article(
    body: str,
    title: str = "Lambda change",
    description: str = "A short note on Lambda.",
    diagram: bool = True,
) -> Article:
    return Article(
        campaign_id="aws-news-30",
        day=1,
        topic=NEWS.title,
        title=title,
        description=description,
        body_markdown=f"{MARKER}\n\n{body}" if diagram else body,
        tags=("lambda",),
        learning_takeaway="I learned something.",
        diagram_json=SPEC if diagram else "",
    )


def _codes(article: Article, **kwargs: object) -> set[str]:
    result = ContentValidator(require_cli_example=True).validate(
        article, NEWS.title, None, source=NEWS, **kwargs  # type: ignore[arg-type]
    )
    return {v.code for v in result.violations}


def test_clean_news_article_passes() -> None:
    assert _codes(_article(_body())) == set()


def test_unknown_operation_without_disclaimer_fails() -> None:
    assert "CLI_UNVERIFIED_NO_DISCLAIMER" in _codes(_article(_body(NEW_CMD)))


def test_unknown_operation_with_disclaimer_passes() -> None:
    assert _codes(_article(_body(NEW_CMD, disclaimer=True))) == set()


def test_unknown_option_with_disclaimer_passes_without_it_fails() -> None:
    cmd = "aws lambda list-functions --brand-new-flag yes"
    assert "CLI_UNVERIFIED_NO_DISCLAIMER" in _codes(_article(_body(cmd)))
    assert _codes(_article(_body(cmd, disclaimer=True))) == set()


def test_missing_required_option_still_fails_with_disclaimer() -> None:
    codes = _codes(_article(_body("aws lambda get-function", disclaimer=True)))
    assert "CLI_COMMAND_INVALID" in codes


def test_no_cli_article_passes_only_with_disclaimer() -> None:
    assert "CLI_EXAMPLE_MISSING" in _codes(_article(_body(cmd=None)))
    with_line = _body(cmd=None, extra=DISCLAIMER)
    assert _codes(_article(with_line)) == set()


def test_disclaimer_must_start_the_line() -> None:
    inline = _body(NEW_CMD, extra="Note: " + DISCLAIMER)
    assert "CLI_UNVERIFIED_NO_DISCLAIMER" in _codes(_article(inline))


def test_link_must_be_the_last_line() -> None:
    body = _body() + "\n\nOne more thought."
    assert "SOURCE_LINK_MISSING" in _codes(_article(body))


def test_link_must_equal_the_source_link() -> None:
    wrong = "Read the announcement: [x](https://aws.amazon.com/other/)"
    assert "SOURCE_LINK_MISSING" in _codes(_article(_body(last=wrong)))


def test_twelve_word_copy_is_caught() -> None:
    copied = "Lambda event source mappings for Amazon SQS accept much larger batches so that high volume"
    assert "COPIED_FROM_SOURCE" in _codes(_article(_body(extra=copied)))


def test_eleven_word_overlap_passes() -> None:
    eleven = "Lambda event source mappings for Amazon SQS accept much larger batches"
    assert len(eleven.split()) == 11
    assert "COPIED_FROM_SOURCE" not in _codes(_article(_body(extra=eleven)))


def test_copy_check_ignores_case_and_punctuation() -> None:
    copied = "LAMBDA event-source mappings, for Amazon SQS: accept much larger batches; so that high volume!"
    assert "COPIED_FROM_SOURCE" in _codes(_article(_body(extra=copied)))


@pytest.mark.parametrize("where", ["title", "description", "body"])
def test_em_dash_is_caught_everywhere(where: str) -> None:
    dash = "\u2014"
    article = _article(_body(extra="A line" + dash + "with a dash." if where == "body" else ""))
    if where == "title":
        article = replace(article, title="Lambda" + dash + "change")
    if where == "description":
        article = replace(article, description="Short" + dash + "note")
    assert "EM_DASH" in _codes(article)


def test_non_news_validation_is_unchanged() -> None:
    body = "Lambda gets a handy change.\n\n```bash\naws lambda frobnicate-widgets --widget-id w1\n```\n"
    validator = ContentValidator(require_cli_example=True)
    result = validator.validate(_article(body), NEWS.title, None)
    codes = {v.code for v in result.violations}
    assert "CLI_COMMAND_INVALID" in codes
    assert "CLI_UNVERIFIED_NO_DISCLAIMER" not in codes
    assert "SOURCE_LINK_MISSING" not in codes


# --------------------------- roundup ---------------------------

ROUNDUP = RoundupContext(
    week=1,
    week_start="2026-11-16",
    week_end="2026-11-22",
    dailies=(
        RoundupDaily("A", "https://cdn.example/day/0001.html", "https://aws.amazon.com/a/", "lambda"),
        RoundupDaily("B", "https://cdn.example/day/0002.html", "https://aws.amazon.com/b/", "s3"),
    ),
    regional=(RoundupRegional("S3 in Cape Town", "https://aws.amazon.com/r1/"),),
)


def _roundup_article(body: str, diagram: bool = True) -> Article:
    return Article(
        campaign_id="aws-news-30",
        day=1,
        topic="AWS news roundup week 1",
        title="AWS news roundup: week 1",
        description="This week in AWS news.",
        body_markdown=f"{MARKER}\n\n{body}" if diagram else body,
        tags=("aws",),
        learning_takeaway="A busy week.",
        kind="roundup",
        diagram_json=SPEC if diagram else "",
    )


def _roundup_codes(body: str, roundup: RoundupContext = ROUNDUP) -> set[str]:
    result = ContentValidator(require_cli_example=True).validate(
        _roundup_article(body), "AWS news roundup week 1", None, roundup=roundup
    )
    return {v.code for v in result.violations}


_GOOD_ROUNDUP = (
    "This week in AWS news I covered two items.\n\n"
    "[A](https://cdn.example/day/0001.html) explained one change.\n\n"
    "[B](https://cdn.example/day/0002.html) explained another.\n\n"
    "## Regional availability\n\n- [S3 in Cape Town](https://aws.amazon.com/r1/)\n\n"
    "Next week I will keep reading."
)


def test_good_roundup_passes_without_a_cli_example() -> None:
    assert _roundup_codes(_GOOD_ROUNDUP) == set()


def test_roundup_missing_a_daily_link_fails() -> None:
    body = _GOOD_ROUNDUP.replace("https://cdn.example/day/0002.html", "https://elsewhere/")
    assert "ROUNDUP_LINK_MISSING" in _roundup_codes(body)


def test_roundup_accepts_the_announcement_link_instead_of_the_article_url() -> None:
    body = _GOOD_ROUNDUP.replace("https://cdn.example/day/0002.html", "https://aws.amazon.com/b/")
    assert "ROUNDUP_LINK_MISSING" not in _roundup_codes(body)


def test_roundup_regional_section_may_only_name_provided_items() -> None:
    body = _GOOD_ROUNDUP.replace("https://aws.amazon.com/r1/", "https://aws.amazon.com/invented/")
    assert "ROUNDUP_REGIONAL_UNKNOWN" in _roundup_codes(body)


def test_roundup_regional_section_with_no_provided_items() -> None:
    assert "ROUNDUP_REGIONAL_UNKNOWN" in _roundup_codes(_GOOD_ROUNDUP, replace(ROUNDUP, regional=()))


def test_roundup_em_dash_fails() -> None:
    assert "EM_DASH" in _roundup_codes(_GOOD_ROUNDUP + "\n\nA\u2014B")


def test_news_article_without_a_diagram_is_flagged_as_soft() -> None:
    from app.validation.validator import DIAGRAM_MISSING, without_soft_violations

    result = ContentValidator(require_cli_example=True).validate(
        _article(_body(), diagram=False), NEWS.title, None, source=NEWS
    )
    assert {v.code for v in result.violations} == {DIAGRAM_MISSING}
    assert without_soft_violations(result).is_valid


def test_news_article_with_an_invalid_spec_is_flagged() -> None:
    article = replace(_article(_body()), diagram_json="not json")
    assert "DIAGRAM_MISSING" in _codes(article)


def test_news_article_with_two_markers_is_flagged() -> None:
    article = _article(f"{MARKER}\n\n{_body()}")
    assert "DIAGRAM_MISSING" in _codes(article)


def test_roundup_without_a_diagram_is_flagged() -> None:
    result = ContentValidator(require_cli_example=True).validate(
        _roundup_article(_GOOD_ROUNDUP, diagram=False),
        "AWS news roundup week 1", None, roundup=ROUNDUP,
    )
    assert {v.code for v in result.violations} == {"DIAGRAM_MISSING"}


def test_hard_violations_survive_without_soft_violations() -> None:
    from app.validation.validator import without_soft_violations

    result = ContentValidator(require_cli_example=True).validate(
        _article(_body(NEW_CMD), diagram=False), NEWS.title, None, source=NEWS
    )
    kept = without_soft_violations(result)
    assert not kept.is_valid
    assert {v.code for v in kept.violations} == {"CLI_UNVERIFIED_NO_DISCLAIMER"}
