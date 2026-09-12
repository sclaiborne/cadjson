"""Output writers: STEP, STL, 3MF, hidden-line SVG/DXF views, PNG previews."""

from __future__ import annotations

import re
from pathlib import Path

from build123d import ExportDXF, ExportSVG, LineType, Mesher, Part, Vector, export_step, export_stl

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


def write_3mf(part: Part, path: Path, tolerance: float, angular_tolerance: float) -> Path:
    m = Mesher()
    m.add_shape(part, linear_deflection=tolerance, angular_deflection=angular_tolerance)
    m.write(str(path))
    return path


def project(part: Part, view: str):
    """Visible and hidden edge compounds for a named view."""
    direction, up = VIEWS[view]
    c = part.center()
    far = c + Vector(*direction).normalized() * (part.bounding_box().diagonal * 10 + 100)
    return part.project_to_viewport(viewport_origin=far, viewport_up=Vector(*up), look_at=c)


def write_view_svg(part: Part, view: str, path: Path, hidden: bool = True, scale: float = 1.0) -> Path:
    vis, hid = project(part, view)
    ex = ExportSVG(scale=scale, margin=5)
    ex.add_layer("visible", line_weight=0.5)
    ex.add_layer("hidden", line_weight=0.25, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="visible")
    if hidden:
        ex.add_shape(hid, layer="hidden")
    ex.write(str(path))
    return path


def write_view_dxf(part: Part, view: str, path: Path, hidden: bool = True) -> Path:
    vis, hid = project(part, view)
    ex = ExportDXF()
    ex.add_layer("visible", line_weight=0.5)
    ex.add_layer("hidden", line_weight=0.25, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="visible")
    if hidden:
        ex.add_shape(hid, layer="hidden")
    ex.write(str(path))
    return path


def svg_to_png(svg_path: Path, png_path: Path, width: int = 1200) -> Path:
    import resvg_py

    text = svg_path.read_text(encoding="utf-8")
    # build123d writes width/height with mm units, which resvg rejects
    text = re.sub(r'(width|height)="([\d.]+)mm"', r'\1="\2"', text, count=2)
    png_path.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=text, width=width, background="white")))
    return png_path
