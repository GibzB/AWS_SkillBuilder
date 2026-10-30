"""Deterministic idempotency-key derivation for a Campaign_Day.

A single campaign day is uniquely identified by ``campaign_id`` and ``day``.
Before any publication the engine derives a stable :class:`Idempotency_Key`
from exactly those two inputs so that a repeated Scheduler invocation or a
manual run computes the *same* key and can be recognised as targeting the same
work item (Requirement 9.1). The key feeds the conditional PUBLISHED write and
the recorded ``idempotency_key`` attribute on the day item.

The derivation is a pure function of its inputs:

* identical ``(campaign_id, day)`` inputs always produce the same key
  (deterministic);
* any difference in ``campaign_id`` or ``day`` produces a different key
  (injective), which holds because the two fields are joined by a separator
  (``#``) that cannot appear in the day number, so the concatenation is an
  unambiguous encoding of the pair before hashing.

Design reference: design.md "Idempotency Design"; Requirement 9.1.
"""

from __future__ import annotations

import hashlib

__all__ = ["IDEMPOTENCY_SEPARATOR", "derive_idempotency_key"]

# Separator joining campaign_id and day. Chosen because a day is a non-negative
# integer whose decimal rendering never contains "#", so the joined string is
# an unambiguous encoding of the (campaign_id, day) pair. Any difference in
# either field therefore yields a different pre-image and a different digest.
IDEMPOTENCY_SEPARATOR = "#"


def derive_idempotency_key(campaign_id: str, day: int) -> str:
    """Return the deterministic SHA-256 Idempotency_Key for a campaign day.

    ``sha256(f"{campaign_id}#{day}")`` rendered as lowercase hex. Identical
    inputs yield identical keys; any difference in ``campaign_id`` or ``day``
    yields a different key (Requirement 9.1).
    """
    preimage = f"{campaign_id}{IDEMPOTENCY_SEPARATOR}{day}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()
