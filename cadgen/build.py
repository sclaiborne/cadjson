"""Orchestrator: Document -> Builder calls -> outputs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from build123d import Axis, Part, Vector
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
    Mirror,
    PatternFeature,
    Revolve,
    Shell,
    StlOptions,
)
from cadgen.selectors import Selection
from cadgen.sketch import build_sketch


def load_document(path: Path | str) -> Document:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CadgenError(f"{path}: not valid JSON: {exc}") from None
    return parse_document(raw, source=str(path))


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


def build_document(doc: Document) -> BuildResult:
    ctx = Context(doc.params, doc.units)
    builder = Builder()
    timings: dict[str, float] = {}
    for feat in doc.features:
        ctx.feature_id = feat.id
        t0 = time.perf_counter()
        _run_feature(feat, ctx, builder)
        timings[feat.id] = time.perf_counter() - t0
    ctx.feature_id = None
    return BuildResult(doc, builder.solid, builder, timings)


def _run_feature(feat, ctx: Context, b: Builder) -> None:
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
            fid, plane, pts, ctx.length(feat.diameter),
            depth=None if feat.depth is None else ctx.length(feat.depth), through=feat.through,
            counterbore=cb, countersink=cs,
        )
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
        files.append(export.write_3mf(part, out_dir / f"{name}.3mf", tol, ang))

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
