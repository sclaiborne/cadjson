"""Output writers: STEP, STL, 3MF, hidden-line SVG/DXF views, PNG previews."""

from __future__ import annotations

import re
from pathlib import Path

import contextlib
import io

from build123d import (
    Align,
    Box,
    Color,
    ExportDXF,
    ExportSVG,
    LineType,
    Mesher,
    Part,
    Plane,
    Vector,
    export_step,
    export_stl,
)

from cadjson.errors import CadjsonError

VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    # name: (direction the viewer sits in, up vector)
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, 1, 0)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "iso": ((1, -1, 1), (0, 0, 1)),
}


def write_step(part: Part, path: Path) -> Path:
    export_step(part, str(path))
    return path


def write_stl(part: Part, path: Path, tolerance: float, angular_tolerance: float) -> Path:
    export_stl(part, str(path), tolerance=tolerance, angular_tolerance=angular_tolerance)
    return path


def write_3mf(part: Part, path: Path, tolerance: float, angular_tolerance: float, *,
              name: str | None = None, part_number: str | None = None) -> Path:
    from cadjson import __version__

    m = Mesher()
    m.add_shape(part, linear_deflection=tolerance, angular_deflection=angular_tolerance, part_number=part_number)
    m.add_meta_data("cadjson", "generator", f"cadjson {__version__}", "str", True)
    if name:
        m.add_meta_data("cadjson", "name", name, "str", True)
    if part_number:
        m.add_meta_data("cadjson", "part_number", part_number, "str", True)
    m.write(str(path))
    return path


def project(part: Part, view: str):
    """Visible and hidden edge compounds for a named view."""
    direction, up = VIEWS[view]
    c = part.center()
    far = c + Vector(*direction).normalized() * (part.bounding_box().diagonal * 10 + 100)
    return part.project_to_viewport(viewport_origin=far, viewport_up=Vector(*up), look_at=c)


def line_weight(part: Part) -> float:
    """Line weight in mm that looks the same for a 20 mm spacer and a 300 mm plate."""
    return max(0.12, min(0.5, part.bounding_box().diagonal / 250))


def write_view_svg(part: Part, view: str, path: Path, hidden: bool = True, scale: float = 1.0) -> Path:
    vis, hid = project(part, view)
    lw = line_weight(part)
    ex = ExportSVG(scale=scale, margin=5)
    ex.add_layer("visible", line_weight=lw)
    ex.add_layer("hidden", line_weight=lw / 2, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="visible")
    if hidden:
        ex.add_shape(hid, layer="hidden")
    ex.write(str(path))
    return path


def write_view_dxf(part: Part, view: str, path: Path, hidden: bool = True) -> Path:
    vis, hid = project(part, view)
    lw = line_weight(part)
    ex = ExportDXF()
    ex.add_layer("visible", line_weight=lw)
    ex.add_layer("hidden", line_weight=lw / 2, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="visible")
    if hidden:
        ex.add_shape(hid, layer="hidden")
    ex.write(str(path))
    return path


# --- section views ---------------------------------------------------------------------------


def section_view(part: Part, plane: Plane, flip: bool = False):
    """Cut the part at `plane`, discard the half on the normal side, and look at the cut face.

    Returns (visible_edges, hidden_edges, cut_faces) all in 2D view coordinates (XY plane).
    """
    n = plane.z_dir.normalized() * (-1 if flip else 1)
    up = plane.y_dir.normalized()
    if abs(up.dot(n)) > 0.999:  # degenerate, pick any perpendicular up
        up = Vector(0, 0, 1) if abs(n.Z) < 0.9 else Vector(0, 1, 0)
    up = (up - n * up.dot(n)).normalized()
    view = Plane(origin=plane.origin, x_dir=up.cross(n), z_dir=n)
    size = part.bounding_box().diagonal * 2 + 10
    remove = view * Box(size, size, size, align=(Align.CENTER, Align.CENTER, Align.MIN))
    kept = part - remove
    if kept.volume < 1e-6 or abs(kept.volume - part.volume) < 1e-6:
        raise CadjsonError("section plane does not pass through the part")
    local = view.to_local_coords(kept)
    cut_faces = [
        f.translate((0, 0, -f.center().Z))
        for f in local.faces()
        if f.geom_type.name == "PLANE" and abs(f.center().Z) < 1e-6 and abs(f.normal_at().Z) > 0.999
    ]
    far = local.bounding_box().diagonal * 10 + 100
    vis, hid = local.project_to_viewport(viewport_origin=(0, 0, far), viewport_up=(0, 1, 0), look_at=(0, 0, 0))
    return vis, hid, cut_faces


def write_section_svg(part: Part, plane: Plane, path: Path, *, flip: bool = False, hidden: bool = False,
                      scale: float = 1.0) -> Path:
    vis, hid, faces = section_view(part, plane, flip)
    lw = line_weight(part)
    ex = ExportSVG(scale=scale, margin=5)
    ex.add_layer("section", fill_color=Color(0.82, 0.82, 0.82), line_color=None, line_weight=lw)
    ex.add_layer("visible", line_weight=lw)
    ex.add_layer("hidden", line_weight=lw / 2, line_type=LineType.HIDDEN)
    for f in faces:
        ex.add_shape(f, layer="section")
    ex.add_shape(vis, layer="visible")
    if hidden:
        ex.add_shape(hid, layer="hidden")
    ex.write(str(path))
    return path


# --- annotated drawing sheet (draftwright) ---------------------------------------------------


def write_sheet(part: Part, stem: Path, *, title: str, formats: list[str], dimensions: bool, projection: str,
                page: str | None, scale: float | None, title_block) -> list[Path]:
    """Full drawing sheet: third-angle views, iso, automatic dimensions, title block."""
    try:
        from draftwright import build_drawing
    except ImportError:
        raise CadjsonError(
            "drawing sheets need the optional draftwright package",
            hints=["pip install \"cadjson[sheets]\"  (draftwright is AGPL-3; see README, Licensing)",
                   "or drop \"sheet\": true / --sheet to get views and previews without a sheet"],
        ) from None

    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            drawing = build_drawing(
                part,
                out=str(stem),
                title=title_block.title or title,
                number=title_block.number,
                tolerance=title_block.tolerance,
                drawn_by=title_block.drawn_by,
                material=title_block.material,
                date=title_block.date,
                revision=title_block.revision,
                company=title_block.company,
                projection=projection,
                page=page,
                scale=scale,
                auto_dims=dimensions,
                frame=True,
            )
            written = drawing.export(str(stem), formats=tuple(formats))
    except Exception as exc:
        raise CadjsonError(f"drawing sheet failed: {exc}", hints=[ln for ln in log.getvalue().splitlines()[-5:]]) from None
    if isinstance(written, dict):
        paths = [Path(p) for p in written.values() if p]
    else:
        paths = [Path(p) for p in written if p]
    return [p for p in paths if p.exists()]


def svg_to_png(svg_path: Path, png_path: Path, width: int = 1200) -> Path:
    import resvg_py

    text = svg_path.read_text(encoding="utf-8")
    # build123d writes width/height with mm units, which resvg rejects
    text = re.sub(r'(width|height)="([\d.]+)mm"', r'\1="\2"', text, count=2)
    png_path.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=text, width=width, background="white")))
    return png_path
