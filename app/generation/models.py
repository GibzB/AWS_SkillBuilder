"""Shared content-generation contracts: Article, GenerationSpec, and limits.

This module is the single source of truth for the structured article contract
produced by the Content_Generator and consumed by the Content_Validator and the
Publishing_Adapter (Requirements 4.3, 6.1). It also defines the article length
limits once (title <= 255, description <= 512, 1..5 tags) so the generator,
validator, and prompt all agree on the same bounds (Requirements 4.2, 5.1 to
5.8), and the :class:`GenerationSpec` that carries the bounded, first-person
generation request (Requirements 4.2, 4.4).

The module is dependency-free (standard library only) so it can be imported by
generation, validation, and publishing without creating cycles.

Design reference: design.md "Shared Contracts" and "Bedrock Design";
Requirements 4.2, 4.3, 4.4, 20.1, 20.2.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "TITLE_MAX",
    "DESCRIPTION_MAX",
    "TAGS_MIN",
    "TAGS_MAX",
    "DIFFICULTY",
    "VOICE",
    "Article",
    "GenerationSpec",
]

# Article length limits (Requirements 4.2, 4.3, 5.1 to 5.8). Defined once here.
TITLE_MAX = 255
DESCRIPTION_MAX = 512
TAGS_MIN = 1
TAGS_MAX = 5

# Fixed generation-request descriptors (Requirements 4.2, 4.4).
DIFFICULTY = "beginner_to_lower_intermediate"
VOICE = "first_person"


@dataclass(frozen=True)
class Article:
    """The structured content produced by generation (Requirement 4.3).

    Frozen so it is an immutable snapshot handed from the generator to the
    validator and publisher. Field bounds (title 1..255, description 1..512,
    non-empty body, 1..5 tags, non-empty learning_takeaway) are guaranteed by
    the generator's parser and re-checked deterministically by the validator.
    ``executed_in_aws`` is the anti-fabrication flag and defaults to False for
    the MVP (Requirements 4.5, 5.10).
    """

    campaign_id: str
    day: int
    topic: str
    title: str
    description: str
    body_markdown: str
    tags: tuple[str, ...]
    learning_takeaway: str
    executed_in_aws: bool = False


@dataclass(frozen=True)
class GenerationSpec:
    """The bounded, first-person specification sent to the generator.

    Carries everything Amazon Bedrock needs for one day's article: the approved
    topic, day number, campaign identifier, the fixed Beginner to
    Lower-Intermediate difficulty, a first-person voice indicator, the
    anti-fabrication ``executed_in_aws`` flag (False for the MVP), and the
    article length limits (Requirements 4.2, 4.4). ``difficulty`` and ``voice``
    default to the fixed descriptors so callers cannot accidentally weaken the
    contract.
    """

    campaign_id: str
    day: int
    topic: str
    title_max: int = TITLE_MAX
    description_max: int = DESCRIPTION_MAX
    tags_max: int = TAGS_MAX
    difficulty: str = DIFFICULTY
    voice: str = VOICE
    executed_in_aws: bool = False
