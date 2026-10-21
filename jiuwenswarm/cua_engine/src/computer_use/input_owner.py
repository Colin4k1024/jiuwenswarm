# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System-level single desktop-input owner (Issue #5 / B2).

A process-wide mutex enforcing the invariant that at most one desktop input
task owns real input at any time (cangming #598). The lock is process-local:
a crash leaves no in-memory owner, so input never silently resumes — a fresh
``acquire`` is required. ``ObservationTicket`` authorizes exactly one input
per observation (cangming's one-shot observation id). ``verify_caller`` is the
fail-closed identity check against the frozen session identity (cangming #596):
a mismatch raises ``IdentityForged`` and the action is refused, never replayed.

Pure Python: ``threading`` + dataclasses. No native driver, no store. The
governance loop (B3) and approval gate (B4) consume this module.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Literal

from .errors import ComputerUseError


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    """Stable identity of the window an input task targets (cangming #613).

    Attributes:
        window_id: The platform window id (0 = PID-only, forward-compatible).
        launch_id: The launch/app-session id; non-empty for a real target.
        pid: The owning process id, when known.
        title: The window title, when known.
    """

    window_id: int
    launch_id: str
    pid: int | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class FrozenSessionIdentity:
    """Immutable session identity frozen at task start (cangming #596).

    Every subsequent input operation must verify its caller matches this
    record; a mismatch means the caller is not the task that owns input and
    the action is refused as ``identity_forged``.

    Attributes:
        session_id: Owning session identifier.
        turn_id: The turn within the session.
        principal: The principal (user/operator) owning the turn.
    """

    session_id: str
    turn_id: str
    principal: str


class InputOwnerError(ComputerUseError):
    """Base for single-input-owner violations."""


class InputBusy(InputOwnerError):
    """Another task already holds the single desktop input owner.

    A transient, retryable failure: the contender may retry once the active
    task releases (or is stopped). Pre-dispatch — no input was sent.
    """

    def __init__(self, *, contender_session_id: str) -> None:
        failure = {
            "code": "busy",
            "effect": "none",
            "target": contender_session_id,
            "detail": "another task already holds the single desktop input owner",
            "next": "stop the active task or wait for it to release, then retry",
        }
        super().__init__(failure, method="InputOwner")


class IdentityForged(InputOwnerError):
    """The caller's identity does not match the frozen session identity.

    Fail-closed: the action is refused and never replayed. A frozen identity
    that no longer matches means the caller is not the task that owns input.
    """

    def __init__(self, *, caller: FrozenSessionIdentity) -> None:
        failure = {
            "code": "invalid_request",
            "effect": "none",
            "target": caller.session_id,
            "detail": "caller identity does not match the frozen session identity",
            "next": "refuse the action and re-observe; do not replay",
        }
        super().__init__(failure, method="InputOwner")


class InputOwnerLock:
    """Process-wide mutex holding at most one desktop input owner.

    The lock is in-memory and process-local. A process restart begins with no
    holder, so a crashed task's input is never silently resumed: a fresh
    ``acquire`` is required, and the governance loop is expected to mark the
    prior live task ``interrupted`` (B3) rather than resume it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: FrozenSessionIdentity | None = None
        self._window: WindowIdentity | None = None

    def acquire(
        self,
        *,
        session_id: str,
        turn_id: str,
        principal: str,
        window: WindowIdentity,
    ) -> FrozenSessionIdentity:
        """Claim the single input owner for one task.

        Args:
            session_id: Owning session identifier.
            turn_id: The turn within the session.
            principal: The principal owning the turn.
            window: The window the task targets.

        Returns:
            The frozen session identity now owning input.

        Raises:
            InputBusy: If another task already holds the owner.
        """
        identity = FrozenSessionIdentity(
            session_id=session_id, turn_id=turn_id, principal=principal
        )
        with self._lock:
            if self._holder is not None:
                raise InputBusy(contender_session_id=session_id)
            self._holder = identity
            self._window = window
        return identity

    def release(self) -> None:
        """Release the single input owner. Idempotent when unheld."""
        with self._lock:
            self._holder = None
            self._window = None

    def holder(self) -> FrozenSessionIdentity | None:
        """Return the current frozen owner, or ``None`` when unheld."""
        with self._lock:
            return self._holder

    def verify_caller(self, caller: FrozenSessionIdentity) -> None:
        """Fail-closed identity check against the frozen owner.

        Args:
            caller: The identity of the caller requesting an input operation.

        Raises:
            IdentityForged: If no owner is held or the caller is not the owner.
        """
        with self._lock:
            if self._holder is None or caller != self._holder:
                raise IdentityForged(caller=caller)


class ObservationTicket:
    """One-shot observation authorization (cangming's observe-window id).

    Exactly one observation is live at a time. Issuing a new observation
    supersedes the previous live one. A token may be consumed exactly once;
    a second consume (or a consume of a superseded/unknown token) is refused,
    forcing the caller to re-observe before dispatching more input.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live_token: str | None = None
        self._live_consumed: bool = False

    def issue(self) -> str:
        """Mint a new one-time observation token, superseding any prior live one."""
        token = uuid.uuid4().hex
        with self._lock:
            self._live_token = token
            self._live_consumed = False
        return token

    def consume(self, token: str) -> bool:
        """Consume ``token`` once. Return False if it is stale, consumed, or unknown.

        Args:
            token: The token returned by a prior :meth:`issue`.

        Returns:
            True iff ``token`` is the current live, unconsumed observation.
        """
        with self._lock:
            if (
                self._live_token is not None
                and token == self._live_token
                and not self._live_consumed
            ):
                self._live_consumed = True
                return True
            return False


__all__ = [
    "FrozenSessionIdentity",
    "IdentityForged",
    "InputBusy",
    "InputOwnerError",
    "InputOwnerLock",
    "ObservationTicket",
    "WindowIdentity",
]
