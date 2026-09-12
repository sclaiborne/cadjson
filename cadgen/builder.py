"""The Python builder API. The JSON layer only ever calls this; if JSON is ever dropped, this
is the script format. Every method takes resolved numbers and build123d objects."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (
    Axis,
    Circle,
    Cone,
    Edge,
    Face,
    Location,
    Part,
    Plane,
    Shape,
    Sketch,
    Vector,
    extrude,
    mirror,
    offset,
    revolve,
)

from cadgen.errors import CadgenError
from cadgen.selectors import edge_sig, face_sig

_TINY = 1e-6


@dataclass
class FeatureRecord:
    added: Part | None = None
    removed: Part | None = None
    new_faces: set = field(default_factory=set)
    new_edges: set = field(default_factory=set)


class Builder:
    def __init__(self) -> None:
        self.solid: Part | None = None
        self.records: dict[str, FeatureRecord] = {}
        self.order: list[str] = []

    # --- bookkeeping -----------------------------------------------------------------------

    @property
    def new_faces(self) -> dict[str, set]:
        return {k: r.new_faces for k, r in self.records.items()}

    @property
    def new_edges(self) -> dict[str, set]:
        return {k: r.new_edges for k, r in self.records.items()}

    def require_solid(self, fid: str, what: str) -> Part:
        if self.solid is None:
            raise CadgenError(f"cannot {what}: there is no solid yet (the first feature must add material)", fid)
        return self.solid

    def _commit(self, fid: str, after: Part, added: Part | None = None, removed: Part | None = None) -> Part:
        before = self.solid
        if after is None or not after.is_valid:
            raise CadgenError("the resulting solid is invalid", fid, ["try a slightly different size or order of features"])
        if after.volume < _TINY:
            raise CadgenError("the resulting solid has no volume", fid)
        rec = FeatureRecord()
        if before is None:
            rec.added = after
            rec.new_faces = {face_sig(f) for f in after.faces()}
            rec.new_edges = {edge_sig(e) for e in after.edges()}
        else:
            rec.added = added if added is not None else _nonempty(after - before)
            rec.removed = removed if removed is not None else _nonempty(before - after)
            old_f = {face_sig(f) for f in before.faces()}
            old_e = {edge_sig(e) for e in before.edges()}
            rec.new_faces = {face_sig(f) for f in after.faces()} - old_f
            rec.new_edges = {edge_sig(e) for e in after.edges()} - old_e
        self.records[fid] = rec
        self.order.append(fid)
        self.solid = after
        return after

    def _combine(self, fid: str, tool: Part, op: str) -> Part:
        if op == "add":
            if self.solid is None:
                return self._commit(fid, tool)
            return self._commit(fid, self.solid + tool, added=_nonempty(tool - self.solid))
        base = self.require_solid(fid, op)
        if op == "cut":
            return self._commit(fid, base - tool, removed=_nonempty(base & tool))
        if op == "intersect":
            return self._commit(fid, base & tool, removed=_nonempty(base - tool))
        raise CadgenError(f"unknown op {op!r}", fid)

    def through_distance(self, plane: Plane) -> float:
        """Long enough to pass through the whole current solid from anywhere on the plane."""
        if self.solid is None:
            return 1000.0
        bb = self.solid.bounding_box()
        return bb.diagonal * 2 + (bb.center() - plane.origin).length + 1.0

    # --- features --------------------------------------------------------------------------

    def extrude(self, fid: str, sketch: Sketch, plane: Plane, *, distance: float | None, through: bool,
                both: bool, taper: float, op: str) -> Part:
        located = plane * sketch
        if through:
            amount, both = self.through_distance(plane), True
        else:
            amount = distance
        if amount is None or abs(amount) < _TINY:
            raise CadgenError("extrude distance must be non-zero", fid)
        try:
            tool = extrude(located, amount=amount, both=both, taper=taper)
        except Exception as exc:  # OCCT failures are not very descriptive
            raise CadgenError(f"extrude failed: {exc}", fid) from None
        return self._combine(fid, tool, op)

    def revolve(self, fid: str, sketch: Sketch, plane: Plane, axis: Axis, angle: float, op: str) -> Part:
        located = plane * sketch
        try:
            tool = revolve(located, axis=axis, revolution_arc=angle)
        except Exception as exc:
            raise CadgenError(f"revolve failed: {exc}", fid, ["the profile must not cross the axis"]) from None
        return self._combine(fid, tool, op)

    def fillet(self, fid: str, edges: list[Edge], radius: float) -> Part:
        base = self.require_solid(fid, "fillet")
        if radius <= 0:
            raise CadgenError("fillet radius must be positive", fid)
        try:
            after = base.fillet(radius, edges)
        except Exception:
            hints = [f"{len(edges)} edge(s) selected"]
            best = _max_feasible(lambda r: base.fillet(r, edges), radius)
            if best is not None:
                hints.append(f"largest radius that works on these edges is about {best:.3g}")
            raise CadgenError(f"fillet of radius {radius:g} failed", fid, hints) from None
        return self._commit(fid, after)

    def chamfer(self, fid: str, edges: list[Edge], length: float, length2: float | None) -> Part:
        base = self.require_solid(fid, "chamfer")
        if length <= 0 or (length2 is not None and length2 <= 0):
            raise CadgenError("chamfer lengths must be positive", fid)
        try:
            after = base.chamfer(length, length2, edges)
        except Exception:
            hints = [f"{len(edges)} edge(s) selected"]
            best = _max_feasible(lambda l: base.chamfer(l, None if length2 is None else l * length2 / length, edges), length)
            if best is not None:
                hints.append(f"largest length that works on these edges is about {best:.3g}")
            raise CadgenError(f"chamfer of length {length:g} failed", fid, hints) from None
        return self._commit(fid, after)

    def shell(self, fid: str, thickness: float, openings: list[Face] | None) -> Part:
        base = self.require_solid(fid, "shell")
        if abs(thickness) < _TINY:
            raise CadgenError("shell thickness must be non-zero", fid)
        try:
            after = offset(base, amount=-thickness, openings=openings or None)
        except Exception as exc:
            raise CadgenError(f"shell failed: {exc}", fid, ["thickness may be too large for the geometry"]) from None
        return self._commit(fid, after)

    def hole(self, fid: str, plane: Plane, points: list[tuple[float, float]], diameter: float, *,
             depth: float | None, through: bool, counterbore: tuple[float, float] | None,
             countersink: tuple[float, float] | None) -> Part:
        base = self.require_solid(fid, "hole")
        r = diameter / 2
        if r <= 0:
            raise CadgenError("hole diameter must be positive", fid)
        amount = self.through_distance(plane) if through else depth
        if amount is None or amount <= 0:
            raise CadgenError("hole depth must be positive", fid)
        tool: Part | None = None
        for u, v in points:
            loc = Location((u, v, 0))
            piece = extrude(plane * (loc * Circle(r)), amount=-amount, both=through)
            if counterbore is not None:
                cb_d, cb_depth = counterbore
                piece = piece + extrude(plane * (loc * Circle(cb_d / 2)), amount=-cb_depth)
            if countersink is not None:
                cs_d, cs_angle = countersink
                h = (cs_d / 2 - r) / math.tan(math.radians(cs_angle / 2))
                cone = Cone(bottom_radius=r, top_radius=cs_d / 2, height=h, align=(Align.CENTER, Align.CENTER, Align.MAX))
                piece = piece + (plane * (loc * cone))
            tool = piece if tool is None else tool + piece
        return self._combine(fid, tool, "cut")

    def mirror(self, fid: str, plane: Plane, feature_ids: list[str] | None) -> Part:
        base = self.require_solid(fid, "mirror")
        if feature_ids is None:
            return self._commit(fid, base + mirror(base, about=plane))
        after = base
        for ref in feature_ids:
            rec = self.records[ref]
            if rec.added is not None:
                after = after + mirror(rec.added, about=plane)
            if rec.removed is not None:
                after = after - mirror(rec.removed, about=plane)
        return self._commit(fid, after)

    def pattern(self, fid: str, feature_ids: list[str], transforms: list) -> Part:
        """transforms: callables Shape -> Shape for each copy (the original is not included)."""
        base = self.require_solid(fid, "pattern")
        after = base
        for ref in feature_ids:
            rec = self.records[ref]
            for tf in transforms:
                if rec.added is not None:
                    after = after + tf(rec.added)
                if rec.removed is not None:
                    after = after - tf(rec.removed)
        return self._commit(fid, after)


from build123d import Align  # noqa: E402  (used by hole)


def _nonempty(shape: Shape | None) -> Part | None:
    if shape is None:
        return None
    try:
        return shape if shape.volume > _TINY else None
    except Exception:
        return None


def _max_feasible(attempt, upper: float, iterations: int = 8) -> float | None:
    """Bisect for the largest value in (0, upper) for which attempt() succeeds."""
    lo, hi = 0.0, upper
    best = None
    for _ in range(iterations):
        mid = (lo + hi) / 2
        try:
            attempt(mid)
            best, lo = mid, mid
        except Exception:
            hi = mid
    return best
