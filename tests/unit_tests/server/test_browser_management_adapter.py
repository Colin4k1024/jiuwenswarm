"""Unit tests for the BrowserManagementAdapter (Issue #2 / A1, slice 2c).

Uses a stub lifecycle (no real Chrome) so the adapter's method dispatch,
open-validation (headless rejected / http-https only), and error paths are
exercised without a subprocess. Real-Chrome lifecycle behaviour is covered by
``test_browser_lifecycle.py``.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.swarm.providers.browser_lifecycle import (
    BrowserConfig,
    BrowserStatus,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.gateway_adapter.browser_management_adapter import (
    BrowserManagementAdapter,
)


class _StubLifecycle:
    def __init__(self, *, headless: bool = False) -> None:
        self.config = BrowserConfig(chrome_path="/x/chrome", port=9222, headless=headless)
        self.calls: list[str] = []

    def status(self) -> BrowserStatus:
        return BrowserStatus(running=False, port=9222, headless=self.config.headless)

    def start(self) -> BrowserStatus:
        self.calls.append("start")
        return BrowserStatus(running=True, port=9222, headless=self.config.headless, pid=123)

    def stop(self) -> BrowserStatus:
        self.calls.append("stop")
        return BrowserStatus(running=False, port=9222, headless=self.config.headless)

    def restart(self) -> BrowserStatus:
        self.calls.append("restart")
        return BrowserStatus(running=True, port=9222, headless=self.config.headless, pid=124)

    def open_url(self, url: str) -> tuple[bool, str]:
        if self.config.headless:
            return False, "headless mode rejects browser.open"
        if not url.startswith(("http://", "https://")):
            return False, "http/https only"
        return True, ""


def _request(method: ReqMethod, **params) -> AgentRequest:
    return AgentRequest(request_id="r1", channel_id="web", req_method=method, params=params)


@pytest.mark.asyncio
async def test_browser_status_returns_lifecycle_status() -> None:
    adapter = BrowserManagementAdapter(_StubLifecycle())
    response = await adapter.handle(_request(ReqMethod.BROWSER_STATUS))
    assert response.ok is True
    assert response.payload["running"] is False
    assert response.payload["port"] == 9222


@pytest.mark.asyncio
async def test_browser_start_calls_lifecycle_start() -> None:
    life = _StubLifecycle()
    response = await BrowserManagementAdapter(life).handle(_request(ReqMethod.BROWSER_START))
    assert response.ok is True
    assert response.payload["running"] is True
    assert life.calls == ["start"]


@pytest.mark.asyncio
async def test_browser_restart_calls_restart() -> None:
    life = _StubLifecycle()
    await BrowserManagementAdapter(life).handle(_request(ReqMethod.BROWSER_RESTART))
    assert life.calls == ["restart"]


@pytest.mark.asyncio
async def test_browser_config_returns_resolved_config() -> None:
    response = await BrowserManagementAdapter(_StubLifecycle()).handle(_request(ReqMethod.BROWSER_CONFIG))
    assert response.ok is True
    assert response.payload["chrome_path"] == "/x/chrome"


@pytest.mark.asyncio
async def test_browser_open_accepts_https() -> None:
    response = await BrowserManagementAdapter(_StubLifecycle(headless=False)).handle(
        _request(ReqMethod.BROWSER_OPEN, url="https://example.com")
    )
    assert response.ok is True
    assert response.payload["accepted"] is True


@pytest.mark.asyncio
async def test_browser_open_rejects_headless() -> None:
    response = await BrowserManagementAdapter(_StubLifecycle(headless=True)).handle(
        _request(ReqMethod.BROWSER_OPEN, url="https://example.com")
    )
    assert response.ok is False
    assert response.payload.get("code") == "BAD_REQUEST"
    assert "headless" in (response.payload.get("error") or "")


@pytest.mark.asyncio
async def test_browser_open_rejects_non_http() -> None:
    response = await BrowserManagementAdapter(_StubLifecycle(headless=False)).handle(
        _request(ReqMethod.BROWSER_OPEN, url="file:///etc")
    )
    assert response.ok is False
    assert response.payload.get("code") == "BAD_REQUEST"
