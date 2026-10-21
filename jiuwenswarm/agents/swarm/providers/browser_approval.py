# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Browser-use approval gate (Issue #3 / A2).

Interrupts high-risk browser actions (``navigate`` / ``evaluate``) for human
approval and re-verifies the bound target identity on resume (cangming #334 /
#345). The bound identity is a ``BrowserTargetIdentity`` (Chrome instance +
CDP ``TargetID`` + origin): a tab switch within the same origin is tolerated;
a cross-site redirect or an instance change refuses the action and forces a
re-observation.

Like the computer-use gate (#7), ``deny`` is structurally its own status and
can never resolve to an approve status — the SEV-1 cangming fixed is impossible
by construction here.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from jiuwenswarm.agents.swarm.providers.browser_use_types import (
    BROWSER_USE_ACTIONS,
    BrowserTargetIdentity,
)

BrowserApprovalOption: type = Literal["approve_once", "approve_session", "deny"]
BrowserApprovalStatus: type = Literal["pending", "approved_once", "approved_session", "denied"]

# Actions that require approval before they touch the page. ``evaluate`` runs
# arbitrary JS and ``navigate`` changes origin — both are gated.
_APPROVAL_REQUIRED_ACTIONS: frozenset[str] = frozenset({"navigate", "evaluate"})
_MAX_PENDING_TICKETS = 256


@dataclass(frozen=True, slots=True)
class BrowserApprovalTicket:
    """One browser approval ticket bound to a target identity.

    Attributes:
        id: Server-assigned ticket id.
        action: The browser action awaiting approval.
        target: The frozen target identity (instance + TargetID + origin).
        status: pending / approved_once / approved_session / denied.
    """

    id: str
    action: str
    target: BrowserTargetIdentity
    status: BrowserApprovalStatus = "pending"


class BrowserApprovalGate:
    """Interrupt-then-resume approval gate for browser-use actions.

    ``request`` is a no-op for actions that are not approval-required (the
    caller proceeds directly); for ``navigate``/``evaluate`` it records a
    pending ticket bound to the live target. ``verify_resume`` re-checks the
    current target against the frozen one via :meth:`BrowserTargetIdentity.covers`
    — a cross-site redirect or instance change returns ``None`` (the caller
    treats that as ``not_executed`` + retryable, forcing a re-snapshot).
    """

    def __init__(self, *, max_pending: int = _MAX_PENDING_TICKETS) -> None:
        self._lock = threading.Lock()
        self._tickets: "OrderedDict[str, BrowserApprovalTicket]" = OrderedDict()
        self._max_pending = max_pending
        self._session_grants: set[tuple[str, str, str]] = set()

    @staticmethod
    def requires_approval(action: str) -> bool:
        """Return True iff ``action`` must be approved before execution."""
        return action in _APPROVAL_REQUIRED_ACTIONS

    def request(
        self,
        *,
        action: str,
        target: BrowserTargetIdentity,
        session_id: str,
    ) -> BrowserApprovalTicket | None:
        """Record a pending ticket for an approval-required action.

        Non-required actions return ``None`` (no ticket — the caller proceeds).
        A prior ``approve_session`` for ``(session_id, action, origin)`` returns
        an already-approved ticket so repeated navigations within an approved
        session do not re-prompt.

        Args:
            action: The browser action (must be a known action).
            target: The frozen target identity at request time.
            session_id: The session owning this turn.

        Returns:
            The recorded ticket, ``None`` for non-approval actions, or an
            already-approved ticket when a session grant covers it.

        Raises:
            ValueError: If ``action`` is not a known browser action.
        """
        if action not in BROWSER_USE_ACTIONS:
            raise ValueError(f"unknown browser-use action: {action!r}")
        if not self.requires_approval(action):
            return None
        origin = target.origin or ""
        ticket_id = uuid.uuid4().hex
        ticket = BrowserApprovalTicket(id=ticket_id, action=action, target=target)
        with self._lock:
            # Session-wide grants are scoped by (instance, action, origin): a
            # prior approve_session for the same Chrome instance + action + origin
            # short-circuits, so repeated same-origin navigations do not re-prompt.
            if (target.instance_key, action, origin) in self._session_grants:
                ticket = BrowserApprovalTicket(
                    id=ticket_id, action=action, target=target,
                    status="approved_session",
                )
            self._tickets[ticket_id] = ticket
            while len(self._tickets) > self._max_pending:
                self._tickets.popitem(last=False)
        return ticket

    def decide(
        self, ticket_id: str, option: BrowserApprovalOption
    ) -> BrowserApprovalTicket:
        """Resolve a pending ticket. ``deny`` resolves to ``denied``, never approve."""
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise KeyError(f"unknown approval ticket {ticket_id!r}")
            if ticket.status != "pending":
                raise ValueError(
                    f"ticket {ticket_id!r} already resolved as {ticket.status!r}"
                )
            status: BrowserApprovalStatus
            if option == "deny":
                status = "denied"
            elif option == "approve_once":
                status = "approved_once"
            else:
                status = "approved_session"
                self._session_grants.add(
                    (ticket.target.instance_key, ticket.action, ticket.target.origin or "")
                )
            resolved = BrowserApprovalTicket(
                id=ticket.id, action=ticket.action, target=ticket.target, status=status,
            )
            self._tickets[ticket_id] = resolved
            return resolved

    def verify_resume(
        self, ticket_id: str, *, current_target: BrowserTargetIdentity
    ) -> BrowserApprovalTicket | None:
        """Re-verify the bound target at resume.

        Uses :meth:`BrowserTargetIdentity.covers`: a tab switch within the same
        origin is tolerated; a cross-site redirect (origin changed) or a
        different Chrome instance refuses with ``None``.

        Args:
            ticket_id: The ticket being resumed.
            current_target: The live target identity observed at resume.

        Returns:
            The approved ticket iff the current target is covered by the frozen
            binding; ``None`` otherwise (caller treats as not_executed+retryable).
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                return None
            if ticket.status not in ("approved_once", "approved_session"):
                return None
            if not ticket.target.covers(current_target):
                return None
            return ticket


__all__ = [
    "BrowserApprovalGate",
    "BrowserApprovalOption",
    "BrowserApprovalStatus",
    "BrowserApprovalTicket",
]
