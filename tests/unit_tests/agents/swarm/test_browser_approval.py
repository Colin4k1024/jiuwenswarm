"""Unit tests for the browser-use approval gate (Issue #3 / A2)."""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.swarm.providers.browser_approval import BrowserApprovalGate
from jiuwenswarm.agents.swarm.providers.browser_use_types import BrowserTargetIdentity


def _target(instance: str = "chrome-9222", origin: str = "https://a.example",
            target_id: str = "T1") -> BrowserTargetIdentity:
    return BrowserTargetIdentity(instance_key=instance, target_id=target_id, origin=origin)


# ---------------------------------------------------------------------------
# requires_approval gating
# ---------------------------------------------------------------------------


def test_navigate_and_evaluate_require_approval() -> None:
    gate = BrowserApprovalGate()
    assert gate.requires_approval("navigate")
    assert gate.requires_approval("evaluate")


def test_click_does_not_require_approval() -> None:
    gate = BrowserApprovalGate()
    assert not gate.requires_approval("click")
    assert gate.request(action="click", target=_target(), session_id="s1") is None


def test_request_rejects_unknown_action() -> None:
    gate = BrowserApprovalGate()
    with pytest.raises(ValueError, match="unknown browser-use action"):
        gate.request(action="bogus", target=_target(), session_id="s1")


# ---------------------------------------------------------------------------
# deny is structurally distinct (SEV-1 impossible by construction)
# ---------------------------------------------------------------------------


def test_deny_resolves_to_denied_never_approve() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(action="navigate", target=_target(), session_id="s1")
    assert ticket is not None and ticket.status == "pending"
    resolved = gate.decide(ticket.id, "deny")
    assert resolved.status == "denied"
    assert resolved.status not in ("approved_once", "approved_session")


def test_approve_once_resolves_approved_once() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(action="navigate", target=_target(), session_id="s1")
    assert ticket is not None
    resolved = gate.decide(ticket.id, "approve_once")
    assert resolved.status == "approved_once"


# ---------------------------------------------------------------------------
# Session-wide approval
# ---------------------------------------------------------------------------


def test_approve_session_short_circuits_same_origin_navigations() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(
        action="navigate", target=_target(origin="https://a.example"), session_id="s1"
    )
    assert ticket is not None
    gate.decide(ticket.id, "approve_session")
    later = gate.request(
        action="navigate", target=_target(origin="https://a.example"), session_id="s1"
    )
    assert later is not None and later.status == "approved_session"


def test_session_grant_does_not_cross_origin() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(
        action="navigate", target=_target(origin="https://a.example"), session_id="s1"
    )
    gate.decide(ticket.id, "approve_session")
    # A navigation to a different origin must re-prompt.
    later = gate.request(
        action="navigate", target=_target(origin="https://b.evil"), session_id="s1"
    )
    assert later is not None and later.status == "pending"


# ---------------------------------------------------------------------------
# Resume re-verification via BrowserTargetIdentity.covers
# ---------------------------------------------------------------------------


def test_verify_resume_tab_switch_within_origin_ok() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(
        action="navigate", target=_target(target_id="T1", origin="https://a.example"),
        session_id="s1",
    )
    gate.decide(ticket.id, "approve_once")
    # Same instance + origin, different tab target id → tolerated.
    resumed = gate.verify_resume(
        ticket.id, current_target=_target(target_id="T2", origin="https://a.example")
    )
    assert resumed is not None
    assert resumed.status == "approved_once"


def test_verify_resume_cross_site_redirect_refuses() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(
        action="navigate", target=_target(origin="https://a.example"), session_id="s1"
    )
    gate.decide(ticket.id, "approve_once")
    # Origin changed (cross-site redirect while awaiting approval) → refuse.
    assert gate.verify_resume(
        ticket.id, current_target=_target(origin="https://b.evil")
    ) is None


def test_verify_resume_different_instance_refuses() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(
        action="navigate", target=_target(instance="chrome-9222"), session_id="s1"
    )
    gate.decide(ticket.id, "approve_once")
    assert gate.verify_resume(
        ticket.id, current_target=_target(instance="chrome-9333")
    ) is None


def test_verify_resume_pending_ticket_refuses() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(action="navigate", target=_target(), session_id="s1")
    assert ticket is not None
    # Not yet decided → resume cannot execute.
    assert gate.verify_resume(ticket.id, current_target=_target()) is None


def test_verify_resume_unknown_ticket_refuses() -> None:
    gate = BrowserApprovalGate()
    assert gate.verify_resume("bogus", current_target=_target()) is None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_decide_already_resolved_raises() -> None:
    gate = BrowserApprovalGate()
    ticket = gate.request(action="navigate", target=_target(), session_id="s1")
    gate.decide(ticket.id, "approve_once")
    with pytest.raises(ValueError):
        gate.decide(ticket.id, "deny")


def test_pending_registry_fifo_bounded() -> None:
    gate = BrowserApprovalGate(max_pending=3)
    for _ in range(5):
        gate.request(action="navigate", target=_target(), session_id="s1")
    assert len(gate._tickets) == 3
