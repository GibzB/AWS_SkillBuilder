"""Common package: logging, redaction, correlation IDs, metrics, errors, clock.

Re-exports the shared building blocks used across the engine so callers can
import from a single stable location. Observability pieces (structured logging,
redaction, correlation IDs, and named metrics) satisfy Requirement 17 and the
secret-safety exclusions of Requirements 8.1 and 10.4.
"""

from app.common.clock import Clock, SystemClock, to_iso8601_utc
from app.common.context import (
    CORRELATION_ID_FIELD,
    ExecutionContext,
    current_context,
    new_correlation_id,
)
from app.common.errors import (
    AmbiguousPublicationError,
    AuthenticationError,
    ConfigurationError,
    EngineError,
    GenerationError,
    IdempotencyConflict,
    PublicationError,
    StateTransitionError,
    ValidationError,
)
from app.common.logging import LogSink, StructuredLogger
from app.common.metrics import (
    CloudWatchMetricsSink,
    EMFMetricsEmitter,
    MetricName,
    MetricSink,
    MetricsEmitter,
)
from app.common.redaction import (
    REDACTED_PLACEHOLDER,
    RedactionEvent,
    RedactionResult,
    redact,
)

__all__ = [
    # clock
    "Clock",
    "SystemClock",
    "to_iso8601_utc",
    # context / correlation
    "CORRELATION_ID_FIELD",
    "ExecutionContext",
    "current_context",
    "new_correlation_id",
    # errors
    "EngineError",
    "ValidationError",
    "GenerationError",
    "AuthenticationError",
    "PublicationError",
    "AmbiguousPublicationError",
    "IdempotencyConflict",
    "ConfigurationError",
    "StateTransitionError",
    # logging
    "LogSink",
    "StructuredLogger",
    # metrics
    "MetricName",
    "MetricSink",
    "MetricsEmitter",
    "EMFMetricsEmitter",
    "CloudWatchMetricsSink",
    # redaction
    "REDACTED_PLACEHOLDER",
    "RedactionEvent",
    "RedactionResult",
    "redact",
]
