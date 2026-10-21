"""Real-Chromium E2E tests for the Playwright-driven browser_use tool (Issue #2 / A1, slice 2b).

Launches a real headless Chromium via Playwright (the cached browser) and
exercises the snapshot-first ref flow, click-by-ref, type, get_text, screenshot,
and the stale-ref ``not_executed`` + retryable invariant. No mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # type: ignore

from jiuwenswarm.agents.swarm.providers.browser_use_tool import BrowserUseTool

_TEST_HTML = """
<!DOCTYPE html><html><body>
  <a id="home" href="#home" role="link">Home</a>
  <button id="go" type="button" role="button">Go</button>
  <input id="field" type="text" />
  <p id="out">initial</p>
  <script>
    document.getElementById('go').addEventListener('click', () => {
      document.getElementById('out').textContent = 'clicked:' + document.getElementById('field').value;
    });
  </script>
</body></html>
"""


@pytest.fixture
def tool_page(tmp_path: Path):
    html = tmp_path / "page.html"
    html.write_text(_TEST_HTML, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html.as_uri())
        yield BrowserUseTool(page), page
        browser.close()


def test_snapshot_captures_interactive_elements(tool_page) -> None:
    tool, _ = tool_page
    result = tool.execute("snapshot")
    assert result.verdict == "executed"
    tags = {el.tag for el in tool.refs.elements}
    assert "a" in tags and "button" in tags and "input" in tags
    # Refs are opaque ids e1, e2, ...
    assert {el.ref for el in tool.refs.elements} == {f"e{i}" for i in range(1, len(tool.refs.elements) + 1)}


def test_click_by_ref_executes(tool_page) -> None:
    tool, page = tool_page
    tool.execute("snapshot")
    go_ref = next(el.ref for el in tool.refs.elements if el.tag == "button")
    result = tool.execute("click", ref=go_ref)
    assert result.verdict == "executed"
    assert result.target == go_ref
    assert page.text_content("#out") == "clicked:"


def test_type_by_ref_then_click(tool_page) -> None:
    tool, page = tool_page
    tool.execute("snapshot")
    field_ref = next(el.ref for el in tool.refs.elements if el.tag == "input")
    go_ref = next(el.ref for el in tool.refs.elements if el.tag == "button")
    assert tool.execute("type", ref=field_ref, text="hello").verdict == "executed"
    assert tool.execute("click", ref=go_ref).verdict == "executed"
    assert page.text_content("#out") == "clicked:hello"


def test_click_unknown_ref_is_not_executed_retryable(tool_page) -> None:
    tool, _ = tool_page
    tool.execute("snapshot")
    result = tool.execute("click", ref="e999")
    assert result.verdict == "not_executed"
    assert result.retryable is True


def test_click_stale_ref_after_label_change_is_not_executed(tool_page) -> None:
    tool, page = tool_page
    tool.execute("snapshot")
    go_ref = next(el.ref for el in tool.refs.elements if el.tag == "button")
    # Relabel the node after snapshot: the bound ref's label no longer matches.
    page.eval_on_selector("#go", "el => el.textContent = 'Go2'")
    result = tool.execute("click", ref=go_ref)
    assert result.verdict == "not_executed"
    assert result.retryable is True


def test_navigate_and_get_url_title(tool_page) -> None:
    tool, page = tool_page
    result = tool.execute("get_url")
    assert result.verdict == "executed"
    assert result.url_after and result.url_after.startswith("file://")
    # Title is empty for this minimal doc; just assert the action executed.
    assert tool.execute("get_title").verdict == "executed"


def test_screenshot_returns_nonempty_png(tool_page) -> None:
    tool, _ = tool_page
    result = tool.execute("screenshot")
    assert result.verdict == "executed"
    assert tool.last_image is not None
    # PNG magic bytes.
    assert tool.last_image[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_text_returns_page_text(tool_page) -> None:
    tool, _ = tool_page
    result = tool.execute("get_text", selector="#out")
    assert result.verdict == "executed"
    assert "initial" in (result.text_verification or "")
