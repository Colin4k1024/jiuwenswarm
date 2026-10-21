"""Unit tests for the ExperienceStore and ExperienceMiner (Issue #10 / C2).

Covers the pure-function miner (deterministic, side-effect-free clustering with
the quality gate), the candidate/active lifecycle, the 4-level scope, and the
store's create/get/list/search/idempotent invariants. Stacked on #9's
EpisodeStore; uses a real on-disk SQLite file under ``tmp_path`` (no mocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.observability.episode_store import (
    Episode,
    EpisodeTrajectory,
    project_trajectory,
)
from jiuwenswarm.observability.experience_store import (
    Experience,
    ExperienceScope,
    ExperienceStatus,
    ExperienceStore,
    experiences_database_path,
    mine,
)


def _episode(
    *,
    episode_id: str,
    objective: str,
    outcome: str,
    tools: tuple[str, ...] = (),
) -> Episode:
    return Episode(
        id=episode_id,
        session_id="sess-1",
        turn_id="turn-1",
        objective=objective,
        outcome=outcome,
        trajectory=EpisodeTrajectory(tools=tools),
    )


# ---------------------------------------------------------------------------
# Miner: clustering, quality gate, purity
# ---------------------------------------------------------------------------


def test_mine_two_failures_same_target_cluster_into_one_active_avoid() -> None:
    episodes = [
        _episode(episode_id="e1", objective="submit the login form", outcome="failure"),
        _episode(episode_id="e2", objective="submit the login form", outcome="failure"),
    ]
    out = mine(episodes, scope="user")
    assert len(out) == 1
    exp = out[0]
    assert exp.content == "avoid submit the login form"
    assert exp.evidence_count == 2
    assert exp.source_episode_ids == ("e1", "e2")
    assert exp.status == "active"  # evidence>=2 and confidence>=0.3 and content>20
    assert exp.scope_level == "user"
    assert exp.target_hints == ("submit the login form",)


def test_mine_single_episode_is_candidate_not_active() -> None:
    episodes = [_episode(episode_id="e1", objective="submit the login form", outcome="failure")]
    out = mine(episodes, scope="user")
    assert len(out) == 1
    assert out[0].status == "candidate"
    assert out[0].evidence_count == 1


def test_mine_success_cluster_uses_strategy() -> None:
    episodes = [
        _episode(episode_id="e1", objective="open the notepad app", outcome="success"),
        _episode(episode_id="e2", objective="open the notepad app", outcome="success"),
    ]
    out = mine(episodes, scope="project")
    assert len(out) == 1
    assert out[0].content == "use open the notepad app"
    assert out[0].status == "active"
    assert out[0].scope_level == "project"


def test_mine_mixed_failure_then_success_is_retry_with() -> None:
    episodes = [
        _episode(episode_id="e1", objective="deploy the service", outcome="failure"),
        _episode(episode_id="e2", objective="deploy the service", outcome="success"),
    ]
    out = mine(episodes, scope="user")
    assert len(out) == 1
    assert out[0].content == "retry with deploy the service"


def test_mine_drops_short_content() -> None:
    # target "x" → content "avoid x" (7 chars) ≤ 20 → dropped.
    episodes = [_episode(episode_id="e1", objective="x", outcome="failure")]
    assert mine(episodes, scope="user") == []


def test_mine_is_pure_function_deterministic() -> None:
    episodes = [
        _episode(episode_id="e1", objective="submit the login form", outcome="failure"),
        _episode(episode_id="e2", objective="submit the login form", outcome="failure"),
    ]
    first = mine(episodes, scope="user")
    second = mine(episodes, scope="user")
    assert first == second


# ---------------------------------------------------------------------------
# ExperienceStore: create/get/list/search/idempotent/lifecycle
# ---------------------------------------------------------------------------


def test_experience_store_create_and_get(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp = mine([_episode(episode_id="e1", objective="submit the login form", outcome="failure")], scope="user")[0]
    created = store.create(exp)
    assert created.id  # store-assigned
    fetched = store.get(created.id)
    assert fetched is not None
    assert fetched.content == "avoid submit the login form"
    assert fetched.status == "candidate"  # single episode
    assert fetched.scope_level == "user"


def test_experience_store_idempotent_on_source_episode_ids(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp = mine(
        [
            _episode(episode_id="e1", objective="submit the login form", outcome="failure"),
            _episode(episode_id="e2", objective="submit the login form", outcome="failure"),
        ],
        scope="user",
    )[0]
    first = store.create(exp)
    second = store.create(exp)  # same source_episode_ids → no duplicate
    assert second.id == first.id
    rows = store.list_experiences()
    assert len(rows) == 1


def test_experience_store_search_by_token(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp = mine(
        [
            _episode(episode_id="e1", objective="submit the login form", outcome="failure"),
            _episode(episode_id="e2", objective="submit the login form", outcome="failure"),
        ],
        scope="user",
    )[0]
    store.create(exp)
    hits = store.search("login")
    assert len(hits) == 1
    assert "login" in hits[0].content
    assert store.search("nonexistent-token") == []


def test_experience_store_status_lifecycle_update(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp = mine([_episode(episode_id="e1", objective="submit the login form", outcome="failure")], scope="user")[0]
    created = store.create(exp)
    assert created.status == "candidate"
    updated = store.update_status(created.id, "active")
    assert updated.status == "active"
    assert store.get(created.id).status == "active"


def test_experience_store_scope_levels_distinct(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    for scope in ("user", "project", "tenant", "global"):
        exp = mine(
            [_episode(episode_id=f"e-{scope}", objective="submit the login form", outcome="failure")],
            scope=scope,  # type: ignore[arg-type]
        )[0]
        store.create(exp)
    rows = store.list_experiences()
    assert {r.scope_level for r in rows} == {"user", "project", "tenant", "global"}
