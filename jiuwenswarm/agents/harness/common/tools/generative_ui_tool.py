"""Generative UI 工具: agent 主动推送沙箱 iframe artifact 到 web 频道.

对齐 OpenIntelligentUI ``generateSandboxedUi`` 的 6 参数固定契约.
每次调用生成独立 ``artifact_id``, 不支持跨调用 patch.
"""

from __future__ import annotations

import uuid
from typing import Any

from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenswarm.common.schema.message import GenerativeUIArtifactPayload


def _get_current_session() -> Any:
    """从当前 harness 上下文取 session.

    使用 openjiuwen 提供的 ``get_current_session`` 全局访问器
    (与 ``wiki_tools.py`` 等工具内获取 session 的模式一致).
    """
    from openjiuwen.core.session import get_current_session

    return get_current_session()


@tool
async def emit_generative_ui(
    initial_height: int,
    placeholder_messages: list[str],
    css: str,
    html: str,
    js_functions: str,
    js_expressions: str,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Push a sandboxed interactive UI artifact (HTML/CSS/JS) to the web channel.

    Args:
        initial_height: iframe 初始高度 (px).
        placeholder_messages: iframe 加载期间父页占位文案列表.
        css: 注入 <style> 的 CSS 文本.
        html: 注入 <body> 的 HTML 文本.
        js_functions: 命名函数定义 (作为客户端脚本库).
        js_expressions: 顶层执行的表达式.
        title: artifact 标题 (可选).
        description: artifact 描述 (可选).

    Returns:
        简短确认字符串, 包含 artifact_id.
    """
    artifact_id = str(uuid.uuid4())
    payload = GenerativeUIArtifactPayload(
        artifact_id=artifact_id,
        initial_height=initial_height,
        placeholder_messages=placeholder_messages,
        css=css,
        html=html,
        js_functions=js_functions,
        js_expressions=js_expressions,
        title=title,
        description=description,
    )
    session = _get_current_session()
    await session.write_stream(
        OutputSchema(
            type="generative_ui",
            index=0,
            payload=payload.to_dict(),
        )
    )
    return f"Generative UI artifact {artifact_id} pushed."
