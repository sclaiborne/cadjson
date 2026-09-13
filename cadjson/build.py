"""Orchestrator: Document -> Builder calls -> outputs."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from build123d import Axis, Compound, Part, Vector
from pydantic import ValidationError

from cadjson import export
from cadjson.builder import Builder
from cadjson.context import Context
from cadjson.errors import CadjsonError
from cadjson.planes import AXIS, resolve_plane
from cadjson.schema import (
    Chamfer,
    Document,
    DrawingOptions,
    Extrude,
    Fillet,
    Hole,
    Loft,
    Mirror,
    PartRef,
    PatternFeature,
    Revolve,
    Shell,
    StlOptions,
    Sweep,
    Thread,
    ThreeMfOptions,
)
from cadjson.selectors import Selection
from cadjson.sketch import build_sketch, open_path_wire

SOURCE_PATHS: dict[int, Path] = {}  # id(document) -> file it was loaded from (for relative part refs)
EXTENDS_CHAIN: dict[int, list[str]] = {}  # id(document) -> base files it was merged from


def load_document(path: Path | str) -> Document:
    path = Path(path)
    raw, chain = load_raw(path)
    doc = parse_document(raw, source=str(path))
    SOURCE_PATHS[id(doc)] = path.resolve()
    EXTENDS_CHAIN[id(doc)] = chain
    return doc


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise CadjsonError(f"file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CadjsonError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise CadjsonError(f"{path}: the document must be a JSON object")
    return raw


_REL_KEYS = ("file", "font_path")


def _rebase_refs(node, base_dir: Path, child_dir: Path):
    """Rewrite relative file references in a base document so they resolve from the child's folder."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _REL_KEYS and isinstance(v, str) and not Path(v).is_absolute():
                node[k] = os.path.relpath((base_dir / v).resolve(), child_dir.resolve()).replace("\\", "/")
            else:
                _rebase_refs(v, base_dir, child_dir)
    elif isinstance(node, list):
        for v in node:
            _rebase_refs(v, base_dir, child_dir)


def load_raw(path: Path, stack: tuple[Path, ...] = ()) -> tuple[dict, list[str]]:
    """Raw document dict with `extends` resolved (merged), plus the chain of base files used."""
    path = path.resolve()
    if path in stack:
        raise CadjsonError("extends forms a cycle: " + " -> ".join(str(p) for p in stack + (path,)))
    raw = _read_json(path)
    ext = raw.get("extends")
    if ext is None:
        return raw, []
    if not isinstance(ext, str):
        raise CadjsonError(f"{path}: extends must be a file path string")
    base_path = (path.parent / ext).resolve()
    base, chain = load_raw(base_path, stack + (path,))
    _rebase_refs(base, base_path.parent, path.parent)
    merged = merge_documents(base, raw, source=str(path))
    return merged, [str(base_path)] + chain


def merge_documents(base: dict, child: dict, source: str = "document") -> dict:
    """Apply a child document on top of its base (both raw dicts). See docs/extending.md."""
    merged = dict(base)
    for key in ("$schema", "schema", "name", "description", "units", "outputs"):
        if key in child:
            merged[key] = child[key]
    if "units" in base and "units" in child and base["units"] != child["units"]:
        raise CadjsonError(f"{source}: units {child['units']!r} differ from the base's {base['units']!r}")
    merged["params"] = {**base.get("params", {}), **child.get("params", {})}
    merged["parts"] = list(base.get("parts", [])) + list(child.get("parts", []))

    features = [dict(f) for f in base.get("features", [])]
    ids = {f.get("id"): i for i, f in enumerate(features)}
    drop = child.get("drop", [])
    if not isinstance(drop, list):
        raise CadjsonError(f"{source}: drop must be a list of feature ids")
    for fid in drop:
        if fid not in ids:
            raise CadjsonError(f"{source}: drop names {fid!r}, which is not a feature of the base",
                              hints=[f"base features: {', '.join(ids)}"])
    features = [f for f in features if f.get("id") not in drop]
    ids = {f.get("id"): i for i, f in enumerate(features)}
    for f in child.get("features", []):
        fid = f.get("id") if isinstance(f, dict) else None
        if fid in ids:
            features[ids[fid]] = f  # replace in place, keeping the base's ordering
        else:
            features.append(f)
    merged["features"] = features
    merged.pop("extends", None)
    merged.pop("drop", None)
    return merged


def source_dir(doc: Document) -> Path:
    src = SOURCE_PATHS.get(id(doc))
    return src.parent if src else Path.cwd()


class _Imports:
    """Builds referenced part files with caching and cycle detection."""

    def __init__(self):
        self.cache: dict[tuple, BuildResult] = {}
        self.stack: list[Path] = []

    def build(self, doc: Document, file: str, overrides: dict, ctx: Context, fid: str | None) -> Part:
        return self.result(doc, file, overrides, ctx, fid).part

    def result(self, doc: Document, file: str, overrides: dict, ctx: Context, fid: str | None) -> BuildResult:
        path = (source_dir(doc) / file).resolve()
        if not path.exists():
            raise CadjsonError(f"part file not found: {path}", fid)
        if path in self.stack:
            raise CadjsonError("part files reference each other in a cycle: " + " -> ".join(str(p) for p in self.stack + [path]), fid)
        resolved = {k: ctx.num(v) for k, v in overrides.items()}
        key = (path, tuple(sorted(resolved.items())))
        if key not in self.cache:
            sub = load_document(path)
            unknown = set(resolved) - set(sub.params)
            if unknown:
                raise CadjsonError(f"{path.name} has no params named {sorted(unknown)}", fid,
                                  [f"its params are: {', '.join(sub.params) or '(none)'}"])
            merged = {**sub.params, **resolved}
            sub2 = sub.model_copy(update={"params": merged})
            SOURCE_PATHS[id(sub2)] = path
            self.stack.append(path)
            try:
                self.cache[key] = build_document(sub2, imports=self)
            finally:
                self.stack.pop()
        return self.cache[key]


def _transform(shape: Part, at: Vector, rotate) -> Part:
    from cadjson.assembly import Pose

    if at.length < 1e-12 and all(abs(r) < 1e-12 for r in rotate):
        return shape
    return Pose.from_euler((at.X, at.Y, at.Z), rotate).apply(shape)


def parse_document(raw: dict, source: str = "document") -> Document:
    from cadjson.plugins import registry

    model = registry.document_model()
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        hints = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            hints.append(f"{loc}: {err['msg']}")
        if registry.features:
            hints.append("plugin feature types available: " + ", ".join(registry.features))
        if registry.errors:
            hints.append("plugin problems: " + "; ".join(registry.errors))
        raise CadjsonError(f"{source} does not match schema ({len(hints)} problem(s))", hints=hints) from None


@dataclass
class BuildResult:
    document: Document
    part: Part
    builder: Builder
    timings: dict[str, float] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    assembly: dict | None = None  # placed parts, poses and check results (see cadjson.assembly)

    def warnings(self) -> list[str]:
        from cadjson.assembly import check_messages

        return check_messages(self.assembly) if self.assembly else []

    def summary(self) -> str:
        bb = self.part.bounding_box()
        lines = [
            f"{self.document.name}: {len(self.document.features)} features, "
            f"volume {self.part.volume:.2f} mm^3, "
            f"bbox {bb.size.X:.2f} x {bb.size.Y:.2f} x {bb.size.Z:.2f} mm, "
            f"{len(self.part.faces())} faces, {len(self.part.edges())} edges",
        ]
        for fid, t in self.timings.items():
            lines.append(f"  {fid:<24} {t * 1000:7.1f} ms")
        for p in (self.assembly or {}).get("parts", []):
            at = ", ".join(f"{v:g}" for v in p["at"])
            rot = ", ".join(f"{v:g}" for v in p["rotate"])
            lines.append(f"  {p['name']:<24} at [{at}] rotate [{rot}]")
        for msg in self.warnings():
            lines.append(f"  check: {msg}")
        return "\n".join(lines)


def build_document(doc: Document, imports: _Imports | None = None) -> BuildResult:
    ctx = Context(doc.params, doc.units)
    ctx.base_dir = source_dir(doc)
    builder = Builder()
    _DOC_OF[id(builder)] = doc
    imports = imports or _Imports()
    timings: dict[str, float] = {}
    for feat in doc.features:
        ctx.feature_id = feat.id
        t0 = time.perf_counter()
        _run_feature(feat, ctx, builder, imports)
        timings[feat.id] = time.perf_counter() - t0
    shape = builder.solid
    assembly = None
    if doc.parts:
        # Placed parts stay separate solids; the document's own features model the host body.
        from cadjson.assembly import Pose, run_checks, solve_mates

        t0 = time.perf_counter()
        placed: list[tuple[str, Part]] = []
        targets: dict = {}

        def selector(res_part, res_builder):
            return lambda sel: Selection(res_part, ctx, res_builder.new_faces, res_builder.new_edges).faces(sel)

        if shape is not None:
            targets[doc.name] = (selector(shape, builder), Pose())
        report_parts = []
        for i, pl in enumerate(doc.parts):
            label = pl.label()
            ctx.feature_id = f"parts[{i}]"
            res = imports.result(doc, pl.file, pl.params, ctx, ctx.feature_id)
            pose = Pose.from_euler([ctx.length(v) for v in pl.at], [ctx.num(r) for r in pl.rotate])
            select = selector(res.part, res.builder)
            if pl.mates:
                pose = solve_mates(pose, label, pl.mates, select, targets, ctx, ctx.feature_id)
            sub = pose.apply(res.part)
            sub.label = label
            placed.append((label, sub))
            targets[label] = (select, pose)
            report_parts.append({"name": label, "file": pl.file, "params": {k: ctx.num(v) for k, v in pl.params.items()},
                                 **pose.describe(), "volume_mm3": round(sub.volume, 4)})
        if shape is not None:
            shape.label = doc.name
            placed.append((doc.name, shape))
        ctx.feature_id = "checks"
        assembly = {"parts": report_parts, **run_checks(placed, doc.checks, ctx)}
        if doc.checks is not None and doc.checks.interference == "error" and assembly["interference"]:
            hit = assembly["interference"][0]
            raise CadjsonError(f"{hit['between'][0]} and {hit['between'][1]} overlap by {hit['volume_mm3']:g} mm^3",
                               "checks", ["set checks.interference to 'warn' to build anyway"])
        solids = [s for _, s in placed]
        shape = Part(children=solids) if len(solids) > 1 else solids[0]
        timings["parts"] = time.perf_counter() - t0
    ctx.feature_id = None
    return BuildResult(doc, shape, builder, timings, assembly=assembly)


class FeatureAPI:
    """What a feature implementation (built-in or plugin) needs from the build context."""

    def __init__(self, feat, ctx: Context, builder: Builder):
        self.feature = feat
        self.fid = feat.id
        self.ctx = ctx
        self.builder = builder

    # dims
    def length(self, dim) -> float:
        return self.ctx.length(dim)

    def num(self, dim) -> float:
        return self.ctx.num(dim)

    def vec2(self, v):
        return self.ctx.vec2(v)

    def vec3(self, v):
        return self.ctx.vec3(v)

    # geometry
    def selection(self) -> Selection:
        solid = self.builder.require_solid(self.fid, "select geometry")
        return Selection(solid, self.ctx, self.builder.new_faces, self.builder.new_edges)

    def faces(self, sel):
        return self.selection().faces(sel)

    def edges(self, sel):
        return self.selection().edges(sel)

    def plane(self, ref):
        return resolve_plane(ref, self.ctx, self.faces)

    def sketch(self, model):
        """A 2D build123d Sketch in plane-local coordinates from a Sketch model."""
        return build_sketch(model, self.ctx)

    def located_sketch(self, model):
        return self.plane(model.plane) * build_sketch(model, self.ctx)

    # solids
    def extrude(self, sketch, plane, *, distance=None, through=False, both=False, taper=0.0, op="add"):
        return self.builder.extrude(self.fid, sketch, plane, distance=distance, through=through, both=both,
                                    taper=taper, op=op)

    def combine(self, tool, op: str = "add"):
        """Combine a ready-made build123d solid with the body (add / cut / intersect)."""
        return self.builder.place(self.fid, tool, op)

    def error(self, message: str, hints: list[str] | None = None) -> CadjsonError:
        return CadjsonError(message, self.fid, hints)


def _run_feature(feat, ctx: Context, b: Builder, imports: _Imports | None = None) -> None:
    fid = feat.id
    api = FeatureAPI(feat, ctx, b)

    def selection() -> Selection:
        return api.selection()

    def faces(sel):
        return api.faces(sel)

    def plane_of(ref):
        return api.plane(ref)

    if isinstance(feat, Extrude):
        plane = plane_of(feat.sketch.plane)
        sk = build_sketch(feat.sketch, ctx)
        b.extrude(
            fid, sk, plane,
            distance=None if feat.distance is None else ctx.length(feat.distance),
            through=feat.through, both=feat.both, taper=ctx.num(feat.taper), op=feat.op,
        )
    elif isinstance(feat, Revolve):
        plane = plane_of(feat.sketch.plane)
        sk = build_sketch(feat.sketch, ctx)
        if isinstance(feat.axis, str):
            axis = Axis(plane.origin, plane.x_dir if feat.axis == "u" else plane.y_dir)
        else:
            u, v = ctx.vec2(feat.axis.point)
            du, dv = ctx.num(feat.axis.dir[0]), ctx.num(feat.axis.dir[1])
            axis = Axis(plane.from_local_coords((u, v, 0)), plane.x_dir * du + plane.y_dir * dv)
        b.revolve(fid, sk, plane, axis, ctx.num(feat.angle), feat.op)
    elif isinstance(feat, Fillet):
        edges = selection().edges(feat.edges)
        b.fillet(fid, edges, ctx.length(feat.radius))
    elif isinstance(feat, Chamfer):
        edges = selection().edges(feat.edges)
        b.chamfer(fid, edges, ctx.length(feat.length), None if feat.length2 is None else ctx.length(feat.length2))
    elif isinstance(feat, Shell):
        openings = faces(feat.remove) if feat.remove is not None else None
        b.shell(fid, ctx.length(feat.thickness), openings)
    elif isinstance(feat, Hole):
        plane = plane_of(_face_plane_ref(feat.face))
        pts = [ctx.vec2(p) for p in feat.at]
        cb = (ctx.length(feat.counterbore.diameter), ctx.length(feat.counterbore.depth)) if feat.counterbore else None
        cs = (ctx.length(feat.countersink.diameter), ctx.num(feat.countersink.angle)) if feat.countersink else None
        b.hole(
            fid, plane, pts, hole_diameter(feat, ctx),
            depth=None if feat.depth is None else ctx.length(feat.depth), through=feat.through,
            counterbore=cb, countersink=cs,
        )
    elif isinstance(feat, Thread):
        from cadjson.standards import thread_spec

        try:
            spec = thread_spec(feat.size)
        except CadjsonError as exc:
            raise CadjsonError(exc.message, fid, exc.hints) from None
        fs = faces(feat.face)
        if len(fs) != 1:
            raise CadjsonError(f"thread face selector matched {len(fs)} faces, need exactly one", fid)
        b.thread(
            fid, fs[0], spec, feat.kind == "external",
            None if feat.length is None else ctx.length(feat.length),
            None if feat.near is None else ctx.vec3(feat.near), feat.hand,
        )
    elif isinstance(feat, Loft):
        sections = [plane_of(s.plane) * build_sketch(s, ctx) for s in feat.sections]
        b.loft(fid, sections, feat.ruled, feat.op)
    elif isinstance(feat, Sweep):
        profile = plane_of(feat.profile.plane) * build_sketch(feat.profile, ctx)
        path = plane_of(feat.path.plane) * open_path_wire(feat.path, ctx)
        b.sweep(fid, profile, path, feat.op)
    elif isinstance(feat, PartRef):
        imports = imports or _Imports()
        doc = _DOC_OF.get(id(b))
        shape = imports.build(doc, feat.file, feat.params, ctx, fid)
        shape = _transform(shape, ctx.vec3(feat.at), [ctx.num(r) for r in feat.rotate])
        b.place(fid, shape, feat.op)
    elif isinstance(feat, Mirror):
        b.mirror(fid, plane_of(feat.plane), feat.features)
    elif isinstance(feat, PatternFeature):
        n = int(round(ctx.num(feat.count)))
        if n < 2:
            raise CadjsonError("pattern count must be at least 2", fid)
        transforms = []
        if feat.kind == "linear":
            d = AXIS[feat.direction] if isinstance(feat.direction, str) else ctx.dir3(feat.direction)
            s = ctx.length(feat.spacing)
            for i in range(1, n):
                transforms.append(lambda shape, v=d * (s * i): shape.translate(v))
        else:
            if isinstance(feat.axis, str):
                axis = Axis((0, 0, 0), AXIS[feat.axis])
            else:
                axis = Axis(ctx.vec3(feat.axis.point), ctx.dir3(feat.axis.dir))
            sweep = ctx.num(feat.angle)
            step = sweep / n if abs(sweep - 360) < 1e-9 else sweep / (n - 1)
            for i in range(1, n):
                transforms.append(lambda shape, a=step * i, ax=axis: shape.rotate(ax, a))
        b.pattern(fid, feat.features, transforms)
    else:
        from cadjson.plugins import registry

        plugin = registry.plugin_for(feat)
        if plugin is None:
            raise CadjsonError(f"unsupported feature type {feat.type!r}", fid)
        try:
            plugin.build(feat, api)
        except CadjsonError:
            raise
        except Exception as exc:
            raise CadjsonError(f"plugin feature {feat.type!r} failed: {type(exc).__name__}: {exc}", fid) from None
        if fid not in b.records:
            raise CadjsonError(f"plugin feature {feat.type!r} did not produce geometry (call api.extrude or api.combine)", fid)


def _face_plane_ref(face_sel):
    from cadjson.schema import FacePlane

    return FacePlane(face=face_sel)


def hole_diameter(feat: Hole, ctx: Context) -> float:
    """Explicit diameter, or the tap/clearance size for a standard thread (always in mm)."""
    if feat.diameter is not None:
        return ctx.length(feat.diameter)
    from cadjson.standards import thread_spec

    try:
        return thread_spec(feat.standard).hole_diameter(feat.fit)
    except CadjsonError as exc:
        raise CadjsonError(exc.message, feat.id, exc.hints) from None


_DOC_OF: dict[int, Document] = {}  # id(builder) -> document, so part refs resolve relative paths


def write_outputs(result: BuildResult, out_dir: Path, *, step: bool | None = None, stl: bool | None = None,
                  png: bool | None = None, views: list[str] | None = None, sheet: bool | None = None,
                  viewer: bool | None = None) -> list[Path]:
    """Write the outputs requested by the document, with optional CLI overrides."""
    doc, part = result.document, result.part
    out = doc.outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    name = doc.name
    files: list[Path] = []
    ctx = Context(doc.params, doc.units)

    def faces(sel):
        return Selection(part, ctx, result.builder.new_faces, result.builder.new_edges).faces(sel)

    if step if step is not None else out.step:
        files.append(export.write_step(part, out_dir / f"{name}.step"))

    stl_opts = out.stl if isinstance(out.stl, StlOptions) else StlOptions()
    tol, ang = ctx.length(stl_opts.tolerance), ctx.num(stl_opts.angular_tolerance)
    if stl if stl is not None else bool(out.stl):
        files.append(export.write_stl(part, out_dir / f"{name}.stl", tol, ang))
    if out.three_mf:
        mf = out.three_mf if isinstance(out.three_mf, ThreeMfOptions) else ThreeMfOptions()
        files.append(export.write_3mf(
            part, out_dir / f"{name}.3mf", ctx.length(mf.tolerance), ctx.num(mf.angular_tolerance),
            name=mf.name or name, part_number=mf.part_number,
        ))

    drawing = out.drawing if isinstance(out.drawing, DrawingOptions) else (DrawingOptions() if out.drawing else None)
    view_names = views if views is not None else (drawing.views if drawing else [])
    if view_names and drawing is None:
        drawing = DrawingOptions()
    if drawing is not None:
        scale = 1.0 if drawing.scale == "auto" else ctx.num(drawing.scale)
        for view in view_names:
            if "svg" in drawing.format or "pdf" in drawing.format:
                files.append(export.write_view_svg(part, view, out_dir / f"{name}_{view}.svg", drawing.hidden_lines, scale))
            if "dxf" in drawing.format:
                files.append(export.write_view_dxf(part, view, out_dir / f"{name}_{view}.dxf", drawing.hidden_lines))
        for i, sec in enumerate(drawing.sections):
            label = sec.name or chr(ord("A") + i)
            try:
                plane = resolve_plane(sec.plane, ctx, faces)
                svg = export.write_section_svg(
                    part, plane, out_dir / f"{name}_section_{label}.svg",
                    flip=sec.flip, hidden=sec.hidden_lines, scale=scale,
                )
            except CadjsonError as exc:
                raise CadjsonError(f"section {label}: {exc.message}", hints=exc.hints) from None
            files.append(svg)
            if png if png is not None else out.png:
                files.append(export.svg_to_png(svg, svg.with_suffix(".png")))
        if sheet if sheet is not None else drawing.sheet:
            files.extend(export.write_sheet(
                part, out_dir / f"{name}_drawing",
                title=name, formats=drawing.format, dimensions=drawing.dimensions,
                projection=drawing.projection, page=drawing.page,
                scale=None if drawing.scale == "auto" else scale, title_block=drawing.title_block,
            ))

    if png if png is not None else out.png:
        preview_views = view_names or ["iso"]
        if "iso" not in preview_views:
            preview_views = list(preview_views) + ["iso"]
        for view in preview_views:
            svg = out_dir / f"{name}_{view}.svg"
            if not svg.exists():
                export.write_view_svg(part, view, svg, True, 1.0)
            files.append(export.svg_to_png(svg, out_dir / f"{name}_{view}.png"))

    if viewer if viewer is not None else out.viewer:
        from cadjson.viewer import write_viewer

        pieces = None
        if result.assembly:
            pieces = [(s.label, s) for s in part.children]
        files.append(write_viewer(part, out_dir / f"{name}.html", tol, ang, name=name, pieces=pieces))

    from cadjson.report import write_report

    files.append(write_report(result, out_dir, files))
    result.files = files
    return files
