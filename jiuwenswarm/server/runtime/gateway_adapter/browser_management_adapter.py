# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Managed-Chrome management RPC adapter (Issue #2 / A1, slice 2c).

Exposes the ``BrowserLifecycle`` over the Gateway RPC plane:

- ``browser.status`` → lifecycle status;
- ``browser.start`` / ``browser.stop`` / ``browser.restart`` → lifecycle control;
- ``browser.config`` → resolved ``BrowserConfig``;
- ``browser.open`` → login-guidance open validation (headless rejected,
  http/https only; the actual navigation is done by the ``browser_use`` tool).

The adapter is a thin delegate; the lifecycle (subprocess + CDP probe) is the
substance and is exercised by real-Chrome tests.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import ClassVar

from jiuwenswarm.agents.swarm.providers.browser_lifecycle import (
    BrowserConfig,
    BrowserLifecycle,
)
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.control.responses import build_error_response
from jiuwenswarm.server.runtime.gateway_adapter.base import GatewayAdapter


class BrowserManagementAdapter(GatewayAdapter):
    """Gateway RPC adapter for the managed-Chrome lifecycle."""

    methods: ClassVar[frozenset[str]] = frozenset(
        {
            ReqMethod.BROWSER_STATUS.value,
            ReqMethod.BROWSER_START.value,
            ReqMethod.BROWSER_STOP.value,
            ReqMethod.BROWSER_RESTART.value,
            ReqMethod.BROWSER_CONFIG.value,
            ReqMethod.BROWSER_OPEN.value,
        }
    )

    def __init__(self, lifecycle: BrowserLifecycle) -> None:
        self._lifecycle = lifecycle

    async def handle(self, request: AgentRequest) -> AgentResponse:
        method = request.req_method
        try:
            if method == ReqMethod.BROWSER_STATUS:
                return self._ok(request, asdict(self._lifecycle.status()))
            if method == ReqMethod.BROWSER_START:
                return self._ok(request, asdict(self._lifecycle.start()))
            if method == ReqMethod.BROWSER_STOP:
                return self._ok(request, asdict(self._lifecycle.stop()))
            if method == ReqMethod.BROWSER_RESTART:
                return self._ok(request, asdict(self._lifecycle.restart()))
            if method == ReqMethod.BROWSER_CONFIG:
                return self._ok(request, asdict(self._lifecycle.config))
            if method == ReqMethod.BROWSER_OPEN:
                return self._handle_open(request)
        except Exception as exc:  # noqa: BLE001
            return build_error_response(request, str(exc), code="INTERNAL_ERROR")
        return build_error_response(
            request, f"unsupported method {method!r}", code="BAD_REQUEST"
        )

    def _handle_open(self, request: AgentRequest) -> AgentResponse:
        params = request.params if isinstance(request.params, dict) else {}
        url = str(params.get("url") or "")
        ok, message = self._lifecycle.open_url(url)
        if not ok:
            return build_error_response(request, message, code="BAD_REQUEST")
        return self._ok(request, {"url": url, "accepted": True})

    @staticmethod
    def _ok(request: AgentRequest, payload: dict) -> AgentResponse:
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
            metadata=request.metadata,
        )


__all__ = ["BrowserManagementAdapter"]
