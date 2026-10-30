"""BuilderCenterPublisher: documented, blocked real-publisher discovery task.

This is the highest-risk component and is deliberately NOT assumed to work.

Discovery finding (AWS sources, February to March 2026): there is no documented
official or public AWS Builder Center article-publishing API. Builder Center is
a signed-in web experience; AWS Builder ID is the credential and now supports
federated sign-in and MFA, which increases the risk and fragility of any
automation. Content was rephrased for compliance with licensing restrictions.

Because no safe, reliable, permitted programmatic mechanism has been confirmed,
this publisher remains a documented blocked task. The deployable MVP runs on the
:class:`~app.publishing.mock.MockPublisher`; this class is never auto-selected
(the selection layer treats ``builder_center`` as an explicit, opt-in choice).

Fail-safe rules enforced here (Requirements 6.3, 8.5, 8.6, 12.3, 12.4, 12.6):

* the adapter never bypasses MFA, CAPTCHA, bot detection, or rate limits;
* authentication or session-expiry conditions raise :class:`AuthenticationError`
  with remediation guidance, which the Orchestrator turns into a FAILED day and
  a remediation notification without any automatic retry;
* :meth:`verify_published` exists so ambiguous results can be checked against
  the target before any re-publish, but it too is blocked until a permitted
  mechanism is confirmed.

A one-time manual Builder ID sign-in / session capture, and confirmation that
automation is permitted by Builder Center terms, are non-coding prerequisites
that are out of scope for this task.

Design reference: design.md "Publishing Adapter Strategy"; Requirements 6.3,
8.5, 8.6, 12.3, 12.4, 12.6.
"""

from __future__ import annotations

from app.common.errors import PublicationError
from app.generation.models import Article
from app.publishing.adapter import PublicationResult

__all__ = ["BUILDER_CENTER_BLOCKER", "BuilderCenterPublisher"]

# Human-readable, secret-free explanation of why this publisher is blocked.
BUILDER_CENTER_BLOCKER = (
    "AWS Builder Center has no confirmed official or permitted programmatic "
    "publishing mechanism (discovery Feb-Mar 2026). Automated publishing is "
    "blocked; publish manually or keep the MockPublisher selected."
)


class BuilderCenterPublisher:
    """A blocked real publisher; fails safe and is never auto-selected.

    Implements the :class:`~app.publishing.adapter.PublishingAdapter` shape so
    it is a drop-in should a permitted mechanism later be confirmed. Until then
    every publish attempt fails safely with a non-transient error summary and
    performs no network access, and it never attempts to bypass any access
    control.
    """

    def publish_article(self, article: Article) -> PublicationResult:
        """Return a non-transient FAILURE describing the documented blocker.

        Performs no network access and does not mutate ``article``. The result
        is a determinate (non-ambiguous, non-transient) failure so the
        Orchestrator marks the day FAILED and notifies with remediation rather
        than retrying (Requirements 6.3, 6.8).
        """
        return PublicationResult.failure(
            error_summary=BUILDER_CENTER_BLOCKER, ambiguous=False
        )

    def verify_published(self, article: Article) -> bool:
        """Ambiguous-result verification hook; blocked until a mechanism exists.

        When a real mechanism is confirmed this queries the target to determine
        whether ``article`` was already published (Requirements 12.3, 12.4).
        Until then it raises :class:`PublicationError` rather than guessing,
        because silently returning False could cause a duplicate re-publish.
        """
        raise PublicationError(
            message=BUILDER_CENTER_BLOCKER,
            transient=False,
            error_summary=BUILDER_CENTER_BLOCKER,
        )
