"""Unit tests for the EpisodeStore (Issue #9 / C1).

Covers the cross-session SQLite episode table, the ``EpisodeTrajectory`` JSON
round-trip, the span→trajectory projector, and the create/get/list/idempotent/
reject-empty invariants. Uses a real on-disk SQLite file under ``tmp_path``
(no mocks) per the evidence standard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jiuwenswarm.observability.episode_store import (
    Episode,
    EpisodeStore,
    EpisodeTrajectory,
    episodes_database_path,
    project_trajectory,
)


# ---------------------------------------------------------------------------
# EpisodeTrajectory round-trip
# ---------------------------------------------------------------------------


def test_episode_trajectory_roundtrip() -> None:
    t = EpisodeTrajectory(
        tasks=("span-1", "span-2"),
        tools=("skill_toolkit", "browser_use"),
        members=("main", "cua_agent"),
        handoffs=("main->cua_agent",),
    )
    restored = EpisodeTrajectory.from_json(t.to_json())
    assert restored == t


def test_episode_trajectory_empty_is_constructible() -> None:
    t = EpisodeTrajectory()
    assert t.tasks == () and t.tools == () and t.members == () and t.handoffs == ()
    assert EpisodeTrajectory.from_json(t.to_json()) == t


# ---------------------------------------------------------------------------
# project_trajectory
# ---------------------------------------------------------------------------


def test_project_trajectory_extracts_tools_and_members() -> None:
    spans = [
        {"span_id": "a", "parent_span_id": None, "tool_name": "skill_toolkit",
         "execution_subject_id": "main"},
        {"span_id": "b", "parent_span_id": "a", "tool_name": "browser_use",
         "execution_subject_id": "cua_agent"},
    ]
    t = project_trajectory(spans)
    assert t.tools == ("browser_use", "skill_toolkit")
    assert t.members == ("cua_agent", "main")
    # Root span (parent absent from set) counts as a task.
    assert t.tasks == ("a",)


def test_project_trajectory_records_handoffs() -> None:
    spans = [
        {"span_id": "root", "parent_span_id": None, "tool_name": None,
         "execution_subject_id": "main"},
        {"span_id": "child", "parent_span_id": "root", "tool_name": "computer_use",
         "execution_subject_id": "cua_agent"},
    ]
    t = project_trajectory(spans)
    assert t.handoffs == ("main->cua_agent",)


def test_project_trajectory_empty_spans_yields_empty_trajectory() -> None:
    t = project_trajectory([])
    assert t == EpisodeTrajectory()


# ---------------------------------------------------------------------------
# episodes_database_path
# ---------------------------------------------------------------------------


def test_episodes_database_path_under_trace_root(tmp_path: Path) -> None:
    p = episodes_database_path(workspace=tmp_path)
    assert p == tmp_path / ".trace" / "episodes.db"
    assert p.name == "episodes.db"
    assert p.parent.name == ".trace"


# ---------------------------------------------------------------------------
# EpisodeStore create/get/list/idempotent/reject-empty
# ---------------------------------------------------------------------------


def _episode(
    *,
    session_id: str = "sess-1",
    turn_id: str = "turn-1",
    execution_subject_id: str = "main",
    spans=None,
    objective: str = "open notepad",
) -> Episode:
    trajectory = project_trajectory(spans or [
        {"span_id": "s1", "parent_span_id": None, "tool_name": "shell",
         "execution_subject_id": execution_subject_id},
    ])
    return Episode(
        session_id=session_id,
        turn_id=turn_id,
        execution_subject_id=execution_subject_id,
        objective=objective,
        trajectory=trajectory,
    )


def test_episode_store_create_and_get(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    ep = _episode()
    created = store.create_episode(ep)
    assert created.id  # server-assigned
    fetched = store.get_episode(created.id)
    assert fetched is not None
    assert fetched.session_id == "sess-1"
    assert fetched.turn_id == "turn-1"
    assert fetched.objective == "open notepad"
    assert isinstance(fetched.trajectory, EpisodeTrajectory)
    assert fetched.trajectory.tools == ("shell",)
    assert fetched.scope_level == "user"  # default scope


def test_episode_store_idempotent_upsert(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    first = store.create_episode(_episode(objective="first"))
    # Same (session, turn, subject) → upsert, not a second row.
    second = store.create_episode(_episode(objective="second"))
    assert second.id == first.id
    fetched = store.get_episode(first.id)
    assert fetched is not None
    assert fetched.objective == "second"
    rows = store.list_episodes("sess-1")
    assert len(rows) == 1


def test_episode_store_list_by_session_ordered(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    store.create_episode(_episode(session_id="s-A", turn_id="t1", objective="a1"))
    store.create_episode(_episode(session_id="s-B", turn_id="t1", objective="b1"))
    store.create_episode(_episode(session_id="s-A", turn_id="t2", objective="a2"))
    a_rows = store.list_episodes("s-A")
    assert [r.turn_id for r in a_rows] == ["t1", "t2"]
    assert all(r.session_id == "s-A" for r in a_rows)


def test_episode_store_rejects_empty_trajectory(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    ep = Episode(
        session_id="sess-empty",
        turn_id="turn-empty",
        trajectory=EpisodeTrajectory(),  # no tasks/tools/members/handoffs
    )
    with pytest.raises(ValueError, match="empty"):
        store.create_episode(ep)


def test_episode_store_get_missing_returns_none(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    assert store.get_episode("nonexistent") is None
