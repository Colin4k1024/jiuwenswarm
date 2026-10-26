"""Generative UI tool: push sandboxed iframe artifacts to the web channel.

Aligns with OpenIntelligentUI ``generateSandboxedUi`` 6-parameter contract.
Each call generates an independent ``artifact_id``; no cross-call patch.
"""

from __future__ import annotations

import uuid
from typing import Any

from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenswarm.common.schema.message import GenerativeUIArtifactPayload


def _get_current_session() -> Any:
    """Get the current harness session.

    Uses ``openjiuwen.core.session.get_current_session`` (consistent with
    ``wiki_tools.py`` and other tools that need session access).
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
        initial_height: iframe initial height in pixels.
        placeholder_messages: placeholder text shown while the iframe loads.
        css: CSS text injected into ``<style>``.
        html: HTML text injected into ``<body>``.
        js_functions: named function definitions (client-side script library).
        js_expressions: top-level expressions to execute.
        title: optional artifact title.
        description: optional artifact description.

    Returns:
        A short confirmation string containing the artifact_id.
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
