"""Publishing_Adapter interface and the PublicationResult contract.

The publishing mechanism is the highest-risk component, so it is isolated
behind a single-operation interface (Requirement 6.1). The Orchestrator
references only :meth:`PublishingAdapter.publish_article` and the
:class:`PublicationResult` it returns, never any field or behavior specific to
one implementation (Requirement 6.4). This lets the concrete publisher
(MockPublisher today, a real BuilderCenterPublisher later) be swapped without
touching the rest of the engine.

Design reference: design.md "Publishing_Adapter" and "Publishing Adapter
Strategy"; Requirements 6.1, 6.4, 6.6, 6.7, 6.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.generation.models import Article

__all__ = [
    "PUBLICATION_URL_MAX",
    "PublicationStatus",
    "PublicationResult",
    "PublishingAdapter",
]

# Maximum length of a publication URL on success-with-url (Requirement 6.6).
PUBLICATION_URL_MAX = 2048

PublicationStatus = Literal["SUCCESS", "FAILURE"]


@dataclass(frozen=True)
class PublicationResult:
    """The sole output contract of every Publishing_Adapter (Requirement 6).

    * ``status`` is ``"SUCCESS"`` or ``"FAILURE"``.
    * ``publication_url`` is 1..2048 chars on success-with-url, and ``""`` when
      the publish succeeded but no URL is available (Requirements 6.6, 6.7).
    * ``error_summary`` is ``""`` on success; on failure it is a sanitized
      description that excludes all authentication material (Requirement 6.8).
    * ``ambiguous`` is True only when success or failure could not be
      determined, which requires verification before any re-publish
      (Requirements 12.3, 12.4).

    Convenience constructors :meth:`success` and :meth:`failure` build results
    that satisfy the contract by construction.
    """

    status: PublicationStatus
    publication_url: str = ""
    error_summary: str = ""
    ambiguous: bool = False

    @property
    def is_success(self) -> bool:
        """True when the publish attempt succeeded."""
        return self.status == "SUCCESS"

    @classmethod
    def success(cls, publication_url: str = "") -> "PublicationResult":
        """Build a success result with an optional publication URL."""
        return cls(status="SUCCESS", publication_url=publication_url)

    @classmethod
    def failure(
        cls, error_summary: str, ambiguous: bool = False
    ) -> "PublicationResult":
        """Build a failure result carrying a sanitized error summary."""
        return cls(
            status="FAILURE",
            error_summary=error_summary,
            ambiguous=ambiguous,
        )


@runtime_checkable
class PublishingAdapter(Protocol):
    """The single publish operation that isolates Builder Center specifics.

    Exactly one operation, accepting one :class:`Article` and returning exactly
    one :class:`PublicationResult` (Requirement 6.1). Implementations must not
    mutate the input article (Requirement 6.8).
    """

    def publish_article(self, article: Article) -> PublicationResult:
        """Publish ``article`` and return its :class:`PublicationResult`."""
        ...
