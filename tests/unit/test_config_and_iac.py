"""Unit tests for configuration validation and IaC safety assertions.

Feature: aws-builder-automation

Validates: Requirements 8.1, 18.1, 18.2, 18.3, 19.2, 19.3, 19.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.common.errors import ConfigurationError
from app.orchestrator.handler import load_config

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INFRA = _REPO_ROOT / "infra"
_CONFIG = _REPO_ROOT / "config"


# -- Configuration loader (Requirement 19.4) ---------------------------


def test_missing_required_names_every_missing_field() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load_config({})
    for field in ("TABLE_NAME", "SNS_TOPIC_ARN", "BEDROCK_MODEL_ID"):
        assert field in exc.value.fields


def test_blank_required_is_rejected() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load_config(
            {"TABLE_NAME": "   ", "SNS_TOPIC_ARN": "arn", "BEDROCK_MODEL_ID": "m"}
        )
    assert "TABLE_NAME" in exc.value.fields


def test_defaults_applied_when_optional_absent() -> None:
    config = load_config(
        {"TABLE_NAME": "t", "SNS_TOPIC_ARN": "arn", "BEDROCK_MODEL_ID": "m"}
    )
    assert config.timezone == "Africa/Nairobi"  # (1.2)
    assert config.total_days == 21
    assert config.publisher_selection == "mock"  # MVP default under budget
    assert config.session_secret_arn == ""


# -- IaC safety assertions (Requirements 18.1, 18.2, 18.3, 8.1, 19.3) --


def _infra_text() -> str:
    return "\n".join(
        p.read_text() for p in _INFRA.glob("*.tf")
    )


def test_dynamodb_is_on_demand() -> None:
    text = _infra_text()
    assert "PAY_PER_REQUEST" in text  # on-demand, no always-on capacity (18.2)
    assert "provisioned_throughput" not in text


def test_dynamodb_key_schema_matches_store_casing() -> None:
    """The table key schema must match the store's lowercase pk/sk marshalling.

    DynamoDB attribute names are case-sensitive; a mismatch (e.g. PK/SK in IaC
    vs pk/sk in code) causes a runtime ValidationException on every GetItem.
    This regression guard keeps the IaC and the store in agreement.
    """
    text = _infra_text()
    assert 'hash_key  = "pk"' in text or 'hash_key = "pk"' in text
    assert 'range_key = "sk"' in text
    # The store marshals to lowercase pk/sk.
    store = (_REPO_ROOT / "app" / "campaign" / "store.py").read_text()
    assert '"pk": {"S":' in store
    assert '"sk": {"S":' in store


def test_no_always_on_compute() -> None:
    text = _infra_text().lower()
    # Event-triggered only: no ECS services, autoscaling, or EC2 instances.
    for banned in ("aws_ecs_service", "aws_autoscaling_group", "aws_instance"):
        assert banned not in text  # (18.1, 18.3)


def test_function_url_requires_iam_auth() -> None:
    text = _infra_text()
    assert 'authorization_type = "AWS_IAM"' in text  # no unauthenticated RUN


def test_example_config_contains_no_secret_values() -> None:
    example = (_CONFIG / "example.tfvars").read_text()
    # Only an ARN placeholder is referenced; no password/cookie/token values.
    for banned in ("password", "cookie", "access_token", "BEGIN PRIVATE KEY"):
        assert banned not in example.lower() if banned.islower() else banned not in example
    assert "REPLACE_WITH_SECRET_ARN" in example  # placeholder only (19.3)


def test_iam_scoped_per_service() -> None:
    text = _infra_text()
    # Separate least-privilege policies exist per service (8.7).
    for policy in ("dynamodb", "bedrock", "sns", "logs"):
        assert f'aws_iam_role_policy" "{policy}"' in text


def test_articles_bucket_is_private_behind_cloudfront_oac() -> None:
    """The S3 articles bucket must be private, served only via CloudFront OAC."""
    text = _infra_text()
    # Public access fully blocked.
    assert 'aws_s3_bucket_public_access_block" "articles"' in text
    assert "block_public_acls       = true" in text
    assert "restrict_public_buckets = true" in text
    # Served via CloudFront using Origin Access Control (not legacy OAI).
    assert 'aws_cloudfront_origin_access_control" "articles"' in text
    assert "redirect-to-https" in text  # HTTPS enforced
    # Bucket policy grants read only to the CloudFront service, scoped by
    # the distribution SourceArn condition.
    assert "AllowCloudFrontOACRead" in text
    assert "AWS:SourceArn" in text
    # Lambda's S3 write is scoped to PutObject on the articles bucket only.
    assert "ArticlesPutObject" in text
    assert '"s3:PutObject"' in text
