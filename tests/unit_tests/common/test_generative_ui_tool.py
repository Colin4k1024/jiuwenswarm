"""swarm.generative_ui 工具与 chat.generative_ui 事件转换的协议测试."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.common.schema.message import (
    EventType,
    GenerativeUIArtifactPayload,
)


# ---------------------------------------------------------------------------
# 工具层测试: emit_generative_ui -> session.write_stream(OutputSchema)
# ---------------------------------------------------------------------------


def _full_tool_kwargs() -> dict:
    """构造一份完整的 emit_generative_ui 工具调用 kwargs (6 固定参数 + 元数据)."""
    return {
        "initial_height": 400,
        "placeholder_messages": ["Rendering...", "Please wait"],
        "css": "body { font-family: sans-serif; }",
        "html": "<div id='root'></div>",
        "js_functions": "function render() { return 'ok'; }",
        "js_expressions": "document.getElementById('root').textContent = render();",
        "title": "Demo",
        "description": "Demo artifact",
    }


@pytest.mark.asyncio
async def test_emit_generative_ui_writes_stream_with_correct_schema() -> None:
    """emit_generative_ui 调用后必须向 session 写入 OutputSchema(type='generative_ui', ...)."""
    from jiuwenswarm.agents.harness.common.tools.generative_ui_tool import (
        emit_generative_ui,
    )

    mock_session = MagicMock()
    mock_session.write_stream = AsyncMock()

    with patch(
        "jiuwenswarm.agents.harness.common.tools.generative_ui_tool._get_current_session",
        return_value=mock_session,
    ):
        result = await emit_generative_ui.invoke(_full_tool_kwargs())

    assert mock_session.write_stream.call_count == 1
    call_args = mock_session.write_stream.call_args
    output_schema = call_args[0][0] if call_args[0] else call_args.kwargs.get("output_schema")
    assert output_schema is not None
    assert output_schema.type == "generative_ui"
    assert output_schema.index == 0
    payload = output_schema.payload
    assert isinstance(payload, dict)
    assert payload["initial_height"] == 400
    assert payload["placeholder_messages"] == ["Rendering...", "Please wait"]
    assert payload["css"] == "body { font-family: sans-serif; }"
    assert payload["html"] == "<div id='root'></div>"
    assert payload["js_functions"] == "function render() { return 'ok'; }"
    assert payload["js_expressions"] == "document.getElementById('root').textContent = render();"
    assert payload["title"] == "Demo"
    assert payload["description"] == "Demo artifact"
    assert "artifact_id" in payload
    assert isinstance(payload["artifact_id"], str)
    assert len(payload["artifact_id"]) > 0
    assert "artifact" in result.lower() or "pushed" in result.lower()


@pytest.mark.asyncio
async def test_emit_generative_ui_optional_metadata_omitted() -> None:
    """title / description 省略时 payload 中对应字段为 None."""
    from jiuwenswarm.agents.harness.common.tools.generative_ui_tool import (
        emit_generative_ui,
    )

    kwargs = _full_tool_kwargs()
    kwargs.pop("title")
    kwargs.pop("description")

    mock_session = MagicMock()
    mock_session.write_stream = AsyncMock()

    with patch(
        "jiuwenswarm.agents.harness.common.tools.generative_ui_tool._get_current_session",
        return_value=mock_session,
    ):
        await emit_generative_ui.invoke(kwargs)

    payload = mock_session.write_stream.call_args[0][0].payload
    assert payload["title"] is None
    assert payload["description"] is None


@pytest.mark.asyncio
async def test_emit_generative_ui_unique_artifact_id_per_call() -> None:
    """每次调用生成独立的 artifact_id (无跨调用 patch)."""
    from jiuwenswarm.agents.harness.common.tools.generative_ui_tool import (
        emit_generative_ui,
    )

    mock_session = MagicMock()
    mock_session.write_stream = AsyncMock()

    with patch(
        "jiuwenswarm.agents.harness.common.tools.generative_ui_tool._get_current_session",
        return_value=mock_session,
    ):
        await emit_generative_ui.invoke(_full_tool_kwargs())
        await emit_generative_ui.invoke(_full_tool_kwargs())

    assert mock_session.write_stream.call_count == 2
    payload_1 = mock_session.write_stream.call_args_list[0][0][0].payload
    payload_2 = mock_session.write_stream.call_args_list[1][0][0].payload
    assert payload_1["artifact_id"] != payload_2["artifact_id"]


# ---------------------------------------------------------------------------
# interface_deep 分支测试: chunk.type == "generative_ui" -> chat.generative_ui
# ---------------------------------------------------------------------------


def _make_generative_ui_chunk(payload: dict) -> MagicMock:
    """构造一个 OutputSchema mock, type='generative_ui'."""
    chunk = MagicMock()
    chunk.type = "generative_ui"
    chunk.payload = payload
    return chunk


def test_parse_stream_chunk_generative_ui() -> None:
    """interface_deep._parse_stream_chunk 必须把 generative_ui chunk 转为 chat.generative_ui 事件."""
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    payload = GenerativeUIArtifactPayload(
        artifact_id="test-id-1",
        initial_height=320,
        placeholder_messages=["Loading..."],
        css="body { margin: 0; }",
        html="<div>hello</div>",
        js_functions="",
        js_expressions="",
        title="Test",
        description="Test artifact",
    ).to_dict()

    chunk = _make_generative_ui_chunk(payload)
    result = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)

    assert result is not None
    assert result["event_type"] == "chat.generative_ui"
    assert result["artifact_id"] == "test-id-1"
    assert result["initial_height"] == 320
    assert result["placeholder_messages"] == ["Loading..."]
    assert result["css"] == "body { margin: 0; }"
    assert result["html"] == "<div>hello</div>"
    assert result["js_functions"] == ""
    assert result["js_expressions"] == ""
    assert result["title"] == "Test"
    assert result["description"] == "Test artifact"


def test_parse_stream_chunk_generative_ui_non_dict_payload() -> None:
    """generative_ui chunk payload 非 dict 时返回 None (跳过) 或原样透传."""
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    chunk = _make_generative_ui_chunk("not-a-dict")
    result = JiuWenSwarmDeepAdapter._parse_stream_chunk(chunk)
    # 非 dict payload 不应崩溃, 返回 None 或包含 event_type 的 dict
    if result is not None:
        assert result.get("event_type") == "chat.generative_ui"


# ---------------------------------------------------------------------------
# prompt 注入测试: response_prompt_rail 挂载 generative_ui section
# ---------------------------------------------------------------------------


def test_generative_ui_prompt_instruction_contains_key_info() -> None:
    """generative_ui prompt 指令必须包含关键信息: 6 参数顺序 / 安全约束 / 与 A2UI 分工."""
    from jiuwenswarm.server.runtime.a2ui.prompt_instructions_generative_ui import (
        build_generative_ui_tool_instruction,
    )

    instruction = build_generative_ui_tool_instruction()
    assert isinstance(instruction, str)
    assert len(instruction) > 0
    # 6 参数固定顺序
    assert "initial_height" in instruction
    assert "placeholder_messages" in instruction
    assert "css" in instruction
    assert "html" in instruction
    assert "js_functions" in instruction
    assert "js_expressions" in instruction
    # 安全约束
    assert "sandbox" in instruction.lower() or "iframe" in instruction.lower()
    assert "no network" in instruction.lower() or "network" in instruction.lower()
    # 与 A2UI 分工
    assert "a2ui" in instruction.lower() or "A2UI" in instruction


@pytest.mark.asyncio
async def test_response_prompt_rail_mounts_generative_ui_section(monkeypatch) -> None:
    """ResponsePromptRail 在 Web/A2UI channel 且开关开启时必须挂载 generative_ui section."""
    from types import SimpleNamespace

    from jiuwenswarm.agents.harness.common.prompt.prompt_builder import LocalSectionName
    from jiuwenswarm.agents.harness.common.rails.response_prompt_rail import ResponsePromptRail
    from jiuwenswarm.server.runtime.a2ui.config import A2UIConfig

    class _FakePromptBuilder:
        def __init__(self) -> None:
            self.language = "en"
            self.sections = {}

        def add_section(self, section) -> None:
            self.sections[section.name] = section

        def remove_section(self, name: str) -> None:
            self.sections.pop(name, None)

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()

    await rail.before_model_call(SimpleNamespace(inputs={"channel": "web"}))

    assert LocalSectionName.GENERATIVE_UI in rail.system_prompt_builder.sections
    content = rail.system_prompt_builder.sections[LocalSectionName.GENERATIVE_UI].content
    assert "emit_generative_ui" in content["en"]


@pytest.mark.asyncio
async def test_response_prompt_rail_skips_generative_ui_for_non_web_channel(monkeypatch) -> None:
    """Non-Web channel 不挂载 generative_ui section."""
    from types import SimpleNamespace

    from jiuwenswarm.agents.harness.common.prompt.prompt_builder import LocalSectionName
    from jiuwenswarm.agents.harness.common.rails.response_prompt_rail import ResponsePromptRail
    from jiuwenswarm.server.runtime.a2ui.config import A2UIConfig

    class _FakePromptBuilder:
        def __init__(self) -> None:
            self.language = "en"
            self.sections = {}

        def add_section(self, section) -> None:
            self.sections[section.name] = section

        def remove_section(self, name: str) -> None:
            self.sections.pop(name, None)

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )
    rail = ResponsePromptRail()
    rail.system_prompt_builder = _FakePromptBuilder()

    await rail.before_model_call(SimpleNamespace(inputs={"channel": "feishu"}))

    assert LocalSectionName.GENERATIVE_UI not in rail.system_prompt_builder.sections


@pytest.mark.asyncio
async def test_response_prompt_rail_skips_generative_ui_for_non_chat_profile(monkeypatch) -> None:
    """Code/design/reviewer/single-agent profile 不挂载 generative_ui section.

    这些 profile 不注册 emit_generative_ui 工具，prompt 注入会导致模型调用不存在的工具。
    """
    from types import SimpleNamespace

    from jiuwenswarm.agents.harness.common.prompt.prompt_builder import LocalSectionName
    from jiuwenswarm.agents.harness.common.rails.response_prompt_rail import ResponsePromptRail
    from jiuwenswarm.server.runtime.a2ui.config import A2UIConfig

    class _FakePromptBuilder:
        def __init__(self) -> None:
            self.language = "en"
            self.sections = {}

        def add_section(self, section) -> None:
            self.sections[section.name] = section

        def remove_section(self, name: str) -> None:
            self.sections.pop(name, None)

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.a2ui.config.get_current_a2ui_config",
        lambda: A2UIConfig(enabled=True),
    )

    for profile in ("code.team", "design", "reviewer", "single-agent"):
        rail = ResponsePromptRail()
        rail.system_prompt_builder = _FakePromptBuilder()
        # Simulate before_invoke storing profile in extra
        ctx = SimpleNamespace(
            inputs={"channel": "web", "profile": profile},
            extra={},
        )
        await rail.before_invoke(ctx)
        await rail.before_model_call(ctx)

        assert LocalSectionName.GENERATIVE_UI not in rail.system_prompt_builder.sections, (
            f"profile={profile} should not get generative_ui prompt"
        )
