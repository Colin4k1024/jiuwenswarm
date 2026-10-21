# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Experience-memory visualization RPC adapter (Issue #12 / C4, slice 12b).

A read-only ``GatewayAdapter`` exposing the C1/C2/C3 stores over the Gateway
RPC plane:

- ``episodes.list`` → ``build_session_tree`` (session → episode → step tree);
- ``experiences.list`` → ``build_experience_rows`` (experience rows + injection counts);
- ``injections.attribution`` → ``build_attribution`` (injection attribution deduped by run).

A fresh workspace (no episodes/experiences tables yet) returns an empty payload,
never an error: the visualization must render an empty state on first use.

The adapter never mutates a store. It resolves the trace root from the active
workspace (overridable for tests) and reads ``episodes.db`` / ``experiences.db``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
from typing import ClassVar

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.common.utils import get_user_workspace_dir
from jiuwenswarm.observability.episode_store import EpisodeStore, episodes_database_path
from jiuwenswarm.observability.experience_store import (
    experiences_database_path,
)
from jiuwenswarm.observability.experience_view import (
    build_attribution,
    build_experience_rows,
    build_session_tree,
)
from jiuwenswarm.server.control.responses import build_error_response
from jiuwenswarm.server.runtime.gateway_adapter.base import GatewayAdapter

logger = logging.getLogger(__name__)


def _jsonable(value: object) -> object:
    """Normalize a dataclass-derived value for JSON-over-the-wire use.

    ``dataclasses.asdict`` preserves tuples, but the RPC payload crosses the
    wire as JSON where tuples become lists; normalize once here so the shape the
    tests assert matches what the frontend receives.
    """
    return json.loads(json.dumps(value))


class ExperienceAdapter(GatewayAdapter):
    """Read-only adapter for experience-memory visualization RPCs."""

    methods: ClassVar[frozenset[str]] = frozenset(
        {
            ReqMethod.EPISODES_LIST.value,
            ReqMethod.EXPERIENCES_LIST.value,
            ReqMethod.INJECTIONS_ATTRIBUTION.value,
        }
    )

    def __init__(self, *, workspace: str | None = None) -> None:
        # Resolve lazily so importing the adapter never touches the filesystem.
        self._workspace = workspace

    def _resolve_workspace(self) -> str:
        if self._workspace is not None:
            return self._workspace
        return str(get_user_workspace_dir())

    async def handle(self, request: AgentRequest) -> AgentResponse:
        method = request.req_method
        params = request.params if isinstance(request.params, dict) else {}
        workspace = self._resolve_workspace()
        try:
            if method == ReqMethod.EPISODES_LIST:
                return self._handle_episodes_list(request, params, workspace)
            if method == ReqMethod.EXPERIENCES_LIST:
                return self._handle_experiences_list(request, workspace)
            if method == ReqMethod.INJECTIONS_ATTRIBUTION:
                return self._handle_injections_attribution(request, workspace)
        except sqlite3.OperationalError as exc:
            # A fresh workspace has no episodes/experiences tables yet: render
            # an empty state, never an error.
            logger.debug("[ExperienceAdapter] empty store for %s: %s", method, exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=self._empty_payload(method, params),
                metadata=request.metadata,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ExperienceAdapter] %s failed: %s", method, exc)
            return build_error_response(request, str(exc), code="INTERNAL_ERROR")
        return build_error_response(
            request, f"unsupported method {method!r}", code="BAD_REQUEST"
        )

    # -- handlers ------------------------------------------------------

    def _handle_episodes_list(
        self, request: AgentRequest, params: dict, workspace: str
    ) -> AgentResponse:
        session_id = str(params.get("session_id") or request.session_id or "").strip()
        if not session_id:
            return build_error_response(
                request, "session_id is required", code="BAD_REQUEST"
            )
        episode_store = EpisodeStore(episodes_database_path(workspace=workspace))
        tree = build_session_tree(session_id, episode_store)
        payload = _jsonable(dataclasses.asdict(tree))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    def _handle_experiences_list(self, request: AgentRequest, workspace: str) -> AgentResponse:
        rows = build_experience_rows(experiences_database_path(workspace=workspace))
        payload = {"experiences": _jsonable([dataclasses.asdict(row) for row in rows])}
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    def _handle_injections_attribution(
        self, request: AgentRequest, workspace: str
    ) -> AgentResponse:
        stats = build_attribution(experiences_database_path(workspace=workspace))
        payload = _jsonable(dataclasses.asdict(stats))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )

    @staticmethod
    def _empty_payload(method: ReqMethod | None, params: dict) -> dict:
        if method == ReqMethod.EPISODES_LIST:
            return {"session_id": str(params.get("session_id") or ""), "episodes": []}
        if method == ReqMethod.INJECTIONS_ATTRIBUTION:
            return {"total_experiences": 0, "total_injections": 0, "per_experience": []}
        return {"experiences": []}


__all__ = ["ExperienceAdapter"]
