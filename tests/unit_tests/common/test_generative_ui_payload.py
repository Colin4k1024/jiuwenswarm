"""GenerativeUIArtifactPayload 与 EventType.CHAT_GENERATIVE_UI 的协议契约测试."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema.message import (
    EventType,
    GenerativeUIArtifactPayload,
)


def _full_kwargs() -> dict:
    """构造一份覆盖全部 6 个固定参数 + 元数据的合法 kwargs."""
    return {
        "artifact_id": "artifact-uuid-1",
        "initial_height": 320,
        "placeholder_messages": ["Loading...", "Please wait"],
        "css": "body { color: #111; }",
        "html": "<div id='app'></div>",
        "js_functions": "function add(a, b) { return a + b; }",
        "js_expressions": "document.getElementById('app').textContent = add(1, 2);",
        "title": "Calculator",
        "description": "A simple calculator artifact",
    }


def test_event_type_chat_generative_ui_value() -> None:
    """EventType.CHAT_GENERATIVE_UI 的 wire 值必须是 'chat.generative_ui'."""
    assert EventType.CHAT_GENERATIVE_UI.value == "chat.generative_ui"


def test_payload_roundtrip_full() -> None:
    """完整字段 to_dict -> from_dict 必须恒等."""
    payload = GenerativeUIArtifactPayload(**_full_kwargs())
    d = payload.to_dict()
    assert d["artifact_id"] == "artifact-uuid-1"
    assert d["initial_height"] == 320
    assert d["placeholder_messages"] == ["Loading...", "Please wait"]
    assert d["css"] == "body { color: #111; }"
    assert d["html"] == "<div id='app'></div>"
    assert d["js_functions"].startswith("function add")
    assert d["js_expressions"].startswith("document.getElementById")
    assert d["title"] == "Calculator"
    assert d["description"] == "A simple calculator artifact"
    restored = GenerativeUIArtifactPayload.from_dict(d)
    assert restored == payload


def test_payload_optional_metadata_default_none() -> None:
    """title/description 可省略, 默认为 None."""
    kwargs = _full_kwargs()
    kwargs.pop("title")
    kwargs.pop("description")
    payload = GenerativeUIArtifactPayload(**kwargs)
    assert payload.title is None
    assert payload.description is None
    d = payload.to_dict()
    assert d["title"] is None
    assert d["description"] is None
    restored = GenerativeUIArtifactPayload.from_dict(d)
    assert restored == payload


@pytest.mark.parametrize(
    "missing_key",
    [
        "artifact_id",
        "initial_height",
        "placeholder_messages",
        "css",
        "html",
        "js_functions",
        "js_expressions",
    ],
)
def test_from_dict_missing_required_field_raises(missing_key: str) -> None:
    """from_dict 缺任一必填字段必须抛错 (KeyError 或 ValueError)."""
    kwargs = _full_kwargs()
    kwargs.pop(missing_key)
    with pytest.raises((KeyError, ValueError)):
        GenerativeUIArtifactPayload.from_dict(kwargs)


def test_from_dict_rejects_wrong_type_initial_height() -> None:
    """initial_height 必须是 int, 其他类型拒绝."""
    kwargs = _full_kwargs()
    kwargs["initial_height"] = "320"  # type: ignore[assignment]
    with pytest.raises((TypeError, ValueError)):
        GenerativeUIArtifactPayload.from_dict(kwargs)


def test_from_dict_rejects_wrong_type_placeholder_messages() -> None:
    """placeholder_messages 必须是 list[str], 其他类型拒绝."""
    kwargs = _full_kwargs()
    kwargs["placeholder_messages"] = "Loading..."  # type: ignore[assignment]
    with pytest.raises((TypeError, ValueError)):
        GenerativeUIArtifactPayload.from_dict(kwargs)


@pytest.mark.parametrize(
    "bad_key",
    ["artifact_id", "css", "html", "js_functions", "js_expressions"],
)
def test_from_dict_rejects_non_str_required_str_fields(bad_key: str) -> None:
    """artifact_id / css / html / js_functions / js_expressions 必须是 str.

    Codex P2 复审覆盖: 防止非 str 值 (None / list / object / int) 静默通过
    后导致 json.dumps(to_dict()) 抛 TypeError 或渲染器收到错误类型.
    """
    kwargs = _full_kwargs()
    for bad_value in (None, 123, ["x"], object(), b"bytes"):
        kwargs[bad_key] = bad_value  # type: ignore[assignment]
        with pytest.raises((TypeError, ValueError)):
            GenerativeUIArtifactPayload.from_dict(kwargs)


@pytest.mark.parametrize("bad_key", ["title", "description"])
def test_from_dict_rejects_non_str_optional_metadata(bad_key: str) -> None:
    """title / description 必须是 str | None, 其他类型拒绝."""
    kwargs = _full_kwargs()
    for bad_value in (123, ["x"], object(), b"bytes"):
        kwargs[bad_key] = bad_value  # type: ignore[assignment]
        with pytest.raises((TypeError, ValueError)):
            GenerativeUIArtifactPayload.from_dict(kwargs)


def test_from_dict_rejects_bool_initial_height() -> None:
    """bool 是 int 子类, 但语义上不是合法的 iframe 高度; 必须显式拒绝."""
    kwargs = _full_kwargs()
    kwargs["initial_height"] = True  # type: ignore[assignment]
    with pytest.raises((TypeError, ValueError)):
        GenerativeUIArtifactPayload.from_dict(kwargs)


def test_to_dict_output_is_json_serializable() -> None:
    """to_dict 输出必须可直接 json.dumps (与 to_dict docstring 承诺一致)."""
    import json

    payload = GenerativeUIArtifactPayload(**_full_kwargs())
    json.dumps(payload.to_dict())


def test_payload_six_params_order_documented() -> None:
    """to_dict 输出的 key 集合必须包含 OpenIntelligentUI 6 个固定参数."""
    payload = GenerativeUIArtifactPayload(**_full_kwargs())
    keys = set(payload.to_dict().keys())
    expected = {
        "artifact_id",
        "initial_height",
        "placeholder_messages",
        "css",
        "html",
        "js_functions",
        "js_expressions",
        "title",
        "description",
    }
    assert expected.issubset(keys), f"missing keys: {expected - keys}"
