"""Specialized computer-use model adapters (Issue #8 / B5)."""

from computer_use.model_adapters.scale_cua import (
    SCALE_CUA_COORDINATE_MAX,
    CuaModelClient,
    CuaObservation,
    ScaleCuaModelPort,
)

__all__ = [
    "CuaModelClient",
    "CuaObservation",
    "SCALE_CUA_COORDINATE_MAX",
    "ScaleCuaModelPort",
]
