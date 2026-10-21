"""Unit tests for the browser-use domain types (Issue #2 / A1, slice 2a).

Covers the closed 17-action space, the BrowserVerdict retryability model, the
``BrowserActionResult`` structured-result carrier, the ``BrowserTargetIdentity``
approval-target predicate, and the snapshot ref table. Pure types only — no
Playwright, no CDP, no Chrome. The behavioural wiring (snapshot JS, tool
dispatch, managed Chrome lifecycle) lands in slices 2b/2c.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.swarm.providers.browser_use_types import (
    BROWSER_USE_ACTIONS,
    BrowserActionResult,
    BrowserTargetIdentity,
    BrowserVerdict,
    SnapshotElement,
    SnapshotRefTable,
    is_retryable,
)


# ---------------------------------------------------------------------------
# Closed action space
# ---------------------------------------------------------------------------


def test_browser_use_action_set_has_18_members() -> None:
    assert BROWSER_USE_ACTIONS == {
        "snapshot",
        "navigate",
        "click",
        "type",
        "scroll",
        "screenshot",
        "get_text",
        "get_html",
        "wait_for",
        "go_back",
        "go_forward",
        "new_tab",
        "close_tab",
        "switch_tab",
        "list_tabs",
        "get_url",
        "get_title",
        "evaluate",
    }
    assert len(BROWSER_USE_ACTIONS) == 18


# ---------------------------------------------------------------------------
# Verdict retryability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict, retryable",
    [
        ("executed", False),
        ("not_executed", True),
        ("execution_uncertain", False),
        ("denied", False),
        ("approval_pending", False),
        ("busy", True),
    ],
)
def test_is_retryable_classification(verdict: BrowserVerdict, retryable: bool) -> None:
    assert is_retryable(verdict) is retryable


def test_is_retryable_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError):
        is_retryable("bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BrowserActionResult
# ---------------------------------------------------------------------------


def test_action_result_derives_retryable_and_url_changed() -> None:
    r = BrowserActionResult(
        action="click",
        verdict="not_executed",
        url_before="https://a.example/x",
        url_after="https://a.example/x",
        text_verification="submit button",
        hint="stale ref; re-snapshot",
    )
    assert r.retryable is True
    assert r.url_changed is False


def test_action_result_execution_uncertain_is_not_retryable() -> None:
    r = BrowserActionResult(action="click", verdict="execution_uncertain")
    assert r.retryable is False


def test_action_result_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError, match="unknown browser-use verdict"):
        BrowserActionResult(action="click", verdict="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BrowserTargetIdentity.covers
# ---------------------------------------------------------------------------


def test_target_identity_covers_same_instance_same_origin() -> None:
    a = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://a.example")
    b = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://a.example")
    assert a.covers(b) is True


def test_target_identity_covers_tab_switch_within_same_origin() -> None:
    a = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://a.example")
    # Same instance + origin, different tab target id → tab switch, still covered.
    b = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T2", origin="https://a.example")
    assert a.covers(b) is True


def test_target_identity_refuses_cross_site_redirect() -> None:
    a = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://a.example")
    b = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://b.evil")
    assert a.covers(b) is False


def test_target_identity_refuses_different_instance() -> None:
    a = BrowserTargetIdentity(instance_key="chrome-9222", target_id="T1", origin="https://a.example")
    b = BrowserTargetIdentity(instance_key="chrome-9333", target_id="T1", origin="https://a.example")
    assert a.covers(b) is False


# ---------------------------------------------------------------------------
# SnapshotRefTable
# ---------------------------------------------------------------------------


def _table() -> SnapshotRefTable:
    return SnapshotRefTable(
        elements=(
            SnapshotElement(ref="e1", css_path="div>a", tag="a", role="link", label="home", bbox=(10, 20, 100, 40)),
            SnapshotElement(ref="e2", css_path="button#go", tag="button", role="button", label="go", bbox=(10, 50, 80, 30)),
        ),
        max_elements=200,
    )


def test_snapshot_ref_table_resolves_known_ref() -> None:
    el = _table().resolve("e1")
    assert el is not None
    assert el.tag == "a"
    assert el.label == "home"


def test_snapshot_ref_table_resolve_unknown_returns_none() -> None:
    assert _table().resolve("e99") is None


def test_snapshot_element_is_stale_when_label_changed() -> None:
    el = _table().resolve("e1")
    assert el is not None
    assert el.is_stale(label="home", tag="a", role="link") is False
    assert el.is_stale(label="login", tag="a", role="link") is True
    assert el.is_stale(label="home", tag="button", role="link") is True
    assert el.is_stale(label="home", tag="a", role="button") is True


def test_snapshot_ref_table_enforces_max_elements() -> None:
    with pytest.raises(ValueError, match="max_elements"):
        SnapshotRefTable(
            elements=tuple(
                SnapshotElement(ref=f"e{i}", css_path="x", tag="a", role="link", label=str(i), bbox=(0, 0, 1, 1))
                for i in range(10)
            ),
            max_elements=5,
        )
