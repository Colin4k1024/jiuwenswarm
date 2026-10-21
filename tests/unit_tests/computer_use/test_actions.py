"""Unit tests for the typed computer-use action space and domain model.

Covers Issue #4 (B1): a closed 16-action enum, a validated ``Action`` carrier
with relative/pixel coordinates and a ``coordinate_max`` bound, a ``Decision``
record, the ``ModelPort`` protocol, and the ``Outcome`` verdict space with a
retryability predicate. Pure types only — no native driver, no store, no LLM.
"""

from __future__ import annotations

import dataclasses

import pytest

from computer_use.actions import (
    COMPUTER_USE_ACTIONS,
    Action,
    CoordinateMismatch,
    Decision,
    ModelPort,
    Outcome,
    is_retryable,
)
from computer_use.errors import ComputerUseError


# ---------------------------------------------------------------------------
# Closed action space
# ---------------------------------------------------------------------------


def test_computer_use_action_set_has_exactly_16_members() -> None:
    assert COMPUTER_USE_ACTIONS == {
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "drag",
        "mouse_move",
        "type",
        "hotkey",
        "press",
        "key_down",
        "key_up",
        "scroll",
        "wait",
        "call_user",
        "finished",
    }
    assert len(COMPUTER_USE_ACTIONS) == 16


# ---------------------------------------------------------------------------
# Action validation
# ---------------------------------------------------------------------------


def test_action_unknown_action_rejected() -> None:
    with pytest.raises(ValueError, match="unknown computer-use action"):
        Action(action="bogus")  # type: ignore[arg-type]


def test_action_negative_pixel_raises_coordinate_mismatch() -> None:
    with pytest.raises(CoordinateMismatch):
        Action(action="left_click", pixel=(-1, 5))


def test_action_pixel_exceeds_coordinate_max_raises_coordinate_mismatch() -> None:
    with pytest.raises(CoordinateMismatch):
        Action(action="left_click", pixel=(1000, 0), coordinate_max=999)


def test_action_relative_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="relative"):
        Action(action="mouse_move", relative=(1.5, 0.5))


def test_action_oversized_text_rejected() -> None:
    with pytest.raises(ValueError, match="text"):
        Action(action="type", text="x" * 8193)


def test_action_valid_construction_is_frozen() -> None:
    a = Action(action="left_click", pixel=(10, 10), coordinate_max=999)
    assert a.action == "left_click"
    assert a.pixel == (10, 10)
    assert a.coordinate_max == 999
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.action = "right_click"  # type: ignore[misc]


def test_action_uses_slots() -> None:
    assert hasattr(Action, "__slots__")
    assert set(Action.__slots__) >= {"action", "relative", "pixel", "coordinate_max", "text"}
    # slots-only instances carry no instance dict; state lives in the slot cells.
    assert not hasattr(Action(action="wait"), "__dict__")


def test_action_defaults() -> None:
    a = Action(action="wait")
    assert a.relative is None
    assert a.pixel is None
    assert a.coordinate_max == 999
    assert a.text is None
    assert a.key is None
    assert a.dx == 0
    assert a.dy == 0


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def test_decision_carries_action_reasoning_prediction() -> None:
    act = Action(action="left_click", pixel=(5, 5))
    d = Decision(action=act, reasoning="click the submit button", prediction="form submits")
    assert d.action is act
    assert d.reasoning == "click the submit button"
    assert d.prediction == "form submits"


def test_decision_defaults() -> None:
    act = Action(action="finished")
    d = Decision(action=act)
    assert d.reasoning == ""
    assert d.prediction == ""


# ---------------------------------------------------------------------------
# ModelPort protocol
# ---------------------------------------------------------------------------


def test_model_port_protocol_satisfied() -> None:
    class _FakeModel:
        def predict(self, observation: object) -> Decision:
            return Decision(action=Action(action="finished"))

        def reset(self) -> None:
            return None

    assert isinstance(_FakeModel(), ModelPort)


def test_model_port_protocol_rejects_missing_reset() -> None:
    class _MissingReset:
        def predict(self, observation: object) -> Decision:
            return Decision(action=Action(action="finished"))

    assert not isinstance(_MissingReset(), ModelPort)


# ---------------------------------------------------------------------------
# Outcome + retryability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome, retryable",
    [
        ("executed", False),
        ("not_executed", True),
        ("execution_uncertain", False),
        ("denied", False),
        ("approval_pending", False),
        ("busy", True),
    ],
)
def test_is_retryable_classification(outcome: Outcome, retryable: bool) -> None:
    assert is_retryable(outcome) is retryable


def test_is_retryable_rejects_unknown_outcome() -> None:
    with pytest.raises(ValueError):
        is_retryable("bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CoordinateMismatch is a typed ComputerUseError
# ---------------------------------------------------------------------------


def test_coordinate_mismatch_is_computer_use_error() -> None:
    exc = CoordinateMismatch(action="left_click", coordinate=(1000, 0), coordinate_max=999)
    assert isinstance(exc, ComputerUseError)
    # Pre-dispatch failure: the command never reached the driver, so it is replayable.
    assert "not_executed" in str(exc) or "coordinate" in str(exc).lower()
