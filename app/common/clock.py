"""Injectable clock abstraction producing ISO-8601 UTC timestamps.

Time is an injected boundary so the pure-logic modules (orchestrator, campaign
manager, notifier) stay deterministic and testable. Production code uses
:class:`SystemClock`; tests inject a fake implementing the :class:`Clock`
protocol (for example returning a fixed instant), which keeps property-based
and unit tests fast and reproducible.

All timestamps are timezone-aware UTC and formatted as ISO-8601 so recorded
created/updated/published timestamps and notification timestamps are
consistent across the engine (Requirements 1.7, 10.1, 10.2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SystemClock", "to_iso8601_utc"]


def to_iso8601_utc(moment: datetime) -> str:
    """Render a datetime as an ISO-8601 string in UTC.

    Naive datetimes are assumed to already be in UTC; aware datetimes are
    converted to UTC. The result always ends in ``+00:00`` and uses
    microsecond precision when present.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


@runtime_checkable
class Clock(Protocol):
    """A source of the current time, injectable for testing.

    Implementations return the current instant both as a timezone-aware UTC
    :class:`datetime` and as an ISO-8601 UTC string. Tests provide fakes that
    return controlled values; production uses :class:`SystemClock`.
    """

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...

    def now_iso(self) -> str:
        """Return the current instant as an ISO-8601 UTC timestamp string."""
        ...


class SystemClock:
    """Real clock backed by the system wall clock in UTC.

    This is the production implementation injected into components that need
    the current time. It holds no state and is safe to share.
    """

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        return datetime.now(timezone.utc)

    def now_iso(self) -> str:
        """Return the current instant as an ISO-8601 UTC timestamp string."""
        return to_iso8601_utc(self.now())
