# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Computer-use approval gate (Issue #7 / B4).

A state machine that interrupts high-risk computer-use actions for human
approval and re-verifies the frozen identity on resume (cangming #596 / #594).
The defining invariant — the SEV-1 cangming fixed — is structural here:
``deny`` is its own status, never an approve status, so a denied action can
never reach execution through a semantic fallback.

- ``request`` records a pending ticket bound to the frozen identity + window;
- ``decide`` moves it to ``approved_once`` / ``approved_session`` / ``denied``;
- ``verify_resume`` re-checks the current identity against the frozen one: a
  mismatch (cross-window / restarted process) refuses with ``None`` (the caller
  treats that as ``not_executed`` + retryable, forcing a re-observation);
- ``approved_session`` lets later same-action requests within the session pass
  without a fresh prompt;
- the registry is FIFO-bounded (256, cangming's cap).
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from computer_use.actions import Action
from computer_use.input_owner import FrozenSessionIdentity, WindowIdentity

ApprovalOption: type = Literal["approve_once", "approve_session", "deny"]
ApprovalStatus: type = Literal["pending", "approved_once", "approved_session", "denied"]

_MAX_PENDING_TICKETS = 256


@dataclass(frozen=True, slots=True)
class ApprovalTicket:
    """One approval ticket bound to a frozen identity.

    Attributes:
        id: Server-assigned ticket id.
        action: The action awaiting approval.
        identity: The frozen session identity at request time.
        window: The frozen window identity at request time.
        status: ``pending`` / ``approved_once`` / ``approved_session`` / ``denied``.
    """

    id: str
    action: Action
    identity: FrozenSessionIdentity
    window: WindowIdentity
    status: ApprovalStatus = "pending"


class ApprovalGate:
    """Interrupt-then-resume approval state machine.

    ``deny`` is structurally distinct from ``approve_once`` / ``approve_session``:
    a denied ticket's status is ``denied`` and the consumer must treat that as a
    cancellation, never an execution. There is no path by which ``decide(deny)``
    resolves to an approve status.
    """

    def __init__(self, *, max_pending: int = _MAX_PENDING_TICKETS) -> None:
        self._lock = threading.Lock()
        self._tickets: "OrderedDict[str, ApprovalTicket]" = OrderedDict()
        self._max_pending = max_pending
        # Session-wide approvals: (session_id, action_verb) → True once granted.
        self._session_grants: set[tuple[str, str]] = set()

    # -- request / decide ---------------------------------------------

    def request(
        self,
        *,
        action: Action,
        identity: FrozenSessionIdentity,
        window: WindowIdentity,
    ) -> ApprovalTicket:
        """Record a pending approval ticket bound to the frozen identity.

        If a prior ``approve_session`` covered ``(session_id, action)`` the
        ticket is returned already-approved, so high-risk actions do not re-prompt
        within an approved session.

        Args:
            action: The high-risk action awaiting approval.
            identity: The frozen session identity.
            window: The frozen window identity.

        Returns:
            The recorded ticket (pending, unless a session grant covers it).
        """
        ticket_id = uuid.uuid4().hex
        ticket = ApprovalTicket(
            id=ticket_id, action=action, identity=identity, window=window,
        )
        with self._lock:
            # A prior session-wide approval for this (session, action) short-circuits.
            if (identity.session_id, action.action) in self._session_grants:
                ticket = ApprovalTicket(
                    id=ticket_id, action=action, identity=identity, window=window,
                    status="approved_session",
                )
            self._tickets[ticket_id] = ticket
            while len(self._tickets) > self._max_pending:
                self._tickets.popitem(last=False)
        return ticket

    def decide(self, ticket_id: str, option: ApprovalOption) -> ApprovalTicket:
        """Move a pending ticket to a terminal status.

        ``deny`` resolves to ``denied`` (a cancellation), never an approve
        status. ``approve_session`` records a session-wide grant for future
        same-action requests.

        Args:
            ticket_id: The ticket to resolve.
            option: ``approve_once`` / ``approve_session`` / ``deny``.

        Returns:
            The resolved ticket.

        Raises:
            KeyError: If the ticket is unknown.
            ValueError: If the ticket is no longer pending.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise KeyError(f"unknown approval ticket {ticket_id!r}")
            if ticket.status != "pending":
                raise ValueError(
                    f"ticket {ticket_id!r} already resolved as {ticket.status!r}"
                )
            status: ApprovalStatus
            if option == "deny":
                status = "denied"
            elif option == "approve_once":
                status = "approved_once"
            else:  # approve_session
                status = "approved_session"
                self._session_grants.add(
                    (ticket.identity.session_id, ticket.action.action)
                )
            resolved = ApprovalTicket(
                id=ticket.id, action=ticket.action,
                identity=ticket.identity, window=ticket.window, status=status,
            )
            self._tickets[ticket_id] = resolved
            return resolved

    # -- resume re-verification ----------------------------------------

    def verify_resume(
        self,
        ticket_id: str,
        *,
        current_identity: FrozenSessionIdentity,
        current_window: WindowIdentity,
    ) -> ApprovalTicket | None:
        """Re-verify the bound identity at resume.

        A mismatch (the window was switched, the process restarted, or the
        session identity changed) refuses the action: returns ``None`` so the
        caller treats it as ``not_executed`` + retryable, forcing a fresh
        observation rather than replaying input against the wrong target.

        Args:
            ticket_id: The ticket being resumed.
            current_identity: The identity observed at resume.
            current_window: The window observed at resume.

        Returns:
            The resolved ticket iff identity+window match the frozen binding
            and the ticket is approved; ``None`` otherwise.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                return None
            if ticket.status not in ("approved_once", "approved_session"):
                return None
            if ticket.identity != current_identity:
                return None
            if ticket.window != current_window:
                return None
            return ticket


__all__ = [
    "ApprovalGate",
    "ApprovalOption",
    "ApprovalStatus",
    "ApprovalTicket",
]
