"""Unit tests for the ExperienceAdapter (Issue #12 / C4, slice 12b).

Covers the three read-only RPCs (``episodes.list`` / ``experiences.list`` /
``injections.attribution``) over the C1/C2/C3 stores: payload shape, the
session_id requirement, and graceful empty-state behaviour on a fresh
workspace. The adapter is constructed with an injected workspace so tests use a
real on-disk SQLite file under ``tmp_path`` (no mocks).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
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
from jiuwenswarm.server.runtime.gateway_adapter.experience_adapter import (
    ExperienceAdapter,
)


def _adapter(tmp_path: Path) -> ExperienceAdapter:
    return ExperienceAdapter(workspace=str(tmp_path))


def _request(method: ReqMethod, *, session_id: str | None = None) -> AgentRequest:
    params = {}
    if session_id is not None:
        params["session_id"] = session_id
    return AgentRequest(
        request_id="req-1",
        channel_id="web",
        session_id=session_id,
        req_method=method,
        params=params,
    )


# ---------------------------------------------------------------------------
# episodes.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_episodes_list_returns_tree(tmp_path: Path) -> None:
    store = EpisodeStore(episodes_database_path(workspace=tmp_path))
    store.ensure()
    spans = [
        {"span_id": "root", "parent_span_id": None, "tool_name": "shell",
         "execution_subject_id": "main"},
        {"span_id": "child", "parent_span_id": "root", "tool_name": "browser_use",
         "execution_subject_id": "cua_agent"},
    ]
    store.create_episode(
        Episode(session_id="s-A", turn_id="t1", objective="open notepad",
                 outcome="success", trajectory=project_trajectory(spans))
    )
    response = await _adapter(tmp_path).handle(_request(ReqMethod.EPISODES_LIST, session_id="s-A"))
    assert response.ok is True
    assert response.payload["session_id"] == "s-A"
    episodes = response.payload["episodes"]
    assert len(episodes) == 1
    steps = episodes[0]["steps"]
    step_kinds = {s["kind"] for s in steps}
    assert {"task", "tool", "member", "handoff"} <= step_kinds


@pytest.mark.asyncio
async def test_adapter_episodes_list_requires_session_id(tmp_path: Path) -> None:
    response = await _adapter(tmp_path).handle(_request(ReqMethod.EPISODES_LIST))
    assert response.ok is False
    assert response.payload.get("code") == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_adapter_episodes_list_empty_session_is_ok(tmp_path: Path) -> None:
    response = await _adapter(tmp_path).handle(
        _request(ReqMethod.EPISODES_LIST, session_id="unknown")
    )
    assert response.ok is True
    assert response.payload["episodes"] == []


# ---------------------------------------------------------------------------
# experiences.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_experiences_list_returns_rows(tmp_path: Path) -> None:
    exp_store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    exp_store.ensure()
    retriever = ExperienceRetriever(experiences_database_path(workspace=tmp_path))
    retriever.ensure()
    episodes = [
        Episode(id=f"e{i}", session_id="s", turn_id="t",
                 objective="submit the login form", outcome="success",
                 trajectory=EpisodeTrajectory())
        for i in range(2)
    ]
    exp_store.create(mine(episodes, scope="user")[0])
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user",
        run_id="r1", session_id="s", turn_id="t1"))

    response = await _adapter(tmp_path).handle(_request(ReqMethod.EXPERIENCES_LIST))
    assert response.ok is True
    rows = response.payload["experiences"]
    assert len(rows) == 1
    assert rows[0]["content"] == "use submit the login form"
    assert rows[0]["injection_count"] == 1


# ---------------------------------------------------------------------------
# injections.attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_injections_attribution(tmp_path: Path) -> None:
    exp_store = ExperienceStore(experiences_database_path(workspace=tmp_path))
    exp_store.ensure()
    retriever = ExperienceRetriever(experiences_database_path(workspace=tmp_path))
    retriever.ensure()
    episodes = [
        Episode(id=f"e{i}", session_id="s", turn_id="t",
                 objective="submit the login form", outcome="success",
                 trajectory=EpisodeTrajectory())
        for i in range(2)
    ]
    exp_store.create(mine(episodes, scope="user")[0])
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user",
        run_id="r1", session_id="s", turn_id="t1"))
    retriever.retrieve(RetrievalQuery(
        objective="submit the login form", scope="user",
        run_id="r2", session_id="s", turn_id="t1"))

    response = await _adapter(tmp_path).handle(_request(ReqMethod.INJECTIONS_ATTRIBUTION))
    assert response.ok is True
    assert response.payload["total_injections"] == 2
    assert response.payload["total_experiences"] == 1
    assert response.payload["per_experience"][0]["runs"] == ["r1", "r2"]


# ---------------------------------------------------------------------------
# Fresh workspace: all three return an empty state, never an error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_fresh_workspace_all_empty(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    episodes_resp = await adapter.handle(
        _request(ReqMethod.EPISODES_LIST, session_id="s"))
    experiences_resp = await adapter.handle(_request(ReqMethod.EXPERIENCES_LIST))
    injections_resp = await adapter.handle(_request(ReqMethod.INJECTIONS_ATTRIBUTION))
    for resp in (episodes_resp, experiences_resp, injections_resp):
        assert resp.ok is True
    assert episodes_resp.payload["episodes"] == []
    assert experiences_resp.payload["experiences"] == []
    assert injections_resp.payload["total_injections"] == 0
