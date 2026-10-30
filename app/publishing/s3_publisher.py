"""S3Publisher: render an article to HTML and publish it behind CloudFront.

This Publishing_Adapter writes each generated article to a private S3 bucket as
a static HTML page and returns the public CloudFront URL that serves it over
HTTPS (Requirement 6.1, 6.6). The bucket is private; only the CloudFront
distribution (via Origin Access Control) can read it, so articles are reachable
only through the CDN URL, never the raw S3 endpoint.

Unlike the MockPublisher this produces a real, reachable URL, and unlike the
blocked BuilderCenterPublisher it uses a fully supported, automatable AWS
mechanism. It fits the adapter contract exactly, so the Orchestrator is
unchanged: it still depends only on ``publish_article(article) ->
PublicationResult``.

The S3 client is injected behind a small protocol so the adapter is unit
testable without real AWS. Markdown is rendered to HTML with markdown-it-py
(already a dependency). The object key is deterministic
(``day/<zero-padded day>.html``) so re-publishing a day overwrites in place and
the URL is stable.

Design reference: design.md "Publishing_Adapter"; Requirements 6.1, 6.2, 6.6,
6.8.
"""

from __future__ import annotations

import html
from typing import Any, Protocol

from markdown_it import MarkdownIt

from app.generation.models import Article
from app.publishing.adapter import PublicationResult

__all__ = ["DAY_KEY_WIDTH", "S3Client", "S3Publisher", "render_article_html"]

# Zero-padding for the day in the object key, so keys sort naturally.
DAY_KEY_WIDTH = 4


class S3Client(Protocol):
    """Minimal structural type for the injected boto3 S3 client.

    Only ``put_object`` is used. The real ``boto3.client("s3")`` satisfies this,
    as does an in-memory fake in tests.
    """

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Write an object to a bucket."""
        ...


def _object_key(day: int) -> str:
    """Deterministic object key for a day's article HTML."""
    return f"day/{day:0{DAY_KEY_WIDTH}d}.html"


def render_article_html(article: Article) -> str:
    """Render an article to a minimal, self-contained HTML page.

    The body Markdown is converted to HTML; the title, description, tags, and
    learning takeaway frame it. Styling is inline and minimal so the page is
    self-contained with no external assets. All dynamic text that is not
    Markdown-rendered is HTML-escaped to avoid broken markup.
    """
    md = MarkdownIt("commonmark")
    body_html = md.render(article.body_markdown)
    safe_title = html.escape(article.title)
    safe_description = html.escape(article.description)
    safe_takeaway = html.escape(article.learning_takeaway)
    tags_html = " ".join(
        f'<span class="tag">{html.escape(tag)}</span>' for tag in article.tags
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<meta name="description" content="{safe_description}">
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 760px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6;
         color: #1a1a1a; }}
  h1, h2, h3 {{ line-height: 1.25; }}
  pre {{ background: #f5f5f5; padding: 1rem; overflow-x: auto; border-radius: 6px; }}
  code {{ background: #f5f5f5; padding: 0.1rem 0.3rem; border-radius: 4px; }}
  pre code {{ background: none; padding: 0; }}
  .tag {{ display: inline-block; background: #eef; color: #224;
          padding: 0.1rem 0.5rem; margin-right: 0.3rem; border-radius: 999px;
          font-size: 0.85rem; }}
  .takeaway {{ border-left: 4px solid #88a; padding-left: 1rem; color: #333;
               font-style: italic; }}
</style>
</head>
<body>
<article>
{body_html}
<p class="takeaway"><strong>What I learned:</strong> {safe_takeaway}</p>
<p>{tags_html}</p>
</article>
</body>
</html>
"""


class S3Publisher:
    """Publishes articles as HTML to a private S3 bucket behind CloudFront."""

    def __init__(
        self, s3_client: S3Client, bucket: str, base_url: str
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        # Normalize the CloudFront base URL to have no trailing slash.
        self._base_url = base_url.rstrip("/")

    def publish_article(self, article: Article) -> PublicationResult:
        """Render ``article`` to HTML, write it to S3, return the CDN URL.

        On a successful write the returned :class:`PublicationResult` carries
        the CloudFront HTTPS URL for the day's page. Any S3 error is returned as
        a sanitized failure result (no retry here; the Orchestrator owns retry
        policy). The input article is never mutated (Requirement 6.8).
        """
        key = _object_key(article.day)
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=render_article_html(article).encode("utf-8"),
                ContentType="text/html; charset=utf-8",
                CacheControl="public, max-age=300",
            )
        except Exception as error:  # noqa: BLE001 - surfaced as a failure result
            return PublicationResult.failure(
                error_summary=f"failed to write article to S3: {type(error).__name__}"
            )
        return PublicationResult.success(publication_url=f"{self._base_url}/{key}")
