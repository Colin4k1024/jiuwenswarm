"""Unit tests for the SCALE-CUA model adapter (Issue #8 / B5).

Uses a fake ``CuaModelClient`` returning canned OpenAI computer-use responses
so the action-space mapping, coordinate-space bounds, and parse failure paths
are exercised without a real model.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from computer_use.actions import Action, Decision, ModelPort
from computer_use.model_adapters import SCALE_CUA_COORDINATE_MAX, ScaleCuaModelPort


@dataclass
class _Obs:
    screenshot: bytes | None
    objective: str
    width: int = 1280
    height: int = 720


class _FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.invoked: list[tuple[list, bytes | None]] = []

    def invoke(self, messages, *, image):
        self.invoked.append((messages, image))
        return self.reply


def test_scale_cua_satisfies_model_port() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"finished"}'))
    assert isinstance(port, ModelPort)


def test_scale_cua_click_maps_to_left_click_with_coord_max() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"click","x":500,"y":300}'))
    decision = port.predict(_Obs(screenshot=None, objective="click submit"))
    assert isinstance(decision, Decision)
    assert decision.action.action == "left_click"
    assert decision.action.pixel == (500, 300)
    assert decision.action.coordinate_max == SCALE_CUA_COORDINATE_MAX  # 1000


def test_scale_cua_type_maps_text() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"type","text":"hello world"}'))
    decision = port.predict(_Obs(screenshot=None, objective="type"))
    assert decision.action.action == "type"
    assert decision.action.text == "hello world"


def test_scale_cua_keypress_maps_hotkey() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"keypress","keys":["ctrl","s"]}'))
    decision = port.predict(_Obs(screenshot=None, objective="save"))
    assert decision.action.action == "hotkey"
    assert decision.action.key == "ctrl+s"


def test_scale_cua_finished() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"finished"}'))
    decision = port.predict(_Obs(screenshot=None, objective="done"))
    assert decision.action.action == "finished"


def test_scale_cua_parses_json_wrapped_in_prose() -> None:
    port = ScaleCuaModelPort(_FakeClient('Sure: {"type":"double_click","x":10,"y":20} ok'))
    decision = port.predict(_Obs(screenshot=None, objective="open"))
    assert decision.action.action == "double_click"
    assert decision.action.pixel == (10, 20)


def test_scale_cua_unknown_type_raises() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"teleport","x":1,"y":1}'))
    with pytest.raises(ValueError, match="unsupported"):
        port.predict(_Obs(screenshot=None, objective="x"))


def test_scale_cua_no_json_raises() -> None:
    port = ScaleCuaModelPort(_FakeClient("I cannot act."))
    with pytest.raises(ValueError, match="no action JSON"):
        port.predict(_Obs(screenshot=None, objective="x"))


def test_scale_cua_reset_clears_image_history() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"screenshot"}'))
    port.predict(_Obs(screenshot=b"frame1", objective="see"))
    assert len(port._images) == 1
    port.reset()
    assert len(port._images) == 0


def test_scale_cua_image_history_bounded() -> None:
    port = ScaleCuaModelPort(_FakeClient('{"type":"screenshot"}'))
    for i in range(8):
        port.predict(_Obs(screenshot=f"frame{i}".encode(), objective="see"))
    assert len(port._images) == 5  # images-to-keep cap
