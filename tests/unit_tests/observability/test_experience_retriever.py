"""Unit tests for the ExperienceRetriever (Issue #11 / C3).

Covers scope-priority recall with conflict resolution, Top-K, token-budget
truncation, per-turn single injection (idempotent), fail-closed behaviour, and
injection-record attribution. Stacked on #9/#10; uses a real on-disk SQLite
file under ``tmp_path`` (no mocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.observability.episode_store import Episode, EpisodeTrajectory
from jiuwenswarm.observability.experience_store import (
    ExperienceStore,
    experiences_database_path,
    mine,
)
from jiuwenswarm.observability.experience_retriever import (
    ExperienceRetriever,
    InjectionRecord,
    RetrievalQuery,
    RetrievalResult,
)


_TARGET = "submit the login form"


def _seed_active(
    store: ExperienceStore,
    *,
    target: str,
    outcome: str,
    scope: str,
    ep_ids: tuple[str, ...],
) -> str:
    """Seed one active experience (2 episodes so the quality gate passes)."""
    episodes = [
        Episode(
            id=eid,
            session_id="sess",
            turn_id="turn",
            objective=target,
            outcome=outcome,
            trajectory=EpisodeTrajectory(),
        )
        for eid in ep_ids
    ]
    exp = mine(episodes, scope=scope)[0]  # type: ignore[arg-type]
    assert exp.status == "active", f"seed experience should be active, got {exp.status}"
    created = store.create(exp)
    return created.id


def _retriever(tmp_path: Path) -> ExperienceRetriever:
    db = experiences_database_path(workspace=tmp_path)
    retriever = ExperienceRetriever(db)
    retriever.ensure()
    return retriever


# ---------------------------------------------------------------------------
# Scope priority + conflict resolution
# ---------------------------------------------------------------------------


def test_retrieve_higher_scope_wins_conflict(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    # Global "avoid" vs user "use" on the same target → user wins, global dropped.
    avoid_id = _seed_active(
        store, target=_TARGET, outcome="failure", scope="global", ep_ids=("g1", "g2")
    )
    use_id = _seed_active(
        store, target=_TARGET, outcome="success", scope="user", ep_ids=("u1", "u2")
    )
    retriever = _retriever(tmp_path)
    result = retriever.retrieve(
        RetrievalQuery(
            objective=_TARGET, scope="user", run_id="r1", session_id="s1", turn_id="t1"
        )
    )
    ids = {r.experience_id for r in result.records}
    assert use_id in ids
    assert avoid_id not in ids  # conflicting lower-scope experience dropped
    assert result.source == "experience_store"
    assert result.already_injected is False


# ---------------------------------------------------------------------------
# Top-K
# ---------------------------------------------------------------------------


def test_retrieve_top_k_truncation(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    for i in range(3):
        _seed_active(
            store,
            target=f"perform distinct task number {i}",
            outcome="success",
            scope="user",
            ep_ids=(f"t{i}-1", f"t{i}-2"),
        )
    retriever = _retriever(tmp_path)
    result = retriever.retrieve(
        RetrievalQuery(
            objective="perform distinct task",
            scope="user",
            top_k=2,
            token_budget=10000,
            run_id="r1",
            session_id="s1",
            turn_id="t1",
        )
    )
    assert len(result.records) == 2


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def test_retrieve_token_budget_truncates(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    _seed_active(
        store, target="perform the first long task here", outcome="success",
        scope="user", ep_ids=("a1", "a2"),
    )
    _seed_active(
        store, target="perform the second long task here", outcome="success",
        scope="user", ep_ids=("b1", "b2"),
    )
    retriever = _retriever(tmp_path)
    # A tiny budget fits at most one experience body.
    result = retriever.retrieve(
        RetrievalQuery(
            objective="perform the long task",
            scope="user",
            top_k=5,
            token_budget=6,
            run_id="r1",
            session_id="s1",
            turn_id="t1",
        )
    )
    assert result.truncated is True
    assert len(result.records) <= 1


# ---------------------------------------------------------------------------
# Per-turn single injection (idempotent)
# ---------------------------------------------------------------------------


def test_retrieve_per_turn_single_injection(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    _seed_active(
        store, target=_TARGET, outcome="success", scope="user", ep_ids=("u1", "u2")
    )
    retriever = _retriever(tmp_path)
    q = RetrievalQuery(
        objective=_TARGET, scope="user", run_id="r1", session_id="s1", turn_id="t1"
    )
    first = retriever.retrieve(q)
    second = retriever.retrieve(q)
    assert first.already_injected is False
    assert second.already_injected is True
    assert second.content == first.content
    # No new injection records on the second (cached) retrieve.
    assert len(second.records) == 0


def test_retrieve_writes_injection_records_once(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp_id = _seed_active(
        store, target=_TARGET, outcome="success", scope="user", ep_ids=("u1", "u2")
    )
    retriever = _retriever(tmp_path)
    q = RetrievalQuery(
        objective=_TARGET, scope="user", run_id="r1", session_id="s1", turn_id="t1"
    )
    first = retriever.retrieve(q)
    # Attribution: the injected record matches the experience that was injected.
    assert any(r.experience_id == exp_id for r in first.records)
    retriever.retrieve(q)  # cached, no new records
    # Re-querying a different turn writes again but does not duplicate for r1/t1.
    injections = retriever.list_injections(run_id="r1")
    # Each (run_id, experience_id) appears exactly once.
    seen = [r.experience_id for r in injections]
    assert seen.count(exp_id) == 1


def test_retrieve_use_count_increments_once_across_turns(tmp_path: Path) -> None:
    """use_count must advance once per (run_id, experience_id), not per turn.

    A cross-turn re-injection of the same experience for the same run hits the
    ``UNIQUE(run_id, experience_id)`` constraint (no new row) — use_count must
    NOT increment again. Guards the P1 where ``rowcount``/``lastrowid`` could
    not distinguish insert from conflict-update.
    """
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    exp_id = _seed_active(
        store, target=_TARGET, outcome="success", scope="user", ep_ids=("u1", "u2")
    )
    retriever = _retriever(tmp_path)
    base = dict(objective=_TARGET, scope="user", run_id="r1", session_id="s1")
    retriever.retrieve(RetrievalQuery(turn_id="t1", **base))  # type: ignore[arg-type]
    retriever.retrieve(RetrievalQuery(turn_id="t2", **base))  # type: ignore[arg-type]
    assert store.get(exp_id).use_count == 1  # NOT 2


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def test_retrieve_fail_closed_on_exception(tmp_path: Path) -> None:
    # A path whose parent is a file cannot be opened or mkdir'd; retrieve must
    # catch the failure and return empty + unavailable, never raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    retriever = ExperienceRetriever(blocker / "x.db")
    result = retriever.retrieve(
        RetrievalQuery(
            objective=_TARGET, scope="user", run_id="r1", session_id="s1", turn_id="t1"
        )
    )
    assert result.content == ""
    assert result.records == ()
    assert result.source == "unavailable"


# ---------------------------------------------------------------------------
# Only active experiences are injected
# ---------------------------------------------------------------------------


def test_retrieve_only_active_injected(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    # Single-episode experience → candidate, not active.
    single = mine(
        [Episode(id="c1", session_id="s", turn_id="t", objective=_TARGET,
                 outcome="failure", trajectory=EpisodeTrajectory())],
        scope="user",
    )[0]
    assert single.status == "candidate"
    store.create(single)
    retriever = _retriever(tmp_path)
    result = retriever.retrieve(
        RetrievalQuery(
            objective=_TARGET, scope="user", run_id="r1", session_id="s1", turn_id="t1"
        )
    )
    assert result.records == ()
    assert result.content == ""


def test_retrieve_empty_when_no_matches(tmp_path: Path) -> None:
    store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    store.ensure()
    retriever = _retriever(tmp_path)
    result = retriever.retrieve(
        RetrievalQuery(
            objective="something totally unrelated",
            scope="user",
            run_id="r1",
            session_id="s1",
            turn_id="t1",
        )
    )
    assert result.records == ()
    assert result.content == ""
    assert result.source == "experience_store"
