"""Keep an article's diagram only when it is usable (shared by dailies and roundups)."""

from __future__ import annotations

import re
from dataclasses import replace

from app.diagrams.spec import parse_spec
from app.generation.models import Article

__all__ = ["DIAGRAM_MARKER_RE", "normalise_diagram"]

# The line where the diagram picture belongs in an article body.
DIAGRAM_MARKER_RE = re.compile(
    r"^[ \t]*!\[Architecture diagram\]\(diagram\.png\)[ \t]*(?:\n|$)", re.MULTILINE
)


def normalise_diagram(article: Article) -> Article:
    """Keep the diagram only when the spec is valid and the marker appears once.

    A bad or missing diagram never fails the article: the spec is cleared and any
    marker line is removed from the body.
    """
    markers = len(DIAGRAM_MARKER_RE.findall(article.body_markdown))
    if markers == 1 and parse_spec(article.diagram_json) is not None:
        return article
    if markers == 0 and not article.diagram_json:
        return article
    return replace(
        article,
        diagram_json="",
        body_markdown=DIAGRAM_MARKER_RE.sub("", article.body_markdown).rstrip()
        if markers
        else article.body_markdown,
    )
