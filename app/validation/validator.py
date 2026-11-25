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
from app.diagrams.normalise import DIAGRAM_MARKER_RE
from app.diagrams.spec import parse_spec
from app.generation.models import (
    TAGS_MAX,
    TAGS_MIN,
    Article,
    NewsContext,
    RoundupContext,
)
from app.validation.cli_check import (
    UNVERIFIABLE_KINDS,
    check_article_commands,
    find_unreproducible_commands,
    find_unshown_files,
    terraform_config_missing,
)

__all__ = [
    "ValidationViolation",
    "ValidationResult",
    "ContentValidator",
    "DIAGRAM_MISSING",
    "without_soft_violations",
]

# News campaign: every daily and roundup should carry one architecture diagram.
# A missing or invalid diagram is reported so the one-shot repair asks for it,
# but it is SOFT: if the repair cannot fix it, the article still publishes
# without a diagram (a diagram never blocks generation).
DIAGRAM_MISSING = "DIAGRAM_MISSING"
SOFT_CODES = frozenset({DIAGRAM_MISSING})

_TITLE_MAX = 255
_DESCRIPTION_MAX = 512

# News campaign: a command the checker cannot verify is allowed only under this
# line (matched at the start of a line, after any leading whitespace).
DISCLAIMER_PREFIX = "> **Validate before you run"
# Number of consecutive words copied verbatim from the announcement that is
# treated as copying rather than summarising.
COPY_WINDOW_WORDS = 12
_EM_DASH = "\u2014"
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_WORD_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


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

    def __init__(self, require_cli_example: bool = False) -> None:
        # A strict CommonMark parser; used to confirm the body parses cleanly.
        self._markdown = MarkdownIt("commonmark")
        # When True, every article must include at least one AWS CLI command and
        # every ``aws`` command in it must verify against botocore's service
        # definitions (real service, operation, flags, required parameters).
        self._require_cli_example = require_cli_example

    def validate(
        self,
        article: Article,
        approved_topic: str,
        existing_day_state: DayState | None,
        *,
        source: NewsContext | None = None,
        roundup: RoundupContext | None = None,
    ) -> ValidationResult:
        """Validate ``article`` against every gate and collect all violations.

        ``approved_topic`` is the topic the selected day must cover
        (Requirement 5.11); ``existing_day_state`` is the current stored state
        of that day, used for the duplicate-publication gate (Requirement 5.12).
        Returns a :class:`ValidationResult` whose ``is_valid`` is True only when
        no gate failed.

        ``source`` switches on the news-campaign rules for a daily article and
        ``roundup`` the rules for a weekly roundup; with neither the behaviour
        is exactly the original one.
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
        if roundup is not None:
            self._check_roundup(article, roundup, violations)
        elif source is not None:
            self._check_news(article, source, violations)
            if self._require_cli_example:
                self._check_cli_examples(article, violations, news=True)
        elif self._require_cli_example:
            self._check_cli_examples(article, violations)

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
        for pattern in _PLACEHOLDER_PATTERNS:
            match = pattern.search(haystack)
            if match is not None:
                # Quote the offending text so a repair attempt knows exactly what
                # to replace (a bare "contains placeholder text" is not actionable).
                violations.append(
                    ValidationViolation(
                        "PLACEHOLDER_PRESENT",
                        "body_markdown",
                        f"content contains placeholder text: '{match.group(0)[:60]}'; "
                        "capture real values from earlier commands with a shell "
                        "variable and --query instead",
                    )
                )
                return

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

    def _check_cli_examples(
        self,
        article: Article,
        violations: list[ValidationViolation],
        news: bool = False,
    ) -> None:
        """Require at least one AWS CLI command, and that all of them verify.

        In news mode (``news=True``) a command the checker cannot verify, and a
        missing CLI example, are accepted when the body carries the
        "Validate before you run" disclaimer line; verifiably wrong commands
        still fail.
        """
        disclaimer = news and _has_disclaimer(article.body_markdown)
        count, issues = check_article_commands(article.body_markdown)
        if count == 0:
            if not disclaimer:
                violations.append(
                    ValidationViolation(
                        "CLI_EXAMPLE_MISSING",
                        "body_markdown",
                        "article has no AWS CLI example in a shell code block",
                    )
                )
            return
        for issue in issues:
            if news and issue.kind in UNVERIFIABLE_KINDS:
                if not disclaimer:
                    violations.append(
                        ValidationViolation(
                            "CLI_UNVERIFIED_NO_DISCLAIMER",
                            "body_markdown",
                            f"command '{issue.command[:80]}' cannot be verified "
                            f"({issue.reason}); either add a line starting "
                            f"'{DISCLAIMER_PREFIX}:**' directly above the code "
                            "block telling the reader to check the commands "
                            "against current AWS documentation, or use a "
                            "command you are sure exists",
                        )
                    )
                continue
            violations.append(
                ValidationViolation(
                    "CLI_COMMAND_INVALID",
                    "body_markdown",
                    f"command '{issue.command[:80]}' is invalid: {issue.reason}",
                )
            )
        for name in find_unshown_files(article.body_markdown):
            violations.append(
                ValidationViolation(
                    "REFERENCED_FILE_MISSING",
                    "body_markdown",
                    f"a command reads file '{name}' but the article never shows its "
                    "contents; add a code block with the full file and say what to "
                    "save it as, before the command",
                )
            )
        for command in find_unreproducible_commands(article.body_markdown):
            violations.append(
                ValidationViolation(
                    "CLI_NOT_REPRODUCIBLE",
                    "body_markdown",
                    f"command '{command[:80]}' needs an account-specific ARN or a "
                    "package file the reader does not have; replace it with a "
                    "read-only command (list, describe, get) that runs as written",
                )
            )
        if terraform_config_missing(article.body_markdown):
            violations.append(
                ValidationViolation(
                    "TERRAFORM_CONFIG_MISSING",
                    "body_markdown",
                    "article runs terraform commands but shows no ```hcl config block",
                )
            )

    # ------------------------------------------------------------------
    # News campaign gates
    # ------------------------------------------------------------------

    def _check_news(
        self,
        article: Article,
        source: NewsContext,
        violations: list[ValidationViolation],
    ) -> None:
        """Rules for a daily news article (see the news_v1 prompt)."""
        _check_em_dash(article, violations)
        _check_diagram(article, violations)
        if not _last_line_links_to(article.body_markdown, source.link):
            violations.append(
                ValidationViolation(
                    "SOURCE_LINK_MISSING",
                    "body_markdown",
                    "the last line of the body must be 'Read the announcement: "
                    f"[title]({source.link})' with exactly that link",
                )
            )
        if _copies_source(source.description_text, article.body_markdown):
            violations.append(
                ValidationViolation(
                    "COPIED_FROM_SOURCE",
                    "body_markdown",
                    f"the body copies {COPY_WINDOW_WORDS} or more consecutive "
                    "words from the announcement; rewrite that part in your "
                    "own words",
                )
            )

    def _check_roundup(
        self,
        article: Article,
        roundup: RoundupContext,
        violations: list[ValidationViolation],
    ) -> None:
        """Rules for a weekly roundup article."""
        _check_em_dash(article, violations)
        _check_diagram(article, violations)
        body = article.body_markdown
        for daily in roundup.dailies:
            candidates = [u for u in (daily.url, daily.source_link) if u]
            if candidates and not any(u in body for u in candidates):
                violations.append(
                    ValidationViolation(
                        "ROUNDUP_LINK_MISSING",
                        "body_markdown",
                        f"the roundup does not link to '{daily.title[:60]}'; "
                        f"include {candidates[0]}",
                    )
                )
        allowed = {r.link.rstrip("/") for r in roundup.regional}
        for url in _regional_section_links(body):
            if url.rstrip("/") not in allowed:
                violations.append(
                    ValidationViolation(
                        "ROUNDUP_REGIONAL_UNKNOWN",
                        "body_markdown",
                        f"the regional section links to '{url[:80]}', which is "
                        "not one of the regional announcements provided",
                    )
                )

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


def without_soft_violations(result: ValidationResult) -> ValidationResult:
    """Drop soft violations (a missing diagram) once repair has had its chance."""
    hard = tuple(v for v in result.violations if v.code not in SOFT_CODES)
    return ValidationResult(is_valid=not hard, violations=hard)


def _check_diagram(article: Article, violations: list[ValidationViolation]) -> None:
    """Require exactly one diagram marker line and a valid diagram spec."""
    markers = len(DIAGRAM_MARKER_RE.findall(article.body_markdown))
    if markers == 1 and parse_spec(article.diagram_json) is not None:
        return
    violations.append(
        ValidationViolation(
            DIAGRAM_MISSING,
            "body_markdown",
            "the article needs one architecture diagram: put the line "
            "'![Architecture diagram](diagram.png)' once in the body and add "
            "'---DIAGRAM---' after the body followed by a valid diagram JSON "
            "object using only the allowed service keys",
        )
    )


def _has_disclaimer(body: str) -> bool:
    """True when a line starts with the "Validate before you run" disclaimer."""
    return any(line.lstrip().startswith(DISCLAIMER_PREFIX) for line in body.splitlines())


def _check_em_dash(article: Article, violations: list[ValidationViolation]) -> None:
    for field_name, text in (
        ("title", article.title),
        ("description", article.description),
        ("body_markdown", article.body_markdown),
    ):
        if _EM_DASH in text:
            violations.append(
                ValidationViolation(
                    "EM_DASH",
                    field_name,
                    f"{field_name} contains an em dash; use a comma, colon or period",
                )
            )


def _last_line_links_to(body: str, link: str) -> bool:
    """True when the last non-empty body line has a Markdown link to ``link``."""
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return False
    wanted = link.strip().rstrip("/")
    return any(url.rstrip("/") == wanted for url in _MD_LINK_RE.findall(lines[-1]))


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _copies_source(source: str, body: str) -> bool:
    """True when ``COPY_WINDOW_WORDS`` consecutive words of ``source`` appear
    verbatim, in order, in ``body`` (case and punctuation ignored)."""
    source_words = _words(source)
    body_words = _words(body)
    n = COPY_WINDOW_WORDS
    if len(source_words) < n or len(body_words) < n:
        return False
    grams = {tuple(source_words[i : i + n]) for i in range(len(source_words) - n + 1)}
    return any(
        tuple(body_words[i : i + n]) in grams for i in range(len(body_words) - n + 1)
    )


def _regional_section_links(body: str) -> list[str]:
    """URLs of Markdown links inside a heading section that mentions "regional"."""
    links: list[str] = []
    in_section = False
    level = 0
    for line in body.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            depth = len(heading.group(1))
            if in_section and depth <= level:
                in_section = False
            if not in_section and "regional" in heading.group(2).casefold():
                in_section = True
                level = depth
            continue
        if in_section:
            links.extend(_MD_LINK_RE.findall(line))
    return links
