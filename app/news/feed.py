"""AWS What's New feed client and parser.

The news campaign picks each day's topic from the public AWS What's New RSS
feed. This module fetches the feed with the standard library only (no new
Lambda dependency), validates that the response really is XML, and parses it
into frozen :class:`FeedItem` values.

AWS answers a plain request (no browser-like ``User-Agent`` and no RSS
``Accept`` header) with a 301 to an HTML 404 page, so the client always sends
both headers and rejects any response that is not XML. The opener is injected so
tests never touch the network.
"""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Protocol

from app.common.errors import GenerationError

__all__ = [
    "FEED_URL",
    "USER_AGENT",
    "ACCEPT",
    "FeedItem",
    "NewsSource",
    "LiveNewsSource",
    "fetch_feed",
    "parse_feed",
    "is_regional",
]

FEED_URL = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
ACCEPT = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8"

_PRODUCT_PREFIX = "general:products/"
# Product slugs that name a group or an edition, not a service.
_GENERIC_SLUGS = frozenset({"aiml", "aws-govcloud-us"})

_GEO = (
    r"(?:regions?|govcloud|sovereign cloud|local zones?|asia pacific|europe|"
    r"us east|us west|africa|middle east|south america|canada|israel|mexico|"
    r"china|additional aws)"
)
_REGIONAL_PATTERNS = (
    re.compile(r"\bavailable in\b.{0,80}\b" + _GEO, re.IGNORECASE),
    re.compile(r"\bexpands? to\b", re.IGNORECASE),
    re.compile(r"\b(?:new|additional) (?:aws )?regions?\b", re.IGNORECASE),
    re.compile(r"\bto additional\b.{0,20}\bregions?\b", re.IGNORECASE),
)

_TAG_RE = re.compile(r"<[^>]*>")
_BREAK_RE = re.compile(r"<\s*(?:br\s*/?|/p)\s*>", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedItem:
    """One announcement from the What's New feed."""

    guid: str
    title: str
    description_text: str
    published_iso: str
    categories: tuple[str, ...]
    link: str
    service_slug: str = ""


class NewsSource(Protocol):
    """Anything that can return the current feed items."""

    def fetch(self) -> list[FeedItem]:
        """Return the feed items, newest first as published."""
        ...


class LiveNewsSource:
    """The production :class:`NewsSource`: fetches the real feed on each call."""

    def fetch(self) -> list[FeedItem]:
        return fetch_feed()


class _Response(Protocol):
    status: int
    headers: Any

    def read(self) -> bytes: ...


Opener = Callable[[urllib.request.Request, float], _Response]


def _default_opener(request: urllib.request.Request, timeout: float) -> _Response:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[no-any-return]  # noqa: S310


def fetch_feed(
    opener: Opener | None = None,
    url: str = FEED_URL,
    timeout: float = 20,
) -> list[FeedItem]:
    """Fetch and parse the feed, or raise ``GenerationError(reason="unavailable")``.

    Only ``https://`` URLs are accepted, because urllib would also open
    ``file://`` and other schemes.
    """
    if not url.startswith("https://"):
        raise GenerationError(
            message="news feed URL must use https", reason="unavailable"
        )
    request = urllib.request.Request(  # noqa: S310 - fixed https URL
        url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}
    )
    open_url = opener or _default_opener
    try:
        response = open_url(request, timeout)
        status = int(response.status)
        content_type = str(response.headers.get("Content-Type", "")).lower()
        raw = response.read()
    except Exception as error:  # noqa: BLE001 - classified as unavailable
        raise GenerationError(
            message=f"news feed request failed: {type(error).__name__}",
            reason="unavailable",
        ) from error
    if status != 200:
        raise GenerationError(
            message=f"news feed returned HTTP {status}", reason="unavailable"
        )
    text = raw.decode("utf-8", errors="replace")
    head = text.lstrip()[:200].lower()
    if (
        "html" in content_type
        or head.startswith("<!doctype html")
        or head.startswith("<html")
    ):
        raise GenerationError(
            message="news feed returned an HTML page instead of RSS",
            reason="unavailable",
        )
    if "xml" not in content_type and not head.startswith("<?xml") and "<rss" not in head:
        raise GenerationError(
            message="news feed response is not XML", reason="unavailable"
        )
    return parse_feed(text)


def _clean_html(fragment: str) -> str:
    """Strip tags and entities, returning single-spaced plain text."""
    with_breaks = _BREAK_RE.sub(" ", fragment)
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", with_breaks))).strip()


def _published_iso(raw: str) -> str:
    try:
        moment = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _service_slug(categories: tuple[str, ...]) -> str:
    slugs = [c[len(_PRODUCT_PREFIX) :] for c in categories if c.startswith(_PRODUCT_PREFIX)]
    for slug in slugs:
        if slug and slug not in _GENERIC_SLUGS:
            return slug
    return ""


def parse_feed(xml_text: str) -> list[FeedItem]:
    """Parse RSS text into items, skipping any item missing guid, title or link.

    Raises ``GenerationError(reason="unavailable")`` when the text is not
    well-formed XML.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise GenerationError(
            message="news feed is not well-formed XML", reason="unavailable"
        ) from error
    items: list[FeedItem] = []
    for node in root.iter("item"):
        guid = (node.findtext("guid") or "").strip()
        title = _clean_html(node.findtext("title") or "")
        link = (node.findtext("link") or "").strip()
        if not (guid and title and link):
            continue
        tokens: list[str] = []
        for category in node.findall("category"):
            tokens.extend(t.strip() for t in (category.text or "").split(",") if t.strip())
        categories = tuple(tokens)
        items.append(
            FeedItem(
                guid=guid,
                title=title,
                description_text=_clean_html(node.findtext("description") or ""),
                published_iso=_published_iso(node.findtext("pubDate") or ""),
                categories=categories,
                link=link,
                service_slug=_service_slug(categories),
            )
        )
    return items


def is_regional(item: FeedItem) -> bool:
    """True for regional-availability announcements (kept out of daily topics)."""
    return any(p.search(item.title) for p in _REGIONAL_PATTERNS)
