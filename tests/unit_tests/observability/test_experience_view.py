"""Unit tests for the experience-memory view data layer (Issue #12 / C4, slice 12a).

Covers the pure-Python read layer that builds the structured DTOs the
visualization (and later the RPC handler / frontend panel) consume from the
C1/C2/C3 stores: a session→episode→step tree, experience rows with injection
counts, and injection attribution deduped by run. Uses real on-disk SQLite
files under ``tmp_path`` (no mocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.observability.episode_store import (
    Episode,
    EpisodeStore,
    EpisodeTrajectory,
    episodes_database_path,
    project_trajectory,
)
from jiuwenswarm.observability.experience_retriever import (
    ExperienceRetriever,
    RetrievalQuery,
)
from jiuwenswarm.observability.experience_store import (
    ExperienceStore,
    experiences_database_path,
    mine,
)
from jiuwenswarm.observability.experience_view import (
    AttributionStats,
    EpisodeStepNode,
    EpisodeTreeNode,
    ExperienceRow,
    SessionTree,
    build_attribution,
    build_experience_rows,
    build_session_tree,
)


def _seed_episode(
    store: EpisodeStore,
    *,
    session_id: str,
    turn_id: str,
    objective: str,
    outcome: str,
    spans: list[dict],
) -> str:
    trajectory = project_trajectory(spans)
    ep = Episode(
        session_id=session_id,
        turn_id=turn_id,
        objective=objective,
        outcome=outcome,
        trajectory=trajectory,
    )
    return store.create_episode(ep).id


# ---------------------------------------------------------------------------
# build_session_tree: session → episode → step monotonic containment
# ---------------------------------------------------------------------------


def test_build_session_tree_groups_episodes_and_steps(tmp_path: Path) -> None:
    episode_store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    episode_store.ensure()
    spans = [
        {"span_id": "root", "parent_span_id": None, "tool_name": "shell",
         "execution_subject_id": "main"},
        {"span_id": "child", "parent_span_id": "root", "tool_name": "browser_use",
         "execution_subject_id": "cua_agent"},
    ]
    _seed_episode(episode_store, session_id="s-A", turn_id="t1",
                  objective="open notepad", outcome="success", spans=spans)
    _seed_episode(episode_store, session_id="s-A", turn_id="t2",
                  objective="close notepad", outcome="success", spans=spans)
    _seed_episode(episode_store, session_id="s-B", turn_id="t1",
                  objective="other session", outcome="failure", spans=spans)

    tree = build_session_tree("s-A", episode_store)
    assert isinstance(tree, SessionTree)
    assert tree.session_id == "s-A"
    # Only s-A's episodes surface; s-B is excluded (monotonic containment).
    assert {ep.turn_id for ep in tree.episodes} == {"t1", "t2"}
    # Each episode's steps come from its own trajectory projection.
    first = tree.episodes[0]
    assert isinstance(first, EpisodeTreeNode)
    step_kinds = {s.kind for s in first.steps}
    assert "task" in step_kinds  # root span
    assert "tool" in step_kinds  # shell + browser_use
    assert "member" in step_kinds  # main + cua_agent
    assert "handoff" in step_kinds  # main->cua_agent


def test_build_session_tree_empty_session_is_not_an_error(tmp_path: Path) -> None:
    episode_store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    episode_store.ensure()
    tree = build_session_tree("nonexistent-session", episode_store)
    assert tree.session_id == "nonexistent-session"
    assert tree.episodes == ()


# ---------------------------------------------------------------------------
# build_experience_rows: experience + injection count
# ---------------------------------------------------------------------------


def _seed_active_experience(
    store: ExperienceStore,
    *,
    target: str,
    outcome: str,
    scope: str,
    ep_ids: tuple[str, ...],
) -> str:
    episodes = [
        Episode(id=eid, session_id="s", turn_id="t", objective=target,
                 outcome=outcome, trajectory=EpisodeTrajectory())
        for eid in ep_ids
    ]
    return store.create(mine(episodes, scope=scope)[0]).id  # type: ignore[arg-type]


def test_build_experience_rows_with_injection_count(tmp_path: Path) -> None:
    exp_store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    exp_store.ensure()
    retriever = ExperienceRetriever(experiences_database_path(workspace=tmp_path))
    retriever.ensure()
    exp_id = _seed_active_experience(
        exp_store, target="submit the login form", outcome="success",
        scope="user", ep_ids=("u1", "u2"),
    )
    # Inject once for run r1 (turn t1) and once for run r2 (turn t1) → 2 injections.
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user", run_id="r1", session_id="s", turn_id="t1"))
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user", run_id="r2", session_id="s", turn_id="t1"))

    rows = build_experience_rows(experiences_database_path(workspace=tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ExperienceRow)
    assert row.id == exp_id
    assert row.content == "use submit the login form"
    assert row.injection_count == 2
    assert row.scope_level == "user"
    assert row.status == "active"


def test_build_experience_rows_empty(tmp_path: Path) -> None:
    rows = build_experience_rows(experiences_database_path(workspace=tmp_path))
    assert rows == []


# ---------------------------------------------------------------------------
# build_attribution: dedup by run_id
# ---------------------------------------------------------------------------


def test_build_attribution_dedup_by_run(tmp_path: Path) -> None:
    exp_store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    exp_store.ensure()
    retriever = ExperienceRetriever(experiences_database_path(workspace=tmp_path))
    retriever.ensure()
    _seed_active_experience(
        exp_store, target="submit the login form", outcome="success",
        scope="user", ep_ids=("u1", "u2"),
    )
    # Two turns for the SAME run r1 → UNIQUE(run_id, experience) dedups to 1.
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user", run_id="r1", session_id="s", turn_id="t1"))
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user", run_id="r1", session_id="s", turn_id="t2"))

    stats = build_attribution(experiences_database_path(workspace=tmp_path))
    assert isinstance(stats, AttributionStats)
    assert stats.total_experiences == 1
    assert stats.total_injections == 1  # deduped by run_id
    assert stats.per_experience[0].injection_count == 1
    assert stats.per_experience[0].runs == ("r1",)


def test_build_attribution_empty(tmp_path: Path) -> None:
    stats = build_attribution(experiences_database_path(workspace=tmp_path))
    assert stats.total_experiences == 0
    assert stats.total_injections == 0
    assert stats.per_experience == ()
