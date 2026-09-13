"""Face and edge selection over a build123d solid (docs/schema-v0.md section 7)."""

from __future__ import annotations

from build123d import Axis, Edge, Face, Shape, Vector

from cadjson.context import Context
from cadjson.errors import CadjsonError
from cadjson.schema import EdgeSelector, FaceSel, FaceSelector, Range

AXIS = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}

SHORTCUTS = {
    "top": FaceSelector(normal="+Z", nth=-1),
    "bottom": FaceSelector(normal="-Z", nth=-1),
    "right": FaceSelector(normal="+X", nth=-1),
    "left": FaceSelector(normal="-X", nth=-1),
    "back": FaceSelector(normal="+Y", nth=-1),
    "front": FaceSelector(normal="-Y", nth=-1),
}

_R = 4  # rounding for topology keys (mm digits)


def _r(v: Vector) -> tuple:
    return (round(v.X, _R) + 0.0, round(v.Y, _R) + 0.0, round(v.Z, _R) + 0.0)


def _sign_normalized(d: Vector) -> Vector:
    """Flip a direction so its first non-zero component is positive (lines have no orientation)."""
    for comp in (d.X, d.Y, d.Z):
        if abs(comp) > 1e-9:
            return d if comp > 0 else d * -1
    return d


def face_key(face: Face) -> tuple:
    """Exact identity of a face as it is now (centre, area, type)."""
    c = face.center()
    return (*_r(c), round(face.area, _R), face.geom_type.name)


def edge_key(edge: Edge) -> tuple:
    """Exact identity of an edge as it is now (centre, length, type)."""
    c = edge.center()
    return (*_r(c), round(edge.length, _R), edge.geom_type.name)


def face_sig(face: Face) -> tuple:
    """Signature of the surface a face lies on. Survives later trimming by cuts, fillets, chamfers,
    so a feature's faces can still be found after other features touched them."""
    kind = face.geom_type.name
    if kind == "PLANE":
        n = face.normal_at()
        return ("PLANE", *_r(n), round(face.center().dot(n), _R) + 0.0)
    if kind == "CYLINDER":
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        ax = cyl.Axis()
        d = _sign_normalized(Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()))
        p = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
        foot = p - d * p.dot(d)
        return ("CYLINDER", *_r(d), *_r(foot), round(cyl.Radius(), _R))
    return face_key(face)


def edge_sig(edge: Edge) -> tuple:
    """Signature of the curve an edge lies on (line or circle), stable under trimming."""
    kind = edge.geom_type.name
    c = edge.center()
    if kind == "LINE":
        d = _sign_normalized(edge.tangent_at(0))
        foot = c - d * c.dot(d)
        return ("LINE", *_r(d), *_r(foot))
    if kind == "CIRCLE":
        return ("CIRCLE", *_r(edge.arc_center), round(edge.radius, _R))
    return edge_key(edge)


def face_radius(face: Face) -> float | None:
    """Radius of a cylindrical face, reference radius of a conical one, else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    geom = face.geom_type.name
    if geom == "CYLINDER":
        return BRepAdaptor_Surface(face.wrapped).Cylinder().Radius()
    if geom == "CONE":
        return BRepAdaptor_Surface(face.wrapped).Cone().RefRadius()
    return None


def _axis_vec(name: str) -> Vector:
    sign = -1 if name[0] == "-" else 1
    return AXIS[name[-1]] * sign


def _in_range(value: float, rng: Range | None, ctx: Context) -> bool:
    if rng is None:
        return True
    if rng.min is not None and value < ctx.length(rng.min) - 1e-9:
        return False
    if rng.max is not None and value > ctx.length(rng.max) + 1e-9:
        return False
    return True


def _describe_face(f: Face) -> str:
    c, n = f.center(), (f.normal_at() if f.geom_type.name == "PLANE" else None)
    ns = f"normal ({n.X:+.2f},{n.Y:+.2f},{n.Z:+.2f})" if n else f.geom_type.name.lower()
    return f"{ns} centre ({c.X:.2f},{c.Y:.2f},{c.Z:.2f}) area {f.area:.2f}"


def _describe_edge(e: Edge) -> str:
    c = e.center()
    extra = ""
    if e.geom_type.name == "LINE":
        t = e.tangent_at(0)
        extra = f" dir ({t.X:+.2f},{t.Y:+.2f},{t.Z:+.2f})"
    elif e.geom_type.name == "CIRCLE":
        extra = f" radius {e.radius:.2f}"
    return f"{e.geom_type.name.lower()} centre ({c.X:.2f},{c.Y:.2f},{c.Z:.2f}) length {e.length:.2f}{extra}"


class Selection:
    """Selection helpers bound to one solid plus the per-feature topology records."""

    def __init__(self, solid: Shape, ctx: Context, new_faces: dict[str, set], new_edges: dict[str, set]):
        self.solid = solid
        self.ctx = ctx
        self.new_faces = new_faces
        self.new_edges = new_edges
        self._adjacency: dict[tuple, list[Face]] | None = None

    # --- faces ---------------------------------------------------------------------------

    def faces(self, sel: FaceSel) -> list[Face]:
        if isinstance(sel, str):
            sel = SHORTCUTS[sel]
        ctx = self.ctx
        cands = list(self.solid.faces())
        steps: list[str] = []
        if sel.of is not None:
            keys = self.new_faces.get(sel.of, set())
            cands = [f for f in cands if face_sig(f) in keys]
            steps.append(f"of={sel.of}")
        if sel.normal is not None:
            d = _axis_vec(sel.normal)
            cands = [f for f in cands if f.geom_type.name == "PLANE" and f.normal_at().dot(d) > 0.999]
            steps.append(f"normal={sel.normal}")
        if sel.geom is not None:
            cands = [f for f in cands if f.geom_type.name == sel.geom.upper()]
            steps.append(f"geom={sel.geom}")
        if sel.area is not None:
            cands = [f for f in cands if _in_range(f.area, sel.area, ctx)]
            steps.append("area range")
        if sel.radius is not None:
            cands = [f for f in cands if (r := face_radius(f)) is not None and _in_range(r, sel.radius, ctx)]
            steps.append("radius range")
        if sel.near is not None:
            p = ctx.vec3(sel.near)
            cands = sorted(cands, key=lambda f: (f.center() - p).length)[:1]
            steps.append("near")
        if sel.nth is not None:
            axis = AXIS[sel.sort_by] if sel.sort_by else (_axis_vec(sel.normal) if sel.normal else AXIS["Z"])
            cands = sorted(cands, key=lambda f: f.center().dot(axis))
            try:
                cands = [cands[sel.nth]]
            except IndexError:
                raise CadjsonError(
                    f"face selector nth={sel.nth} but only {len(cands)} face(s) matched {', '.join(steps)}",
                    ctx.feature_id,
                    [f"candidate: {_describe_face(f)}" for f in cands[:20]],
                ) from None
        if not cands:
            raise CadjsonError(
                f"face selector matched nothing ({', '.join(steps) or 'no filters'})",
                ctx.feature_id,
                self._face_hints(sel),
            )
        return cands

    def _face_hints(self, sel: FaceSelector) -> list[str]:
        pool = list(self.solid.faces())
        if sel.of is not None and sel.of in self.new_faces:
            pool = [f for f in pool if face_sig(f) in self.new_faces[sel.of]] or pool
        hints = [f"available: {_describe_face(f)}" for f in pool[:20]]
        if len(pool) > 20:
            hints.append(f"... and {len(pool) - 20} more")
        return hints

    # --- edges ---------------------------------------------------------------------------

    def edges(self, sel: EdgeSelector) -> list[Edge]:
        ctx = self.ctx
        cands = list(self.solid.edges())
        steps: list[str] = []
        if sel.of is not None:
            keys = self.new_edges.get(sel.of, set())
            cands = [e for e in cands if edge_sig(e) in keys]
            steps.append(f"of={sel.of}")
        if sel.of_face is not None:
            keys = {edge_key(e) for f in self.faces(sel.of_face) for e in f.edges()}
            cands = [e for e in cands if edge_key(e) in keys]
            steps.append("of_face")
        if sel.parallel_to is not None:
            d = AXIS[sel.parallel_to]
            cands = [e for e in cands if e.geom_type.name == "LINE" and abs(e.tangent_at(0).dot(d)) > 0.999]
            steps.append(f"parallel_to={sel.parallel_to}")
        if sel.geom is not None:
            want = {"line": {"LINE"}, "circle": {"CIRCLE"}, "arc": {"CIRCLE"}, "ellipse": {"ELLIPSE"}, "spline": {"BSPLINE", "BEZIER"}}[sel.geom]
            cands = [e for e in cands if e.geom_type.name in want]
            if sel.geom == "circle":
                cands = [e for e in cands if e.is_closed]
            elif sel.geom == "arc":
                cands = [e for e in cands if not e.is_closed]
            steps.append(f"geom={sel.geom}")
        if sel.radius is not None:
            cands = [e for e in cands if e.geom_type.name == "CIRCLE" and _in_range(e.radius, sel.radius, ctx)]
            steps.append("radius range")
        if sel.length is not None:
            cands = [e for e in cands if _in_range(e.length, sel.length, ctx)]
            steps.append("length range")
        if sel.convex is not None:
            cands = [e for e in cands if self.is_convex(e) is sel.convex]
            steps.append(f"convex={sel.convex}")
        if sel.near is not None:
            p = ctx.vec3(sel.near)
            cands = sorted(cands, key=lambda e: (e.center() - p).length)[:1]
            steps.append("near")
        if sel.nth is not None:
            axis = AXIS[sel.sort_by or "Z"]
            cands = sorted(cands, key=lambda e: e.center().dot(axis))
            try:
                cands = [cands[sel.nth]]
            except IndexError:
                raise CadjsonError(
                    f"edge selector nth={sel.nth} but only {len(cands)} edge(s) matched {', '.join(steps)}",
                    ctx.feature_id,
                    [f"candidate: {_describe_edge(e)}" for e in cands[:20]],
                ) from None
        if not cands:
            pool = list(self.solid.edges())
            hints = [f"available: {_describe_edge(e)}" for e in pool[:20]]
            if len(pool) > 20:
                hints.append(f"... and {len(pool) - 20} more")
            raise CadjsonError(f"edge selector matched nothing ({', '.join(steps) or 'no filters'})", ctx.feature_id, hints)
        return cands

    def is_convex(self, edge: Edge) -> bool | None:
        """True for an outer corner, False for an inner corner, None if flat or ambiguous."""
        if self._adjacency is None:
            adj: dict[tuple, list[Face]] = {}
            for f in self.solid.faces():
                for e in f.edges():
                    adj.setdefault(edge_key(e), []).append(f)
            self._adjacency = adj
        faces = self._adjacency.get(edge_key(edge), [])
        if len(faces) != 2:
            return None
        m = edge.center()
        n1 = faces[0].normal_at(m)
        n2 = faces[1].normal_at(m)
        d = n1 - n2
        if d.length < 1e-6:
            return None
        d = d.normalized()
        eps = max(self.solid.bounding_box().diagonal * 1e-4, 1e-4)
        inside = (self.solid.is_inside(m + d * eps), self.solid.is_inside(m - d * eps))
        if inside == (True, True):
            return False
        if inside == (False, False):
            return True
        return None
