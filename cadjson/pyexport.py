"""Export a document as a standalone build123d script: the escape hatch when the schema is not
enough. The script needs only build123d and reproduces the cadjson build (same solid).

Params become named constants at the top of the script and every dimension written in the part
file is emitted as the same expression over those names, so the script stays editable by
dimension. Values cadjson derives from the geometry (selected faces and edges, `through` depths,
revolve axes, sweep paths placed on faces) are recorded as numbers at export time.

With `cadgen=True` the part becomes a text-to-cad model: a function decorated with cadgen's
`@step` (plus `@stl` / `@threemf` when the part asks for them)."""

from __future__ import annotations

import ast
import keyword
import math
import re
from pathlib import Path

import build123d
from build123d import Plane

from cadjson import __version__
from cadjson.build import _run_feature
from cadjson.builder import Builder
from cadjson.context import Context
from cadjson.errors import CadjsonError
from cadjson.expr import CONSTANTS, Dim, names_in, parse_ast
from cadjson.planes import AXIS, resolve_plane
from cadjson.schema import (
    Chamfer,
    CircleShape,
    Document,
    Extrude,
    FacePlane,
    Fillet,
    GridPattern,
    Hole,
    LinearPattern,
    Loft,
    Mirror,
    OffsetPlane,
    PathShape,
    PatternFeature,
    PointsShape,
    PolarPattern,
    PolygonShape,
    RectShape,
    Revolve,
    Shell,
    SlotShape,
    Sweep,
    TextShape,
    Thread,
)
from cadjson.selectors import Selection
from cadjson.sketch import _SEG

# Names a param must not take in the script: build123d's star import, Python itself, and the
# script's own variables.
_TAKEN = (
    set(getattr(build123d, "__all__", dir(build123d)))
    | set(keyword.kwlist)
    | {"math", "part", "min", "max", "abs", "round", "range", "int", "print", "step", "stl", "threemf"}
)


def _n(v: float) -> str:
    """A number as short, exact-enough Python."""
    text = repr(round(float(v), 6) + 0.0)
    return text[:-2] if text.endswith(".0") else text


def _v(vec) -> str:
    return f"({_n(vec.X)}, {_n(vec.Y)}, {_n(vec.Z)})"


# --- code strings with just enough precedence handling --------------------------------------------

_PREC_ADD, _PREC_MUL, _PREC_UNARY, _PREC_ATOM = 1, 2, 3, 4


def _prec(code: str) -> int:
    node = ast.parse(code, mode="eval").body
    if isinstance(node, ast.BinOp):
        return _PREC_ADD if isinstance(node.op, (ast.Add, ast.Sub)) else _PREC_MUL
    if isinstance(node, ast.UnaryOp):
        return _PREC_UNARY
    return _PREC_ATOM


def _const(code: str) -> float | None:
    try:
        value = ast.literal_eval(code)
    except (ValueError, SyntaxError):
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _wrap(code: str, need: int) -> str:
    return f"({code})" if _prec(code) < need else code


def _bin(a: str, op: str, b: str) -> str:
    ca, cb = _const(a), _const(b)
    if ca is not None and cb is not None and not (op == "/" and cb == 0):
        return _n({"+": ca + cb, "-": ca - cb, "*": ca * cb, "/": ca / cb if cb else 0}[op])
    if op in "+-" and cb == 0:
        return a
    if op == "+" and ca == 0:
        return b
    if op == "-" and ca == 0:
        return _neg(b)
    if op in "*/" and cb == 1:
        return a
    if op == "*" and ca == 1:
        return b
    p = _PREC_ADD if op in "+-" else _PREC_MUL
    # right operand of - and / binds tighter than its own level
    return f"{_wrap(a, p)} {op} {_wrap(b, p + 1 if op in '-/' else p)}"


def _neg(code: str) -> str:
    c = _const(code)
    if c is not None:
        return _n(-c)
    if code.startswith("-") and _prec(code) == _PREC_UNARY:
        return code[1:]
    return f"-{_wrap(code, _PREC_UNARY)}"


def _identifier(name: str, taken: set[str]) -> str:
    ident = re.sub(r"\W", "_", name)
    if not ident or ident[0].isdigit() or ident.startswith("_"):
        ident = "p_" + ident.lstrip("_")
    while ident in taken:
        ident += "_"
    return ident


class PythonExporter:
    def __init__(self, doc: Document, cadgen: bool = False):
        self.doc = doc
        self.cadgen = cadgen
        self.ctx = Context(doc.params, doc.units)
        self.builder = Builder()
        self.lines: list[str] = []
        self.helpers: set[str] = set()
        self.tracked = {fid for f in doc.features if isinstance(f, (Mirror, PatternFeature)) for fid in (f.features or [])}
        self.function = _identifier(doc.name, _TAKEN)
        taken = _TAKEN | {self.function}
        self.names: dict[str, str] = {}
        for name in doc.params:
            self.names[name] = _identifier(name, taken)
            taken.add(self.names[name])

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    # --- dims ---------------------------------------------------------------------------------

    def expr(self, dim: Dim) -> str:
        """A dim as Python over the param names, in document units."""
        if not isinstance(dim, str):
            return _n(dim)

        def render(node) -> str:
            kind = node[0]
            if kind == "num":
                return _n(node[1])
            if kind == "name":
                if node[1] in self.names:
                    return self.names[node[1]]
                if node[1] in CONSTANTS:
                    return f"math.{node[1]}"
                raise CadjsonError(f"unknown parameter {node[1]!r}", self.ctx.feature_id)
            if kind == "neg":
                return _neg(render(node[1]))
            if kind == "bin":
                l, r = render(node[2]), render(node[3])
                p = _PREC_ADD if node[1] in "+-" else _PREC_MUL
                return f"{_wrap(l, p)} {node[1]} {_wrap(r, p + 1 if node[1] in '-/' else p)}"
            fn, args = node[1], [render(a) for a in node[2]]
            if fn in ("sin", "cos", "tan"):
                return f"math.{fn}(math.radians({args[0]}))"
            if fn in ("asin", "acos", "atan"):
                return f"math.degrees(math.{fn}({args[0]}))"
            if fn in ("sqrt", "floor", "ceil"):
                return f"math.{fn}({args[0]})"
            return f"{fn}({', '.join(args)})"

        return render(parse_ast(dim))

    def num(self, dim: Dim) -> str:
        """A unitless number (count, angle)."""
        return self.expr(dim)

    def length(self, dim: Dim) -> str:
        """A length in mm: the expression, scaled by build123d's IN for inch documents."""
        code = self.expr(dim)
        if self.ctx.scale == 1 or _const(code) == 0:
            return code
        return _bin(code, "*", "IN")

    def vec2(self, v) -> str:
        return f"({self.length(v[0])}, {self.length(v[1])})"

    def vec3(self, v) -> str:
        return f"({self.length(v[0])}, {self.length(v[1])}, {self.length(v[2])})"

    def _is_set(self, dim: Dim) -> bool:
        """Worth writing out: an expression, or a number other than zero."""
        return isinstance(dim, str) or float(dim) != 0

    # --- planes and sketches --------------------------------------------------------------------

    def _plane_code(self, ref, plane: Plane) -> str:
        if isinstance(ref, str):
            return f"Plane.{ref}"
        if isinstance(ref, OffsetPlane) and ref.origin is None:
            return f"Plane.{ref.base}.offset({self.length(ref.offset)})"
        return f"Plane(origin={_v(plane.origin)}, x_dir={_v(plane.x_dir)}, z_dir={_v(plane.z_dir)})"

    @staticmethod
    def _placed(cx: str, cy: str, code: str) -> str:
        return code if _const(cx) == 0 and _const(cy) == 0 else f"Pos({cx}, {cy}) * {code}"

    def _rotation(self, angle: Dim) -> str:
        return f", rotation={self.num(angle)}" if self._is_set(angle) else ""

    def _path_code(self, start, segments) -> str:
        self.helpers.add("_path")
        items = []
        for seg in segments:
            m = _SEG.match(seg)
            if not m:
                raise CadjsonError(f"path segment {seg!r}: unknown form", self.ctx.feature_id)
            word, rest = m.group(1).lower(), m.group(2).strip()
            args = [self.length(a.strip()) for a in rest.split(",")] if rest else []
            items.append("(" + ", ".join([repr(word), *args]) + (",)" if not args else ")"))
        return f"_path({self.vec2(start)}, [{', '.join(items)}])"

    def _shape_code(self, shape) -> str:
        if isinstance(shape, RectShape):
            w, h = self.length(shape.w), self.length(shape.h)
            half_w, half_h = _bin(w, "/", "2"), _bin(h, "/", "2")
            if shape.corner is not None:
                u, v = self.length(shape.corner[0]), self.length(shape.corner[1])
                cx, cy = _bin(u, "+", half_w), _bin(v, "+", half_h)
            elif shape.top_left is not None:
                u, v = self.length(shape.top_left[0]), self.length(shape.top_left[1])
                cx, cy = _bin(u, "+", half_w), _bin(v, "-", half_h)
            elif shape.top_right is not None:
                u, v = self.length(shape.top_right[0]), self.length(shape.top_right[1])
                cx, cy = _bin(u, "-", half_w), _bin(v, "-", half_h)
            elif shape.bottom_right is not None:
                u, v = self.length(shape.bottom_right[0]), self.length(shape.bottom_right[1])
                cx, cy = _bin(u, "-", half_w), _bin(v, "+", half_h)
            elif shape.center is not None:
                cx, cy = self.length(shape.center[0]), self.length(shape.center[1])
            else:
                cx = cy = "0"
            rot = self._rotation(shape.angle)
            if self.ctx.length(shape.radius) > 0:
                return self._placed(cx, cy, f"RectangleRounded({w}, {h}, {self.length(shape.radius)}{rot})")
            return self._placed(cx, cy, f"Rectangle({w}, {h}{rot})")
        if isinstance(shape, CircleShape):
            r = self.length(shape.r) if shape.r is not None else _bin(self.length(shape.d), "/", "2")
            return self._placed(self.length(shape.center[0]), self.length(shape.center[1]), f"Circle({r})")
        if isinstance(shape, SlotShape):
            code = f"SlotOverall({self.length(shape.length)}, {self.length(shape.width)}{self._rotation(shape.angle)})"
            return self._placed(self.length(shape.center[0]), self.length(shape.center[1]), code)
        if isinstance(shape, PolygonShape):
            if shape.d is not None:
                radius = _bin(self.length(shape.d), "/", "2")
            else:
                radius = _bin(_bin(self.length(shape.flat), "/", "2"), "/", f"math.cos(math.pi / {shape.sides})")
            code = f"RegularPolygon({radius}, {shape.sides}{self._rotation(shape.angle)})"
            return self._placed(self.length(shape.center[0]), self.length(shape.center[1]), code)
        if isinstance(shape, PointsShape):
            return f"Polygon({', '.join(self.vec2(p) for p in shape.points)}, align=None)"
        if isinstance(shape, TextShape):
            style = "FontStyle.BOLD" if shape.bold else "FontStyle.REGULAR"
            font_path = ""
            if shape.font_path:
                path = Path(shape.font_path)
                if not path.is_absolute():
                    path = (self.ctx.base_dir or Path.cwd()) / path
                font_path = f", font_path={str(path)!r}"
            return (f"Location(({self.length(shape.center[0])}, {self.length(shape.center[1])}, 0), {self.num(shape.angle)}) * "
                    f"Text({shape.text!r}, {self.length(shape.size)}, font={shape.font!r}{font_path}, font_style={style}, "
                    f"align=(Align.CENTER, Align.CENTER))")
        if isinstance(shape, PathShape):
            return f"make_face({self._path_code(shape.start, shape.segments)})"
        raise CadjsonError(f"cannot export shape {shape.type}", self.ctx.feature_id)

    def _locations_code(self, pattern) -> str:
        if isinstance(pattern, LinearPattern):
            self.helpers.add("_linear")
            direction = {"u": "(1, 0)", "v": "(0, 1)"}.get(pattern.direction) if isinstance(pattern.direction, str) \
                else f"({self.num(pattern.direction[0])}, {self.num(pattern.direction[1])})"
            return f"_linear({self.num(pattern.count)}, {self.length(pattern.spacing)}, {direction}, centered={pattern.centered})"
        if isinstance(pattern, PolarPattern):
            self.helpers.add("_polar")
            return (f"_polar({self.num(pattern.count)}, {self.length(pattern.radius)}, center={self.vec2(pattern.center)}, "
                    f"start_angle={self.num(pattern.start_angle)}, angle={self.num(pattern.angle)}, rotate={pattern.rotate})")
        if isinstance(pattern, GridPattern):
            self.helpers.add("_grid")
            return (f"_grid({tuple(pattern.count)}, ({self.length(pattern.spacing[0])}, {self.length(pattern.spacing[1])}), "
                    f"centered={pattern.centered})")
        raise CadjsonError(f"unknown pattern {pattern!r}", self.ctx.feature_id)

    def _sketch_code(self, sketch_model) -> None:
        self.emit("sk = None")
        for shape in sketch_model.shapes:
            code = self._shape_code(shape)
            if shape.pattern is None:
                self.emit(f"piece = {code}")
            else:
                self.emit(f"piece = Sketch() + [loc * {_wrap(code, _PREC_ATOM)} for loc in {self._locations_code(shape.pattern)}]")
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
        self.helpers.add("_edges")
        return "[" + ", ".join(f"({_v(e.bounding_box().center())}, {_n(e.length)})" for e in edges) + "]"

    def _face_keys(self, faces) -> str:
        self.helpers.add("_faces")
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
            self.helpers.add("_delta")
            self.emit("before = part")
        if isinstance(feat, Extrude):
            plane = self._plane(feat.sketch.plane)
            self._sketch_code(feat.sketch)
            pc = self._plane_code(feat.sketch.plane, plane)
            if feat.through:
                amount = self.builder.through_distance(plane)
                self.emit(f"tool = extrude({pc} * sk, amount={_n(amount)}, both=True)")
            else:
                extra = ", both=True" if feat.both else ""
                extra += f", taper={self.num(feat.taper)}" if self._is_set(feat.taper) else ""
                self.emit(f"tool = extrude({pc} * sk, amount={self.length(feat.distance)}{extra})")
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
                f"axis=Axis({_v(origin)}, {_v(direction)}), revolution_arc={self.num(feat.angle)})"
            )
            self._combine(feat.op)
        elif isinstance(feat, Fillet):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            self.emit(f"part = part.fillet({self.length(feat.radius)}, _edges(part, {keys}))")
        elif isinstance(feat, Chamfer):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            l2 = "None" if feat.length2 is None else self.length(feat.length2)
            self.emit(f"part = part.chamfer({self.length(feat.length)}, {l2}, _edges(part, {keys}))")
        elif isinstance(feat, Shell):
            openings = "None" if feat.remove is None else f"_faces(part, {self._face_keys(self._selection().faces(feat.remove))})"
            self.emit(f"part = offset(part, amount={_neg(self.length(feat.thickness))}, openings={openings})")
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
            self.emit(f"path = {self._plane_code(feat.path.plane, path_plane)} * Wire({self._path_code(feat.path.start, feat.path.segments)})")
            self.emit("tool = sweep(profile, path=path, transition=Transition.ROUND)")
            self._combine(feat.op)
        elif isinstance(feat, Thread):
            from cadjson.standards import thread_spec

            self.helpers.add("_thread")
            spec = thread_spec(feat.size)
            keys = self._face_keys([self._selection().faces(feat.face)[0]])
            ext = feat.kind == "external"
            self.emit(f"thread_face = _faces(part, {keys})[0]")
            self.emit(f"part = _thread(part, thread_face, major={_n(spec.major)}, pitch={_n(spec.pitch)}, external={ext}, "
                      f"length={'None' if feat.length is None else self.length(feat.length)}, "
                      f"near={'None' if feat.near is None else self.vec3(feat.near)}, hand={feat.hand!r})  # {feat.size}")
        elif isinstance(feat, Hole):
            from cadjson.build import hole_diameter

            plane = self._plane(FacePlane(face=feat.face))
            if feat.diameter is not None:
                r = _bin(self.length(feat.diameter), "/", "2")
            else:
                r = _n(hole_diameter(feat, ctx) / 2)
                self.emit(f"# {feat.standard} {feat.fit} fit: diameter {_n(hole_diameter(feat, ctx))}")
            amount = _n(self.builder.through_distance(plane)) if feat.through else self.length(feat.depth)
            self.emit(f"plane = {self._plane_code(FacePlane(face=feat.face), plane)}")
            self.emit("tool = None")
            for p in feat.at:
                pos = f"Pos{self.vec2(p)}"
                self.emit(f"piece = extrude(plane * ({pos} * Circle({r})), amount={_neg(amount)}, both={feat.through})")
                if feat.counterbore:
                    self.emit(
                        f"piece = piece + extrude(plane * ({pos} * Circle({_bin(self.length(feat.counterbore.diameter), '/', '2')})), "
                        f"amount={_neg(self.length(feat.counterbore.depth))})"
                    )
                if feat.countersink:
                    cs_r = _bin(self.length(feat.countersink.diameter), "/", "2")
                    half = _bin(self.num(feat.countersink.angle), "/", "2")
                    h = _bin(_bin(cs_r, "-", r), "/", f"math.tan(math.radians({half}))")
                    self.emit(
                        f"piece = piece + (plane * ({pos} * Cone({r}, {cs_r}, {h}, "
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
                self.helpers.add("_apply")
                for ref in feat.features:
                    self.emit(f"part = _apply(part, deltas[{ref!r}], lambda s: mirror(s, about={pc}))")
        elif isinstance(feat, PatternFeature):
            self.helpers.add("_apply")
            count = self.num(feat.count)
            n = count if _const(count) is not None else f"round({count})"
            if feat.kind == "linear":
                d = AXIS[feat.direction] if isinstance(feat.direction, str) else ctx.dir3(feat.direction)
                spacing = self.length(feat.spacing)
                for ref in feat.features:
                    self.emit(f"for i in range(1, {n}):")
                    self.emit(f"    part = _apply(part, deltas[{ref!r}], lambda s, i=i: s.translate(Vector{_v(d)} * {_wrap(_bin(spacing, '*', 'i'), _PREC_ATOM)}))")
            else:
                if isinstance(feat.axis, str):
                    origin, direction = AXIS[feat.axis] * 0, AXIS[feat.axis]
                else:
                    origin, direction = ctx.vec3(feat.axis.point), ctx.dir3(feat.axis.dir)
                full = abs(ctx.num(feat.angle) - 360) < 1e-9
                step = _bin(self.num(feat.angle), "/", n if full else _bin(n, "-", "1"))
                for ref in feat.features:
                    self.emit(f"for i in range(1, {n}):")
                    self.emit(f"    part = _apply(part, deltas[{ref!r}], lambda s, i=i: s.rotate(Axis({_v(origin)}, {_v(direction)}), {_bin(step, '*', 'i')}))")
        else:
            from cadjson.plugins import registry

            plugin = registry.plugin_for(feat)
            if plugin is None or plugin.python is None:
                raise CadjsonError(f"cannot export feature type {feat.type!r} to a script", fid)
            plugin.python(feat, self)
        if fid in self.tracked:
            self.emit(f"deltas[{fid!r}] = _delta(before, part)")
        self.emit()

    # --- assembly -----------------------------------------------------------------------------

    def _params_code(self) -> list[str]:
        """Params in an order where each is defined before it is used, as written in the file."""
        raw = self.doc.params
        order: list[str] = []

        def visit(name: str) -> None:
            if name in order:
                return
            for dep in sorted(names_in(raw[name])):
                if dep in raw and dep != name:
                    visit(dep)
            order.append(name)

        for name in raw:
            visit(name)
        lines = []
        for name in order:
            renamed = "" if self.names[name] == name else f"  # {name} in the part file"
            lines.append(f"{self.names[name]} = {self.expr(raw[name])}{renamed}")
        return lines

    def _decorators(self) -> tuple[str, list[str]]:
        outputs = self.doc.outputs
        names = ["step"]
        if outputs.stl:
            names.append("stl")
        if outputs.three_mf:
            names.append("threemf")
        decorators = [f"@{n}" for n in reversed(names[1:])] + ["@step"]
        return f"from cadgen import build123d as bd\nfrom cadgen import {', '.join(names)}\n", decorators

    def export(self) -> str:
        if self.doc.parts:
            raise CadjsonError("assemblies (parts) cannot be exported to a script yet; export the individual parts")
        for feat in self.doc.features:
            self.ctx.feature_id = feat.id
            self._feature(feat)
            _run_feature(feat, self.ctx, self.builder)
        self.ctx.feature_id = None
        while self.lines and not self.lines[-1]:
            self.lines.pop()

        units = "inches; lengths are multiplied by IN (25.4) where used" if self.ctx.scale != 1 else "mm"
        params = self._params_code() or ["# (none)"]
        body = ["part = None"]
        if self.tracked:
            body.append("deltas = {}  # what each mirrored/patterned feature added and removed")
        body += ["", *self.lines, "return part"]
        helpers = "".join(_HELPERS[h] for h in _HELPER_ORDER if h in self.helpers)
        if self.cadgen:
            imports, decorators = self._decorators()
            main = f"    {self.function}()\n"
            kind = "text-to-cad model"
        else:
            imports, decorators = "from build123d import *\n", []
            main = (f"    part = {self.function}()\n"
                    f'    print("volume", round(part.volume, 3), "valid", part.is_valid)\n'
                    f'    export_step(part, "{self.doc.name}.step")\n'
                    f'    export_stl(part, "{self.doc.name}.stl")\n')
            kind = "standalone build123d script"
        text = TEMPLATE.format(
            name=self.doc.name,
            kind=kind,
            version=__version__,
            imports=imports,
            units=units,
            params="\n".join(params),
            helpers=helpers,
            decorators="".join(d + "\n" for d in decorators),
            function=self.function,
            body="\n".join(("    " + line) if line else "" for line in body),
            main=main,
        )
        # cadgen imports the kernel lazily through `bd`, which keeps its no-op reruns fast
        return _qualify(text) if self.cadgen else text


def _qualify(text: str) -> str:
    """Prefix every build123d name with `bd.`, leaving attributes, keyword arguments and strings alone."""
    import io
    import tokenize

    exported = set(getattr(build123d, "__all__", dir(build123d)))
    tree = ast.parse(text)
    local = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    local |= {a.arg for n in ast.walk(tree) if isinstance(n, ast.arguments) for a in n.args}
    clash = sorted(local & exported)
    if clash:
        raise CadjsonError(f"script variables shadow build123d names: {', '.join(clash)}")
    lines = text.splitlines(keepends=True)
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    starts = []
    for i, tok in enumerate(toks):
        if tok.type != tokenize.NAME or tok.string not in exported:
            continue
        if (i and toks[i - 1].string == ".") or (i + 1 < len(toks) and toks[i + 1].string == "="):
            continue
        starts.append(tok.start)
    for row, col in sorted(starts, reverse=True):
        lines[row - 1] = lines[row - 1][:col] + "bd." + lines[row - 1][col:]
    return "".join(lines)


def export_python(doc: Document, out_dir: Path, cadgen: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # cadgen writes outputs beside the script under its stem, so that script takes the part's name
    path = out_dir / (f"{doc.name}.py" if cadgen else f"{doc.name}_build123d.py")
    path.write_text(PythonExporter(doc, cadgen=cadgen).export(), encoding="utf-8")
    return path


TEMPLATE = '''"""{name}: {kind} generated by cadjson {version}.

Change the parameters and run it. Values cadjson took from the geometry at export time
(selected faces and edges, through-cut depths, revolve axes) are plain numbers, so fillets
and chamfers may need re-checking after large changes.
"""

import math

{imports}
# --- parameters ({units})
{params}

{helpers}
{decorators}def {function}():
{body}


if __name__ == "__main__":
{main}'''


_HELPERS = {
    "_path": '''
def _path(start, segments):
    """Edges of a cadjson path: ("right", d), ("line", du, dv), ("to", u, v), ("arc", du, dv, r), ("close",)."""
    start = cur = Vector(*start)
    out = []
    for word, *args in segments:
        if word == "close":
            if (cur - start).length > 1e-9:
                out.append(Line(cur, start))
            break
        if word == "to":
            nxt = Vector(*args)
        else:
            moves = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
            du, dv = (moves[word][0] * args[0], moves[word][1] * args[0]) if word in moves else args[:2]
            nxt = cur + Vector(du, dv)
        out.append(RadiusArc(cur, nxt, args[2]) if word == "arc" else Line(cur, nxt))
        cur = nxt
    return out

''',
    "_linear": '''
def _linear(count, spacing, direction, centered):
    d = Vector(direction[0], direction[1], 0).normalized()
    n = int(round(count))
    first = -(n - 1) / 2 * spacing if centered else 0.0
    return [Location(d * (first + i * spacing)) for i in range(n)]

''',
    "_polar": '''
def _polar(count, radius, center, start_angle, angle, rotate):
    n = int(round(count))
    step = angle / n if abs(angle - 360) < 1e-9 else (angle / (n - 1) if n > 1 else 0)
    out = []
    for i in range(n):
        a = start_angle + i * step
        x = center[0] + radius * math.cos(math.radians(a))
        y = center[1] + radius * math.sin(math.radians(a))
        out.append(Location((x, y, 0), a if rotate else 0))
    return out

''',
    "_grid": '''
def _grid(count, spacing, centered):
    (nu, nv), (su, sv) = count, spacing
    ou = -(nu - 1) / 2 * su if centered else 0.0
    ov = -(nv - 1) / 2 * sv if centered else 0.0
    return [Location((ou + i * su, ov + j * sv, 0)) for i in range(nu) for j in range(nv)]

''',
    "_edges": '''
def _edges(part, keys):
    """Edges recorded at export time, found again by bounding-box centre and length."""
    out = []
    for center, length in keys:
        cands = [e for e in part.edges() if abs(e.length - length) <= max(1e-3, 1e-3 * length)]
        if not cands:
            raise ValueError(f"no edge of length {length} any more (near {center}); update the selection")
        out.append(min(cands, key=lambda e: (e.bounding_box().center() - Vector(*center)).length))
    return out

''',
    "_faces": '''
def _faces(part, keys):
    """Faces recorded at export time, found again by bounding-box centre and area."""
    out = []
    for center, area in keys:
        cands = [f for f in part.faces() if abs(f.area - area) <= max(1e-3, 1e-3 * area)]
        if not cands:
            raise ValueError(f"no face of area {area} any more (near {center}); update the selection")
        out.append(min(cands, key=lambda f: (f.bounding_box().center() - Vector(*center)).length))
    return out

''',
    "_delta": '''
def _delta(before, after):
    if before is None:
        return (after, None)
    added, removed = after - before, before - after
    return (added if added.volume > 1e-6 else None, removed if removed.volume > 1e-6 else None)

''',
    "_apply": '''
def _apply(part, change, transform):
    added, removed = change
    if added is not None:
        part = part + transform(added)
    if removed is not None:
        part = part - transform(removed)
    return part

''',
    "_thread": '''
def _thread(part, cyl_face, major, pitch, external, length, near, hand):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from bd_warehouse.thread import IsoThread

    cyl = BRepAdaptor_Surface(cyl_face.wrapped).Cylinder()
    ax = cyl.Axis()
    d = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()).normalized()
    p = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    ts = [(Vector(v.X, v.Y, v.Z) - p).dot(d) for v in cyl_face.vertices()]
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

''',
}
_HELPER_ORDER = ["_path", "_linear", "_polar", "_grid", "_edges", "_faces", "_delta", "_apply", "_thread"]
