"""MockPublisher: a Publishing_Adapter that never contacts Builder Center.

The MockPublisher is the MVP default (Requirement 6.2, Publishing Adapter
Strategy). It returns a successful :class:`PublicationResult` without
transmitting any network request to Builder Center, which lets the full
pipeline (schedule, generate, validate, record, notify) run end to end and stay
under budget while the real publishing mechanism remains a documented blocked
discovery task.

It synthesizes a deterministic, clearly-marked mock publication URL from the
article's campaign and day so downstream recording and notifications have a
value to carry, while making it obvious the URL is not a real Builder Center
link. It never mutates the input article.

Design reference: design.md "Publishing Adapter Strategy"; Requirement 6.2.
"""

from __future__ import annotations

from app.generation.models import Article
from app.publishing.adapter import PublicationResult

__all__ = ["MOCK_URL_PREFIX", "MockPublisher"]

# A clearly non-real host so a mock URL is never mistaken for a Builder Center
# link in notifications or logs.
MOCK_URL_PREFIX = "https://mock.local/aws-builder"


class MockPublisher:
    """Simulates publication with no network to Builder Center (Req 6.2)."""

    def __init__(self, url_prefix: str = MOCK_URL_PREFIX) -> None:
        self._url_prefix = url_prefix

    def publish_article(self, article: Article) -> PublicationResult:
        """Return a SUCCESS result with a synthetic URL; performs no network I/O."""
        url = f"{self._url_prefix}/{article.campaign_id}/day/{article.day}"
        return PublicationResult.success(publication_url=url)
