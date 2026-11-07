"""Tests for offline AWS CLI command verification and its validator gate."""

from __future__ import annotations

import pytest

from app.campaign.models import DayState
from app.generation.models import Article
from app.orchestrator.handler import BedrockRuntimeClient
from app.validation.cli_check import (
    check_article_commands,
    check_command,
    extract_aws_commands,
)
from app.validation.validator import ContentValidator

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "command",
    [
        "aws apigateway get-rest-apis",
        "aws apigateway get-rest-api --rest-api-id abc123",
        "aws s3 ls s3://my-learning-bucket",
        "aws s3api list-buckets --query 'Buckets[].Name' --output text",
        "aws lambda invoke --function-name hello out.json",
        "aws ec2 describe-security-groups --group-ids sg-0123456789abcdef0",
        "aws dynamodb describe-table --table-name Orders --region eu-west-1",
        "aws events list-rules",
    ],
)
def test_valid_commands_pass(command: str) -> None:
    assert check_command(command) is None


def test_unknown_service_rejected() -> None:
    assert "unknown service" in (check_command("aws notaservice list-things") or "")


def test_invented_operation_rejected() -> None:
    reason = check_command("aws apigateway list-everything") or ""
    assert "no command 'list-everything'" in reason


def test_invented_flag_rejected() -> None:
    reason = check_command("aws apigateway get-rest-apis --fancy-mode") or ""
    assert "--fancy-mode" in reason


def test_missing_required_option_rejected() -> None:
    reason = check_command("aws apigateway get-rest-api") or ""
    assert "--rest-api-id" in reason


def test_extract_handles_prompt_and_line_continuations() -> None:
    md = (
        "Intro\n\n```bash\n$ aws s3 ls\naws dynamodb describe-table \\\n"
        "  --table-name Orders\n# a comment\n```\n\n```json\n{\"aws\": 1}\n```\n"
    )
    got = extract_aws_commands(md)
    assert [c.split() for c in got] == [
        ["aws", "s3", "ls"],
        ["aws", "dynamodb", "describe-table", "--table-name", "Orders"],
    ]


def test_non_shell_blocks_ignored() -> None:
    md = "```yaml\naws s3 ls\n```\n"
    assert extract_aws_commands(md) == []


def test_check_article_commands_counts_and_reports() -> None:
    md = "```bash\naws s3 ls\naws s3api bogus-op\n```\n"
    count, issues = check_article_commands(md)
    assert count == 2
    assert len(issues) == 1 and "bogus-op" in issues[0].command


def _article(body: str) -> Article:
    return Article(
        campaign_id="c",
        day=1,
        topic="Amazon S3",
        title="Amazon S3 basics",
        description="A short intro to Amazon S3.",
        body_markdown=body,
        tags=("s3", "storage"),
        learning_takeaway="I learned S3 stores objects.",
        executed_in_aws=False,
    )


def _codes(article: Article, require: bool) -> set[str]:
    result = ContentValidator(require_cli_example=require).validate(
        article, "Amazon S3", DayState.READY
    )
    return {v.code for v in result.violations}


def test_validator_requires_cli_example_when_enabled() -> None:
    article = _article("# Amazon S3\n\nS3 stores objects in buckets.\n")
    assert "CLI_EXAMPLE_MISSING" in _codes(article, require=True)
    assert "CLI_EXAMPLE_MISSING" not in _codes(article, require=False)


def test_validator_flags_invalid_command() -> None:
    article = _article("# Amazon S3\n\nStuff about Amazon S3.\n\n```bash\naws s3api nope\n```\n")
    assert "CLI_COMMAND_INVALID" in _codes(article, require=True)


def test_validator_accepts_valid_command() -> None:
    article = _article(
        "# Amazon S3\n\nStuff about Amazon S3.\n\n```bash\naws s3 ls s3://my-learning-bucket\n```\n"
    )
    assert _codes(article, require=True) == set()


class _FakeRuntime:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def converse(self, **kwargs: object) -> dict[str, object]:
        self.kwargs = kwargs
        return {
            "output": {
                "message": {"content": [{"text": "part one "}, {"text": "part two"}]}
            }
        }


def test_bedrock_client_uses_converse_and_joins_text_blocks() -> None:
    runtime = _FakeRuntime()
    out = BedrockRuntimeClient(runtime, "eu.anthropic.claude-sonnet-4-6").invoke("hi", 500)
    assert out == "part one part two"
    assert runtime.kwargs["modelId"] == "eu.anthropic.claude-sonnet-4-6"
    assert runtime.kwargs["inferenceConfig"] == {"maxTokens": 500, "temperature": 0.2}
