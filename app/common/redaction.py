"""Redaction of sensitive values before any log or metric is emitted.

The engine must never expose passwords, cookies, access tokens, session
headers, or personal authentication details in logs, metrics, or notifications
(Requirements 8.1, 10.4, 17.4). When a payload is found to contain a value from
an excluded category, the value is removed before emission and a valueless
redaction event is recorded indicating that a sensitive value was removed,
without the value itself appearing anywhere in the output (Requirement 17.5).

Detection is deterministic and testable. It works two ways:

* by key name: any mapping key whose name matches a sensitive pattern
  (``password``, ``cookie``, ``token``, ``authorization``, ``set-cookie``,
  ``session``, ``secret``, ...) has its value redacted regardless of the value;
* by obvious value shape: standalone string values that clearly look like
  bearer tokens or ``Set-Cookie`` headers are redacted even when the key name
  is innocuous, so a secret slipped into a free-text field is still removed.

Redaction walks nested mappings and sequences so a secret nested anywhere in a
structured payload is caught. The redacted value is replaced by a fixed,
valueless placeholder that carries no information about the original.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "REDACTED_PLACEHOLDER",
    "SENSITIVE_KEY_PATTERN",
    "RedactionEvent",
    "RedactionResult",
    "redact",
]

# Fixed, information-free replacement for any redacted value. It is a constant
# so no fragment of the original value can ever leak through the placeholder.
REDACTED_PLACEHOLDER: Final[str] = "[REDACTED]"

# Key-name patterns for the excluded categories: passwords, cookies, access
# tokens, session headers, and personal authentication details. Matched
# case-insensitively against the full key name (substring match) so that
# variants like ``builder_id_password`` or ``x-session-token`` are caught.
_SENSITIVE_KEY_TERMS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "cookie",  # covers "cookie" and "set-cookie"
    "token",  # covers "access_token", "session_token", "refresh_token"
    "authorization",
    "auth",  # covers "auth_header", "authtoken", personal auth detail
    "session",
    "secret",
    "credential",
    "apikey",
    "api_key",
    "bearer",
    "mfa",
    "otp",
)

SENSITIVE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(term) for term in _SENSITIVE_KEY_TERMS),
    re.IGNORECASE,
)

# Obvious value shapes that indicate a secret even under an innocuous key name.
# Kept conservative and deterministic to avoid redacting ordinary text.
_SENSITIVE_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # "Bearer <token>" authorization header value.
    re.compile(r"^\s*Bearer\s+\S+", re.IGNORECASE),
    # "Basic <base64>" authorization header value.
    re.compile(r"^\s*Basic\s+\S+", re.IGNORECASE),
    # A Set-Cookie / cookie assignment such as "session=abc123; Path=/".
    re.compile(r"(?i)\b(?:session|sid|token|auth)\w*=[^;\s]+"),
    # A JWT-shaped value: three dot-separated base64url segments.
    re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),
)


def _key_is_sensitive(key: str) -> bool:
    """Return True when a mapping key names an excluded category."""
    return SENSITIVE_KEY_PATTERN.search(key) is not None


def _value_is_sensitive(value: str) -> bool:
    """Return True when a standalone string value clearly looks like a secret."""
    return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)


@dataclass(frozen=True)
class RedactionEvent:
    """A valueless record that a sensitive value was removed.

    It names the dotted path to the removed value and why it was redacted
    (``"key"`` for a sensitive key name, ``"value"`` for a sensitive value
    shape) but never contains the removed value itself (Requirement 17.5).
    """

    path: str
    reason: str


@dataclass(frozen=True)
class RedactionResult:
    """The outcome of redacting a payload.

    ``payload`` is the sanitized copy safe to emit; ``events`` lists one
    valueless :class:`RedactionEvent` per removed value. ``redacted`` is True
    iff at least one value was removed.
    """

    payload: object
    events: tuple[RedactionEvent, ...]

    @property
    def redacted(self) -> bool:
        """True when at least one sensitive value was removed."""
        return len(self.events) > 0


def _join_path(prefix: str, segment: str) -> str:
    """Build a dotted path, avoiding a leading dot at the root."""
    return segment if not prefix else f"{prefix}.{segment}"


def _redact_node(node: object, path: str, events: list[RedactionEvent]) -> object:
    """Recursively redact a payload node, appending events for removals.

    Mappings, lists, and tuples are traversed structurally. Strings are
    checked against the sensitive-value patterns. All other scalar types are
    returned unchanged. Key-name based redaction happens at the mapping level
    so a sensitive key redacts its whole subtree.
    """
    if isinstance(node, dict):
        result: dict[object, object] = {}
        for key, value in node.items():
            child_path = _join_path(path, str(key))
            if isinstance(key, str) and _key_is_sensitive(key):
                events.append(RedactionEvent(path=child_path, reason="key"))
                result[key] = REDACTED_PLACEHOLDER
            else:
                result[key] = _redact_node(value, child_path, events)
        return result
    if isinstance(node, (list, tuple)):
        items = [
            _redact_node(item, _join_path(path, str(index)), events)
            for index, item in enumerate(node)
        ]
        return type(node)(items) if isinstance(node, tuple) else items
    if isinstance(node, str) and _value_is_sensitive(node):
        events.append(RedactionEvent(path=path or "$", reason="value"))
        return REDACTED_PLACEHOLDER
    return node


def redact(payload: object) -> RedactionResult:
    """Return a redacted copy of ``payload`` plus valueless redaction events.

    The input is never mutated. Any value in an excluded category is replaced
    by :data:`REDACTED_PLACEHOLDER`, and one :class:`RedactionEvent` is
    recorded per removal. The removed value never appears in the result or in
    any event (Requirements 17.4, 17.5, 8.1, 10.4).
    """
    events: list[RedactionEvent] = []
    sanitized = _redact_node(payload, "", events)
    return RedactionResult(payload=sanitized, events=tuple(events))
