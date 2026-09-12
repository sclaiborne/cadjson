"""Export a document as a standalone build123d script: the escape hatch when the schema is not
enough. The script needs only build123d and reproduces the cadgen build (same solid), with
selectors resolved to concrete geometry recorded at export time."""

from __future__ import annotations

import math
from pathlib import Path

from build123d import Location, Plane

from cadgen import __version__
from cadgen.build import _run_feature
from cadgen.builder import Builder
from cadgen.context import Context
from cadgen.errors import CadgenError
from cadgen.planes import AXIS, resolve_plane
from cadgen.schema import (
    Chamfer,
    CircleShape,
    Document,
    Extrude,
    FacePlane,
    Fillet,
    Hole,
    Mirror,
    OffsetPlane,
    PathShape,
    Loft,
    PatternFeature,
    PointsShape,
    PolygonShape,
    RectShape,
    Revolve,
    Shell,
    SlotShape,
    Sweep,
    TextShape,
    Thread,
)
from cadgen.selectors import Selection
from cadgen.sketch import _pattern_locations, open_path_wire


def _n(v: float) -> str:
    return f"{round(v, 6) + 0.0:g}"


def _v(vec) -> str:
    return f"({_n(vec.X)}, {_n(vec.Y)}, {_n(vec.Z)})"


class PythonExporter:
    def __init__(self, doc: Document):
        self.doc = doc
        self.ctx = Context(doc.params, doc.units)
        self.builder = Builder()
        self.lines: list[str] = []
        self.tracked = {fid for f in doc.features if isinstance(f, (Mirror, PatternFeature)) for fid in (f.features or [])}

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    # --- planes and sketches --------------------------------------------------------------------

    def _plane_code(self, ref, plane: Plane) -> str:
        if isinstance(ref, str):
            return f"Plane.{ref}"
        if isinstance(ref, OffsetPlane) and ref.origin is None:
            return f"Plane.{ref.base}.offset({_n(self.ctx.length(ref.offset))})"
        return f"Plane(origin={_v(plane.origin)}, x_dir={_v(plane.x_dir)}, z_dir={_v(plane.z_dir)})"

    def _shape_code(self, shape) -> str:
        ctx = self.ctx
        if isinstance(shape, RectShape):
            w, h = ctx.length(shape.w), ctx.length(shape.h)
            if shape.corner is not None:
                u, v = ctx.vec2(shape.corner); cx, cy = u + w / 2, v + h / 2
            elif shape.top_left is not None:
                u, v = ctx.vec2(shape.top_left); cx, cy = u + w / 2, v - h / 2
            elif shape.top_right is not None:
                u, v = ctx.vec2(shape.top_right); cx, cy = u - w / 2, v - h / 2
            elif shape.bottom_right is not None:
                u, v = ctx.vec2(shape.bottom_right); cx, cy = u - w / 2, v + h / 2
            else:
                cx, cy = ctx.vec2(shape.center) if shape.center is not None else (0.0, 0.0)
            rot = f", rotation={_n(ctx.num(shape.angle))}" if ctx.num(shape.angle) else ""
            radius = ctx.length(shape.radius)
            if radius > 0:
                return f"Pos({_n(cx)}, {_n(cy)}) * RectangleRounded({_n(w)}, {_n(h)}, {_n(radius)}{rot})"
            return f"Pos({_n(cx)}, {_n(cy)}) * Rectangle({_n(w)}, {_n(h)}{rot})"
        if isinstance(shape, CircleShape):
            r = ctx.length(shape.r) if shape.r is not None else ctx.length(shape.d) / 2
            cx, cy = ctx.vec2(shape.center)
            return f"Pos({_n(cx)}, {_n(cy)}) * Circle({_n(r)})"
        if isinstance(shape, SlotShape):
            cx, cy = ctx.vec2(shape.center)
            return (f"Pos({_n(cx)}, {_n(cy)}) * SlotOverall({_n(ctx.length(shape.length))}, "
                    f"{_n(ctx.length(shape.width))}, rotation={_n(ctx.num(shape.angle))})")
        if isinstance(shape, PolygonShape):
            radius = ctx.length(shape.d) / 2 if shape.d is not None else ctx.length(shape.flat) / 2 / math.cos(math.pi / shape.sides)
            cx, cy = ctx.vec2(shape.center)
            return f"Pos({_n(cx)}, {_n(cy)}) * RegularPolygon({_n(radius)}, {shape.sides}, rotation={_n(ctx.num(shape.angle))})"
        if isinstance(shape, PointsShape):
            pts = ", ".join(f"({_n(u)}, {_n(v)})" for u, v in (ctx.vec2(p) for p in shape.points))
            return f"Polygon({pts}, align=None)"
        if isinstance(shape, TextShape):
            cx, cy = ctx.vec2(shape.center)
            style = "FontStyle.BOLD" if shape.bold else "FontStyle.REGULAR"
            return (f"Location(({_n(cx)}, {_n(cy)}, 0), {_n(ctx.num(shape.angle))}) * Text({shape.text!r}, "
                    f"{_n(ctx.length(shape.size))}, font={shape.font!r}, font_style={style}, align=(Align.CENTER, Align.CENTER))")
        if isinstance(shape, PathShape):
            from cadgen.sketch import _path

            face = _path(shape, ctx)
            parts = []
            for e in face.edges():
                if e.geom_type.name == "LINE":
                    parts.append(f"Line(({_n(e.position_at(0).X)}, {_n(e.position_at(0).Y)}), ({_n(e.position_at(1).X)}, {_n(e.position_at(1).Y)}))")
                else:
                    a, m, b = e.position_at(0), e.position_at(0.5), e.position_at(1)
                    parts.append(f"ThreePointArc(({_n(a.X)}, {_n(a.Y)}), ({_n(m.X)}, {_n(m.Y)}), ({_n(b.X)}, {_n(b.Y)}))")
            return "make_face([" + ", ".join(parts) + "])"
        raise CadgenError(f"cannot export shape {shape.type}", self.ctx.feature_id)

    def _sketch_code(self, sketch_model) -> None:
        self.emit("sk = None")
        for shape in sketch_model.shapes:
            code = self._shape_code(shape)
            locs = _pattern_locations(shape.pattern, self.ctx)
            if len(locs) == 1 and shape.pattern is None:
                self.emit(f"piece = {code}")
            else:
                loc_list = ", ".join(
                    f"Location(({_n(l.position.X)}, {_n(l.position.Y)}, 0), {_n(l.orientation.Z)})" for l in locs
                )
                self.emit(f"piece = Sketch() + [loc * ({code}) for loc in [{loc_list}]]")
            if shape.mode == "add":
                self.emit("sk = piece if sk is None else sk + piece")
            else:
                self.emit("sk = sk - piece")

    # --- features -----------------------------------------------------------------------------

    def _selection(self) -> Selection:
        solid = self.builder.require_solid(self.ctx.feature_id, "select geometry")
        return Selection(solid, self.ctx, self.builder.new_faces, self.builder.new_edges)

    def _plane(self, ref) -> Plane:
        return resolve_plane(ref, self.ctx, lambda sel: self._selection().faces(sel))

    def _edge_keys(self, edges) -> str:
        return "[" + ", ".join(f"({_v(e.bounding_box().center())}, {_n(e.length)})" for e in edges) + "]"

    def _face_keys(self, faces) -> str:
        return "[" + ", ".join(f"({_v(f.bounding_box().center())}, {_n(f.area)})" for f in faces) + "]"

    def _combine(self, op: str) -> None:
        if op == "add":
            self.emit("part = tool if part is None else part + tool")
        elif op == "cut":
            self.emit("part = part - tool")
        else:
            self.emit("part = part & tool")

    def _feature(self, feat) -> None:
        ctx = self.ctx
        fid = feat.id
        self.emit(f"# --- {fid} ({feat.type})")
        if fid in self.tracked:
            self.emit("_before = part")
        if isinstance(feat, Extrude):
            plane = self._plane(feat.sketch.plane)
            self._sketch_code(feat.sketch)
            if feat.through:
                amount = self.builder.through_distance(plane)
                self.emit(f"tool = extrude({self._plane_code(feat.sketch.plane, plane)} * sk, amount={_n(amount)}, both=True)")
            else:
                extra = ", both=True" if feat.both else ""
                extra += f", taper={_n(ctx.num(feat.taper))}" if ctx.num(feat.taper) else ""
                self.emit(f"tool = extrude({self._plane_code(feat.sketch.plane, plane)} * sk, amount={_n(ctx.length(feat.distance))}{extra})")
            self._combine(feat.op)
        elif isinstance(feat, Revolve):
            plane = self._plane(feat.sketch.plane)
            self._sketch_code(feat.sketch)
            if isinstance(feat.axis, str):
                origin, direction = plane.origin, (plane.x_dir if feat.axis == "u" else plane.y_dir)
            else:
                u, v = ctx.vec2(feat.axis.point)
                origin = plane.from_local_coords((u, v, 0))
                direction = plane.x_dir * ctx.num(feat.axis.dir[0]) + plane.y_dir * ctx.num(feat.axis.dir[1])
            self.emit(
                f"tool = revolve({self._plane_code(feat.sketch.plane, plane)} * sk, "
                f"axis=Axis({_v(origin)}, {_v(direction)}), revolution_arc={_n(ctx.num(feat.angle))})"
            )
            self._combine(feat.op)
        elif isinstance(feat, Fillet):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            self.emit(f"part = part.fillet({_n(ctx.length(feat.radius))}, _edges(part, {keys}))")
        elif isinstance(feat, Chamfer):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            l2 = "None" if feat.length2 is None else _n(ctx.length(feat.length2))
            self.emit(f"part = part.chamfer({_n(ctx.length(feat.length))}, {l2}, _edges(part, {keys}))")
        elif isinstance(feat, Shell):
            openings = "None" if feat.remove is None else f"_faces(part, {self._face_keys(self._selection().faces(feat.remove))})"
            self.emit(f"part = offset(part, amount={_n(-ctx.length(feat.thickness))}, openings={openings})")
        elif isinstance(feat, Loft):
            names = []
            for i, sec in enumerate(feat.sections):
                plane = self._plane(sec.plane)
                self._sketch_code(sec)
                self.emit(f"sec{i} = {self._plane_code(sec.plane, plane)} * sk")
                names.append(f"sec{i}.faces()[0]")
            self.emit(f"tool = loft([{', '.join(names)}], ruled={feat.ruled})")
            self._combine(feat.op)
        elif isinstance(feat, Sweep):
            pplane = self._plane(feat.profile.plane)
            self._sketch_code(feat.profile)
            self.emit(f"profile = ({self._plane_code(feat.profile.plane, pplane)} * sk).faces()[0]")
            path_plane = self._plane(feat.path.plane)
            wire = path_plane * open_path_wire(feat.path, ctx)
            parts = []
            for e in wire.edges():
                if e.geom_type.name == "LINE":
                    parts.append(f"Line({_v(e.position_at(0))}, {_v(e.position_at(1))})")
                else:
                    parts.append(f"ThreePointArc({_v(e.position_at(0))}, {_v(e.position_at(0.5))}, {_v(e.position_at(1))})")
            self.emit(f"path = Wire([{', '.join(parts)}])")
            self.emit("tool = sweep(profile, path=path, transition=Transition.ROUND)")
            self._combine(feat.op)
        elif isinstance(feat, Thread):
            from cadgen.standards import thread_spec

            spec = thread_spec(feat.size)
            face = self._selection().faces(feat.face)[0]
            keys = self._face_keys([face])
            ext = feat.kind == "external"
            self.emit("from bd_warehouse.thread import IsoThread")
            self.emit(f"face = _faces(part, {keys})[0]")
            self.emit(f"part = _thread(part, face, major={_n(spec.major)}, pitch={_n(spec.pitch)}, external={ext}, "
                      f"length={'None' if feat.length is None else _n(ctx.length(feat.length))}, "
                      f"near={'None' if feat.near is None else _v(ctx.vec3(feat.near))}, hand={feat.hand!r})")
        elif isinstance(feat, Hole):
            plane = self._plane(FacePlane(face=feat.face))
            from cadgen.build import hole_diameter

            r = hole_diameter(feat, ctx) / 2
            amount = self.builder.through_distance(plane) if feat.through else ctx.length(feat.depth)
            self.emit(f"plane = {self._plane_code(FacePlane(face=feat.face), plane)}")
            self.emit("tool = None")
            for p in feat.at:
                u, v = ctx.vec2(p)
                self.emit(f"piece = extrude(plane * (Pos({_n(u)}, {_n(v)}) * Circle({_n(r)})), amount={_n(-amount)}, both={feat.through})")
                if feat.counterbore:
                    self.emit(
                        f"piece = piece + extrude(plane * (Pos({_n(u)}, {_n(v)}) * Circle({_n(ctx.length(feat.counterbore.diameter) / 2)})), "
                        f"amount={_n(-ctx.length(feat.counterbore.depth))})"
                    )
                if feat.countersink:
                    cs_r = ctx.length(feat.countersink.diameter) / 2
                    h = (cs_r - r) / math.tan(math.radians(ctx.num(feat.countersink.angle) / 2))
                    self.emit(
                        f"piece = piece + (plane * (Pos({_n(u)}, {_n(v)}) * Cone({_n(r)}, {_n(cs_r)}, {_n(h)}, "
                        f"align=(Align.CENTER, Align.CENTER, Align.MAX))))"
                    )
                self.emit("tool = piece if tool is None else tool + piece")
            self.emit("part = part - tool")
        elif isinstance(feat, Mirror):
            plane = self._plane(feat.plane)
            pc = self._plane_code(feat.plane, plane)
            if feat.features is None:
                self.emit(f"part = part + mirror(part, about={pc})")
            else:
                for ref in feat.features:
                    self.emit(f"part = _apply(part, D[{ref!r}], lambda s: mirror(s, about={pc}))")
        elif isinstance(feat, PatternFeature):
            n = int(round(ctx.num(feat.count)))
            if feat.kind == "linear":
                d = AXIS[feat.direction] if isinstance(feat.direction, str) else ctx.dir3(feat.direction)
                s = ctx.length(feat.spacing)
                for ref in feat.features:
                    self.emit(f"for i in range(1, {n}):")
                    self.emit(f"    part = _apply(part, D[{ref!r}], lambda s, i=i: s.translate(Vector{_v(d)} * ({_n(s)} * i)))")
            else:
                if isinstance(feat.axis, str):
                    origin, direction = AXIS[feat.axis] * 0, AXIS[feat.axis]
                else:
                    origin, direction = ctx.vec3(feat.axis.point), ctx.dir3(feat.axis.dir)
                sweep = ctx.num(feat.angle)
                step = sweep / n if abs(sweep - 360) < 1e-9 else sweep / (n - 1)
                for ref in feat.features:
                    self.emit(f"for i in range(1, {n}):")
                    self.emit(f"    part = _apply(part, D[{ref!r}], lambda s, i=i: s.rotate(Axis({_v(origin)}, {_v(direction)}), {_n(step)} * i))")
        else:
            raise CadgenError(f"cannot export feature type {feat.type!r}", fid)
        if fid in self.tracked:
            self.emit(f"D[{fid!r}] = _delta(_before, part)")
        self.emit()

    def export(self) -> str:
        if self.doc.parts:
            raise CadgenError("assemblies (parts) cannot be exported to a script yet; export the individual parts")
        for feat in self.doc.features:
            self.ctx.feature_id = feat.id
            self._feature(feat)
            _run_feature(feat, self.ctx, self.builder)
        self.ctx.feature_id = None
        body = "\n".join(self.lines)
        params = "\n".join(f"#   {k} = {v:g}" for k, v in self.ctx.params.items()) or "#   (none)"
        return TEMPLATE.format(name=self.doc.name, version=__version__, params=params, body=body)


def export_python(doc: Document, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{doc.name}_build123d.py"
    path.write_text(PythonExporter(doc).export(), encoding="utf-8")
    return path


TEMPLATE = '''"""{name}: standalone build123d script generated by cadgen {version}.

Equivalent to `cadgen build` for this part. Selectors are resolved to concrete geometry
(bounding-box centre and size), so edit freely but re-check fillets after changing sizes.
Resolved params:
{params}
"""

from build123d import *


def _edges(part, keys):
    out = []
    for center, length in keys:
        cands = [e for e in part.edges() if abs(e.length - length) <= max(1e-3, 1e-3 * length)]
        out.append(min(cands, key=lambda e: (e.bounding_box().center() - Vector(*center)).length))
    return out


def _faces(part, keys):
    out = []
    for center, area in keys:
        cands = [f for f in part.faces() if abs(f.area - area) <= max(1e-3, 1e-3 * area)]
        out.append(min(cands, key=lambda f: (f.bounding_box().center() - Vector(*center)).length))
    return out


def _delta(before, after):
    if before is None:
        return (after, None)
    added, removed = after - before, before - after
    return (added if added.volume > 1e-6 else None, removed if removed.volume > 1e-6 else None)


def _apply(part, delta, transform):
    added, removed = delta
    if added is not None:
        part = part + transform(added)
    if removed is not None:
        part = part - transform(removed)
    return part


def _thread(part, face, major, pitch, external, length, near, hand):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from bd_warehouse.thread import IsoThread

    cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
    ax = cyl.Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
    p = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    ts = [(Vector(v.X, v.Y, v.Z) - p).dot(d) for v in face.vertices()]
    tmin, tmax = min(ts), max(ts)
    start, direction = tmin, d
    if near is not None and (Vector(*near) - (p + d * tmax)).length < (Vector(*near) - (p + d * tmin)).length:
        start, direction = tmax, d * -1
    L = (tmax - tmin) if length is None else min(length, tmax - tmin)
    n = direction
    x_dir = Vector(1, 0, 0) if abs(n.Z) > 0.999 else Vector(0, 0, 1).cross(n).normalized()
    plane = Plane(origin=p + d * start, x_dir=x_dir, z_dir=n)
    th = IsoThread(major_diameter=major, pitch=pitch, length=L, external=external, hand=hand, end_finishes=("fade", "fade"))
    th = plane * th.translate((0, 0, -th.bounding_box().min.Z))
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    if external:
        ring = Cylinder(major / 2 + 0.05, L, align=align) - Cylinder(th.min_radius, L, align=align)
        return (part - (plane * ring)) + th
    return (part - (plane * Cylinder(major / 2, L, align=align))) + th


part = None
D = {{}}

{body}

if __name__ == "__main__":
    print("volume", round(part.volume, 3), "valid", part.is_valid)
    export_step(part, "{name}.step")
    export_stl(part, "{name}.stl")
'''
