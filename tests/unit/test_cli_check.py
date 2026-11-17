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


# -- examples must be reproducible from the article alone ---------------------

from app.validation.cli_check import find_unshown_files, terraform_config_missing  # noqa: E402

_CFN_CMD = (
    "```bash\naws cloudformation create-stack --stack-name demo "
    "--template-body file://s3-bucket-template.json\n```\n"
)


def test_file_reference_without_contents_is_flagged() -> None:
    assert find_unshown_files("Intro.\n\n" + _CFN_CMD) == ["s3-bucket-template.json"]


def test_file_reference_with_contents_shown_is_accepted() -> None:
    md = (
        "Save this as `s3-bucket-template.json`:\n\n```json\n"
        '{"Resources": {"B": {"Type": "AWS::S3::Bucket"}}}\n```\n\n' + _CFN_CMD
    )
    assert find_unshown_files(md) == []


def test_binary_fileb_reference_is_not_checked() -> None:
    md = "```bash\naws lambda update-function-code --function-name f --zip-file fileb://f.zip\n```\n"
    assert find_unshown_files(md) == []


def test_terraform_commands_without_config_are_flagged() -> None:
    bare = "```bash\nterraform init\nterraform apply -auto-approve\n```\n"
    assert terraform_config_missing(bare)
    shown = '```hcl\nresource "aws_s3_bucket" "b" {}\n```\n' + bare
    assert not terraform_config_missing(shown)


def test_validator_reports_unshown_file_and_missing_terraform_config() -> None:
    body = "# Amazon S3\n\nAbout Amazon S3.\n\n" + _CFN_CMD + "\n```bash\nterraform apply\n```\n"
    codes = _codes(_article(body), require=True)
    assert {"REFERENCED_FILE_MISSING", "TERRAFORM_CONFIG_MISSING"} <= codes


# -- options added by the AWS CLI itself (not in the API model) ---------------


@pytest.mark.parametrize(
    "command",
    [
        "aws lambda create-function --function-name f --runtime python3.12 "
        "--role arn:aws:iam::123456789012:role/r --handler app.handler --zip-file fileb://f.zip",
        "aws ec2 run-instances --image-id ami-0abcdef1234567890 --count 1 --instance-type t3.micro",
        "aws ec2 authorize-security-group-ingress --group-id sg-0123456789abcdef0 "
        "--protocol tcp --port 22 --cidr 10.0.0.0/16",
        "aws eks update-kubeconfig --name demo",
        "aws ecr get-login-password --region eu-west-1",
        "aws configure list",
        "aws deploy list-applications",
    ],
)
def test_cli_customizations_are_accepted(command: str) -> None:
    assert check_command(command) is None


def test_customization_does_not_excuse_other_missing_options() -> None:
    reason = check_command("aws lambda create-function --function-name f --zip-file fileb://f.zip") or ""
    assert "--role" in reason and "--code" not in reason


def test_customization_flags_do_not_leak_to_other_commands() -> None:
    assert "--zip-file" in (check_command("aws lambda list-functions --zip-file x") or "")


# -- commands a beginner cannot run as written --------------------------------

from app.validation.cli_check import find_unreproducible_commands  # noqa: E402


def test_dummy_account_arn_and_binary_package_are_flagged() -> None:
    md = (
        "```bash\n"
        "aws lambda create-function --function-name f --runtime python3.12 "
        "--role arn:aws:iam::123456789012:role/r --handler h --zip-file fileb://f.zip\n"
        "aws sns publish --topic-arn arn:aws:sns:eu-west-1:123456789012:t --message hi\n"
        "```\n"
    )
    assert len(find_unreproducible_commands(md)) == 2


def test_read_only_commands_are_not_flagged() -> None:
    md = "```bash\naws lambda list-functions\naws sts get-caller-identity\n```\n"
    assert find_unreproducible_commands(md) == []


def test_validator_reports_not_reproducible() -> None:
    body = (
        "# Amazon S3\n\nAbout Amazon S3.\n\n```bash\n"
        "aws iam get-role --role-name x --cli-input-json arn:aws:iam::123456789012:role/r\n```\n"
    )
    assert "CLI_NOT_REPRODUCIBLE" in _codes(_article(body), require=True)


# -- chaining commands without placeholders -----------------------------------


def test_captured_variable_commands_are_extracted_and_checked() -> None:
    md = (
        "```bash\n"
        "QUEUE_URL=$(aws sqs create-queue --queue-name learning-queue --query QueueUrl --output text)\n"
        'aws sqs send-message --queue-url "$QUEUE_URL" --message-body hello\n'
        'aws sqs delete-queue --queue-url "$QUEUE_URL"\n'
        "```\n"
    )
    commands = extract_aws_commands(md)
    assert len(commands) == 3
    assert all(check_command(c) is None for c in commands)
    assert find_unreproducible_commands(md) == []


def test_dummy_account_id_in_queue_url_is_not_reproducible() -> None:
    md = (
        "```bash\naws sqs send-message --queue-url "
        "https://sqs.us-east-1.amazonaws.com/123456789012/q --message-body hi\n```\n"
    )
    assert len(find_unreproducible_commands(md)) == 1


def test_placeholder_violation_quotes_the_offending_text() -> None:
    body = "# Amazon S3\n\nAbout Amazon S3.\n\n```bash\naws s3api delete-object --bucket b --key <object-key>\n```\n"
    result = ContentValidator(require_cli_example=True).validate(
        _article(body), "Amazon S3", DayState.READY
    )
    message = next(v.message for v in result.violations if v.code == "PLACEHOLDER_PRESENT")
    assert "<object-key>" in message
