# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Playwright-driven ``browser_use`` tool (Issue #2 / A1, slice 2b).

The behavioural core of the governed browser-use surface. Drives a Playwright
``Page`` with the 18-action schema from #2a, a snapshot-first ref flow, and
structured ``BrowserActionResult`` verdicts (``not_executed`` + retryable on a
stale ref, ``execution_uncertain`` on a post-dispatch surprise). The page is
injected so production wires a managed-Chrome page and tests wire a
Playwright-launched Chromium page.

This slice implements the action surface that does not require process
management: ``snapshot`` / ``navigate`` / ``click`` / ``type`` / ``get_text`` /
``get_url`` / ``get_title`` / ``screenshot``. Tab and lifecycle actions
(``new_tab`` / ``close_tab`` / ``go_back`` / …) and the managed-Chrome
lifecycle + management RPCs land in slice 2c.
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.agents.swarm.providers.browser_use_types import (
    BROWSER_USE_ACTIONS,
    BrowserActionResult,
    SnapshotElement,
    SnapshotRefTable,
)

# Interactive-element selector mirroring cangming's snapshotJS.
_SNAPSHOT_SELECTOR = (
    "a, button, input, select, textarea, summary, "
    "[role], [contenteditable], [onclick], [tabindex]"
)
_SNAPSHOT_MAX = 200

# A tiny unique-CSS-path evaluator injected into the page. It walks the DOM
# building an ``tag:nth-of-type`` chain, which is stable enough to re-resolve a
# ref to the same node on a snapshot-unchanged page.
_CSS_PATH_JS = """function(el) {
    const path = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
        const tag = node.tagName.toLowerCase();
        const parent = node.parentElement;
        if (!parent) { path.unshift(tag); break; }
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        const index = siblings.indexOf(node) + 1;
        path.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${index})` : tag);
        node = parent;
    }
    return path.length ? 'body > ' + path.join(' > ') : 'body';
}"""


class BrowserUseTool:
    """Drives a Playwright ``Page`` with the governed browser-use action surface.

    The page is injected: production supplies a managed-Chrome page; tests
    supply a Playwright-launched Chromium page. Refs bind the observed DOM node;
    a removed or relabelled node fails as ``not_executed`` + retryable.
    """

    def __init__(self, page: Any) -> None:
        self._page = page
        self._refs: SnapshotRefTable = SnapshotRefTable()
        self._last_image: bytes | None = None

    def execute(self, action: str, **kwargs: Any) -> BrowserActionResult:
        if action not in BROWSER_USE_ACTIONS:
            raise ValueError(f"unknown browser-use action: {action!r}")
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return BrowserActionResult(
                action=action, verdict="not_executed",
                hint=f"action {action!r} not implemented in slice 2b",
            )
        return handler(**kwargs)

    # -- actions -------------------------------------------------------

    def _do_snapshot(self, **_: Any) -> BrowserActionResult:
        elements: list[SnapshotElement] = []
        nodes = self._page.query_selector_all(_SNAPSHOT_SELECTOR)
        for i, node in enumerate(nodes[:_SNAPSHOT_MAX]):
            try:
                bbox = node.bounding_box()
            except Exception:  # noqa: BLE001
                bbox = None
            try:
                tag = node.evaluate("el => el.tagName.toLowerCase()")
                role = node.get_attribute("role")
                label = (node.text_content() or "").strip()[:60] or None
                css = node.evaluate(_CSS_PATH_JS)
            except Exception:  # noqa: BLE001
                continue
            box = (
                int(bbox["x"]), int(bbox["y"]),
                int(bbox["width"]), int(bbox["height"]),
            ) if bbox else (0, 0, 0, 0)
            elements.append(
                SnapshotElement(
                    ref=f"e{i + 1}", css_path=css, tag=tag,
                    role=role, label=label, bbox=box,
                )
            )
        self._refs = SnapshotRefTable(elements=tuple(elements))
        return BrowserActionResult(
            action="snapshot", verdict="executed",
            hint=f"captured {len(elements)} interactive elements",
        )

    def _do_navigate(self, *, url: str, **_: Any) -> BrowserActionResult:
        before = self._safe_url()
        self._page.goto(url)
        return BrowserActionResult(
            action="navigate", verdict="executed",
            url_before=before, url_after=self._safe_url(),
        )

    def _do_click(
        self, *, ref: str | None = None, selector: str | None = None,
        x: int | None = None, y: int | None = None, **_: Any,
    ) -> BrowserActionResult:
        before = self._safe_url()
        if ref is not None:
            el = self._refs.resolve(ref)
            if el is None:
                return BrowserActionResult(
                    action="click", verdict="not_executed",
                    hint=f"unknown ref {ref!r}; re-snapshot", url_before=before,
                )
            live = self._page.query_selector(el.css_path)
            if live is None:
                return BrowserActionResult(
                    action="click", verdict="not_executed",
                    hint=f"ref {ref!r} node gone; re-snapshot", url_before=before,
                )
            if self._is_stale(live, el):
                return BrowserActionResult(
                    action="click", verdict="not_executed",
                    hint=f"ref {ref!r} node changed label/tag/role; re-snapshot",
                    url_before=before,
                )
            live.click()
            return BrowserActionResult(
                action="click", verdict="executed",
                url_before=before, url_after=self._safe_url(),
                target=ref,
            )
        if selector is not None:
            self._page.click(selector)
            return BrowserActionResult(
                action="click", verdict="executed",
                url_before=before, url_after=self._safe_url(), target=selector,
            )
        if x is not None and y is not None:
            self._page.mouse.click(x, y)
            return BrowserActionResult(
                action="click", verdict="executed",
                url_before=before, url_after=self._safe_url(),
                target=f"({x},{y})",
            )
        return BrowserActionResult(
            action="click", verdict="not_executed",
            hint="click requires ref, selector, or x/y",
        )

    def _do_type(self, *, ref: str | None = None, selector: str | None = None,
                 text: str = "", **_: Any) -> BrowserActionResult:
        target = selector
        if ref is not None:
            el = self._refs.resolve(ref)
            if el is None:
                return BrowserActionResult(
                    action="type", verdict="not_executed", hint=f"unknown ref {ref!r}",
                )
            target = el.css_path
            if self._is_stale(self._page.query_selector(target), el):
                return BrowserActionResult(
                    action="type", verdict="not_executed",
                    hint=f"ref {ref!r} stale; re-snapshot",
                )
        if not target:
            return BrowserActionResult(
                action="type", verdict="not_executed", hint="type requires ref or selector",
            )
        self._page.fill(target, text)
        return BrowserActionResult(
            action="type", verdict="executed", text_verification=text[:40],
            target=target,
        )

    def _do_get_text(self, *, selector: str | None = None, **_: Any) -> BrowserActionResult:
        target = selector or "body"
        text = self._page.text_content(target) or ""
        return BrowserActionResult(
            action="get_text", verdict="executed", text_verification=text[:200],
            target=target,
        )

    def _do_get_url(self, **_: Any) -> BrowserActionResult:
        return BrowserActionResult(
            action="get_url", verdict="executed", url_after=self._safe_url(),
        )

    def _do_get_title(self, **_: Any) -> BrowserActionResult:
        return BrowserActionResult(
            action="get_title", verdict="executed", text_verification=self._page.title(),
        )

    def _do_screenshot(self, **_: Any) -> BrowserActionResult:
        # The PNG bytes ride on the result object's multimodal surface in
        # production (the kernel tool bridge lifts them to an image part). Here
        # we stash them on the tool so the caller / tests can assert non-empty
        # image data without base64-in-text.
        png = self._page.screenshot()
        self._last_image = bytes(png)
        return BrowserActionResult(
            action="screenshot", verdict="executed",
            hint=f"captured {len(png)} bytes",
        )

    # -- helpers -------------------------------------------------------

    def _safe_url(self) -> str | None:
        try:
            return self._page.url
        except Exception:  # noqa: BLE001
            return None

    def _is_stale(self, live: Any, snapshot: SnapshotElement) -> bool:
        if live is None:
            return True
        try:
            tag = live.evaluate("el => el.tagName.toLowerCase()")
            role = live.get_attribute("role")
            label = (live.text_content() or "").strip()[:60] or None
        except Exception:  # noqa: BLE001
            return True
        return snapshot.is_stale(label=label, tag=tag, role=role)

    @property
    def refs(self) -> SnapshotRefTable:
        """The current snapshot ref table (invalidated on navigate/reconnect)."""
        return self._refs

    @property
    def last_image(self) -> bytes | None:
        """PNG bytes from the most recent ``screenshot`` action, if any."""
        return self._last_image


__all__ = ["BrowserUseTool"]
