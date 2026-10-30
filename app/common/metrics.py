"""Named CloudWatch metrics emitted via the embedded metric format (EMF).

The engine emits the named metrics required by Requirement 17.3 -
``ArticlesGenerated``, ``ArticlesPublished``, ``GenerationFailures``,
``PublicationFailures``, ``AuthenticationFailures``, ``DuplicateRunsPrevented``,
and ``CampaignsCompleted`` - each a non-negative integer count carrying the
execution's Correlation_ID (Requirements 17.1, 17.3).

Metrics are published as EMF: a structured JSON log line that CloudWatch parses
into metrics automatically, so no separate ``PutMetricData`` call (and no extra
IAM permission or synchronous AWS dependency) is required on the hot path. The
sink is injected, so tests capture the rendered EMF payloads and production
points the sink at stdout, which Lambda forwards to CloudWatch Logs.

Every metric payload is passed through redaction before emission, so a
correlation-carried dimension can never leak a secret (Requirements 8.1, 17.4,
17.5). The metric names are defined once as an enum to prevent typos and to
keep the emitted names stable across the codebase.

An optional real-CloudWatch path is provided via :class:`CloudWatchMetricsSink`,
which wraps an injected boto3 CloudWatch client as a boundary; it is fully
optional and the default EMF path needs no AWS at all, keeping the emitter
testable without real AWS.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Callable, Final, Protocol

from app.common.clock import Clock
from app.common.context import CORRELATION_ID_FIELD, ExecutionContext
from app.common.redaction import redact

__all__ = [
    "MetricName",
    "MetricSink",
    "MetricsEmitter",
    "EMFMetricsEmitter",
    "CloudWatchMetricsSink",
]

# The EMF metric namespace under which all engine metrics are grouped.
METRIC_NAMESPACE: Final[str] = "AwsBuilderAutomation"


class MetricName(str, Enum):
    """The exact set of CloudWatch metric names emitted by the engine.

    Defined once as a string enum so the emitted names are stable and typos are
    impossible. The members map one-to-one to Requirement 17.3.
    """

    ARTICLES_GENERATED = "ArticlesGenerated"
    ARTICLES_PUBLISHED = "ArticlesPublished"
    GENERATION_FAILURES = "GenerationFailures"
    PUBLICATION_FAILURES = "PublicationFailures"
    AUTHENTICATION_FAILURES = "AuthenticationFailures"
    DUPLICATE_RUNS_PREVENTED = "DuplicateRunsPrevented"
    CAMPAIGNS_COMPLETED = "CampaignsCompleted"


class MetricSink(Protocol):
    """A destination for a rendered metric payload."""

    def __call__(self, payload: str) -> None:
        """Write one rendered metric payload."""
        ...


class MetricsEmitter(Protocol):
    """Emits named, correlation-carrying, non-negative integer metrics."""

    def emit(self, name: MetricName, value: int = 1, **dimensions: str) -> None:
        """Emit ``value`` (>= 0) for ``name`` with the current Correlation_ID."""
        ...


class EMFMetricsEmitter:
    """Emits metrics as embedded-metric-format JSON lines.

    Bound to one :class:`ExecutionContext` so every metric carries the same
    Correlation_ID (Requirement 17.1). Values must be non-negative integers
    (Requirement 17.3); a negative value is rejected before emission. Payloads
    are redacted before they reach the sink.
    """

    def __init__(
        self,
        context: ExecutionContext,
        clock: Clock,
        sink: MetricSink | Callable[[str], None] = print,
        namespace: str = METRIC_NAMESPACE,
    ) -> None:
        self._context = context
        self._clock = clock
        self._sink = sink
        self._namespace = namespace

    @property
    def correlation_id(self) -> str:
        """The Correlation_ID attached to every metric from this emitter."""
        return self._context.correlation_id

    def emit(self, name: MetricName, value: int = 1, **dimensions: str) -> None:
        """Emit a single named metric as an EMF payload.

        ``value`` must be a non-negative integer (Requirement 17.3); a
        :class:`ValueError` is raised otherwise. The Correlation_ID is always
        attached, along with any extra string dimensions. The payload is
        redacted before emission so no dimension can carry a secret
        (Requirements 8.1, 17.4, 17.5).
        """
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"metric value for {name.value} must be an int")
        if value < 0:
            raise ValueError(
                f"metric value for {name.value} must be non-negative, got {value}"
            )

        # Correlation_ID plus any caller dimensions become EMF dimensions so the
        # metric can be sliced by execution.
        dimension_fields: dict[str, str] = {
            CORRELATION_ID_FIELD: self._context.correlation_id,
            **dimensions,
        }
        dimension_keys = sorted(dimension_fields.keys())

        payload: dict[str, object] = {
            "_aws": {
                "Timestamp": self._epoch_millis(),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self._namespace,
                        "Dimensions": [dimension_keys],
                        "Metrics": [{"Name": name.value, "Unit": "Count"}],
                    }
                ],
            },
            name.value: value,
            "timestamp": self._clock.now_iso(),
        }
        payload.update(dimension_fields)

        result = redact(payload)
        sanitized = result.payload
        assert isinstance(sanitized, dict)
        self._sink(json.dumps(sanitized, separators=(",", ":"), sort_keys=True))

    def _epoch_millis(self) -> int:
        """Current time as integer milliseconds since the Unix epoch (EMF)."""
        return int(self._clock.now().timestamp() * 1000)


class CloudWatchMetricsSink:
    """Optional sink that publishes a metric via an injected boto3 client.

    This is a boundary wrapper for callers that prefer a direct
    ``PutMetricData`` call over EMF. The CloudWatch client is injected so this
    stays testable without real AWS. Most callers should use the default EMF
    path instead; this exists so the metric name/value contract has a concrete
    real-AWS option when required.
    """

    def __init__(self, client: object, namespace: str = METRIC_NAMESPACE) -> None:
        self._client = client
        self._namespace = namespace

    def __call__(self, payload: str) -> None:
        """Publish one already-rendered EMF payload as CloudWatch metric data.

        The payload is parsed back to extract the metric name, value, and
        dimensions, then forwarded through the injected client's
        ``put_metric_data``. Kept intentionally simple; the EMF path is the
        primary route.
        """
        data = json.loads(payload)
        aws_block = data.get("_aws", {})
        metric_defs = aws_block.get("CloudWatchMetrics", [])
        put = getattr(self._client, "put_metric_data")
        for definition in metric_defs:
            for metric in definition.get("Metrics", []):
                name = metric["Name"]
                dimension_names: list[str] = []
                for group in definition.get("Dimensions", []):
                    dimension_names.extend(group)
                dimensions = [
                    {"Name": key, "Value": str(data[key])}
                    for key in dimension_names
                    if key in data
                ]
                put(
                    Namespace=self._namespace,
                    MetricData=[
                        {
                            "MetricName": name,
                            "Value": float(data[name]),
                            "Unit": metric.get("Unit", "Count"),
                            "Dimensions": dimensions,
                        }
                    ],
                )
