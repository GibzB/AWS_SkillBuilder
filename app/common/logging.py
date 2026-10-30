"""Structured single-line JSON logging with correlation IDs and redaction.

Every component emits structured CloudWatch logs correlated by a
Correlation_ID, with sensitive values redacted before emission (Requirements
17.1, 17.2, 17.4, 17.5). This module provides a small, dependency-free logger
that:

* renders each record as a single line of JSON (one record per line, which is
  what CloudWatch Logs Insights expects);
* stamps every record with the execution's Correlation_ID and an ISO-8601 UTC
  timestamp from the injected :class:`~app.common.clock.Clock`;
* redacts any sensitive value in the record (and records valueless redaction
  events) before the line is written, so no secret can reach the log sink
  (Requirements 8.1, 10.4, 17.4, 17.5);
* supports the fields called out by Requirement 17.2 (``campaign_id``,
  ``day``, ``execution_id``, ``stage``, ``status``, ``duration_ms``,
  ``error_category``) as optional structured attributes.

The sink is injected (any callable taking the rendered line), so tests can
capture output and production can point it at ``print``/stdout, which Lambda
forwards to CloudWatch Logs.
"""

from __future__ import annotations

import json
from typing import Callable, Final, Protocol

from app.common.clock import Clock
from app.common.context import CORRELATION_ID_FIELD, ExecutionContext
from app.common.redaction import RedactionEvent, redact

__all__ = [
    "LogSink",
    "StructuredLogger",
]

# The event name emitted for each redaction so operators can see that a
# sensitive value was removed without seeing the value (Requirement 17.5).
_REDACTION_EVENT_NAME: Final[str] = "redaction"

# Reserved fields recognised by :meth:`StructuredLogger.log` as first-class
# structured attributes (Requirement 17.2). Any other keyword becomes a plain
# field in the record.
_RESERVED_FIELDS: Final[tuple[str, ...]] = (
    "campaign_id",
    "day",
    "execution_id",
    "stage",
    "status",
    "duration_ms",
    "error_category",
)


class LogSink(Protocol):
    """A destination for rendered, single-line JSON log records."""

    def __call__(self, line: str) -> None:
        """Write one rendered log line."""
        ...


class StructuredLogger:
    """Emits single-line JSON log records for one execution.

    The logger is bound to a single :class:`ExecutionContext` so every record
    it writes carries the same Correlation_ID (Requirement 17.1). Timestamps
    come from the injected :class:`Clock`; output goes to the injected
    :class:`LogSink`. All records are redacted before emission.
    """

    def __init__(
        self,
        context: ExecutionContext,
        clock: Clock,
        sink: LogSink | Callable[[str], None] = print,
    ) -> None:
        self._context = context
        self._clock = clock
        self._sink = sink

    @property
    def correlation_id(self) -> str:
        """The Correlation_ID stamped on every record from this logger."""
        return self._context.correlation_id

    def debug(self, message: str, **fields: object) -> None:
        """Emit a DEBUG record."""
        self.log("DEBUG", message, **fields)

    def info(self, message: str, **fields: object) -> None:
        """Emit an INFO record."""
        self.log("INFO", message, **fields)

    def warning(self, message: str, **fields: object) -> None:
        """Emit a WARNING record."""
        self.log("WARNING", message, **fields)

    def error(self, message: str, **fields: object) -> None:
        """Emit an ERROR record."""
        self.log("ERROR", message, **fields)

    def log(self, level: str, message: str, **fields: object) -> None:
        """Build, redact, and emit a single structured record.

        The record always includes ``correlation_id``, ``level``, ``message``,
        and ``timestamp``. Reserved fields (Requirement 17.2) and any extra
        keyword fields are attached when provided. Sensitive values anywhere in
        the record are redacted first, and one valueless redaction event is
        emitted per removed value (Requirements 17.4, 17.5).
        """
        record: dict[str, object] = {
            CORRELATION_ID_FIELD: self._context.correlation_id,
            "level": level,
            "message": message,
            "timestamp": self._clock.now_iso(),
        }
        for key, value in fields.items():
            if value is not None:
                record[key] = value

        result = redact(record)
        sanitized = result.payload
        assert isinstance(sanitized, dict)
        self._write(sanitized)

        # A separate valueless event per redaction, so the fact that a
        # sensitive value was removed is itself observable and correlated,
        # while the value never appears (Requirement 17.5).
        for event in result.events:
            self._emit_redaction_event(event)

    def _emit_redaction_event(self, event: RedactionEvent) -> None:
        """Emit a valueless record noting that a sensitive value was removed."""
        record: dict[str, object] = {
            CORRELATION_ID_FIELD: self._context.correlation_id,
            "level": "WARNING",
            "event": _REDACTION_EVENT_NAME,
            "message": "sensitive value removed before emission",
            "redacted_path": event.path,
            "redaction_reason": event.reason,
            "timestamp": self._clock.now_iso(),
        }
        self._write(record)

    def _write(self, record: dict[str, object]) -> None:
        """Render a record as a single JSON line and hand it to the sink."""
        self._sink(json.dumps(record, separators=(",", ":"), sort_keys=True))
