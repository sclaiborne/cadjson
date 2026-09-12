"""Build a 2D build123d Sketch (in plane-local coordinates) from a Sketch model."""

from __future__ import annotations

import math
import re

from build123d import (
    Align,
    Circle,
    FontStyle,
    Line,
    Location,
    Plane,
    Polygon,
    RadiusArc,
    Rectangle,
    RegularPolygon,
    Sketch as B3dSketch,
    SlotOverall,
    Text,
    Vector,
    Wire,
    make_face,
)

from cadjson.context import Context
from cadjson.errors import CadjsonError
from cadjson.schema import (
    CircleShape,
    GridPattern,
    LinearPattern,
    OpenPath,
    PathShape,
    PointsShape,
    PolarPattern,
    PolygonShape,
    RectShape,
    Sketch,
    SlotShape,
    TextShape,
)

_SEG = re.compile(r"^\s*(right|left|up|down|line|arc|to|close)\b\s*(.*)$", re.IGNORECASE)


def _rect(shape: RectShape, ctx: Context) -> B3dSketch:
    w, h = ctx.length(shape.w), ctx.length(shape.h)
    angle = ctx.num(shape.angle)
    if shape.corner is not None:
        u, v = ctx.vec2(shape.corner)
        cx, cy = u + w / 2, v + h / 2
    elif shape.top_left is not None:
        u, v = ctx.vec2(shape.top_left)
        cx, cy = u + w / 2, v - h / 2
    elif shape.top_right is not None:
        u, v = ctx.vec2(shape.top_right)
        cx, cy = u - w / 2, v - h / 2
    elif shape.bottom_right is not None:
        u, v = ctx.vec2(shape.bottom_right)
        cx, cy = u - w / 2, v + h / 2
    else:
        cx, cy = ctx.vec2(shape.center) if shape.center is not None else (0.0, 0.0)
    radius = ctx.length(shape.radius)
    if radius > 0:
        from build123d import RectangleRounded

        if radius * 2 >= min(w, h) - 1e-9:
            raise CadjsonError(f"rect corner radius {radius:g} is too large for a {w:g} x {h:g} rectangle", ctx.feature_id)
        return Location((cx, cy, 0)) * RectangleRounded(w, h, radius, rotation=angle)
    return Location((cx, cy, 0)) * Rectangle(w, h, rotation=angle)


def _circle(shape: CircleShape, ctx: Context) -> B3dSketch:
    r = ctx.length(shape.r) if shape.r is not None else ctx.length(shape.d) / 2
    cx, cy = ctx.vec2(shape.center)
    return Location((cx, cy, 0)) * Circle(r)


def _slot(shape: SlotShape, ctx: Context) -> B3dSketch:
    cx, cy = ctx.vec2(shape.center)
    return Location((cx, cy, 0)) * SlotOverall(ctx.length(shape.length), ctx.length(shape.width), rotation=ctx.num(shape.angle))


def _polygon(shape: PolygonShape, ctx: Context) -> B3dSketch:
    if shape.d is not None:
        radius = ctx.length(shape.d) / 2
    else:
        radius = ctx.length(shape.flat) / 2 / math.cos(math.pi / shape.sides)
    cx, cy = ctx.vec2(shape.center)
    return Location((cx, cy, 0)) * RegularPolygon(radius, shape.sides, rotation=ctx.num(shape.angle))


def _points(shape: PointsShape, ctx: Context) -> B3dSketch:
    pts = [ctx.vec2(p) for p in shape.points]
    return Polygon(*pts, align=None)


def _text(shape: TextShape, ctx: Context) -> B3dSketch:
    cx, cy = ctx.vec2(shape.center)
    style = FontStyle.BOLD if shape.bold else FontStyle.REGULAR
    font_path = None
    if shape.font_path:
        from pathlib import Path

        font_path = Path(shape.font_path)
        if not font_path.is_absolute():
            font_path = (ctx.base_dir or Path.cwd()) / font_path
        if not font_path.exists():
            raise CadjsonError(f"font file not found: {font_path}", ctx.feature_id)
        font_path = str(font_path)
    txt = Text(shape.text, ctx.length(shape.size), font=shape.font, font_path=font_path, font_style=style,
               align=(Align.CENTER, Align.CENTER))
    return Location((cx, cy, 0), ctx.num(shape.angle)) * txt


def path_edges(start_pt, segments: list[str], ctx: Context, allow_open: bool = False):
    """Parse path segments into 2D edges. Returns (edges, closed)."""
    start = Vector(*ctx.vec2(start_pt))
    cur = start
    edges = []
    closed = False
    for i, seg in enumerate(segments):
        m = _SEG.match(seg)
        if not m:
            raise CadjsonError(
                f"path segment {i} {seg!r}: unknown form",
                ctx.feature_id,
                ['use "right L", "up h", "line du, dv", "arc du, dv, r", "to u, v", or "close"'],
            )
        word, rest = m.group(1).lower(), m.group(2).strip()
        args = [a.strip() for a in rest.split(",")] if rest else []

        def need(n: int) -> list[float]:
            if len(args) != n:
                raise CadjsonError(f"path segment {i} {seg!r}: {word} needs {n} value(s), got {len(args)}", ctx.feature_id)
            return [ctx.length(a) for a in args]

        if word == "close":
            if (cur - start).length > 1e-9:
                edges.append(Line(cur, start))
            closed = True
            if i != len(segments) - 1:
                raise CadjsonError(f"path segment {i}: close must be the last segment", ctx.feature_id)
            break
        if word in ("right", "left", "up", "down"):
            (d,) = need(1)
            delta = {"right": (d, 0), "left": (-d, 0), "up": (0, d), "down": (0, -d)}[word]
            nxt = cur + Vector(*delta)
            edges.append(Line(cur, nxt))
        elif word == "line":
            du, dv = need(2)
            nxt = cur + Vector(du, dv)
            edges.append(Line(cur, nxt))
        elif word == "to":
            u, v = need(2)
            nxt = Vector(u, v)
            edges.append(Line(cur, nxt))
        elif word == "arc":
            du, dv, r = need(3)
            nxt = cur + Vector(du, dv)
            chord = (nxt - cur).length
            if abs(r) * 2 < chord - 1e-9:
                raise CadjsonError(
                    f"path segment {i} {seg!r}: radius {abs(r):g} is smaller than half the chord {chord / 2:g}",
                    ctx.feature_id,
                )
            edges.append(RadiusArc(cur, nxt, r))
        cur = nxt
    if not closed and not allow_open:
        raise CadjsonError('path must end with "close"', ctx.feature_id)
    if closed and allow_open:
        raise CadjsonError("an open path (sweep) must not end with close", ctx.feature_id)
    return edges, closed


def _path(shape: PathShape, ctx: Context) -> B3dSketch:
    edges, _ = path_edges(shape.start, shape.segments, ctx)
    return make_face(edges)


def open_path_wire(path: OpenPath, ctx: Context) -> Wire:
    """A 2D open path as a Wire in plane-local coordinates."""
    edges, _ = path_edges(path.start, path.segments, ctx, allow_open=True)
    return Wire(edges)


_BUILDERS = {
    RectShape: _rect,
    CircleShape: _circle,
    SlotShape: _slot,
    PolygonShape: _polygon,
    PointsShape: _points,
    PathShape: _path,
    TextShape: _text,
}


def _pattern_locations(pattern, ctx: Context) -> list[Location]:
    if pattern is None:
        return [Location()]
    if isinstance(pattern, LinearPattern):
        n = int(round(ctx.num(pattern.count)))
        s = ctx.length(pattern.spacing)
        if isinstance(pattern.direction, str):
            d = Vector(1, 0, 0) if pattern.direction == "u" else Vector(0, 1, 0)
        else:
            d = Vector(ctx.num(pattern.direction[0]), ctx.num(pattern.direction[1]), 0).normalized()
        offset = -(n - 1) / 2 * s if pattern.centered else 0.0
        return [Location(d * (offset + i * s)) for i in range(n)]
    if isinstance(pattern, PolarPattern):
        n = int(round(ctx.num(pattern.count)))
        r = ctx.length(pattern.radius)
        cx, cy = ctx.vec2(pattern.center)
        a0, sweep = ctx.num(pattern.start_angle), ctx.num(pattern.angle)
        step = sweep / n if abs(sweep - 360) < 1e-9 else (sweep / (n - 1) if n > 1 else 0)
        locs = []
        for i in range(n):
            a = a0 + i * step
            x = cx + r * math.cos(math.radians(a))
            y = cy + r * math.sin(math.radians(a))
            locs.append(Location((x, y, 0), a if pattern.rotate else 0))
        return locs
    if isinstance(pattern, GridPattern):
        nu, nv = pattern.count
        su, sv = ctx.length(pattern.spacing[0]), ctx.length(pattern.spacing[1])
        ou = -(nu - 1) / 2 * su if pattern.centered else 0.0
        ov = -(nv - 1) / 2 * sv if pattern.centered else 0.0
        return [Location((ou + i * su, ov + j * sv, 0)) for i in range(nu) for j in range(nv)]
    raise CadjsonError(f"unknown pattern {pattern!r}", ctx.feature_id)


def build_sketch(model: Sketch, ctx: Context) -> B3dSketch:
    """Union/subtract the shapes into one 2D profile in plane-local coordinates."""
    result: B3dSketch | None = None
    for idx, shape in enumerate(model.shapes):
        piece = _BUILDERS[type(shape)](shape, ctx)
        instances = [loc * piece for loc in _pattern_locations(shape.pattern, ctx)]
        merged = instances[0]
        for inst in instances[1:]:
            merged = merged + inst
        if shape.mode == "add":
            result = merged if result is None else result + merged
        else:
            if result is None:
                raise CadjsonError(f"sketch shape {idx} subtracts but nothing was added before it", ctx.feature_id)
            result = result - merged
    if result is None or result.area < 1e-9:
        raise CadjsonError("sketch has no area", ctx.feature_id)
    return result


def locate(sketch: B3dSketch, plane: Plane) -> B3dSketch:
    return plane * sketch
