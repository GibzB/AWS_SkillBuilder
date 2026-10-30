"""Per-execution context carrying a unique Correlation_ID.

When an execution begins the engine generates one unique Correlation_ID and
attaches it to every structured log record and metric emitted during that
execution, persisting across all components invoked within that execution
(Requirement 17.1). A single :class:`ExecutionContext` is created at the start
of an execution and threaded through the components (logger and metrics
emitter) so they all share the same identifier.

For convenience across components that are not directly passed the context, the
active context is also published on a :class:`contextvars.ContextVar`. This
keeps the identifier consistent within a single execution (including across
async boundaries) without relying on a process-global mutable, which is
important because a Lambda process may serve multiple executions over its
lifetime.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import TracebackType
from typing import Final

__all__ = [
    "CORRELATION_ID_FIELD",
    "ExecutionContext",
    "new_correlation_id",
    "current_context",
]

# The canonical field name under which the Correlation_ID appears in every log
# record and metric payload.
CORRELATION_ID_FIELD: Final[str] = "correlation_id"

_active_context: ContextVar["ExecutionContext | None"] = ContextVar(
    "active_execution_context", default=None
)


def new_correlation_id() -> str:
    """Generate a fresh, globally unique Correlation_ID for one execution."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable per-execution context holding the Correlation_ID.

    Create one at the start of each execution (``ExecutionContext.start()``)
    and pass it to the logger and metrics emitter. Used as a context manager it
    also publishes itself as the active context for the duration of the
    ``with`` block, so components that read :func:`current_context` observe the
    same identifier (Requirement 17.1).
    """

    correlation_id: str = field(default_factory=new_correlation_id)

    @classmethod
    def start(cls) -> "ExecutionContext":
        """Begin a new execution with a freshly generated Correlation_ID."""
        return cls()

    def __enter__(self) -> "ExecutionContext":
        # Store the reset token on the instance without breaking frozen-ness.
        object.__setattr__(self, "_token", _active_context.set(self))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        token: Token[ExecutionContext | None] | None = getattr(self, "_token", None)
        if token is not None:
            _active_context.reset(token)
            object.__setattr__(self, "_token", None)


def current_context() -> ExecutionContext | None:
    """Return the active :class:`ExecutionContext`, or None outside one."""
    return _active_context.get()
