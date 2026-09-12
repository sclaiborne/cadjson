"""Evaluation context: resolved params, unit scale, and dim helpers shared by all layers."""

from __future__ import annotations

from build123d import Vector

from cadgen.errors import CadgenError
from cadgen.expr import Dim, evaluate, resolve_params

UNIT_SCALE = {"mm": 1.0, "in": 25.4}


class Context:
    def __init__(self, params: dict[str, Dim], units: str = "mm"):
        self.params = resolve_params(params)
        self.scale = UNIT_SCALE[units]
        self.feature_id: str | None = None  # set by the orchestrator while a feature runs

    def num(self, dim: Dim) -> float:
        """A unitless number (counts, angles, degrees)."""
        try:
            return evaluate(dim, self.params)
        except CadgenError as exc:
            raise CadgenError(exc.message, self.feature_id) from None

    def length(self, dim: Dim) -> float:
        """A length in document units, returned in mm."""
        return self.num(dim) * self.scale

    def vec2(self, v) -> tuple[float, float]:
        return (self.length(v[0]), self.length(v[1]))

    def vec3(self, v) -> Vector:
        return Vector(self.length(v[0]), self.length(v[1]), self.length(v[2]))

    def dir3(self, v) -> Vector:
        d = Vector(self.num(v[0]), self.num(v[1]), self.num(v[2]))
        if d.length == 0:
            raise CadgenError("direction vector cannot be zero", self.feature_id)
        return d.normalized()
