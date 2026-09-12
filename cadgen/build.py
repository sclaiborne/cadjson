"""Orchestrator: Document -> Builder calls -> outputs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from build123d import Axis, Compound, Part, Vector
from pydantic import ValidationError

from cadgen import export
from cadgen.builder import Builder
from cadgen.context import Context
from cadgen.errors import CadgenError
from cadgen.planes import AXIS, resolve_plane
from cadgen.schema import (
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
from cadgen.selectors import Selection
from cadgen.sketch import build_sketch, open_path_wire

SOURCE_PATHS: dict[int, Path] = {}  # id(document) -> file it was loaded from (for relative part refs)


def load_document(path: Path | str) -> Document:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CadgenError(f"{path}: not valid JSON: {exc}") from None
    doc = parse_document(raw, source=str(path))
    SOURCE_PATHS[id(doc)] = path.resolve()
    return doc


def source_dir(doc: Document) -> Path:
    src = SOURCE_PATHS.get(id(doc))
    return src.parent if src else Path.cwd()


class _Imports:
    """Builds referenced part files with caching and cycle detection."""

    def __init__(self):
        self.cache: dict[tuple, Part] = {}
        self.stack: list[Path] = []

    def build(self, doc: Document, file: str, overrides: dict, ctx: Context, fid: str | None) -> Part:
        path = (source_dir(doc) / file).resolve()
        if not path.exists():
            raise CadgenError(f"part file not found: {path}", fid)
        if path in self.stack:
            raise CadgenError("part files reference each other in a cycle: " + " -> ".join(str(p) for p in self.stack + [path]), fid)
        resolved = {k: ctx.num(v) for k, v in overrides.items()}
        key = (path, tuple(sorted(resolved.items())))
        if key not in self.cache:
            sub = load_document(path)
            unknown = set(resolved) - set(sub.params)
            if unknown:
                raise CadgenError(f"{path.name} has no params named {sorted(unknown)}", fid,
                                  [f"its params are: {', '.join(sub.params) or '(none)'}"])
            merged = {**sub.params, **resolved}
            sub2 = sub.model_copy(update={"params": merged})
            SOURCE_PATHS[id(sub2)] = path
            self.stack.append(path)
            try:
                self.cache[key] = build_document(sub2, imports=self).part
            finally:
                self.stack.pop()
        return self.cache[key]


def _transform(shape: Part, at: Vector, rotate) -> Part:
    out = shape
    for axis, deg in zip((Axis.X, Axis.Y, Axis.Z), rotate):
        if abs(deg) > 1e-12:
            out = out.rotate(axis, deg)
    if at.length > 1e-12:
        out = out.translate(at)
    return out


def parse_document(raw: dict, source: str = "document") -> Document:
    try:
        return Document.model_validate(raw)
    except ValidationError as exc:
        hints = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            hints.append(f"{loc}: {err['msg']}")
        raise CadgenError(f"{source} does not match schema ({len(hints)} problem(s))", hints=hints) from None


@dataclass
class BuildResult:
    document: Document
    part: Part
    builder: Builder
    timings: dict[str, float] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)

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
        return "\n".join(lines)


def build_document(doc: Document, imports: _Imports | None = None) -> BuildResult:
    ctx = Context(doc.params, doc.units)
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
    if doc.parts:
        # Placed parts stay separate solids; the document's own features model the host body.
        t0 = time.perf_counter()
        placed = []
        for i, pl in enumerate(doc.parts):
            label = pl.name or Path(pl.file).stem
            ctx.feature_id = f"parts[{i}]"
            sub = imports.build(doc, pl.file, pl.params, ctx, ctx.feature_id)
            sub = _transform(sub, ctx.vec3(pl.at), [ctx.num(r) for r in pl.rotate])
            sub.label = label
            placed.append(sub)
        if shape is not None:
            shape.label = doc.name
            placed.append(shape)
        shape = Part(children=placed) if len(placed) > 1 else placed[0]
        timings["parts"] = time.perf_counter() - t0
    ctx.feature_id = None
    return BuildResult(doc, shape, builder, timings)


def _run_feature(feat, ctx: Context, b: Builder, imports: _Imports | None = None) -> None:
    fid = feat.id

    def selection() -> Selection:
        solid = b.require_solid(fid, "select geometry")
        return Selection(solid, ctx, b.new_faces, b.new_edges)

    def faces(sel):
        return selection().faces(sel)

    def plane_of(ref):
        return resolve_plane(ref, ctx, faces)

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
        from cadgen.standards import thread_spec

        try:
            spec = thread_spec(feat.size)
        except CadgenError as exc:
            raise CadgenError(exc.message, fid, exc.hints) from None
        fs = faces(feat.face)
        if len(fs) != 1:
            raise CadgenError(f"thread face selector matched {len(fs)} faces, need exactly one", fid)
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
            raise CadgenError("pattern count must be at least 2", fid)
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
        raise CadgenError(f"unsupported feature type {feat.type!r}", fid)


def _face_plane_ref(face_sel):
    from cadgen.schema import FacePlane

    return FacePlane(face=face_sel)


def hole_diameter(feat: Hole, ctx: Context) -> float:
    """Explicit diameter, or the tap/clearance size for a standard thread (always in mm)."""
    if feat.diameter is not None:
        return ctx.length(feat.diameter)
    from cadgen.standards import thread_spec

    try:
        return thread_spec(feat.standard).hole_diameter(feat.fit)
    except CadgenError as exc:
        raise CadgenError(exc.message, feat.id, exc.hints) from None


_DOC_OF: dict[int, Document] = {}  # id(builder) -> document, so part refs resolve relative paths


def write_outputs(result: BuildResult, out_dir: Path, *, step: bool | None = None, stl: bool | None = None,
                  png: bool | None = None, views: list[str] | None = None, sheet: bool | None = None) -> list[Path]:
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
            except CadgenError as exc:
                raise CadgenError(f"section {label}: {exc.message}", hints=exc.hints) from None
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

    from cadgen.report import write_report

    files.append(write_report(result, out_dir, files))
    result.files = files
    return files
