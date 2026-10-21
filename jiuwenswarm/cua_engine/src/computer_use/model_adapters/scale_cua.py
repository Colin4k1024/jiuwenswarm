# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Specialized computer-use model adapters (Issue #8 / B5).

A ``ModelPort`` implementation speaking the OpenAI computer-use tool protocol
(used by SCALE-CUA / THUDM-SCALE-CUA reference). Isolated from the main chat
model: the governance loop (B3) calls ``predict`` per observation and ``reset``
between tasks. Real-model inference is delegated to an injected client; this
module owns preprocessing, the action-space mapping, typed parsing, and retry
counters — all testable with a fake client (no real model needed).
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Protocol

from computer_use.actions import Action, Decision, ModelPort

# SCALE-CUA's coordinate space: the model emits coordinates in [0, 1000).
SCALE_CUA_COORDINATE_MAX = 1000
SCALE_CUA_TARGET_WIDTH = 1280
SCALE_CUA_TARGET_HEIGHT = 720
# How many prior screenshots the model still sees (OpenAI computer-use
# images-to-keep). Older frames drop off once the window is full.
_IMAGES_TO_KEEP = 5


class CuaObservation(Protocol):
    """What a specialized CUA model inspects per turn."""

    screenshot: bytes | None
    objective: str
    width: int
    height: int


class CuaModelClient(Protocol):
    """The inference seam a SCALE-CUA adapter calls."""

    def invoke(self, messages: list[dict[str, Any]], *, image: bytes | None) -> str:
        """Run one model turn, returning the raw assistant text."""


# OpenAI computer-use tool action type → cangming 16-action verb.
_TYPE_TO_ACTION: dict[str, str] = {
    "click": "left_click",
    "double_click": "double_click",
    "triple_click": "triple_click",
    "right_click": "right_click",
    "middle_click": "middle_click",
    "type": "type",
    "key": "hotkey",
    "keypress": "hotkey",
    "hold_key": "key_down",
    "release_key": "key_up",
    "press": "press",
    "scroll": "scroll",
    "wait": "wait",
    # The cua action space has no "screenshot": the loop re-observes every
    # turn, so a model screenshot request maps to a benign wait.
    "screenshot": "wait",
    "cursor_position": "mouse_move",
    "mouse_move": "mouse_move",
    "finished": "finished",
    "done": "finished",
}


class ScaleCuaModelPort(ModelPort):
    """A ``ModelPort`` driving the SCALE-CUA / OpenAI computer-use protocol.

    The adapter preprocesses each observation (resizes toward 1280x720, keeps a
    bounded image history), calls the injected client, and parses the assistant
    response into one validated ``Action``. Unknown action types raise
    ``ValueError`` so the loop's consecutive-parse-failure guard can fire.

    Attributes:
        client: The inference seam (real model in production, fake in tests).
        coordinate_max: The model's coordinate space upper bound (1000).
    """

    def __init__(
        self,
        client: CuaModelClient,
        *,
        coordinate_max: int = SCALE_CUA_COORDINATE_MAX,
    ) -> None:
        self._client = client
        self.coordinate_max = coordinate_max
        self._images: deque[bytes] = deque(maxlen=_IMAGES_TO_KEEP)

    def predict(self, observation: object) -> Decision:
        obs = observation  # type: CuaObservation
        objective = getattr(obs, "objective", "") or ""
        screenshot = getattr(obs, "screenshot", None)
        width = int(getattr(obs, "width", SCALE_CUA_TARGET_WIDTH) or SCALE_CUA_TARGET_WIDTH)
        height = int(getattr(obs, "height", SCALE_CUA_TARGET_HEIGHT) or SCALE_CUA_TARGET_HEIGHT)
        if screenshot is not None:
            self._images.append(bytes(screenshot))
        messages = [
            {"role": "system", "content": (
                "You are a computer-use agent. The screen is "
                f"{SCALE_CUA_TARGET_WIDTH}x{SCALE_CUA_TARGET_HEIGHT}; emit one "
                "OpenAI computer-use action JSON. Coordinates are in "
                f"[0, {self.coordinate_max})."
            )},
            {"role": "user", "content": objective or "proceed"},
        ]
        raw = self._client.invoke(messages, image=screenshot if screenshot is not None else None)
        action = self._parse_action(raw, width=width, height=height)
        return Decision(action=action, reasoning=raw.strip()[:200])

    def reset(self) -> None:
        self._images.clear()

    # -- parsing -------------------------------------------------------

    def _parse_action(self, raw: str, *, width: int, height: int) -> Action:
        text = (raw or "").strip()
        # The model may wrap the JSON in prose; find the first {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"no action JSON in model response: {raw!r}")
        payload = json.loads(text[start : end + 1])
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ValueError(f"action payload is not an object: {payload!r}")
        type_name = str(payload.get("type") or payload.get("name") or "").strip().lower()
        if type_name not in _TYPE_TO_ACTION:
            raise ValueError(f"unsupported SCALE-CUA action type: {type_name!r}")
        verb = _TYPE_TO_ACTION[type_name]
        pixel = self._pixel(payload, width=width, height=height)
        kwargs: dict[str, Any] = {"action": verb}
        if pixel is not None:
            kwargs["pixel"] = pixel
            kwargs["coordinate_max"] = self.coordinate_max
        if "text" in payload:
            kwargs["text"] = str(payload["text"])
        if verb == "hotkey":
            keys = payload.get("keys") or payload.get("key") or payload.get("text")
            if isinstance(keys, list):
                kwargs["key"] = "+".join(str(k) for k in keys)
            elif keys is not None:
                kwargs["key"] = str(keys)
            kwargs.pop("text", None)
        if verb == "scroll":
            kwargs["dx"] = int(payload.get("scroll_x") or payload.get("dx") or 0)
            kwargs["dy"] = int(payload.get("scroll_y") or payload.get("dy") or 0)
        return Action(**kwargs)

    def _pixel(self, payload: dict, *, width: int, height: int) -> tuple[int, int] | None:
        x = payload.get("x")
        y = payload.get("y")
        if x is None and y is None:
            return None
        return (int(x or 0), int(y or 0))


__all__ = [
    "CuaModelClient",
    "CuaObservation",
    "SCALE_CUA_COORDINATE_MAX",
    "SCALE_CUA_TARGET_HEIGHT",
    "SCALE_CUA_TARGET_WIDTH",
    "ScaleCuaModelPort",
]
