# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Managed Chrome lifecycle (Issue #2 / A1, slice 2c).

Launches and stops a dedicated Chrome process with ``--remote-debugging-port``
and a persistent ``--user-data-dir`` (cangming ADR-004 / #184): the managed
browser is the agent's durable, login-stateful Chrome. ``headless`` is a
config flag the lifecycle honors; the management adapter (``browser.open``)
additionally rejects headless for login-guidance opens, mirroring cangming's
visible-Chrome contract.

Real-Chrome lifecycle tests launch a headless Chrome subprocess and probe the
CDP ``/json/version`` endpoint; no mocks.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Managed-Chrome configuration.

    Attributes:
        chrome_path: Absolute path to the Chrome binary.
        port: The remote-debugging port.
        user_data_dir: The persistent profile directory.
        headless: Whether to launch headless (production login guidance is
            visible, so ``browser.open`` rejects headless regardless).
    """

    chrome_path: str
    port: int = 9222
    user_data_dir: str = ""
    headless: bool = True


@dataclass(frozen=True, slots=True)
class BrowserStatus:
    """The lifecycle's current status."""

    running: bool
    port: int
    headless: bool
    pid: int | None = None
    error: str | None = None


class BrowserLifecycle:
    """Owns one managed Chrome subprocess.

    The lifecycle is process-local; ``stop`` terminates the subprocess. Repeated
    ``start`` on a running browser is a no-op. ``open`` validates the URL scheme
    (http/https only) and rejects headless (login guidance needs a visible
    window); the actual tab navigation is delegated to the ``browser_use`` tool.
    """

    def __init__(self, config: BrowserConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def config(self) -> BrowserConfig:
        return self._config

    def status(self) -> BrowserStatus:
        running = self._proc is not None and self._proc.poll() is None
        return BrowserStatus(
            running=running,
            port=self._config.port,
            headless=self._config.headless,
            pid=self._proc.pid if self._proc is not None else None,
        )

    def start(self) -> BrowserStatus:
        """Launch the managed Chrome subprocess (no-op if already running)."""
        if self._proc is not None and self._proc.poll() is None:
            return self.status()
        user_data_dir = self._config.user_data_dir or str(
            Path.home() / ".jiuwenswarm" / "chrome-profile"
        )
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        args = [
            self._config.chrome_path,
            f"--remote-debugging-port={self._config.port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self._config.headless:
            args.append("--headless=new")
        try:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            return BrowserStatus(
                running=False, port=self._config.port,
                headless=self._config.headless, error=str(exc),
            )
        # Wait for the CDP endpoint to respond (≤ 10s).
        if not self._wait_for_cdp(timeout=10.0):
            return BrowserStatus(
                running=True, port=self._config.port,
                headless=self._config.headless, pid=self._proc.pid,
                error="Chrome started but CDP /json/version did not respond",
            )
        return self.status()

    def stop(self) -> BrowserStatus:
        """Terminate the managed Chrome subprocess."""
        if self._proc is None:
            return self.status()
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
        return self.status()

    def restart(self) -> BrowserStatus:
        """Stop, then start again."""
        self.stop()
        return self.start()

    # -- login-guidance open ------------------------------------------

    def open_url(self, url: str) -> tuple[bool, str]:
        """Validate a login-guidance open. Returns ``(ok, message)``.

        Mirrors cangming ADR-004: the managed browser is visible, so a headless
        config rejects the open; only http/https URLs are accepted. The actual
        navigation is performed by the ``browser_use`` tool on the live page.

        Args:
            url: The URL to open.

        Returns:
            ``(True, "")`` on acceptance; ``(False, reason)`` on rejection.
        """
        if self._config.headless:
            return False, "headless mode rejects browser.open (login guidance needs a visible window)"
        if not url.startswith(("http://", "https://")):
            return False, "browser.open accepts http/https URLs only"
        return True, ""

    # -- helpers -------------------------------------------------------

    def _wait_for_cdp(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{self._config.port}/json/version"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:  # noqa: S310
                    json.loads(resp.read().decode("utf-8"))
                return True
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        return False


__all__ = ["BrowserConfig", "BrowserLifecycle", "BrowserStatus"]
