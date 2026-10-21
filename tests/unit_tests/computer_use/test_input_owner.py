"""Unit tests for the single desktop-input owner (Issue #5 / B2).

Covers the system-level single-input mutex (``InputOwnerLock``), the
``WindowIdentity`` / ``FrozenSessionIdentity`` records, the one-time
``ObservationTicket``, and the fail-closed identity verification. Pure Python
with a real threading concurrency probe (no mocks).
"""

from __future__ import annotations

import threading

import pytest

from computer_use.actions import Action, Decision
from computer_use.errors import ComputerUseError
from computer_use.input_owner import (
    FrozenSessionIdentity,
    IdentityForged,
    InputBusy,
    InputOwnerLock,
    ObservationTicket,
    WindowIdentity,
)


def _window() -> WindowIdentity:
    return WindowIdentity(window_id=42, launch_id="launch-1", pid=1234, title="Notepad")


# ---------------------------------------------------------------------------
# InputOwnerLock single-holder invariant
# ---------------------------------------------------------------------------


def test_lock_acquire_sets_holder() -> None:
    lock = InputOwnerLock()
    holder = lock.acquire(
        session_id="s1", turn_id="t1", principal="alice", window=_window()
    )
    assert holder.session_id == "s1"
    assert holder.turn_id == "t1"
    assert holder.principal == "alice"
    assert lock.holder() == holder


def test_lock_second_acquire_raises_busy() -> None:
    lock = InputOwnerLock()
    lock.acquire(session_id="s1", turn_id="t1", principal="alice", window=_window())
    with pytest.raises(InputBusy):
        lock.acquire(
            session_id="s2", turn_id="t1", principal="bob", window=_window()
        )


def test_lock_concurrent_only_one_holder() -> None:
    lock = InputOwnerLock()
    successes: list[int] = []
    busy: list[int] = []
    counter_lock = threading.Lock()
    barrier = threading.Barrier(20)

    def contend(i: int) -> None:
        barrier.wait()
        try:
            lock.acquire(
                session_id=f"s{i}", turn_id="t", principal=f"p{i}", window=_window()
            )
            with counter_lock:
                successes.append(i)
            # Hold briefly so later contenders observe the owner.
            threading.Event().wait(0.05)
            lock.release()
        except InputBusy:
            with counter_lock:
                busy.append(i)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1
    assert len(busy) == 19


def test_lock_release_allows_reacquire() -> None:
    lock = InputOwnerLock()
    lock.acquire(session_id="s1", turn_id="t1", principal="alice", window=_window())
    lock.release()
    assert lock.holder() is None
    # A different task may now acquire.
    lock.acquire(session_id="s2", turn_id="t1", principal="bob", window=_window())
    assert lock.holder().session_id == "s2"


def test_fresh_lock_has_no_holder() -> None:
    # A new process has no in-memory owner: input never silently resumes after
    # a crash; a fresh acquire is required.
    lock = InputOwnerLock()
    assert lock.holder() is None


# ---------------------------------------------------------------------------
# Identity verification (fail-closed)
# ---------------------------------------------------------------------------


def test_verify_caller_match_ok() -> None:
    lock = InputOwnerLock()
    holder = lock.acquire(
        session_id="s1", turn_id="t1", principal="alice", window=_window()
    )
    # No raise when the caller is the frozen holder.
    lock.verify_caller(holder)


def test_verify_caller_mismatch_raises_forged() -> None:
    lock = InputOwnerLock()
    lock.acquire(session_id="s1", turn_id="t1", principal="alice", window=_window())
    bogus = FrozenSessionIdentity(session_id="s1", turn_id="t1", principal="mallory")
    with pytest.raises(IdentityForged):
        lock.verify_caller(bogus)


def test_verify_caller_when_no_holder_raises_forged() -> None:
    lock = InputOwnerLock()
    with pytest.raises(IdentityForged):
        lock.verify_caller(
            FrozenSessionIdentity(session_id="s1", turn_id="t1", principal="alice")
        )


# ---------------------------------------------------------------------------
# ObservationTicket one-time consumption
# ---------------------------------------------------------------------------


def test_observation_ticket_issue_and_consume_once() -> None:
    tickets = ObservationTicket()
    token = tickets.issue()
    assert tickets.consume(token) is True
    # A second consume of the same token is refused.
    assert tickets.consume(token) is False


def test_observation_ticket_new_issue_invalidates_previous() -> None:
    tickets = ObservationTicket()
    first = tickets.issue()
    second = tickets.issue()
    # A fresh observation supersedes the previous live one.
    assert tickets.consume(first) is False
    assert tickets.consume(second) is True


def test_observation_ticket_consume_unknown_token_false() -> None:
    tickets = ObservationTicket()
    tickets.issue()
    assert tickets.consume("bogus-token") is False


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_input_busy_is_computer_use_error() -> None:
    lock = InputOwnerLock()
    lock.acquire(session_id="s1", turn_id="t1", principal="alice", window=_window())
    with pytest.raises(InputBusy) as excinfo:
        lock.acquire(session_id="s2", turn_id="t1", principal="bob", window=_window())
    assert isinstance(excinfo.value, ComputerUseError)
    assert excinfo.value.code == "busy"


def test_identity_forged_is_computer_use_error() -> None:
    lock = InputOwnerLock()
    lock.acquire(session_id="s1", turn_id="t1", principal="alice", window=_window())
    with pytest.raises(IdentityForged) as excinfo:
        lock.verify_caller(
            FrozenSessionIdentity(session_id="s1", turn_id="t1", principal="mallory")
        )
    assert isinstance(excinfo.value, ComputerUseError)


# ---------------------------------------------------------------------------
# WindowIdentity / FrozenSessionIdentity
# ---------------------------------------------------------------------------


def test_window_identity_fields() -> None:
    w = WindowIdentity(window_id=7, launch_id="L", pid=99, title="App")
    assert w.window_id == 7
    assert w.launch_id == "L"
    assert w.pid == 99
    assert w.title == "App"


def test_frozen_session_identity_is_immutable() -> None:
    ident = FrozenSessionIdentity(session_id="s1", turn_id="t1", principal="alice")
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        ident.principal = "mallory"  # type: ignore[misc]
