"""Content_Validator: deterministic publication quality gates.

The validator is the deterministic gate applied to every generated article
before publication (Requirements 5, 20.3, 20.4). It runs a fixed set of checks
and returns a :class:`ValidationResult` listing one :class:`ValidationViolation`
per failed check, each with a stable ``code``, the offending ``field``, and a
safe human-readable ``message`` that the failure notification can reuse
(Requirement 5.14). All checks run every time so a single result reports every
problem at once rather than stopping at the first.

Every check is pure and deterministic: the same article, approved topic, and
existing day state always produce the same result. Markdown validity is decided
by parsing with ``markdown-it-py`` (Requirements 5.6, 5.7); the remaining checks
are string and structural rules. The validator never mutates the article and
never publishes.

Design reference: design.md "Content Validation Design"; Requirements 5.1 to
5.12, 20.3, 20.4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

from app.campaign.models import DayState
from app.generation.models import TAGS_MAX, TAGS_MIN, Article

__all__ = [
    "ValidationViolation",
    "ValidationResult",
    "ContentValidator",
]

_TITLE_MAX = 255
_DESCRIPTION_MAX = 512


@dataclass(frozen=True)
class ValidationViolation:
    """A single failed validation check (Requirement 5.14).

    ``code`` is a stable machine-readable identifier (for example
    ``"TITLE_TOO_LONG"``); ``field`` names the offending article field; and
    ``message`` is a safe, human-readable explanation reused in failure
    notifications. None of these ever contain secret material.
    """

    code: str
    field: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating an article.

    ``is_valid`` is True iff ``violations`` is empty; otherwise it lists every
    failed check so the orchestrator can report all of them (Requirement 5.14).
    """

    is_valid: bool
    violations: tuple[ValidationViolation, ...]


# Placeholder markers that must never survive into a published article
# (Requirement 5.9). Matched case-insensitively.
_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"lorem ipsum", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    # Angle-bracket fill-ins such as <your-bucket> or <INSERT NAME>.
    re.compile(r"<[^>\n]{0,40}>"),
    # Curly-brace or bracket fill-ins such as {{value}} or [INSERT].
    re.compile(r"\{\{[^}\n]{0,40}\}\}"),
    re.compile(r"\[(?:INSERT|TODO|PLACEHOLDER)[^\]\n]*\]", re.IGNORECASE),
)

# Claims that an AWS operation was ACTUALLY executed in the author's real
# environment. Rejected when the article is flagged as not executed in AWS
# (Requirement 5.10). These patterns require a concrete real-world execution
# signal (my/production/live account, real resource state, actually did it) so
# ordinary tutorial prose describing example steps ("I created a bucket in this
# example", "I ran the command to see the output") is NOT flagged. The intent
# is to catch fabricated claims of real deployments, not first-person teaching.
_EXECUTION_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # An action explicitly tied to the author's own/production/live environment.
    re.compile(
        r"\bI (?:deployed|provisioned|launched|created|spun up|set up|configured|ran)\b"
        r"[^.\n]{0,60}\bin (?:my|our|the) (?:production|prod|live|real|personal|own)\b"
        r"[^.\n]{0,20}\b(?:account|environment|cluster|vpc|region|infrastructure)\b",
        re.IGNORECASE,
    ),
    # Explicit assertion that it really happened / was actually done.
    re.compile(
        r"\b(?:when I actually|I actually|I really)\s+(?:deployed|ran|provisioned|launched|created)\b",
        re.IGNORECASE,
    ),
    # A resource named as currently existing in the author's real account.
    re.compile(
        r"\bin my (?:production|prod|live|real|personal|own) (?:account|environment|cluster)\b",
        re.IGNORECASE,
    ),
)

# Fabricated personal experience: employment, projects, incidents,
# certifications, customer environments, or performance measurements
# (Requirement 20.3).
_FABRICATED_EXPERIENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bas an? (?:certified|aws-certified)\b", re.IGNORECASE),
    re.compile(r"\bI am (?:aws )?certified\b", re.IGNORECASE),
    re.compile(r"\bmy (?:certification|solutions architect certification)\b", re.IGNORECASE),
    re.compile(r"\bat my (?:job|company|employer)\b", re.IGNORECASE),
    re.compile(r"\bmy (?:client|customer)(?:'s)?\b", re.IGNORECASE),
    re.compile(r"\b(?:during|in) a production incident\b", re.IGNORECASE),
    re.compile(r"\bwe (?:saved|reduced costs by|cut costs by)\b", re.IGNORECASE),
    re.compile(r"\breduced (?:latency|cost|costs) by \d", re.IGNORECASE),
    re.compile(r"\bwith (?:\d+|\w+) years of experience\b", re.IGNORECASE),
)

# Non-portable diagram syntaxes whose Builder Center rendering is not assured
# (Requirement 20.4). Only portable fenced text/ASCII diagrams are permitted.
_NON_PORTABLE_DIAGRAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```\s*mermaid", re.IGNORECASE),
    re.compile(r"```\s*plantuml", re.IGNORECASE),
    re.compile(r"```\s*graphviz", re.IGNORECASE),
    re.compile(r"```\s*dot\b", re.IGNORECASE),
    re.compile(r"@startuml", re.IGNORECASE),
)


class ContentValidator:
    """Runs deterministic publication gates and returns a ValidationResult."""

    def __init__(self) -> None:
        # A strict CommonMark parser; used to confirm the body parses cleanly.
        self._markdown = MarkdownIt("commonmark")

    def validate(
        self,
        article: Article,
        approved_topic: str,
        existing_day_state: DayState | None,
    ) -> ValidationResult:
        """Validate ``article`` against every gate and collect all violations.

        ``approved_topic`` is the topic the selected day must cover
        (Requirement 5.11); ``existing_day_state`` is the current stored state
        of that day, used for the duplicate-publication gate (Requirement 5.12).
        Returns a :class:`ValidationResult` whose ``is_valid`` is True only when
        no gate failed.
        """
        violations: list[ValidationViolation] = []

        self._check_title(article, violations)
        self._check_description(article, violations)
        self._check_body(article, violations)
        self._check_markdown(article, violations)
        self._check_tags(article, violations)
        self._check_placeholders(article, violations)
        self._check_fabricated_execution(article, violations)
        self._check_fabricated_experience(article, violations)
        self._check_topic_match(article, approved_topic, violations)
        self._check_duplicate(existing_day_state, violations)
        self._check_non_portable_diagrams(article, violations)
        self._check_malformed_fences(article, violations)

        return ValidationResult(
            is_valid=not violations, violations=tuple(violations)
        )

    # ------------------------------------------------------------------
    # Individual gates
    # ------------------------------------------------------------------

    def _check_title(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        trimmed = article.title.strip()
        if len(trimmed) < 1:
            violations.append(
                ValidationViolation(
                    "TITLE_MISSING", "title", "title is missing or empty"
                )
            )
        elif len(trimmed) > _TITLE_MAX:
            violations.append(
                ValidationViolation(
                    "TITLE_TOO_LONG",
                    "title",
                    f"title exceeds {_TITLE_MAX} characters",
                )
            )

    def _check_description(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        trimmed = article.description.strip()
        if len(trimmed) < 1:
            violations.append(
                ValidationViolation(
                    "DESCRIPTION_MISSING",
                    "description",
                    "description is missing or empty",
                )
            )
        elif len(trimmed) > _DESCRIPTION_MAX:
            violations.append(
                ValidationViolation(
                    "DESCRIPTION_TOO_LONG",
                    "description",
                    f"description exceeds {_DESCRIPTION_MAX} characters",
                )
            )

    def _check_body(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        if len(article.body_markdown.strip()) < 1:
            violations.append(
                ValidationViolation(
                    "BODY_EMPTY", "body_markdown", "body is missing or empty"
                )
            )

    def _check_markdown(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        # An empty body is reported by _check_body; skip the parse to avoid a
        # duplicate, less specific violation.
        if len(article.body_markdown.strip()) < 1:
            return
        try:
            tokens = self._markdown.parse(article.body_markdown)
        except Exception:  # noqa: BLE001 - any parser error is a validation failure
            violations.append(
                ValidationViolation(
                    "MARKDOWN_INVALID",
                    "body_markdown",
                    "body did not parse as valid Markdown",
                )
            )
            return
        # markdown-it is lenient; a hard failure is rare, but a total absence of
        # tokens for non-empty text indicates a parsing problem.
        if not tokens:
            violations.append(
                ValidationViolation(
                    "MARKDOWN_INVALID",
                    "body_markdown",
                    "body did not parse into any Markdown content",
                )
            )

    def _check_tags(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        count = len(article.tags)
        if count < TAGS_MIN or count > TAGS_MAX:
            violations.append(
                ValidationViolation(
                    "TAG_COUNT_INVALID",
                    "tags",
                    f"tag count {count} is outside {TAGS_MIN} to {TAGS_MAX}",
                )
            )

    def _check_placeholders(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        haystack = f"{article.title}\n{article.description}\n{article.body_markdown}"
        if any(pattern.search(haystack) for pattern in _PLACEHOLDER_PATTERNS):
            violations.append(
                ValidationViolation(
                    "PLACEHOLDER_PRESENT",
                    "body_markdown",
                    "content contains placeholder text",
                )
            )

    def _check_fabricated_execution(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        if article.executed_in_aws:
            return
        haystack = f"{article.body_markdown}\n{article.learning_takeaway}"
        if any(p.search(haystack) for p in _EXECUTION_CLAIM_PATTERNS):
            violations.append(
                ValidationViolation(
                    "FABRICATED_EXECUTION",
                    "body_markdown",
                    "content claims an AWS operation was executed when it was not",
                )
            )

    def _check_fabricated_experience(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        haystack = (
            f"{article.title}\n{article.description}\n"
            f"{article.body_markdown}\n{article.learning_takeaway}"
        )
        if any(p.search(haystack) for p in _FABRICATED_EXPERIENCE_PATTERNS):
            violations.append(
                ValidationViolation(
                    "FABRICATED_EXPERIENCE",
                    "body_markdown",
                    "content asserts fabricated personal experience",
                )
            )

    def _check_topic_match(
        self,
        article: Article,
        approved_topic: str,
        violations: list[ValidationViolation],
    ) -> None:
        if not self._topic_matches(article, approved_topic):
            violations.append(
                ValidationViolation(
                    "TOPIC_MISMATCH",
                    "topic",
                    "article does not correspond to the approved topic",
                )
            )

    def _check_duplicate(
        self,
        existing_day_state: DayState | None,
        violations: list[ValidationViolation],
    ) -> None:
        if existing_day_state is DayState.PUBLISHED:
            violations.append(
                ValidationViolation(
                    "DUPLICATE_PUBLICATION",
                    "day",
                    "a PUBLISHED article already exists for this campaign day",
                )
            )

    def _check_non_portable_diagrams(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        if any(
            p.search(article.body_markdown) for p in _NON_PORTABLE_DIAGRAM_PATTERNS
        ):
            violations.append(
                ValidationViolation(
                    "NON_PORTABLE_DIAGRAM",
                    "body_markdown",
                    "content contains a non-portable diagram syntax",
                )
            )

    def _check_malformed_fences(
        self, article: Article, violations: list[ValidationViolation]
    ) -> None:
        """Reject a body whose fenced code blocks are malformed.

        Two defects break Markdown rendering everywhere (the Builder Center
        editor, the S3 HTML pages, any CommonMark renderer):

        * a ```markdown / ```md wrapper fence, which the model sometimes uses to
          wrap the whole article or an example; its inner fences then terminate
          the outer block prematurely;
        * nested same-type fences (a ``` opened before the previous one closed),
          which standard Markdown does not support.

        Either condition is reported as a MALFORMED_FENCE violation so the
        article is never published with broken code blocks.
        """
        body = article.body_markdown
        # A markdown-language wrapper fence is always wrong for our content.
        if re.search(r"```[ \t]*(?:markdown|md)\b", body, re.IGNORECASE):
            violations.append(
                ValidationViolation(
                    "MALFORMED_FENCE",
                    "body_markdown",
                    "content wraps text in a ```markdown fence, which breaks "
                    "rendering; use fenced blocks only for real code",
                )
            )
            return

        # Nested same-type fences: scan ``` toggles; an info string appearing
        # while already inside a fence means a block was opened before the
        # previous one closed.
        inside = False
        for match in re.finditer(r"```([^\n`]*)", body):
            info = match.group(1).strip()
            if not inside:
                inside = True  # opening fence (info string may name a language)
            else:
                if info:
                    # A new info string while still open => nested opening fence.
                    violations.append(
                        ValidationViolation(
                            "MALFORMED_FENCE",
                            "body_markdown",
                            "content contains nested code fences, which break "
                            "Markdown rendering",
                        )
                    )
                    return
                inside = False  # closing fence

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _topic_matches(article: Article, approved_topic: str) -> bool:
        """Return whether the article corresponds to the approved topic.

        The article carries the topic it was generated for; the primary check
        is that it equals the approved topic (case-insensitive, trimmed). As a
        secondary safeguard against a mismatched ``article.topic``, at least one
        significant word from the approved topic must also appear in the title
        or body so a correct label over off-topic content is still caught.
        """
        approved = approved_topic.strip().casefold()
        if not approved:
            return False
        if article.topic.strip().casefold() != approved:
            return False
        significant = [
            word
            for word in re.findall(r"[A-Za-z0-9]+", approved)
            if len(word) >= 3
        ]
        if not significant:
            return True
        content = f"{article.title}\n{article.body_markdown}".casefold()
        return any(word in content for word in significant)
