"""The Python builder API. The JSON layer only ever calls this; if JSON is ever dropped, this
is the script format. Every method takes resolved numbers and build123d objects."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from build123d import (
    Align,
    Axis,
    Circle,
    Cone,
    Cylinder,
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

from cadjson.errors import CadjsonError
from cadjson.selectors import edge_sig, face_sig

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
            raise CadjsonError(f"cannot {what}: there is no solid yet (the first feature must add material)", fid)
        return self.solid

    def _commit(self, fid: str, after: Part, added: Part | None = None, removed: Part | None = None) -> Part:
        before = self.solid
        if after is None or not after.is_valid:
            raise CadjsonError("the resulting solid is invalid", fid, ["try a slightly different size or order of features"])
        if after.volume < _TINY:
            raise CadjsonError("the resulting solid has no volume", fid)
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
        raise CadjsonError(f"unknown op {op!r}", fid)

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
            raise CadjsonError("extrude distance must be non-zero", fid)
        try:
            tool = extrude(located, amount=amount, both=both, taper=taper)
        except Exception as exc:  # OCCT failures are not very descriptive
            raise CadjsonError(f"extrude failed: {exc}", fid) from None
        return self._combine(fid, tool, op)

    def revolve(self, fid: str, sketch: Sketch, plane: Plane, axis: Axis, angle: float, op: str) -> Part:
        located = plane * sketch
        try:
            tool = revolve(located, axis=axis, revolution_arc=angle)
        except Exception as exc:
            raise CadjsonError(f"revolve failed: {exc}", fid, ["the profile must not cross the axis"]) from None
        return self._combine(fid, tool, op)

    def fillet(self, fid: str, edges: list[Edge], radius: float) -> Part:
        base = self.require_solid(fid, "fillet")
        if radius <= 0:
            raise CadjsonError("fillet radius must be positive", fid)
        try:
            after = base.fillet(radius, edges)
        except Exception:
            hints = [f"{len(edges)} edge(s) selected"]
            best = _max_feasible(lambda r: base.fillet(r, edges), radius)
            if best is not None:
                hints.append(f"largest radius that works on these edges is about {best:.3g}")
            raise CadjsonError(f"fillet of radius {radius:g} failed", fid, hints) from None
        return self._commit(fid, after)

    def chamfer(self, fid: str, edges: list[Edge], length: float, length2: float | None) -> Part:
        base = self.require_solid(fid, "chamfer")
        if length <= 0 or (length2 is not None and length2 <= 0):
            raise CadjsonError("chamfer lengths must be positive", fid)
        try:
            after = base.chamfer(length, length2, edges)
        except Exception:
            hints = [f"{len(edges)} edge(s) selected"]
            best = _max_feasible(lambda l: base.chamfer(l, None if length2 is None else l * length2 / length, edges), length)
            if best is not None:
                hints.append(f"largest length that works on these edges is about {best:.3g}")
            raise CadjsonError(f"chamfer of length {length:g} failed", fid, hints) from None
        return self._commit(fid, after)

    def shell(self, fid: str, thickness: float, openings: list[Face] | None) -> Part:
        base = self.require_solid(fid, "shell")
        if abs(thickness) < _TINY:
            raise CadjsonError("shell thickness must be non-zero", fid)
        try:
            after = offset(base, amount=-thickness, openings=openings or None)
        except Exception as exc:
            raise CadjsonError(f"shell failed: {exc}", fid, ["thickness may be too large for the geometry"]) from None
        return self._commit(fid, after)

    def hole(self, fid: str, plane: Plane, points: list[tuple[float, float]], diameter: float, *,
             depth: float | None, through: bool, counterbore: tuple[float, float] | None,
             countersink: tuple[float, float] | None) -> Part:
        base = self.require_solid(fid, "hole")
        r = diameter / 2
        if r <= 0:
            raise CadjsonError("hole diameter must be positive", fid)
        amount = self.through_distance(plane) if through else depth
        if amount is None or amount <= 0:
            raise CadjsonError("hole depth must be positive", fid)
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

    def loft(self, fid: str, sections: list, ruled: bool, op: str) -> Part:
        from build123d import loft as b3d_loft

        faces = []
        for i, located in enumerate(sections):
            fs = located.faces()
            if len(fs) != 1:
                raise CadjsonError(f"loft section {i} must be exactly one closed shape, got {len(fs)}", fid)
            faces.append(fs[0])
        try:
            tool = b3d_loft(faces, ruled=ruled)
        except Exception as exc:
            raise CadjsonError(f"loft failed: {exc}", fid, ["sections should have compatible shapes and not cross"]) from None
        return self._combine(fid, tool, op)

    def sweep(self, fid: str, profile, path_wire, op: str) -> Part:
        from build123d import Transition
        from build123d import sweep as b3d_sweep

        fs = profile.faces()
        if len(fs) != 1:
            raise CadjsonError(f"sweep profile must be exactly one closed shape, got {len(fs)}", fid)
        try:
            tool = b3d_sweep(fs[0], path=path_wire, transition=Transition.ROUND)
        except Exception as exc:
            raise CadjsonError(f"sweep failed: {exc}", fid, ["the profile should sit at the path start, perpendicular to it",
                                                         "bend radii must exceed the profile's half-width"]) from None
        return self._combine(fid, tool, op)

    def place(self, fid: str, shape: Part, op: str) -> Part:
        """Combine a ready-made shape (an imported part) with the body."""
        return self._combine(fid, shape, op)

    def thread(self, fid: str, face: Face, spec, external: bool, length: float | None, near: Vector | None,
               hand: str) -> Part:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        from cadjson.planes import plane_from_normal

        base = self.require_solid(fid, "thread")
        if face.geom_type.name != "CYLINDER":
            raise CadjsonError(f"thread needs a cylindrical face, got {face.geom_type.name.lower()}", fid)
        try:
            from bd_warehouse.thread import IsoThread
        except ImportError:
            raise CadjsonError("threads need the bd_warehouse package (pip install 'bd_warehouse<0.3')", fid) from None
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        ax = cyl.Axis()
        d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
        p = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
        ts = [(Vector(v.X, v.Y, v.Z) - p).dot(d) for v in face.vertices()]
        tmin, tmax = min(ts), max(ts)
        face_len = tmax - tmin
        if face_len < 1e-6:
            raise CadjsonError("cannot determine the length of the cylindrical face", fid)
        start_t, direction = tmin, d
        if near is not None:
            if (near - (p + d * tmax)).length < (near - (p + d * tmin)).length:
                start_t, direction = tmax, d * -1
        L = face_len if length is None else min(length, face_len)
        if L <= spec.pitch:
            raise CadjsonError(f"thread length {L:g} is shorter than one pitch ({spec.pitch:g})", fid)
        radius = cyl.Radius()
        expect = spec.major / 2 if external else spec.tap_drill / 2
        hints = []
        if abs(radius - expect) > 0.6:
            hints.append(f"face radius is {radius:g}, expected about {expect:g} for {spec.designation}")
        plane = plane_from_normal(p + d * start_t, direction)
        try:
            th = IsoThread(major_diameter=spec.major, pitch=spec.pitch, length=L, external=external,
                           hand=hand, end_finishes=("fade", "fade"))
            bb = th.bounding_box()
            th = th.translate((0, 0, -bb.min.Z))
            th = plane * th
            align = (Align.CENTER, Align.CENTER, Align.MIN)
            if external:
                ring = Cylinder(spec.major / 2 + 0.05, L, align=align) - Cylinder(th.min_radius, L, align=align)
                after = (base - (plane * ring)) + th
            else:
                bore = Cylinder(spec.major / 2, L, align=align)
                after = (base - (plane * bore)) + th
        except Exception as exc:
            raise CadjsonError(f"thread failed: {exc}", fid, hints) from None
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
