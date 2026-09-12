"""Export a document as an Autodesk Fusion 360 script that rebuilds it with a native timeline.

Strategy: cadgen builds the part with build123d exactly as `cadgen build` does, and while it
does so it records the geometry each Fusion feature needs: sketch curves in world coordinates,
profile areas and centroids, and the bounding-box centre plus size of every edge or face a
selector picked. The generated script replays the features through the Fusion API and finds
the matching edges/faces by that recorded geometry, so the Fusion timeline contains real
sketches, extrudes, fillets, chamfers, shells, holes, mirrors and patterns, all editable.

Parameters become Fusion user parameters (with their expressions) and drive feature values
such as extrude distances and fillet radii. Sketch geometry itself is numeric.
"""

from __future__ import annotations

import json
from pathlib import Path

from build123d import Plane, Vector

from cadgen import __version__
from cadgen.build import _run_feature
from cadgen.builder import Builder
from cadgen.context import Context
from cadgen.errors import CadgenError
from cadgen.expr import UnitsError, names_in, to_fusion
from cadgen.planes import AXIS, resolve_plane
from cadgen.schema import (
    Chamfer,
    Document,
    ExplicitPlane,
    Extrude,
    FacePlane,
    Fillet,
    Hole,
    Mirror,
    OffsetPlane,
    PatternFeature,
    Revolve,
    Shell,
)
from cadgen.selectors import Selection
from cadgen.sketch import build_sketch

UNITLESS_KEYS = {"count", "angle", "start_angle", "sides", "taper", "normal", "dir", "direction", "x_dir", "scale"}

MM = 0.1  # Fusion's API works in centimetres


def _r(v: float) -> float:
    return round(v, 6) + 0.0


def _pt(v: Vector) -> tuple:
    return (_r(v.X * MM), _r(v.Y * MM), _r(v.Z * MM))


def _dir(v: Vector) -> tuple:
    v = v.normalized()
    return (_r(v.X), _r(v.Y), _r(v.Z))


def infer_param_kinds(doc: Document) -> dict[str, str]:
    """'len' or 'none' for every param, from where it is used."""
    kinds: dict[str, str] = {}

    def walk(obj, key: str | None):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v, key)
        elif isinstance(obj, str) and key not in ("id", "type", "of", "of_face", "plane", "base", "name",
                                                   "schema", "description", "units", "op", "mode", "geom",
                                                   "axis", "kind", "sort_by", "parallel_to", "segments"):
            try:
                names = names_in(obj)
            except CadgenError:
                return
            kind = "none" if key in UNITLESS_KEYS else "len"
            for n in names:
                kinds.setdefault(n, kind)

    raw = doc.model_dump(by_alias=True, exclude={"params", "outputs"})
    walk(raw, None)
    # path segments are lengths
    for feat in doc.features:
        sketch = getattr(feat, "sketch", None)
        if sketch is None:
            continue
        for shape in sketch.shapes:
            for seg in getattr(shape, "segments", []) or []:
                body = seg.split(None, 1)[1] if " " in seg else ""
                for piece in body.split(","):
                    try:
                        for n in names_in(piece.strip()):
                            kinds.setdefault(n, "len")
                    except CadgenError:
                        pass
    # propagate through param expressions (a length param's operands are lengths, unless
    # already classified) and default the rest to length
    for _ in range(3):
        for name, expr in doc.params.items():
            if isinstance(expr, str) and name in kinds:
                for n in names_in(expr):
                    kinds.setdefault(n, kinds[name])
    for name in doc.params:
        kinds.setdefault(name, "len")
    return kinds


class FusionExporter:
    def __init__(self, doc: Document):
        self.doc = doc
        self.ctx = Context(doc.params, doc.units)
        self.kinds = infer_param_kinds(doc)
        self.body: list[str] = []
        self.notes: list[str] = []
        self.builder = Builder()

    # --- expression helpers ----------------------------------------------------------------

    def _len(self, dim) -> str:
        """Fusion expression for a length; falls back to the evaluated value with a note."""
        try:
            return to_fusion(dim, self.kinds, "len")
        except (UnitsError, CadgenError) as exc:
            value = self.ctx.length(dim)
            self.notes.append(f"{self.ctx.feature_id}: {dim!r} written as {value:g} mm ({exc})")
            return f"{value:g} mm"

    def _angle(self, dim) -> str:
        try:
            return to_fusion(dim, self.kinds, "none") + " deg"
        except (UnitsError, CadgenError):
            return f"{self.ctx.num(dim):g} deg"

    def _count(self, dim) -> str:
        try:
            return to_fusion(dim, self.kinds, "none")
        except (UnitsError, CadgenError):
            return f"{int(round(self.ctx.num(dim)))}"

    # --- emit ------------------------------------------------------------------------------

    def emit(self, line: str = "") -> None:
        self.body.append(("        " + line) if line else "")

    def export(self) -> str:
        doc = self.doc
        if doc.parts:
            raise CadgenError("assemblies (parts) cannot be exported to Fusion yet; export the individual parts")
        for name, expr in doc.params.items():
            unit = "mm" if self.kinds[name] == "len" else ""
            try:
                text = to_fusion(expr, self.kinds, self.kinds[name])
            except UnitsError:
                value = self.ctx.params[name]
                text = f"{value:g} mm" if unit else f"{value:g}"
                self.notes.append(f"param {name}: expression {expr!r} written as its value ({value:g})")
            self.emit(f"H.param({name!r}, {text!r}, {unit!r})")
        self.emit()
        ctx = self.ctx
        for feat in doc.features:
            ctx.feature_id = feat.id
            self.emit(f"# --- {feat.id} ({feat.type})")
            self._emit_feature(feat)
            _run_feature(feat, ctx, self.builder)
            self.emit()
        ctx.feature_id = None
        return self._render()

    def _selection(self) -> Selection:
        solid = self.builder.require_solid(self.ctx.feature_id, "select geometry")
        return Selection(solid, self.ctx, self.builder.new_faces, self.builder.new_edges)

    def _plane(self, ref) -> Plane:
        return resolve_plane(ref, self.ctx, lambda sel: self._selection().faces(sel))

    def _plane_spec(self, ref, plane: Plane) -> str:
        """Python literal describing a plane for the script: a named plane or explicit geometry."""
        if isinstance(ref, str):
            return repr(ref)
        if isinstance(ref, OffsetPlane) and ref.origin is None and abs(self.ctx.length(ref.offset)) < 1e-9:
            return repr(ref.base)
        return f"({_pt(plane.origin)}, {_dir(plane.z_dir)}, {_dir(plane.x_dir)})"

    def _emit_sketch(self, sketch_model, plane: Plane, var: str = "sk") -> list[tuple]:
        """Emit sketch curves; return profile keys [(area_cm2, (cx, cy, cz))]."""
        sk2d = build_sketch(sketch_model, self.ctx)
        located = plane * sk2d
        self.emit(f"{var} = H.sketch({self._plane_spec(sketch_model.plane, plane)})")
        profiles = []
        for face in located.faces():
            wires = [face.outer_wire()] + list(face.inner_wires())
            for wire in wires:
                for edge in wire.edges():
                    kind = edge.geom_type.name
                    if kind not in ("LINE", "CIRCLE"):
                        raise CadgenError(
                            f"sketch contains {kind.lower()} curves (text?) which the Fusion exporter cannot write yet",
                            self.ctx.feature_id,
                        )
                    if kind == "LINE":
                        self.emit(f"H.line({var}, {_pt(edge.position_at(0))}, {_pt(edge.position_at(1))})")
                    elif kind == "CIRCLE" and edge.is_closed:
                        self.emit(f"H.circle({var}, {_pt(edge.arc_center)}, {_r(edge.radius * MM)})")
                    else:
                        self.emit(
                            f"H.arc({var}, {_pt(edge.position_at(0))}, {_pt(edge.position_at(0.5))}, "
                            f"{_pt(edge.position_at(1))})"
                        )
            centroid = face.center()
            profiles.append((_r(face.area * MM * MM), _pt(centroid)))
        return profiles

    def _edge_keys(self, edges) -> list[tuple]:
        keys = []
        for e in edges:
            bb = e.bounding_box()
            keys.append((_pt(bb.center()), _r(e.length * MM)))
        return keys

    def _face_keys(self, faces) -> list[tuple]:
        keys = []
        for f in faces:
            bb = f.bounding_box()
            keys.append((_pt(bb.center()), _r(f.area * MM * MM)))
        return keys

    def _emit_feature(self, feat) -> None:
        fid = feat.id
        ctx = self.ctx
        if isinstance(feat, Extrude):
            plane = self._plane(feat.sketch.plane)
            profiles = self._emit_sketch(feat.sketch, plane)
            dist = "None" if feat.through else repr(self._len(feat.distance))
            if ctx.num(feat.taper):
                self.notes.append(f"{fid}: taper is not exported")
            self.emit(
                f"F[{fid!r}] = H.extrude(H.profiles(sk, {profiles!r}), sk, {_dir(plane.z_dir)}, "
                f"distance={dist}, through={feat.through}, both={feat.both}, op={feat.op!r})"
            )
        elif isinstance(feat, Revolve):
            plane = self._plane(feat.sketch.plane)
            profiles = self._emit_sketch(feat.sketch, plane)
            if isinstance(feat.axis, str):
                origin, direction = plane.origin, (plane.x_dir if feat.axis == "u" else plane.y_dir)
            else:
                u, v = ctx.vec2(feat.axis.point)
                origin = plane.from_local_coords((u, v, 0))
                direction = plane.x_dir * ctx.num(feat.axis.dir[0]) + plane.y_dir * ctx.num(feat.axis.dir[1])
            p2 = origin + direction.normalized() * 10
            self.emit(
                f"F[{fid!r}] = H.revolve(H.profiles(sk, {profiles!r}), sk, {_pt(origin)}, {_pt(p2)}, "
                f"{self._angle(feat.angle)!r}, op={feat.op!r})"
            )
        elif isinstance(feat, Fillet):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            self.emit(f"F[{fid!r}] = H.fillet(H.edges({keys!r}), {self._len(feat.radius)!r})")
        elif isinstance(feat, Chamfer):
            keys = self._edge_keys(self._selection().edges(feat.edges))
            l2 = "None" if feat.length2 is None else repr(self._len(feat.length2))
            self.emit(f"F[{fid!r}] = H.chamfer(H.edges({keys!r}), {self._len(feat.length)!r}, {l2})")
        elif isinstance(feat, Shell):
            faces = "None" if feat.remove is None else repr(self._face_keys(self._selection().faces(feat.remove)))
            self.emit(f"F[{fid!r}] = H.shell({faces}, {self._len(feat.thickness)!r})")
        elif isinstance(feat, Hole):
            faces = self._selection().faces(feat.face)
            if len(faces) != 1:
                raise CadgenError(f"hole face selector matched {len(faces)} faces, need exactly one", fid)
            plane = self._plane(FacePlane(face=feat.face))
            fkey = self._face_keys(faces)[0]
            pts = [_pt(plane.from_local_coords((*ctx.vec2(p), 0))) for p in feat.at]
            depth = "None" if feat.through else repr(self._len(feat.depth))
            from cadgen.build import hole_diameter

            diameter = self._len(feat.diameter) if feat.diameter is not None else f"{hole_diameter(feat, ctx):g} mm"
            cb = "None"
            cs = "None"
            if feat.counterbore:
                cb = repr((self._len(feat.counterbore.diameter), self._len(feat.counterbore.depth)))
            if feat.countersink:
                cs = repr((self._len(feat.countersink.diameter), self._angle(feat.countersink.angle)))
            self.emit(
                f"F[{fid!r}] = H.hole({fkey!r}, {pts!r}, {diameter!r}, depth={depth}, "
                f"counterbore={cb}, countersink={cs})"
            )
        elif isinstance(feat, Mirror):
            plane = self._plane(feat.plane)
            feats = "None" if feat.features is None else repr(feat.features)
            self.emit(f"F[{fid!r}] = H.mirror({feats}, {self._plane_spec(feat.plane, plane)})")
        elif isinstance(feat, PatternFeature):
            if feat.kind == "linear":
                if isinstance(feat.direction, str):
                    d = repr(feat.direction)
                else:
                    d = repr(_dir(ctx.dir3(feat.direction)))
                self.emit(
                    f"F[{fid!r}] = H.pattern_linear({feat.features!r}, {d}, {self._count(feat.count)!r}, "
                    f"{self._len(feat.spacing)!r})"
                )
            else:
                if isinstance(feat.axis, str):
                    ax = repr(feat.axis)
                else:
                    ax = repr((_pt(ctx.vec3(feat.axis.point)), _dir(ctx.dir3(feat.axis.dir))))
                self.emit(
                    f"F[{fid!r}] = H.pattern_circular({feat.features!r}, {ax}, {self._count(feat.count)!r}, "
                    f"{self._angle(feat.angle)!r})"
                )
        else:
            from cadgen.plugins import registry

            plugin = registry.plugin_for(feat)
            if plugin is not None and plugin.fusion is not None:
                plugin.fusion(feat, self)
                return
            raise CadgenError(
                f"feature type {feat.type!r} cannot be exported to Fusion yet", fid,
                ["supported: extrude, revolve, fillet, chamfer, shell, hole, mirror, pattern"],
            )

    def _render(self) -> str:
        notes = "\n".join(f"#   - {n}" for n in self.notes) or "#   (none)"
        return TEMPLATE.replace("{{NAME}}", self.doc.name).replace("{{VERSION}}", __version__) \
            .replace("{{NOTES}}", notes).replace("{{BODY}}", "\n".join(self.body))


def export_fusion(doc: Document, out_dir: Path) -> list[Path]:
    """Write `<name>_fusion/<name>_fusion.py` plus the manifest Fusion expects next to it."""
    script = FusionExporter(doc).export()
    folder = out_dir / f"{doc.name}_fusion"
    folder.mkdir(parents=True, exist_ok=True)
    py = folder / f"{doc.name}_fusion.py"
    py.write_text(script, encoding="utf-8")
    manifest = folder / f"{doc.name}_fusion.manifest"
    manifest.write_text(json.dumps({
        "autodeskProduct": "Fusion360",
        "type": "script",
        "author": "cadgen",
        "description": f"Rebuilds {doc.name} with a native parametric timeline",
        "supportedOS": "windows|mac",
        "editEnabled": True,
    }, indent=2), encoding="utf-8")
    return [py, manifest]


TEMPLATE = '''"""{{NAME}}: generated by cadgen {{VERSION}}. Run from Fusion 360: Utilities > Add-Ins > Scripts.

Creates a new design and rebuilds the part with a native parametric timeline.
Notes from the exporter:
{{NOTES}}
"""

import math
import traceback

import adsk.core
import adsk.fusion

TOL = 0.002  # cm, for matching edges and faces by geometry


class Helpers:
    def __init__(self, design, root):
        self.design = design
        self.root = root
        self.features = root.features
        self.has_body = False
        self.named_planes = {
            "XY": root.xYConstructionPlane, "XZ": root.xZConstructionPlane, "YZ": root.yZConstructionPlane,
        }
        self.named_axes = {"X": root.xConstructionAxis, "Y": root.yConstructionAxis, "Z": root.zConstructionAxis}

    # --- basics ------------------------------------------------------------------------------

    @staticmethod
    def P(p):
        return adsk.core.Point3D.create(p[0], p[1], p[2])

    @staticmethod
    def V(v):
        return adsk.core.Vector3D.create(v[0], v[1], v[2])

    @staticmethod
    def val(text):
        return adsk.core.ValueInput.createByString(text)

    @staticmethod
    def coll(items):
        c = adsk.core.ObjectCollection.create()
        for it in items:
            c.add(it)
        return c

    def param(self, name, expression, unit):
        self.design.userParameters.add(name, self.val(expression), unit, "")

    def plane_entity(self, spec):
        if isinstance(spec, str):
            return self.named_planes[spec]
        origin, normal, _x = spec
        planes = self.root.constructionPlanes
        inp = planes.createInput()
        inp.setByPlane(adsk.core.Plane.create(self.P(origin), self.V(normal)))
        return planes.add(inp)

    def axis_entity(self, spec):
        if isinstance(spec, str):
            return self.named_axes[spec]
        origin, direction = spec
        axes = self.root.constructionAxes
        inp = axes.createInput()
        inp.setByLine(adsk.core.InfiniteLine3D.create(self.P(origin), self.V(direction)))
        return axes.add(inp)

    # --- sketches ----------------------------------------------------------------------------

    def sketch(self, spec):
        sk = self.root.sketches.add(self.plane_entity(spec))
        sk.name = "cadgen"
        return sk

    def sp(self, sk, p):
        return sk.modelToSketchSpace(self.P(p))

    def line(self, sk, a, b):
        return sk.sketchCurves.sketchLines.addByTwoPoints(self.sp(sk, a), self.sp(sk, b))

    def circle(self, sk, c, r):
        return sk.sketchCurves.sketchCircles.addByCenterRadius(self.sp(sk, c), r)

    def arc(self, sk, a, m, b):
        return sk.sketchCurves.sketchArcs.addByThreePoints(self.sp(sk, a), self.sp(sk, m), self.sp(sk, b))

    def profiles(self, sk, keys):
        """Profiles matching recorded (area, centroid) pairs."""
        found = adsk.core.ObjectCollection.create()
        for area, centroid in keys:
            want = self.P(centroid)
            best, best_d = None, None
            for i in range(sk.profiles.count):
                prof = sk.profiles.item(i)
                props = prof.areaProperties(adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy)
                if abs(props.area - area) > max(1e-4, 1e-3 * area):
                    continue
                c = props.centroid
                d = min(c.distanceTo(want), sk.sketchToModelSpace(c).distanceTo(want))
                if best_d is None or d < best_d:
                    best, best_d = prof, d
            if best is None:
                raise RuntimeError("no sketch profile with area %g near %s" % (area, centroid))
            found.add(best)
        return found

    def sketch_normal(self, sk):
        return sk.xDirection.crossProduct(sk.yDirection)

    # --- topology matching -------------------------------------------------------------------

    def _bb_center(self, ent):
        bb = ent.boundingBox
        return ((bb.minPoint.x + bb.maxPoint.x) / 2, (bb.minPoint.y + bb.maxPoint.y) / 2,
                (bb.minPoint.z + bb.maxPoint.z) / 2)

    def _match(self, ents, keys, size_of, what):
        out = []
        for center, size in keys:
            best, best_d = None, None
            for ent in ents:
                if abs(size_of(ent) - size) > max(TOL, 1e-3 * size):
                    continue
                c = self._bb_center(ent)
                d = math.dist(c, center)
                if best_d is None or d < best_d:
                    best, best_d = ent, d
            if best is None or best_d > 50 * TOL:
                raise RuntimeError("no %s of size %g near %s" % (what, size, center))
            out.append(best)
        return self.coll(out)

    def edges(self, keys):
        ents = [e for b in self.root.bRepBodies for e in b.edges]
        return self._match(ents, keys, lambda e: e.length, "edge")

    def faces(self, keys):
        ents = [f for b in self.root.bRepBodies for f in b.faces]
        return self._match(ents, keys, lambda f: f.area, "face")

    # --- features ----------------------------------------------------------------------------

    def op(self, name):
        ops = adsk.fusion.FeatureOperations
        if name == "add":
            if not self.has_body:
                self.has_body = True
                return ops.NewBodyFeatureOperation
            return ops.JoinFeatureOperation
        return ops.CutFeatureOperation if name == "cut" else ops.IntersectFeatureOperation

    def signed(self, expression, sk, normal):
        """Flip an expression when Fusion's sketch normal opposes the cadgen plane normal."""
        if self.sketch_normal(sk).dotProduct(self.V(normal)) < 0:
            return "-(%s)" % expression
        return expression

    def extrude(self, profiles, sk, normal, distance, through, both, op):
        ext = self.features.extrudeFeatures
        inp = ext.createInput(profiles, self.op(op))
        if through:
            inp.setAllExtent(adsk.fusion.ExtentDirections.SymmetricExtentDirection)
        elif both:
            inp.setSymmetricExtent(self.val(distance), False)
        else:
            inp.setDistanceExtent(False, self.val(self.signed(distance, sk, normal)))
        return ext.add(inp)

    def revolve(self, profiles, sk, a, b, angle, op):
        axis = self.line(sk, a, b)
        axis.isConstruction = True
        rev = self.features.revolveFeatures
        inp = rev.createInput(profiles, axis, self.op(op))
        inp.setAngleExtent(False, self.val(angle))
        return rev.add(inp)

    def fillet(self, edges, radius):
        fil = self.features.filletFeatures
        inp = fil.createInput()
        inp.edgeSetInputs.addConstantRadiusEdgeSet(edges, self.val(radius), False)
        return fil.add(inp)

    def chamfer(self, edges, length, length2=None):
        ch = self.features.chamferFeatures
        inp = ch.createInput2()
        if length2 is None:
            inp.chamferEdgeSets.addEqualDistanceChamferEdgeSet(edges, self.val(length), False)
        else:
            inp.chamferEdgeSets.addTwoDistancesChamferEdgeSet(edges, self.val(length), self.val(length2), False)
        return ch.add(inp)

    def shell(self, face_keys, thickness):
        sh = self.features.shellFeatures
        if face_keys is None:
            ents = self.coll([b for b in self.root.bRepBodies])
        else:
            ents = self.faces(face_keys)
        inp = sh.createInput(ents, False)
        inp.insideThickness = self.val(thickness)
        return sh.add(inp)

    def hole(self, face_key, points, diameter, depth=None, counterbore=None, countersink=None):
        holes = self.features.holeFeatures
        face = self.faces([face_key]).item(0)
        last = None
        for p in points:
            if counterbore is not None:
                inp = holes.createCounterboreInput(self.val(diameter), self.val(counterbore[0]), self.val(counterbore[1]))
            elif countersink is not None:
                inp = holes.createCountersinkInput(self.val(diameter), self.val(countersink[0]), self.val(countersink[1]))
            else:
                inp = holes.createSimpleInput(self.val(diameter))
            inp.setPositionByPoint(face, self.P(p))
            if depth is None:
                inp.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
            else:
                inp.setDistanceExtent(self.val(depth))
            last = holes.add(inp)
        return last

    def mirror(self, feature_ids, plane_spec):
        mir = self.features.mirrorFeatures
        if feature_ids is None:
            ents = self.coll([b for b in self.root.bRepBodies])
        else:
            ents = self.coll([F[i] for i in feature_ids])
        inp = mir.createInput(ents, self.plane_entity(plane_spec))
        return mir.add(inp)

    def pattern_linear(self, feature_ids, direction, count, spacing):
        rp = self.features.rectangularPatternFeatures
        ents = self.coll([F[i] for i in feature_ids])
        axis = self.axis_entity(direction) if isinstance(direction, str) else self.axis_entity(((0, 0, 0), direction))
        inp = rp.createInput(ents, axis, self.val(count), self.val(spacing),
                             adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
        return rp.add(inp)

    def pattern_circular(self, feature_ids, axis, count, angle):
        cp = self.features.circularPatternFeatures
        ents = self.coll([F[i] for i in feature_ids])
        inp = cp.createInput(ents, self.axis_entity(axis))
        inp.quantity = self.val(count)
        inp.totalAngle = self.val(angle)
        inp.isSymmetric = False
        return cp.add(inp)


F = {}


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent
        root.name = {{NAME!r}}
        H = Helpers(design, root)

{{BODY}}
        ui.messageBox("cadgen: built {{NAME}} with %d features" % len(F))
    except Exception:
        if ui:
            ui.messageBox("cadgen script failed:\\n" + traceback.format_exc())
        raise
'''.replace("{{NAME!r}}", '"{{NAME}}"')
