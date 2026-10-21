"""Unit tests for the computer-use governance loop (Issue #6 / B3).

The driver and model are fakes so the loop's governance invariants — single UI
action per turn, cancel discards a pending decision, the four exit conditions,
and the busy/awaiting-user/done terminal states — are exercised without a real
desktop driver or model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from computer_use.actions import Action, Decision
from computer_use.input_owner import (
    FrozenSessionIdentity,
    InputOwnerLock,
    ObservationTicket,
    WindowIdentity,
)
from computer_use.loop import GovernanceLoop, LoopResult, StepRecord


def _window() -> WindowIdentity:
    return WindowIdentity(window_id=1, launch_id="L", pid=2, title="App")


class _FakeDriver:
    def __init__(self) -> None:
        self.invoked: list[Action] = []
        self._observe_calls = 0

    def observe(self, window: WindowIdentity) -> object:
        self._observe_calls += 1
        return {"objective": "task", "window": window}

    def invoke(self, action: Action) -> str:
        self.invoked.append(action)
        return "executed"


class _ScriptedModel:
    """Returns a scripted sequence of Decisions, one per predict() call."""

    def __init__(self, actions: list[Action]) -> None:
        self._actions = list(actions)
        self._i = 0

    def predict(self, observation: object) -> Decision:
        action = self._actions[self._i]
        self._i += 1
        return Decision(action=action, reasoning="scripted")

    def reset(self) -> None:
        self._i = 0


class _RaisingModel:
    def __init__(self, *, fail_times: int) -> None:
        self._fails_left = fail_times

    def predict(self, observation: object) -> Decision:
        if self._fails_left > 0:
            self._fails_left -= 1
            raise ValueError("parse failure")
        return Decision(action=Action(action="finished"))

    def reset(self) -> None:
        pass


def _loop(*, driver, model, **kwargs: Any) -> GovernanceLoop:
    return GovernanceLoop(
        driver=driver, model=model,
        input_lock=InputOwnerLock(), observation_ticket=ObservationTicket(),
        **kwargs,
    )


def _run(loop: GovernanceLoop, **kwargs: Any) -> LoopResult:
    return loop.run(goal="task", session_id="s1", turn_id="t1",
                    principal="alice", window=_window(), **kwargs)


# ---------------------------------------------------------------------------
# Single UI action per turn
# ---------------------------------------------------------------------------


def test_loop_runs_one_invoke_per_turn_then_reobserves() -> None:
    driver = _FakeDriver()
    model = _ScriptedModel([
        Action(action="left_click", pixel=(1, 1), coordinate_max=999),
        Action(action="left_click", pixel=(2, 2), coordinate_max=999),
        Action(action="finished"),
    ])
    result = _run(_loop(driver=driver, model=model))
    assert result.status == "done"
    assert result.steps == 3
    assert len(driver.invoked) == 2  # one invoke per non-terminal turn
    assert [a.pixel for a in driver.invoked] == [(1, 1), (2, 2)]


def test_loop_wait_does_not_invoke_driver() -> None:
    driver = _FakeDriver()
    model = _ScriptedModel([Action(action="wait"), Action(action="finished")])
    result = _run(_loop(driver=driver, model=model))
    assert result.status == "done"
    assert driver.invoked == []  # wait never reaches the driver


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


def test_loop_finished_is_done() -> None:
    model = _ScriptedModel([Action(action="finished")])
    result = _run(_loop(driver=_FakeDriver(), model=model))
    assert result.status == "done"
    assert result.steps == 1


def test_loop_call_user_is_awaiting_user() -> None:
    model = _ScriptedModel([Action(action="call_user")])
    result = _run(_loop(driver=_FakeDriver(), model=model))
    assert result.status == "awaiting_user"


def test_loop_busy_when_lock_held() -> None:
    lock = InputOwnerLock()
    # Pre-claim the lock so the loop cannot acquire it.
    lock.acquire(session_id="other", turn_id="t", principal="bob", window=_window())
    loop = GovernanceLoop(
        driver=_FakeDriver(), model=_ScriptedModel([Action(action="finished")]),
        input_lock=lock, observation_ticket=ObservationTicket(),
    )
    result = _run(loop)
    assert result.status == "busy"
    assert result.steps == 0


# ---------------------------------------------------------------------------
# Exit conditions
# ---------------------------------------------------------------------------


def test_loop_max_steps_exits_as_fail() -> None:
    # The model never finishes; each turn is a click.
    model = _ScriptedModel([Action(action="left_click", pixel=(1, 1), coordinate_max=999)])
    # Recycle the single action forever by re-queueing it.
    class _Repeating:
        def predict(self, obs):
            return Decision(action=Action(action="left_click", pixel=(1, 1), coordinate_max=999))
        def reset(self):
            pass
    result = _run(_loop(driver=_FakeDriver(), model=_Repeating(), max_steps=4, max_no_progress_steps=99))
    assert result.status == "fail"
    assert result.steps == 4


def test_loop_consecutive_parse_failures_exit() -> None:
    model = _RaisingModel(fail_times=99)
    result = _run(_loop(driver=_FakeDriver(), model=model, max_consecutive_parse_failures=3, max_steps=50, max_no_progress_steps=99))
    assert result.status == "fail"
    assert "parse failures" in result.final_message


def test_loop_no_progress_exit() -> None:
    class _NoProgress:
        def predict(self, obs):
            return Decision(action=Action(action="left_click", pixel=(1, 1), coordinate_max=999))
        def reset(self):
            pass
    result = _run(_loop(driver=_FakeDriver(), model=_NoProgress(), max_no_progress_steps=3, max_steps=50))
    assert result.status == "fail"
    assert "no-progress" in result.final_message


# ---------------------------------------------------------------------------
# Cancel discards a pending decision
# ---------------------------------------------------------------------------


def test_loop_cancel_discards_pending_decision() -> None:
    driver = _FakeDriver()
    model = _ScriptedModel([
        Action(action="left_click", pixel=(1, 1), coordinate_max=999),
        Action(action="left_click", pixel=(2, 2), coordinate_max=999),
    ])
    loop = _loop(driver=driver, model=model, max_steps=10, max_no_progress_steps=99)

    # Cancel after the first action lands; the pending second decision must not
    # be executed.
    cancel_driver = _CancelDriver(loop)
    loop._driver = cancel_driver
    result = _run(loop)
    assert result.status == "canceled"
    assert len(cancel_driver.invoked) == 1  # second decision discarded


class _CancelDriver(_FakeDriver):
    def __init__(self, loop: GovernanceLoop) -> None:
        super().__init__()
        self._loop = loop

    def invoke(self, action: Action) -> str:
        result = super().invoke(action)
        self._loop.cancel()
        return result


# ---------------------------------------------------------------------------
# Outcomes recorded
# ---------------------------------------------------------------------------


def test_loop_records_outcomes_in_order() -> None:
    driver = _FakeDriver()
    model = _ScriptedModel([
        Action(action="left_click", pixel=(1, 1), coordinate_max=999),
        Action(action="type", text="hi"),
        Action(action="finished"),
    ])
    result = _run(_loop(driver=driver, model=model, max_no_progress_steps=99))
    assert [r.action.action for r in result.outcomes] == ["left_click", "type"]
    assert all(r.outcome == "executed" for r in result.outcomes)
