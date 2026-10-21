"""Unit tests for the computer-use approval gate (Issue #7 / B4)."""

from __future__ import annotations

import pytest

from computer_use.actions import Action
from computer_use.approval import ApprovalGate, ApprovalTicket
from computer_use.input_owner import FrozenSessionIdentity, WindowIdentity


def _identity(session: str = "s1") -> FrozenSessionIdentity:
    return FrozenSessionIdentity(session_id=session, turn_id="t1", principal="alice")


def _window(wid: int = 1) -> WindowIdentity:
    return WindowIdentity(window_id=wid, launch_id="L", pid=2, title="App")


def _action(verb: str = "left_click") -> Action:
    return Action(action=verb, pixel=(10, 10), coordinate_max=999)


# ---------------------------------------------------------------------------
# deny is structurally distinct (the SEV-1 cangming fixed)
# ---------------------------------------------------------------------------


def test_deny_resolves_to_denied_never_an_approve_status() -> None:
    gate = ApprovalGate()
    ticket = gate.request(action=_action(), identity=_identity(), window=_window())
    resolved = gate.decide(ticket.id, "deny")
    assert resolved.status == "denied"
    assert resolved.status not in ("approved_once", "approved_session")


def test_approve_once_resolves_to_approved_once() -> None:
    gate = ApprovalGate()
    ticket = gate.request(action=_action(), identity=_identity(), window=_window())
    resolved = gate.decide(ticket.id, "approve_once")
    assert resolved.status == "approved_once"


# ---------------------------------------------------------------------------
# Session-wide approval short-circuits future same-action prompts
# ---------------------------------------------------------------------------


def test_approve_session_grants_future_same_action_requests() -> None:
    gate = ApprovalGate()
    ident = _identity()
    ticket = gate.request(action=_action("hotkey"), identity=ident, window=_window())
    gate.decide(ticket.id, "approve_session")
    # A later same-action request in the same session is pre-approved.
    later = gate.request(action=_action("hotkey"), identity=ident, window=_window())
    assert later.status == "approved_session"


def test_approve_once_does_not_grant_session() -> None:
    gate = ApprovalGate()
    ident = _identity()
    ticket = gate.request(action=_action("hotkey"), identity=ident, window=_window())
    gate.decide(ticket.id, "approve_once")
    later = gate.request(action=_action("hotkey"), identity=ident, window=_window())
    assert later.status == "pending"


def test_session_grant_does_not_cover_a_different_action() -> None:
    gate = ApprovalGate()
    ident = _identity()
    ticket = gate.request(action=_action("hotkey"), identity=ident, window=_window())
    gate.decide(ticket.id, "approve_session")
    later = gate.request(action=_action("type"), identity=ident, window=_window())
    assert later.status == "pending"


# ---------------------------------------------------------------------------
# Resume re-verification (frozen identity)
# ---------------------------------------------------------------------------


def test_verify_resume_match_returns_ticket() -> None:
    gate = ApprovalGate()
    ident, win = _identity(), _window()
    ticket = gate.request(action=_action(), identity=ident, window=win)
    gate.decide(ticket.id, "approve_once")
    resumed = gate.verify_resume(
        ticket.id, current_identity=ident, current_window=win
    )
    assert resumed is not None
    assert resumed.status == "approved_once"


def test_verify_resume_window_mismatch_refuses() -> None:
    gate = ApprovalGate()
    ident, win = _identity(), _window(1)
    ticket = gate.request(action=_action(), identity=ident, window=win)
    gate.decide(ticket.id, "approve_once")
    # The window changed (switched / restarted) → refuse, forcing re-observation.
    assert gate.verify_resume(
        ticket.id, current_identity=ident, current_window=_window(2)
    ) is None


def test_verify_resume_identity_mismatch_refuses() -> None:
    gate = ApprovalGate()
    ident, win = _identity("s1"), _window()
    ticket = gate.request(action=_action(), identity=ident, window=win)
    gate.decide(ticket.id, "approve_once")
    assert gate.verify_resume(
        ticket.id,
        current_identity=_identity("s2"),  # different session
        current_window=win,
    ) is None


def test_verify_resume_pending_ticket_refuses() -> None:
    gate = ApprovalGate()
    ident, win = _identity(), _window()
    ticket = gate.request(action=_action(), identity=ident, window=win)
    # Not yet decided → resume cannot execute.
    assert gate.verify_resume(
        ticket.id, current_identity=ident, current_window=win
    ) is None


def test_verify_resume_unknown_ticket_refuses() -> None:
    gate = ApprovalGate()
    assert gate.verify_resume(
        "bogus", current_identity=_identity(), current_window=_window()
    ) is None


# ---------------------------------------------------------------------------
# Lifecycle / idempotency
# ---------------------------------------------------------------------------


def test_decide_unknown_ticket_raises() -> None:
    gate = ApprovalGate()
    with pytest.raises(KeyError):
        gate.decide("bogus", "approve_once")


def test_decide_already_resolved_raises() -> None:
    gate = ApprovalGate()
    ticket = gate.request(action=_action(), identity=_identity(), window=_window())
    gate.decide(ticket.id, "approve_once")
    with pytest.raises(ValueError):
        gate.decide(ticket.id, "deny")


def test_pending_registry_is_fifo_bounded() -> None:
    gate = ApprovalGate(max_pending=3)
    for _ in range(5):
        gate.request(action=_action(), identity=_identity(), window=_window())
    # Only the most-recent ``max_pending`` tickets survive; oldest evicted FIFO.
    assert len(gate._tickets) == 3
