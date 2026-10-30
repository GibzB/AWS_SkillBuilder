"""Command_API: authenticated request interface for campaign operations.

The Command_API is the single entry point for campaign commands (create,
approve, run, retry, status, pause, resume). It enforces three cross-cutting
guarantees before any business logic runs (Requirements 11.6 to 11.9, 13.4,
14.6):

1. Authentication first: an unauthenticated request is rejected with an
   authentication-required response and no downstream component is invoked and
   no state is created or modified (Requirements 11.6, 11.7, 13.4, 14.6).
2. Replay protection: a state-changing request must carry a unique nonce and a
   fresh timestamp; a reused nonce or a stale/future timestamp is rejected
   before any downstream call.
3. Payload validation: the request payload is validated before any downstream
   component is invoked; an invalid payload is rejected without modifying state
   (Requirements 11.8, 11.9).

Only after all three pass does the handler dispatch to the Campaign_Manager or
Orchestrator. Deployment uses a Lambda Function URL with IAM (SigV4) auth, so
transport-level authentication is enforced by AWS; this handler additionally
requires an authenticated principal to be present and applies replay and
payload checks in application code so the guarantees hold regardless of
transport.

Design reference: design.md "Command_API" and "API Design"; Requirements 11,
13, 14.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.campaign.manager import (
    CampaignManager,
    CreateCampaignRequest,
)
from app.common.clock import Clock
from app.common.errors import (
    EngineError,
    StateTransitionError,
    ValidationError,
)
from app.orchestrator.orchestrator import ExecutionOutcome, Orchestrator

__all__ = [
    "REPLAY_WINDOW_SECONDS",
    "CommandRequest",
    "CommandResponse",
    "InMemoryNonceStore",
    "CommandAPI",
]

# Requests older or newer than this window (in seconds) are rejected as
# potential replays / clock-skew abuse.
REPLAY_WINDOW_SECONDS = 300

# State-changing actions require replay protection; read-only ones do not.
_STATE_CHANGING = frozenset(
    {"create", "approve", "run", "retry", "pause", "resume"}
)
_READ_ONLY = frozenset({"status"})
_KNOWN_ACTIONS = _STATE_CHANGING | _READ_ONLY


@dataclass(frozen=True)
class CommandRequest:
    """A normalized inbound command.

    ``authenticated`` reflects whether the transport (IAM/SigV4) established an
    authenticated principal. ``nonce`` and ``timestamp`` (ISO-8601) drive replay
    protection on state-changing actions. ``payload`` carries action-specific
    fields.
    """

    action: str
    authenticated: bool
    campaign_id: str = ""
    nonce: str = ""
    timestamp: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResponse:
    """A structured command outcome.

    ``status_code`` follows HTTP conventions (200 ok, 401 unauthenticated, 400
    validation, 409 conflict/replay, 404 not found). ``ok`` is True only for
    2xx. ``body`` carries the response payload.
    """

    status_code: int
    ok: bool
    body: Mapping[str, Any]


class InMemoryNonceStore:
    """Tracks seen nonces to reject replays (process-local, test-friendly).

    Production would back this with a short-TTL DynamoDB item keyed by nonce;
    the in-memory store keeps the replay logic testable and dependency-free.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def seen(self, nonce: str) -> bool:
        """Return True if the nonce was already used."""
        return nonce in self._seen

    def remember(self, nonce: str) -> None:
        """Record a nonce as used."""
        self._seen.add(nonce)


class CommandAPI:
    """Routes authenticated, validated, replay-protected campaign commands."""

    def __init__(
        self,
        manager: CampaignManager,
        orchestrator: Orchestrator,
        clock: Clock,
        nonce_store: InMemoryNonceStore | None = None,
    ) -> None:
        self._manager = manager
        self._orchestrator = orchestrator
        self._clock = clock
        self._nonces = nonce_store or InMemoryNonceStore()

    def handle(self, request: CommandRequest) -> CommandResponse:
        """Handle one command, enforcing auth, replay, and validation first.

        No downstream component is invoked until authentication, replay, and
        payload checks all pass (Requirements 11.6 to 11.9, 13.4, 14.6).
        """
        # 1. Authentication before any downstream effect (Req 11.6, 11.7, 13.4).
        if not request.authenticated:
            return CommandResponse(
                401, False, {"error": "authentication is required"}
            )

        # 2. Known action.
        if request.action not in _KNOWN_ACTIONS:
            return CommandResponse(
                400, False, {"error": f"unknown action '{request.action}'"}
            )

        # 3. Replay protection on state-changing actions, before downstream.
        if request.action in _STATE_CHANGING:
            replay_error = self._check_replay(request)
            if replay_error is not None:
                return replay_error

        # 4. Payload validation before any downstream call (Req 11.8, 11.9).
        validation_error = self._validate_payload(request)
        if validation_error is not None:
            return validation_error

        # 5. Dispatch. Record the nonce only once we are about to act.
        if request.action in _STATE_CHANGING and request.nonce:
            self._nonces.remember(request.nonce)

        try:
            return self._dispatch(request)
        except ValidationError as error:
            # Not-found and field errors surface as 404/400.
            code = 404 if "campaign_id" in error.fields else 400
            return CommandResponse(code, False, {"error": error.message})
        except StateTransitionError as error:
            return CommandResponse(409, False, {"error": error.message})
        except EngineError as error:
            return CommandResponse(400, False, {"error": error.message})

    # ------------------------------------------------------------------
    # Cross-cutting checks
    # ------------------------------------------------------------------

    def _check_replay(self, request: CommandRequest) -> CommandResponse | None:
        """Reject reused nonces and stale/future timestamps (returns error or None)."""
        if not request.nonce or not request.timestamp:
            return CommandResponse(
                400,
                False,
                {"error": "nonce and timestamp are required for this action"},
            )
        if self._nonces.seen(request.nonce):
            return CommandResponse(
                409, False, {"error": "duplicate request (nonce already used)"}
            )
        try:
            ts = datetime.fromisoformat(request.timestamp)
        except ValueError:
            return CommandResponse(
                400, False, {"error": "timestamp is not valid ISO-8601"}
            )
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = self._clock.now()
        skew = abs((now - ts).total_seconds())
        if skew > REPLAY_WINDOW_SECONDS:
            return CommandResponse(
                409,
                False,
                {"error": "request timestamp is outside the allowed window"},
            )
        return None

    def _validate_payload(
        self, request: CommandRequest
    ) -> CommandResponse | None:
        """Validate action-specific payload shape before any downstream call."""
        if request.action == "create":
            missing = [
                f
                for f in ("name", "total_days", "start_date", "execution_time", "topics")
                if f not in request.payload
            ]
            if missing:
                return CommandResponse(
                    400,
                    False,
                    {"error": f"missing required fields: {', '.join(missing)}"},
                )
        elif request.action in ("approve", "run", "retry", "pause", "resume", "status"):
            if not request.campaign_id.strip():
                return CommandResponse(
                    400, False, {"error": "campaign_id is required"}
                )
        return None

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, request: CommandRequest) -> CommandResponse:
        handlers: Mapping[str, Callable[[CommandRequest], CommandResponse]] = {
            "create": self._create,
            "approve": self._approve,
            "run": self._run,
            "retry": self._run,  # retry re-runs the eligible (FAILED) day
            "pause": self._pause,
            "resume": self._resume,
            "status": self._status,
        }
        return handlers[request.action](request)

    def _create(self, request: CommandRequest) -> CommandResponse:
        p = request.payload
        req = CreateCampaignRequest(
            name=str(p["name"]),
            total_days=int(p["total_days"]),
            start_date=str(p["start_date"]),
            execution_time=str(p["execution_time"]),
            topics=tuple(str(t) for t in p["topics"]),
            description=str(p.get("description", "")),
            content_style=str(p.get("content_style", "")),
            timezone=str(p.get("timezone", "")),
            notification_email=str(p.get("notification_email", "")),
            notification_phone=p.get("notification_phone"),
            publisher_selection=str(p.get("publisher_selection", "")),
            campaign_id=str(p.get("campaign_id", "")),
        )
        campaign = self._manager.create_campaign(req)
        return CommandResponse(
            201,
            True,
            {"campaign_id": campaign.campaign_id, "state": campaign.campaign_state.value},
        )

    def _approve(self, request: CommandRequest) -> CommandResponse:
        campaign = self._manager.approve(request.campaign_id)
        return CommandResponse(200, True, {"state": campaign.campaign_state.value})

    def _pause(self, request: CommandRequest) -> CommandResponse:
        campaign = self._manager.pause(request.campaign_id)
        return CommandResponse(200, True, {"state": campaign.campaign_state.value})

    def _resume(self, request: CommandRequest) -> CommandResponse:
        campaign = self._manager.resume(request.campaign_id)
        return CommandResponse(200, True, {"state": campaign.campaign_state.value})

    def _run(self, request: CommandRequest) -> CommandResponse:
        # Reject a manual run when no eligible day exists, WITHOUT invoking the
        # Orchestrator's publish path (Requirement 11.5). The Orchestrator
        # returns NO_ELIGIBLE_DAY / ALREADY_COMPLETE without publishing, which
        # we surface as a rejection.
        result = self._orchestrator.execute(request.campaign_id)
        if result.outcome in (
            ExecutionOutcome.NO_ELIGIBLE_DAY,
            ExecutionOutcome.ALREADY_COMPLETE,
        ):
            return CommandResponse(
                409,
                False,
                {"error": "no eligible campaign day", "outcome": result.outcome.value},
            )
        return CommandResponse(
            200,
            True,
            {
                "outcome": result.outcome.value,
                "day": result.day,
                "publication_url": result.publication_url,
            },
        )

    def _status(self, request: CommandRequest) -> CommandResponse:
        status = self._manager.status(request.campaign_id)
        return CommandResponse(
            200,
            True,
            {
                "campaign_id": status.campaign_id,
                "state": status.campaign_state.value,
                "total_days": status.total_days,
                "current_day": status.current_day,
                "completed_days": status.completed_days,
                "failed_days": status.failed_days,
                "next_execution": status.next_execution,
                "last_execution": status.last_execution,
                "publication_urls": list(status.publication_urls),
            },
        )
