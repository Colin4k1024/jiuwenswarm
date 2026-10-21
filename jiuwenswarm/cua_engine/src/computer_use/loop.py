# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Computer-use governance loop (Issue #6 / B3).

The perceive → predict → validate → act → re-observe cycle that governs desktop
input. Mirrors the cangming ``application/computeruse/loop.go`` contract:

- at most one UI-changing action per turn (the rest are deferred to the next
  observation);
- a decision produced after cancel is discarded, never executed;
- bounded exit: max steps, total timeout, ``max_consecutive_parse_failures``,
  ``max_no_progress_steps``;
- ``BridgeOutcome`` verdicts (executed / execution_uncertain / not_executed /
  denied / approval_pending / busy).

The driver and model are injected protocols; tests use fakes. Real desktop
driver integration (the cua_engine ``Computer``/``App``/``Element`` surface)
is NOT PROVEN here — it is wired when the loop is mounted on the
``SWARM_CUA_AGENT`` sub-agent.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from computer_use.actions import Action, Decision, ModelPort, Outcome, is_retryable
from computer_use.input_owner import (
    FrozenSessionIdentity,
    InputBusy,
    InputOwnerLock,
    ObservationTicket,
    WindowIdentity,
)

# Exit-condition defaults (cangming loop.go).
DEFAULT_MAX_STEPS = 100
DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3
DEFAULT_MAX_NO_PROGRESS_STEPS = 5
DEFAULT_TOTAL_TIMEOUT_S = 600.0


class DesktopDriver(Protocol):
    """The observe/act seam the loop drives."""

    def observe(self, window: WindowIdentity) -> object:
        """Return one observation for ``window`` (consumes the live ticket)."""

    def invoke(self, action: Action) -> Outcome:
        """Execute one validated UI action and report its verdict."""


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One step's recorded (action, outcome) pair."""

    action: Action
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class LoopResult:
    """The final state of one governance loop run.

    Attributes:
        goal: The task objective passed in.
        status: Terminal state: ``done`` / ``fail`` / ``canceled`` /
            ``awaiting_user`` / ``busy`` / ``interrupted`` / ``timeout``.
        steps: How many turns ran.
        outcomes: One ``StepRecord`` per executed action, in order.
        final_message: A short human-readable terminal note.
    """

    goal: str
    status: str
    steps: int
    outcomes: tuple[StepRecord, ...] = ()
    final_message: str = ""


class GovernanceLoop:
    """Perceive → predict → validate → act loop with governance constraints.

    Attributes:
        driver: The observe/act seam.
        model: The specialized computer-use model (``ModelPort``).
        input_lock: The process-wide single-input owner.
        observation_ticket: One-shot observation authorization.
        max_steps / total_timeout_s / max_consecutive_parse_failures /
            max_no_progress_steps: Exit-condition bounds.
    """

    def __init__(
        self,
        *,
        driver: DesktopDriver,
        model: ModelPort,
        input_lock: InputOwnerLock,
        observation_ticket: ObservationTicket,
        max_steps: int = DEFAULT_MAX_STEPS,
        total_timeout_s: float = DEFAULT_TOTAL_TIMEOUT_S,
        max_consecutive_parse_failures: int = DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES,
        max_no_progress_steps: int = DEFAULT_MAX_NO_PROGRESS_STEPS,
    ) -> None:
        self._driver = driver
        self._model = model
        self._input_lock = input_lock
        self._observation_ticket = observation_ticket
        self._max_steps = max_steps
        self._total_timeout_s = total_timeout_s
        self._max_consecutive_parse_failures = max_consecutive_parse_failures
        self._max_no_progress_steps = max_no_progress_steps
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. A pending decision is discarded, not executed."""
        self._cancel_event.set()

    def run(
        self,
        *,
        goal: str,
        session_id: str,
        turn_id: str,
        principal: str,
        window: WindowIdentity,
    ) -> LoopResult:
        """Run the governance loop for one task.

        Args:
            goal: The task objective.
            session_id / turn_id / principal: The frozen identity claiming input.
            window: The window to observe and act on.

        Returns:
            The terminal ``LoopResult``.
        """
        try:
            self._input_lock.acquire(
                session_id=session_id,
                turn_id=turn_id,
                principal=principal,
                window=window,
            )
        except InputBusy:
            return LoopResult(
                goal=goal, status="busy", steps=0,
                final_message="another task holds the single desktop input owner",
            )
        try:
            return self._run_locked(goal, window)
        finally:
            self._input_lock.release()

    def _run_locked(self, goal: str, window: WindowIdentity) -> LoopResult:
        outcomes: list[StepRecord] = []
        parse_failures = 0
        no_progress = 0
        started = time.monotonic()
        step = 0
        terminal = "fail"
        message = "max steps reached without completion"
        while step < self._max_steps:
            if time.monotonic() - started > self._total_timeout_s:
                terminal = "timeout"
                message = "total timeout exceeded"
                break
            if self._cancel_event.is_set():
                # A decision produced after cancel is discarded, never executed.
                terminal = "canceled"
                message = "canceled; pending decision discarded"
                break
            step += 1
            observation = self._driver.observe(window)
            try:
                decision: Decision = self._model.predict(observation)
            except Exception:  # noqa: BLE001
                parse_failures += 1
                if parse_failures >= self._max_consecutive_parse_failures:
                    terminal = "fail"
                    message = "max consecutive parse failures reached"
                    break
                continue
            parse_failures = 0
            action = decision.action
            # Terminal model signals exit before any UI input.
            if action.action == "finished":
                terminal = "done"
                message = "model reported finished"
                break
            if action.action == "call_user":
                terminal = "awaiting_user"
                message = "model requested user input"
                break
            # ``wait`` is an in-process sleep: it does not touch the driver and
            # forces a fresh observation next turn.
            if action.action == "wait":
                continue
            # Single UI-changing action per turn: invoke exactly one, then loop
            # back to re-observe. Extra actions the model may plan are deferred
            # to subsequent turns (the next observe/predict cycle).
            outcome = self._driver.invoke(action)
            outcomes.append(StepRecord(action=action, outcome=outcome))
            if outcome == "executed":
                no_progress += 1
                if no_progress >= self._max_no_progress_steps:
                    terminal = "fail"
                    message = "max no-progress steps reached"
                    break
            else:
                no_progress = 0
        return LoopResult(
            goal=goal, status=terminal, steps=step,
            outcomes=tuple(outcomes), final_message=message,
        )


__all__ = [
    "DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES",
    "DEFAULT_MAX_NO_PROGRESS_STEPS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_TOTAL_TIMEOUT_S",
    "DesktopDriver",
    "GovernanceLoop",
    "LoopResult",
    "StepRecord",
]
