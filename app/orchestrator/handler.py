"""AWS Lambda entry point wiring the engine from environment configuration.

This module is the single deployable entry point referenced by the Terraform
Lambda resource (``app.orchestrator.handler.lambda_handler``). It reads
configuration from environment variables (never secrets in cleartext; the
session secret is referenced by ARN only), constructs the dependency-injected
components once per container, and routes each invocation to either the
scheduled daily execution or a Command_API request. There are no orphaned code
paths: both entry points flow through the same Orchestrator and components with
a per-execution Correlation_ID (Requirements 3, 4, 5, 7, 10, 11, 17.1).

Two invocation shapes are handled:

* Scheduler event (``{"source": "eventbridge-scheduler", ...}``): evaluate the
  campaign state and, when it is READY or RUNNING, run the next eligible day
  (Requirements 11.1 to 11.3).
* Command_API event (a Function URL request or a direct command object):
  dispatch through the authenticated, replay-protected :class:`CommandAPI`
  (Requirements 11.4 to 11.9, 13, 14).

The Bedrock client is a thin boundary wrapper over boto3 ``bedrock-runtime``
InvokeModel for the configured model; all other AWS clients (DynamoDB, SNS) are
boto3 clients injected into the typed components.

Design reference: design.md "Architecture", "Components and Interfaces";
Requirements 11, 13, 14, 17.1, 19.2.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, cast

from app.campaign.manager import CampaignManager
from app.campaign.store import CampaignStore
from app.common.clock import SystemClock
from app.common.context import ExecutionContext
from app.common.errors import ConfigurationError
from app.common.metrics import EMFMetricsEmitter
from app.generation.generator import ContentGenerator
from app.notify.notifier import Notifier
from app.orchestrator.command_api import (
    CommandAPI,
    CommandRequest,
    InMemoryNonceStore,
)
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.schedule import evaluate_trigger
from app.publishing.selection import select_publisher
from app.validation.validator import ContentValidator

__all__ = ["EngineConfig", "load_config", "BedrockRuntimeClient", "lambda_handler"]


@dataclass(frozen=True)
class EngineConfig:
    """Deployment configuration read from environment variables.

    Only non-secret values are read directly. ``session_secret_arn`` is a
    reference to Secrets Manager; the secret value is never read into config
    (Requirements 8.1, 8.3, 19.2).
    """

    campaign_id: str
    table_name: str
    sns_topic_arn: str
    bedrock_model_id: str
    timezone: str
    total_days: int
    publisher_selection: str
    session_secret_arn: str
    notification_phone: str
    article_bucket: str
    article_base_url: str


def load_config(env: dict[str, str] | None = None) -> EngineConfig:
    """Build :class:`EngineConfig` from the environment, naming what is missing.

    Required keys must be present and non-empty; a missing required key raises
    :class:`ConfigurationError` naming every missing value (Requirement 19.4).
    """
    source = env if env is not None else dict(os.environ)
    required = ("TABLE_NAME", "SNS_TOPIC_ARN", "BEDROCK_MODEL_ID")
    missing = [key for key in required if not source.get(key, "").strip()]
    if missing:
        raise ConfigurationError(
            message=f"missing required configuration: {', '.join(missing)}",
            fields=tuple(missing),
        )
    return EngineConfig(
        campaign_id=source.get("CAMPAIGN_ID", ""),
        table_name=source["TABLE_NAME"],
        sns_topic_arn=source["SNS_TOPIC_ARN"],
        bedrock_model_id=source["BEDROCK_MODEL_ID"],
        timezone=source.get("TIMEZONE", "Africa/Nairobi"),
        total_days=int(source.get("TOTAL_DAYS", "21")),
        publisher_selection=source.get("PUBLISHER_SELECTION", "mock"),
        session_secret_arn=source.get("SESSION_SECRET_ARN", ""),
        notification_phone=source.get("NOTIFICATION_PHONE", ""),
        article_bucket=source.get("ARTICLE_BUCKET", ""),
        article_base_url=source.get("ARTICLE_BASE_URL", ""),
    )


class BedrockRuntimeClient:
    """Thin boundary wrapper over boto3 bedrock-runtime InvokeModel.

    Satisfies the generator's ``BedrockClient`` protocol (``invoke(prompt,
    max_tokens) -> str``). Uses the Amazon Bedrock Converse-style Messages API
    body for the configured model and extracts the generated text. Kept minimal
    and isolated so the generator stays testable with a fake.
    """

    def __init__(self, client: Any, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    def invoke(self, prompt: str, max_tokens: int) -> str:
        """Invoke the model and return the raw generated text."""
        body = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.2},
        }
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        raw = response["body"].read()
        payload = json.loads(raw)
        # Nova/Converse-style response: output.message.content[].text
        output = payload.get("output", {}).get("message", {}).get("content", [])
        for part in output:
            if isinstance(part, dict) and "text" in part:
                return str(part["text"])
        # Fallback for models that return a top-level text field.
        return str(payload.get("outputText", ""))


def _build_orchestrator(
    config: EngineConfig,
) -> tuple[Orchestrator, CampaignManager, CommandAPI, CampaignStore]:
    """Construct the fully wired components for one container lifecycle."""
    import boto3  # imported lazily so unit tests need no AWS SDK at import time

    clock = SystemClock()
    dynamodb = boto3.client("dynamodb")
    sns = boto3.client("sns")
    bedrock = boto3.client("bedrock-runtime")

    # The concrete boto3 clients satisfy the component protocols at runtime;
    # boto3-stubs declares stricter TypedDict kwargs, so cast at the boundary.
    from app.campaign.store import DynamoDBClient
    from app.notify.notifier import SNSClient
    from app.publishing.s3_publisher import S3Client
    from app.publishing.selection import PublisherConfig

    store = CampaignStore(cast(DynamoDBClient, dynamodb), config.table_name)
    manager = CampaignManager(store, clock)
    generator = ContentGenerator(
        BedrockRuntimeClient(bedrock, config.bedrock_model_id)
    )
    validator = ContentValidator()
    # Build the S3 publisher's dependencies only when selected, so the mock
    # path needs no S3 client. The bucket/base URL come from env config.
    publisher_config = PublisherConfig(
        s3_client=cast(S3Client, boto3.client("s3"))
        if config.publisher_selection.strip().casefold() == "s3"
        else None,
        s3_bucket=config.article_bucket,
        s3_base_url=config.article_base_url,
    )
    publisher = select_publisher(config.publisher_selection, publisher_config)
    notifier = Notifier(cast(SNSClient, sns), clock)
    context = ExecutionContext.start()
    metrics = EMFMetricsEmitter(context, clock)

    orchestrator = Orchestrator(
        store=store,
        manager=manager,
        generator=generator,
        validator=validator,
        publisher=publisher,
        notifier=notifier,
        clock=clock,
        metrics=metrics,
        topic_arn=config.sns_topic_arn,
    )
    command_api = CommandAPI(manager, orchestrator, clock, InMemoryNonceStore())
    return orchestrator, manager, command_api, store


def _is_scheduler_event(event: dict[str, Any]) -> bool:
    """Return True when the event was delivered by EventBridge Scheduler."""
    return event.get("source") == "eventbridge-scheduler"


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Route a Lambda invocation to scheduled execution or a command.

    A Correlation_ID is established for the execution so all logs and metrics
    correlate (Requirement 17.1). Scheduler events run the next eligible day
    when the campaign is READY or RUNNING; everything else is treated as a
    Command_API request.
    """
    config = load_config()
    with ExecutionContext.start():
        orchestrator, manager, command_api, store = _build_orchestrator(config)

        if _is_scheduler_event(event):
            campaign_id = event.get("campaign_id") or config.campaign_id
            campaign = store.get_campaign(campaign_id)  # read-only load
            if campaign is None:
                return {"statusCode": 404, "body": "campaign not found"}
            decision = evaluate_trigger(campaign.campaign_state)
            if not decision.should_execute:
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {"skipped": True, "reason": decision.skipped_reason}
                    ),
                }
            result = orchestrator.execute(campaign_id)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"outcome": result.outcome.value, "day": result.day}
                ),
            }

        # Command_API request.
        request = _parse_command(event)
        response = command_api.handle(request)
        return {
            "statusCode": response.status_code,
            "body": json.dumps(dict(response.body)),
        }


def _parse_command(event: dict[str, Any]) -> CommandRequest:
    """Normalize a Function URL / direct event into a CommandRequest.

    Authentication is derived from the presence of an IAM request-context
    identity (the Function URL is configured with ``AuthType = AWS_IAM``, so
    AWS enforces SigV4 before the handler runs; the presence of the caller
    identity confirms an authenticated principal).
    """
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {}) if isinstance(
        request_context, dict
    ) else {}
    iam = authorizer.get("iam", {}) if isinstance(authorizer, dict) else {}
    authenticated = bool(iam.get("userId") or event.get("authenticated"))

    # Body may be a JSON string (Function URL) or an already-parsed object.
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            body = {}
    if not isinstance(body, dict):
        body = {}

    return CommandRequest(
        action=str(body.get("action", "")),
        authenticated=authenticated,
        campaign_id=str(body.get("campaign_id", "")),
        nonce=str(body.get("nonce", "")),
        timestamp=str(body.get("timestamp", "")),
        payload=body.get("payload", {}) if isinstance(body.get("payload"), dict) else {},
    )
