"""Shared campaign domain models: state enums and single-table item dataclasses.

This module is the shared home for the campaign state enums
(:class:`CampaignState`, :class:`DayState`) and the strongly typed item
dataclasses (:class:`Campaign`, :class:`CampaignDay`) that the campaign store
marshals to and from DynamoDB. It is deliberately dependency-free (standard
library only) so both the store client (``store.py``) and the transition rules
(``state.py``) can import from a single source without collision.

Single-table design (see design.md, Data Models):

- Campaign item:      PK ``CAMPAIGN#<campaign_id>``  SK ``META``
- Campaign_Day item:  PK ``CAMPAIGN#<campaign_id>``  SK ``DAY#<zero-padded day>``

Day numbers are zero-padded to width 4 in the sort key (for example
``DAY#0007``) so day items sort numerically under a campaign partition, which
makes "lowest eligible day" a simple ordered scan (Requirements 3.1, 3.2).

Design references: Requirements 1.1, 1.7, 3.1, 3.2, 9.2, 12.7, 13.1 and the
design "Data Models" and "State Machines" sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "CampaignState",
    "DayState",
    "DAY_SK_WIDTH",
    "Campaign",
    "CampaignDay",
    "campaign_pk",
    "campaign_meta_sk",
    "day_sk",
    "day_number_from_sk",
]

# Zero-padding width for the ``DAY#`` sort key. A campaign has at most 365 days
# (Requirement 1.4), so width 4 keeps every key numerically ordered as a string.
DAY_SK_WIDTH = 4


class CampaignState(str, Enum):
    """Lifecycle state of a Campaign (Requirements 15, 16.1, 16.2).

    Subclasses ``str`` so the value marshals directly to a DynamoDB string
    attribute and compares cleanly against stored values.
    """

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class DayState(str, Enum):
    """Lifecycle state of a Campaign_Day (Requirements 16.3 to 16.6).

    Subclasses ``str`` so the value marshals directly to a DynamoDB string
    attribute and compares cleanly against stored values.
    """

    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    READY = "READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Key helpers (single-table design)
# ---------------------------------------------------------------------------


def campaign_pk(campaign_id: str) -> str:
    """Build the partition key for every item belonging to a campaign."""
    return f"CAMPAIGN#{campaign_id}"


def campaign_meta_sk() -> str:
    """Build the sort key for the campaign META item."""
    return "META"


def day_sk(day: int) -> str:
    """Build the zero-padded ``DAY#`` sort key for a day number.

    Example: ``day_sk(7) == "DAY#0007"``. Zero-padding to
    :data:`DAY_SK_WIDTH` keeps day items numerically ordered under the
    campaign partition (Requirements 3.1, 3.2).
    """
    return f"DAY#{day:0{DAY_SK_WIDTH}d}"


def day_number_from_sk(sk: str) -> int:
    """Recover the integer day number from a ``DAY#`` sort key.

    Inverse of :func:`day_sk`. Raises :class:`ValueError` if ``sk`` is not a
    well-formed day sort key.
    """
    prefix = "DAY#"
    if not sk.startswith(prefix):
        raise ValueError(f"Not a day sort key: {sk!r}")
    return int(sk[len(prefix) :])


# ---------------------------------------------------------------------------
# Item dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Campaign:
    """The campaign META item (Requirements 1.1, 1.7, 13, 15).

    Frozen so callers treat it as an immutable snapshot of stored state; the
    store produces new instances on read and accepts instances to write.
    """

    campaign_id: str
    name: str
    campaign_state: CampaignState
    total_days: int
    timezone: str
    start_date: str
    execution_time: str
    created_at: str
    updated_at: str
    description: str = ""
    content_style: str = ""
    notification_email: str = ""
    notification_phone: str | None = None
    publisher_selection: str = ""


@dataclass(frozen=True)
class CampaignDay:
    """A single Campaign_Day item (Requirements 3, 5, 7, 9, 12, 13).

    Frozen so callers treat it as an immutable snapshot. Optional article and
    publication fields are empty/None until the corresponding stage records
    them; ``attempt_count`` starts at 0 and is incremented before each attempt
    (Requirement 12.7).
    """

    campaign_id: str
    day: int
    day_state: DayState
    updated_at: str
    topic: str = ""
    article_id: str = ""
    title: str = ""
    description: str = ""
    body_markdown: str = ""
    tags: tuple[str, ...] = ()
    publication_url: str = ""
    published_at: str | None = None
    attempt_count: int = 0
    last_error: str = ""
    idempotency_key: str = ""
    correlation_id: str = ""
