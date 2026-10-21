"""Real-Chrome lifecycle tests for the managed browser (Issue #2 / A1, slice 2c).

Launches a real headless Chrome subprocess, probes the CDP ``/json/version``
endpoint, and verifies stop. Headless-rejection and URL-scheme validation for
``browser.open`` are pure logic over the config. No mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.agents.swarm.providers.browser_lifecycle import (
    BrowserConfig,
    BrowserLifecycle,
)

_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CHROME_PRESENT = Path(_CHROME).exists()


@pytest.fixture
def lifecycle(tmp_path: Path) -> BrowserLifecycle:
    return BrowserLifecycle(
        BrowserConfig(
            chrome_path=_CHROME,
            port=9333,
            user_data_dir=str(tmp_path / "chrome-profile"),
            headless=True,
        )
    )


@pytest.mark.skipif(not _CHROME_PRESENT, reason="requires Google Chrome.app")
def test_lifecycle_starts_headless_chrome_and_probes_cdp(lifecycle: BrowserLifecycle) -> None:
    status = lifecycle.start()
    try:
        assert status.running is True
        assert status.pid is not None
        # The CDP /json/version endpoint responded (start() waited for it).
    finally:
        lifecycle.stop()
    assert lifecycle.status().running is False


@pytest.mark.skipif(not _CHROME_PRESENT, reason="requires Google Chrome.app")
def test_lifecycle_stop_is_idempotent(lifecycle: BrowserLifecycle) -> None:
    lifecycle.start()
    lifecycle.stop()
    # A second stop must not raise.
    lifecycle.stop()
    assert lifecycle.status().running is False


def test_open_url_rejects_headless_config() -> None:
    life = BrowserLifecycle(
        BrowserConfig(chrome_path=_CHROME, port=9334, headless=True)
    )
    ok, msg = life.open_url("https://example.com")
    assert ok is False
    assert "headless" in msg


def test_open_url_rejects_non_http_schemes() -> None:
    life = BrowserLifecycle(
        BrowserConfig(chrome_path=_CHROME, port=9335, headless=False)
    )
    ok, _ = life.open_url("file:///etc/passwd")
    assert ok is False
    ok, _ = life.open_url("javascript:void(0)")
    assert ok is False
    ok, _ = life.open_url("https://example.com")
    assert ok is True
