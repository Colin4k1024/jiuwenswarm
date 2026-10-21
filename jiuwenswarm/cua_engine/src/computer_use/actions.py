"""Typed computer-use action space and domain model.

A closed 16-action surface (mirroring the cangming ``domain/computeruse``
contract), a validated ``Action`` carrier with relative/pixel coordinates and a
``coordinate_max`` bound, a ``Decision`` record, the ``ModelPort`` protocol for
specialized computer-use models, and an ``Outcome`` verdict space with a
retryability predicate. Pure types: no native driver, no store, no LLM. The
existing ``Computer``/``App``/``Element`` intent surface is unchanged and
coexists; this module is the foundation for the governance loop (B2/B3/B5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from .errors import ComputerUseError

# The closed action space. ``Literal`` gives static typing; ``COMPUTER_USE_ACTIONS``
# is the runtime-closed set used to reject unknown action strings at construction.
ComputerUseAction: TypeAlias = Literal[
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
]

COMPUTER_USE_ACTIONS: frozenset[str] = frozenset(
    {
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
)

# Maximum text payload accepted by a single ``type`` action. Bounds the input
# the model can dispatch in one turn; longer payloads must be chunked by the loop.
_MAX_TEXT_BYTES = 8192

# The BridgeOutcome verdict space: what the governance loop reports per action.
Outcome: TypeAlias = Literal[
    "executed",
    "execution_uncertain",
    "not_executed",
    "denied",
    "approval_pending",
    "busy",
]

# Retryability per verdict. Pre-dispatch failures and transient lock contention
# are replayable; anything that may have reached the desktop is not, because
# replaying an uncertain action could double-execute real input.
OUTCOME_RETRYABLE: Mapping[Outcome, bool] = {
    "executed": False,
    "not_executed": True,
    "execution_uncertain": False,
    "denied": False,
    "approval_pending": False,
    "busy": True,
}


def is_retryable(outcome: Outcome) -> bool:
    """Return whether a verdict permits a safe replay of the action.

    Args:
        outcome: One of the ``Outcome`` verdicts.

    Returns:
        True for ``not_executed`` and ``busy``; False otherwise.

    Raises:
        ValueError: If ``outcome`` is not a known verdict.
    """
    if outcome not in OUTCOME_RETRYABLE:
        raise ValueError(f"unknown computer-use outcome: {outcome!r}")
    return OUTCOME_RETRYABLE[outcome]


@dataclass(frozen=True, slots=True)
class Action:
    """A single computer-use action emitted by a model, pre-execution validation.

    Coordinates are optional because not every action is spatial (``type`` uses
    ``text``, ``hotkey`` uses ``key``, ``wait`` uses neither). When ``pixel`` is
    set it must lie in ``[0, coordinate_max)``; ``relative`` (when set) must lie
    in ``[0.0, 1.0]``.

    Attributes:
        action: A member of the closed ``ComputerUseAction`` space.
        relative: Normalized ``(x, y)`` in ``[0, 1]``, or ``None``.
        pixel: Absolute ``(x, y)`` in the model's coordinate space, or ``None``.
        coordinate_max: Exclusive upper bound for ``pixel`` (UI-Mate=999,
            SCALE-CUA=1000).
        text: Payload for ``type`` actions.
        key: Key/combo for ``hotkey``/``press``/``key_down``/``key_up``.
        dx, dy: Scroll deltas for ``scroll``.
    """

    action: ComputerUseAction
    relative: tuple[float, float] | None = None
    pixel: tuple[int, int] | None = None
    coordinate_max: int = 999
    text: str | None = None
    key: str | None = None
    dx: int = 0
    dy: int = 0

    def __post_init__(self) -> None:
        if self.action not in COMPUTER_USE_ACTIONS:
            raise ValueError(f"unknown computer-use action: {self.action!r}")
        if self.relative is not None:
            rx, ry = self.relative
            if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
                raise ValueError(
                    f"relative coordinates must lie in [0, 1]; got {self.relative}"
                )
        if self.pixel is not None:
            px, py = self.pixel
            upper = self.coordinate_max
            if not (0 <= px < upper and 0 <= py < upper):
                raise CoordinateMismatch(
                    action=self.action, coordinate=self.pixel, coordinate_max=upper
                )
        if self.text is not None and len(self.text) > _MAX_TEXT_BYTES:
            raise ValueError(
                f"text payload exceeds {_MAX_TEXT_BYTES} bytes; chunk the input"
            )


@dataclass(frozen=True, slots=True)
class Decision:
    """A model's chosen action with its rationale.

    Attributes:
        action: The validated ``Action`` to dispatch.
        reasoning: Why the model chose this action.
        prediction: What the model expects to observe after dispatch.
    """

    action: Action
    reasoning: str = ""
    prediction: str = ""


@runtime_checkable
class ModelPort(Protocol):
    """Contract for a specialized computer-use model (e.g. UI-Mate, SCALE-CUA).

    Isolated from the main chat model: the loop calls ``predict`` per
    observation and ``reset`` between tasks. Implementations are added in B5.
    """

    def predict(self, observation: object) -> Decision:
        """Map one desktop observation to a single decision.

        Args:
            observation: An opaque observation object (screenshot + context).

        Returns:
            A ``Decision`` carrying one validated ``Action``.
        """

    def reset(self) -> None:
        """Clear per-task state between independent tasks."""


class CoordinateMismatch(ComputerUseError):
    """A model emitted a pixel coordinate outside the declared coordinate space.

    A pre-dispatch failure: no input reached the driver, so the action is
    safely replayable after the model corrects the coordinate.
    """

    def __init__(
        self,
        *,
        action: str,
        coordinate: tuple[int, int],
        coordinate_max: int,
    ) -> None:
        failure = {
            "code": "invalid_request",
            "effect": "none",
            "target": action,
            "detail": (
                f"coordinate {coordinate} exceeds bound [0, {coordinate_max}); "
                "the model must emit coordinates within the observed window"
            ),
            "next": "re-observe and emit a coordinate within bounds",
        }
        super().__init__(failure, method="Action")


__all__ = [
    "COMPUTER_USE_ACTIONS",
    "ComputerUseAction",
    "Action",
    "Decision",
    "ModelPort",
    "Outcome",
    "OUTCOME_RETRYABLE",
    "is_retryable",
    "CoordinateMismatch",
]
