"""Shared typed error hierarchy for the AWS Builder Learning Automation Engine.

These errors drive the engine's error-handling policy (see design.md, Error
Handling). Each error carries meaningful, strongly typed attributes so callers
can react by category rather than parsing message strings. All errors derive
from :class:`EngineError` so a single ``except EngineError`` can catch any
domain failure while still allowing precise per-category handling.

Design references: Requirements 4.6, 4.7, 5.14, 8.4, 8.5, 12.6, 16.2, 16.4,
19.4 and the design "Components and Interfaces" / "Error Handling" sections.
"""

from __future__ import annotations

__all__ = [
    "EngineError",
    "ValidationError",
    "GenerationError",
    "AuthenticationError",
    "PublicationError",
    "AmbiguousPublicationError",
    "IdempotencyConflict",
    "ConfigurationError",
    "StateTransitionError",
]


class EngineError(Exception):
    """Base class for every domain error raised by the engine.

    A single ``message`` is always available. Subclasses add typed attributes
    describing the specific failure. Messages are expected to be safe to log
    and to include in notifications (they must never carry secret material);
    redaction of any accidental sensitive content is handled by the logging
    layer.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message: str = message


class ValidationError(EngineError):
    """Raised when input or generated content fails a deterministic gate.

    Covers request validation (Requirement 19.4) and content validation
    (Requirement 5.14). ``fields`` names every offending or missing field so
    callers can report each violation, satisfying the "name every offending
    field" requirements.
    """

    def __init__(self, message: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.fields: tuple[str, ...] = tuple(fields)


class GenerationError(EngineError):
    """Raised when Bedrock generation fails (Requirements 4.6, 4.7).

    ``reason`` classifies the failure (for example ``"timeout"``,
    ``"parse_error"``, ``"out_of_bounds"``, ``"empty_field"``, or
    ``"unavailable"``). ``timed_out`` distinguishes the 120-second timeout
    case (Requirement 4.7) from other generation failures.
    """

    def __init__(
        self, message: str, reason: str = "generation_failure", timed_out: bool = False
    ) -> None:
        super().__init__(message)
        self.reason: str = reason
        self.timed_out: bool = timed_out


class AuthenticationError(EngineError):
    """Raised on secret-read failure, auth rejection, or session expiry.

    Fails safe: the engine never bypasses controls and never retries an
    authentication failure automatically (Requirements 8.4, 8.5, 12.6). The
    engine always surfaces remediation instructions to the user rather than
    the underlying credential detail, so ``remediation`` is a user-facing hint
    and ``session_expired`` flags expiry versus an outright rejection.
    """

    def __init__(
        self,
        message: str,
        remediation: str = "",
        session_expired: bool = False,
    ) -> None:
        super().__init__(message)
        self.remediation: str = remediation
        self.session_expired: bool = session_expired


class PublicationError(EngineError):
    """Raised when publication fails with a determinate outcome.

    ``transient`` marks retryable failures (bounded exponential backoff,
    Requirement 12.2) versus permanent ones. ``error_summary`` is a sanitized,
    secret-free description suitable for notifications (<= 500 chars when set
    by the orchestrator).
    """

    def __init__(
        self,
        message: str,
        transient: bool = False,
        error_summary: str = "",
    ) -> None:
        super().__init__(message)
        self.transient: bool = transient
        self.error_summary: str = error_summary


class AmbiguousPublicationError(PublicationError):
    """Raised when publication success or failure cannot be determined.

    Distinct from :class:`PublicationError`: an ambiguous result MUST be
    verified against the publication target before any re-publish attempt
    (Requirements 12.3, 12.4). It is always treated as transient because the
    correct next step is verification, not an immediate terminal failure.
    """

    def __init__(self, message: str, error_summary: str = "") -> None:
        super().__init__(message, transient=True, error_summary=error_summary)


class IdempotencyConflict(EngineError):
    """Raised when a conditional PUBLISHED write is blocked by an existing one.

    Represents a prevented duplicate (Requirements 9.3, 9.4): the day is
    already PUBLISHED, so no publication action is taken and the write is not
    retried. ``campaign_id`` and ``day`` identify the affected Campaign_Day.
    """

    def __init__(self, message: str, campaign_id: str, day: int) -> None:
        super().__init__(message)
        self.campaign_id: str = campaign_id
        self.day: int = day


class ConfigurationError(EngineError):
    """Raised when configuration is missing, invalid, or ambiguous.

    Covers missing/invalid deployment configuration (Requirement 19.4) and
    invalid publisher selection where zero or more than one adapter is chosen
    (Requirement 6.5), which blocks any publish attempt. ``fields`` names each
    offending configuration value.
    """

    def __init__(self, message: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.fields: tuple[str, ...] = tuple(fields)


class StateTransitionError(EngineError):
    """Raised when a Campaign_State or Day_State transition is not permitted.

    The stored state is preserved and the error names both the current and the
    requested state (Requirements 16.2, 16.4). ``scope`` distinguishes a
    campaign transition from a day transition.
    """

    def __init__(
        self,
        current_state: str,
        requested_state: str,
        scope: str = "state",
        message: str | None = None,
    ) -> None:
        resolved = message or (
            f"Illegal {scope} transition from '{current_state}' "
            f"to '{requested_state}'"
        )
        super().__init__(resolved)
        self.current_state: str = current_state
        self.requested_state: str = requested_state
        self.scope: str = scope
