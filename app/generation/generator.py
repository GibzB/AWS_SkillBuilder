"""Content_Generator: turn an approved topic into a structured Article.

The generator is the Amazon Bedrock boundary (Requirements 4.2 to 4.7, 20.1,
20.2). It builds a bounded, first-person generation request from a
:class:`GenerationSpec`, invokes an injected Bedrock client under a 120-second
timeout, parses the model's JSON response into a bounded :class:`Article`, and
classifies anything malformed, out-of-bounds, or slow as a
:class:`GenerationError`. It never publishes and holds no state beyond its
injected collaborators.

The Bedrock client is injected behind the small :class:`BedrockClient`
protocol returning the raw model text for a prompt, so the generator is
unit-testable without real AWS and without coupling to a specific model's wire
format. The 120-second bound is enforced by an injected
:class:`TimeoutRunner`, which production wires to a thread-based timeout and
tests wire to a deterministic fake.

Design reference: design.md "Content_Generator" and "Bedrock Design";
Requirements 4.2, 4.3, 4.4, 4.5, 4.6, 4.7.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from importlib import resources
from typing import Any, Callable, Protocol, TypeVar

from app.generation.models import (
    TAGS_MAX,
    TAGS_MIN,
    Article,
    GenerationSpec,
)
from app.common.errors import GenerationError

__all__ = [
    "GENERATION_TIMEOUT_SECONDS",
    "PROMPT_RESOURCE",
    "MAX_OUTPUT_TOKENS",
    "BedrockClient",
    "TimeoutRunner",
    "ThreadTimeoutRunner",
    "ContentGenerator",
    "load_prompt_template",
]

# Hard timeout for a single Bedrock call (Requirement 4.7).
GENERATION_TIMEOUT_SECONDS = 120.0

# Bounded output size to minimize token cost (Bedrock Design). A short article
# fits comfortably; a runaway response is truncated by the model, then rejected
# by parsing/validation if malformed.
MAX_OUTPUT_TOKENS = 1500

# Versioned prompt asset packaged with the application (Requirement 20.1).
PROMPT_RESOURCE = ("app.prompts", "article_v1.md")

# Delimiter separating the metadata JSON header from the raw Markdown body in
# the two-part output format. Matched on its own line, tolerating surrounding
# whitespace and optional surrounding dashes the model may add.
_BODY_DELIMITER_RE = re.compile(r"^[ \t]*-{3,}\s*BODY\s*-{3,}[ \t]*$", re.MULTILINE)

T = TypeVar("T")


class BedrockClient(Protocol):
    """Minimal structural type for the injected model client.

    ``invoke`` sends a fully rendered prompt and returns the raw model text
    (expected to be a single JSON object per the prompt's output contract).
    The real implementation wraps boto3 ``bedrock-runtime`` InvokeModel for the
    configured model; a fake returns canned text in tests.
    """

    def invoke(self, prompt: str, max_tokens: int) -> str:
        """Return the raw generated text for ``prompt``."""
        ...


class TimeoutRunner(Protocol):
    """Runs a callable under a wall-clock timeout.

    Injected so the 120-second bound is enforced in production but made
    deterministic in tests. Implementations raise :class:`TimeoutError` when
    the callable does not complete within ``seconds``.
    """

    def run(self, seconds: float, func: Callable[[], T]) -> T:
        """Run ``func`` and return its result, or raise on timeout."""
        ...


class ThreadTimeoutRunner:
    """Thread-based timeout runner for production use.

    Runs the callable in a worker thread and abandons the wait after
    ``seconds``. The underlying request may continue briefly in the background,
    but the generator treats the timeout as a generation failure and produces
    no article, which is the required behavior (Requirement 4.7).
    """

    def run(self, seconds: float, func: Callable[[], T]) -> T:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                return future.result(timeout=seconds)
            except FuturesTimeout as error:
                raise TimeoutError(
                    f"operation exceeded {seconds:.0f}s timeout"
                ) from error


def load_prompt_template() -> str:
    """Load the versioned prompt template packaged with the application."""
    package, filename = PROMPT_RESOURCE
    return resources.files(package).joinpath(filename).read_text(encoding="utf-8")


class ContentGenerator:
    """Builds the request, invokes Bedrock, and parses a bounded Article."""

    def __init__(
        self,
        client: BedrockClient,
        timeout_runner: TimeoutRunner | None = None,
        prompt_template: str | None = None,
        timeout_seconds: float = GENERATION_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._timeout_runner: TimeoutRunner = timeout_runner or ThreadTimeoutRunner()
        self._prompt_template = prompt_template or load_prompt_template()
        self._timeout_seconds = timeout_seconds

    def generate(self, spec: GenerationSpec) -> Article:
        """Generate one Article for ``spec`` or raise a :class:`GenerationError`.

        Builds the bounded first-person request (Requirements 4.2, 4.4), invokes
        Bedrock under the 120-second timeout (Requirement 4.7), and parses the
        response into a bounded Article, classifying malformed or out-of-bounds
        output as a generation failure (Requirements 4.3, 4.6). No article is
        produced on any failure.
        """
        prompt = self.build_prompt(spec)

        try:
            raw = self._timeout_runner.run(
                self._timeout_seconds,
                lambda: self._client.invoke(prompt, MAX_OUTPUT_TOKENS),
            )
        except TimeoutError as error:
            raise GenerationError(
                message=f"Bedrock did not respond within {self._timeout_seconds:.0f}s",
                reason="timeout",
                timed_out=True,
            ) from error
        except Exception as error:  # noqa: BLE001 - classified as generation failure
            raise GenerationError(
                message="Bedrock invocation failed",
                reason="unavailable",
            ) from error

        return self._parse(raw, spec)

    def build_prompt(self, spec: GenerationSpec) -> str:
        """Render the versioned template with the spec's bounded parameters.

        The request carries the approved topic, day number, campaign identifier,
        difficulty, first-person voice, the anti-fabrication ``executed_in_aws``
        flag, and the length limits (Requirements 4.2, 4.4). The rendered input
        JSON is appended so the model receives the exact structured task.
        """
        # Substitute only our named placeholders. We deliberately avoid
        # str.format(), because the prompt asset contains literal JSON braces
        # ({ "title": ... }) that str.format() would misinterpret as fields and
        # raise KeyError. Explicit replacement leaves literal braces untouched.
        substitutions = {
            "{title_max}": str(spec.title_max),
            "{description_max}": str(spec.description_max),
            "{tags_min}": str(TAGS_MIN),
            "{tags_max}": str(spec.tags_max),
        }
        template = self._prompt_template
        for token, value in substitutions.items():
            template = template.replace(token, value)
        request_input = {
            "campaign_id": spec.campaign_id,
            "day": spec.day,
            "topic": spec.topic,
            "difficulty": spec.difficulty,
            "voice": spec.voice,
            "executed_in_aws": spec.executed_in_aws,
            "article_requirements": {
                "title_max_chars": spec.title_max,
                "description_max_chars": spec.description_max,
                "max_tags": spec.tags_max,
            },
        }
        return f"{template}\n\n# Task input\n\n```json\n{json.dumps(request_input)}\n```\n"

    def _parse(self, raw: str, spec: GenerationSpec) -> Article:
        """Parse raw model text into a bounded Article or raise on any defect.

        Supports the two-part output format: a small JSON metadata header
        (title, description, tags, learning_takeaway) followed by a
        ``---BODY---`` delimiter and the raw Markdown body. Keeping the body
        OUTSIDE the JSON avoids the double-escaping that made small models emit
        invalid JSON when the body contained code/diagrams (the intermittent
        "not valid JSON" failures). For backward compatibility, a legacy single
        JSON object containing ``body_markdown`` is still accepted.
        """
        header_text, body = self._split_body(raw)
        payload = self._extract_json(header_text)

        title = self._require_str(payload, "title")
        description = self._require_str(payload, "description")
        takeaway = self._require_str(payload, "learning_takeaway")
        tags = self._require_tags(payload)

        # Two-part body (from the delimiter) takes precedence; otherwise fall
        # back to a legacy body_markdown field inside the JSON.
        if body is None:
            body = self._require_str(payload, "body_markdown")

        if len(title.strip()) < 1 or len(title) > spec.title_max:
            raise GenerationError(
                message="generated title is empty or exceeds the limit",
                reason="out_of_bounds",
            )
        if len(description.strip()) < 1 or len(description) > spec.description_max:
            raise GenerationError(
                message="generated description is empty or exceeds the limit",
                reason="out_of_bounds",
            )
        if len(body.strip()) < 1:
            raise GenerationError(
                message="generated body is empty", reason="empty_field"
            )

        return Article(
            campaign_id=spec.campaign_id,
            day=spec.day,
            topic=spec.topic,
            title=title,
            description=description,
            body_markdown=body,
            tags=tags,
            learning_takeaway=takeaway,
            executed_in_aws=spec.executed_in_aws,
        )

    @staticmethod
    def _split_body(raw: str) -> tuple[str, str | None]:
        """Split a two-part response into (header_text, body) on the delimiter.

        Returns the text before the first ``---BODY---`` line as the header and
        everything after it (stripped) as the body. When the delimiter is
        absent, returns (raw, None) so the legacy single-JSON format still
        parses. A blank body after the delimiter is treated as absent so the
        caller's empty-body check reports it clearly.
        """
        match = _BODY_DELIMITER_RE.search(raw)
        if match is None:
            return raw, None
        header = raw[: match.start()]
        body = raw[match.end() :].strip()
        return header, (body if body else None)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        """Extract a JSON object from a model response, tolerantly.

        Small language models occasionally wrap the required JSON in a code
        fence, add a leading sentence ("Here is the article:"), append trailing
        prose, or place the fence mid-text. Rather than demand a perfectly clean
        response (which caused intermittent "not valid JSON" generation
        failures in production), this:

        1. strips a surrounding ``` fence anywhere in the text, then tries a
           direct parse;
        2. if that fails, finds the first ``{`` and brace-matches to its
           closing ``}`` (respecting strings/escapes) and parses that slice.

        Crucially, the article's ``body_markdown`` often itself contains a
        fenced code block (an ASCII diagram), so the response can hold several
        ``` sequences. Position-based fence stripping breaks on that. Instead we
        rely on brace-matching the outer ``{...}`` object, which treats any
        inner ``` as ordinary string content. Only when no balanced JSON object
        can be recovered is the response classified as a parse-error.
        """
        text = raw.strip()

        # 1. Direct parse: a clean, unfenced JSON object.
        obj = ContentGenerator._try_parse_object(text)
        if obj is not None:
            return obj

        # 2. Brace-match the first balanced {...} object anywhere in the text.
        #    This is the robust path: it ignores any surrounding code fence and
        #    any inner ``` inside string values (e.g. a diagram in
        #    body_markdown), because the scanner tracks JSON string state.
        sliced = ContentGenerator._first_json_object(text)
        if sliced is not None:
            obj = ContentGenerator._try_parse_object(sliced)
            if obj is not None:
                return obj

        raise GenerationError(
            message="Bedrock response did not contain a parseable JSON object",
            reason="parse_error",
        )

    @staticmethod
    def _try_parse_object(text: str) -> dict[str, Any] | None:
        """Return the parsed object if ``text`` is a JSON object, else None."""
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _first_json_object(text: str) -> str | None:
        """Return the first balanced ``{...}`` substring, or None.

        Scans for the first ``{`` and tracks brace depth, correctly skipping
        braces inside JSON strings (honoring backslash escapes), so trailing
        prose after the object is ignored.
        """
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise GenerationError(
                message=f"generated field '{key}' is missing or empty",
                reason="empty_field",
            )
        return value

    @staticmethod
    def _require_tags(payload: dict[str, Any]) -> tuple[str, ...]:
        value = payload.get("tags")
        if not isinstance(value, list):
            raise GenerationError(
                message="generated tags are missing or not a list",
                reason="empty_field",
            )
        tags = tuple(str(t) for t in value if isinstance(t, str) and t.strip())
        if len(tags) < TAGS_MIN or len(tags) > TAGS_MAX:
            raise GenerationError(
                message=f"generated tag count {len(tags)} is outside {TAGS_MIN}..{TAGS_MAX}",
                reason="out_of_bounds",
            )
        return tags
