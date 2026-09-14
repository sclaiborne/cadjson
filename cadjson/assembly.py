"""Assemblies: placing parts by mates, and checking that the placed parts fit.

A placement starts from its `at` / `rotate` pose and each mate then fixes some of the six
degrees of freedom: `against` and `flush` fix one rotation direction and one translation,
`coaxial` fixes a direction and two translations, `parallel` fixes a direction only. What a
mate leaves free keeps the value it had, so an assembly can mix typed coordinates and mates.
Mates apply in order; a mate that cannot be satisfied without undoing an earlier one is an
error that names both."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from build123d import Face, Plane, Shape, Vector

from cadjson.context import Context
from cadjson.errors import CadjsonError

_TOL = 1e-6
_DEG = 1e-4  # cosine tolerance for "already aligned"

DATUM_PLANES = {"XY": (0, 0, 1), "XZ": (0, -1, 0), "YZ": (1, 0, 0)}  # normals, same as the sketch planes
DATUM_AXES = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = np.linalg.norm(a)
    if n < _TOL:
        raise ValueError("zero direction")
    return a / n


def _rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector a onto unit vector b."""
    c = float(np.clip(a @ b, -1.0, 1.0))
    if c > 1 - _TOL:
        return np.eye(3)
    if c < -1 + _TOL:
        # antiparallel: any axis perpendicular to a
        k = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(k) < 1e-3:
            k = np.cross(a, [0.0, 1.0, 0.0])
        return _rotation_about(_unit(k), math.pi)
    k = np.cross(a, b)
    return _rotation_about(k / np.linalg.norm(k), math.acos(c))


def _rotation_about(k: np.ndarray, angle: float) -> np.ndarray:
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def euler_to_matrix(rotate) -> np.ndarray:
    """`rotate` semantics: degrees about global X, then Y, then Z."""
    a, b, c = (math.radians(float(r)) for r in rotate)
    rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return rz @ ry @ rx


def matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]:
    """Inverse of euler_to_matrix, in degrees."""
    sb = -float(np.clip(R[2, 0], -1.0, 1.0))
    b = math.asin(sb)
    if abs(math.cos(b)) > 1e-9:
        a = math.atan2(R[2, 1], R[2, 2])
        c = math.atan2(R[1, 0], R[0, 0])
    else:
        a = math.atan2(-R[1, 2], R[1, 1])
        c = 0.0
    return tuple(round(math.degrees(x) + 0.0, 6) for x in (a, b, c))


@dataclass
class Pose:
    R: np.ndarray = field(default_factory=lambda: np.eye(3))
    t: np.ndarray = field(default_factory=lambda: np.zeros(3))

    @classmethod
    def from_euler(cls, at, rotate) -> "Pose":
        return cls(euler_to_matrix(rotate), np.asarray([float(v) for v in at]))

    def point(self, p) -> np.ndarray:
        return self.R @ np.asarray(p, dtype=float) + self.t

    def direction(self, d) -> np.ndarray:
        return self.R @ np.asarray(d, dtype=float)

    def rotate_about(self, R_delta: np.ndarray, pivot: np.ndarray) -> None:
        self.R = R_delta @ self.R
        self.t = R_delta @ (self.t - pivot) + pivot

    def apply(self, shape: Shape) -> Shape:
        plane = Plane(origin=tuple(self.t), x_dir=tuple(self.R[:, 0]), z_dir=tuple(self.R[:, 2]))
        return shape.moved(plane.location)

    def describe(self) -> dict:
        return {
            "at": [round(float(v), 6) + 0.0 for v in self.t],
            "rotate": list(matrix_to_euler(self.R)),
        }


# --- face geometry ---------------------------------------------------------------------------


@dataclass
class Datum:
    """A plane (point + normal) or an axis (point + direction) in assembly coordinates."""

    point: np.ndarray
    direction: np.ndarray
    kind: str  # "plane" | "axis"


def face_datum(face: Face, want: str, what: str, fid: str) -> Datum:
    geom = face.geom_type.name
    if want == "plane":
        if geom != "PLANE":
            raise CadjsonError(f"{what} must be a flat face, got {geom.lower()}", fid,
                               ["against, flush and parallel mate flat faces; coaxial mates cylinders, cones and holes"])
        c, n = face.center(), face.normal_at()
        return Datum(np.array([c.X, c.Y, c.Z]), _unit([n.X, n.Y, n.Z]), "plane")
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    surf = BRepAdaptor_Surface(face.wrapped)
    if geom == "CYLINDER":
        ax = surf.Cylinder().Axis()
    elif geom == "CONE":
        ax = surf.Cone().Axis()
    else:
        raise CadjsonError(f"{what} must be a cylindrical or conical face, got {geom.lower()}", fid,
                           ['select the hole wall or the shank, e.g. {"of": "holes", "geom": "cylinder"}'])
    p, d = ax.Location(), ax.Direction()
    return Datum(np.array([p.X(), p.Y(), p.Z()]), _unit([d.X(), d.Y(), d.Z()]), "axis")


def shared_datum(faces: list[Face], want: str, what: str, fid: str) -> Datum:
    """One datum for a selection: several faces are fine when they lie on the same plane or axis
    (a ledge and the boss tops level with it, the two halves of a split bore)."""
    datums = [face_datum(f, want, what, fid) for f in faces]
    first = datums[0]
    for d in datums[1:]:
        same_dir = abs(abs(float(first.direction @ d.direction)) - 1) < _DEG
        gap = d.point - first.point
        if want == "plane":
            same = same_dir and abs(float(gap @ first.direction)) < 1e-4
        else:
            same = same_dir and np.linalg.norm(np.cross(gap, first.direction)) < 1e-4
        if not same:
            raise CadjsonError(f"{what} selects {len(faces)} faces that are not on one {want}", fid,
                               ["add nth, near, area or radius to the selector"])
    return first


def datum_named(name: str) -> Datum | None:
    if name in DATUM_PLANES:
        return Datum(np.zeros(3), _unit(DATUM_PLANES[name]), "plane")
    if name in DATUM_AXES:
        return Datum(np.zeros(3), _unit(DATUM_AXES[name]), "axis")
    return None


# --- the solver -----------------------------------------------------------------------------


class Placer:
    """Accumulates mates for one placement into a Pose."""

    def __init__(self, pose: Pose, name: str):
        self.pose = pose
        self.name = name
        self.free_rot: np.ndarray | None | str = None  # None = all, vector = about that axis, "none"
        self.free_trans = np.eye(3)  # columns: free translation directions
        self.fixed_by: list[str] = []  # mate labels in order, for conflict messages
        # Rotations turn about the first mated geometry (a point on the mated axis or plane) so
        # they never undo it; before anything is mated they turn about the part's own origin.
        self.pivot: np.ndarray | None = None
        # After a coaxial mate the part may still be turned end for end: the axis line is the
        # same either way. A later face mate uses that when it needs the opposite direction.
        self.can_flip = False

    def _conflict(self, label: str, fid: str, what: str) -> CadjsonError:
        earlier = ", ".join(self.fixed_by) or "the initial pose"
        return CadjsonError(f"{label}: cannot {what} without undoing {earlier}", fid,
                            ["mates apply in order; check the faces they select with `cadjson info`",
                             "if a coaxial mate turned the part the wrong way, add \"flip\": true to it"])

    def align(self, a: np.ndarray, b: np.ndarray, target_point: np.ndarray, label: str, fid: str,
              line: bool = False) -> None:
        """Rotate so that this direction a (already in assembly coordinates) equals b.
        `line`: the mate only needs the line, so the direction may still be reversed later."""
        if self.free_rot is None:
            self.pose.rotate_about(_rotation_between(a, b), self.pose.t if self.pivot is None else self.pivot)
            self.free_rot = b.copy()
            self.can_flip = line
            if self.pivot is None:
                self.pivot = target_point.copy()
            return
        if isinstance(self.free_rot, str):
            if a @ b < 1 - _DEG:
                raise self._conflict(label, fid, "turn the part")
            return
        k = self.free_rot
        if abs(a @ k + b @ k) < 1e-4 and abs(a @ k) > 1e-4 and self.can_flip:
            u = np.cross(k, [1.0, 0.0, 0.0])
            if np.linalg.norm(u) < 1e-3:
                u = np.cross(k, [0.0, 1.0, 0.0])
            u = _unit(u)
            self.pose.rotate_about(_rotation_about(u, math.pi), self.pivot)
            a = 2 * (a @ u) * u - a  # a after the half turn; its component along k is now reversed
        if abs(a @ k - b @ k) > 1e-4:
            raise self._conflict(label, fid, "turn the part")
        ap, bp = a - (a @ k) * k, b - (b @ k) * k
        if np.linalg.norm(ap) > _TOL:
            angle = math.atan2(np.cross(ap, bp) @ k, ap @ bp)
            if abs(angle) > _TOL:
                self.pose.rotate_about(_rotation_about(k, angle), self.pivot)
        if abs(abs(b @ k) - 1) > _DEG:
            self.free_rot = "none"
        self.can_flip = False

    def translate(self, constraints: list[tuple[np.ndarray, float]], label: str, fid: str) -> None:
        """Move within the free directions so that delta . n_i = k_i for every constraint."""
        if not constraints:
            return
        N = np.array([n for n, _ in constraints])
        k = np.array([v for _, v in constraints])
        B = self.free_trans
        if B.shape[1] == 0:
            if np.max(np.abs(k)) > 1e-4:
                raise self._conflict(label, fid, "move the part")
            return
        M = N @ B
        c, *_ = np.linalg.lstsq(M, k, rcond=None)
        if np.max(np.abs(M @ c - k)) > 1e-4:
            raise self._conflict(label, fid, "move the part")
        self.pose.t = self.pose.t + B @ c
        # the directions still free are those in B along which every constraint is unchanged
        _, s, vt = np.linalg.svd(M)
        rank = int(np.sum(s > 1e-9))
        self.free_trans = B @ vt[rank:].T

    def twist(self, axis: np.ndarray, pivot: np.ndarray, degrees: float, label: str, fid: str) -> None:
        if isinstance(self.free_rot, str) or (self.free_rot is not None and abs(abs(self.free_rot @ axis) - 1) > _DEG):
            raise self._conflict(label, fid, "turn the part about the axis")
        self.pose.rotate_about(_rotation_about(axis, math.radians(degrees)), pivot)
        self.can_flip = False


def solve_mates(pose: Pose, name: str, mates, select_this, targets, ctx: Context, fid_base: str) -> Pose:
    """select_this(sel) -> faces of this part in its own coordinates.
    targets: name -> (select(sel) -> faces, Pose) for parts placed so far and the host body."""
    placer = Placer(pose, name)
    for j, mate in enumerate(mates):
        fid = f"{fid_base}.mates[{j}]"
        label = f"mate {j} ({mate.type} to {mate.to})"
        want = "axis" if mate.type == "coaxial" else "plane"
        # this side, in assembly coordinates under the current pose
        mine = shared_datum(select_this(mate.this), want, f"{label}: 'this'", fid)
        p_this, d_this = placer.pose.point(mine.point), placer.pose.direction(mine.direction)
        # target side
        target = datum_named(mate.to) if mate.face is None else None
        if target is None:
            if mate.to not in targets:
                raise CadjsonError(f"{label}: unknown target {mate.to!r}", fid,
                                   [f"targets: {', '.join([*targets, *DATUM_PLANES, *DATUM_AXES])}"])
            if mate.face is None:
                raise CadjsonError(f"{label}: give 'face' to say which face of {mate.to!r}", fid)
            select, tpose = targets[mate.to]
            tdat = shared_datum(select(mate.face), want, f"{label}: 'face' of {mate.to!r}", fid)
            p_t, d_t = tpose.point(tdat.point), tpose.direction(tdat.direction)
        else:
            if target.kind != want:
                raise CadjsonError(f"{label}: {mate.to} is {'an axis' if target.kind == 'axis' else 'a plane'}, "
                                   f"which a {mate.type} mate cannot use", fid)
            p_t, d_t = target.point, target.direction
        offset = ctx.length(mate.offset)

        if mate.type in ("against", "flush", "parallel"):
            goal = -d_t if mate.type == "against" else d_t
            if mate.flip:
                goal = -goal
            placer.align(d_this, goal, p_t, label, fid)
            if mate.type != "parallel":
                p_now = placer.pose.point(mine.point)
                placer.translate([(d_t, offset - float((p_now - p_t) @ d_t))], label, fid)
        else:  # coaxial
            goal = d_t if (d_this @ d_t >= 0) != mate.flip else -d_t
            placer.align(d_this, goal, p_t, label, fid, line=True)
            p_now = placer.pose.point(mine.point)
            u = np.cross(d_t, [1.0, 0.0, 0.0])
            if np.linalg.norm(u) < 1e-3:
                u = np.cross(d_t, [0.0, 1.0, 0.0])
            u = _unit(u)
            v = np.cross(d_t, u)
            delta = p_t - p_now
            placer.translate([(u, float(delta @ u)), (v, float(delta @ v))], label, fid)
            angle = ctx.num(mate.angle)
            if angle:
                placer.twist(d_t, p_t, angle, label, fid)
        placer.fixed_by.append(label)
    return placer.pose


# --- checks ---------------------------------------------------------------------------------


def _volume(shape) -> float:
    """Volume of a boolean result. build123d hands back a Solid, a Compound, a ShapeList of
    disjoint pieces (four bosses through one plate) or None; faces and edges count as 0."""
    if shape is None:
        return 0.0
    if isinstance(shape, (list, tuple)):
        return sum(_volume(s) for s in shape)
    if getattr(shape, "wrapped", None) is None:  # an empty result from OCC is not always None
        return 0.0
    return float(shape.volume)


def run_checks(placed: list[tuple[str, Shape]], checks, ctx: Context) -> dict:
    """Interference between every pair and the requested clearances. Returns the report block."""
    out: dict = {"interference": [], "clearance": []}
    mode = checks.interference if checks else "warn"
    if mode != "off":
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                (na, a), (nb, b) = placed[i], placed[j]
                try:
                    vol = _volume(a.intersect(b))
                except Exception as exc:  # a failed boolean is a real error, not "no overlap"
                    raise CadjsonError(f"interference check between {na} and {nb} failed: {exc}", "checks",
                                       ["set checks.interference to 'off' to skip the check"]) from exc
                if vol > 1e-3:
                    out["interference"].append({"between": [na, nb], "volume_mm3": round(vol, 4)})
    if checks:
        by_name = dict(placed)
        for k, c in enumerate(checks.clearance):
            fid = f"checks.clearance[{k}]"
            missing = [n for n in c.between if n not in by_name]
            if missing:
                raise CadjsonError(f"clearance check names unknown part(s) {missing}", fid,
                                   [f"parts: {', '.join(by_name)}"])
            a, b = (by_name[n] for n in c.between)
            dist = float(a.distance_to(b))
            lo = ctx.length(c.min)
            hi = ctx.length(c.max) if c.max is not None else None
            ok = dist >= lo - 1e-6 and (hi is None or dist <= hi + 1e-6)
            entry = {"between": list(c.between), "distance_mm": round(dist, 4), "min_mm": round(lo, 4), "ok": ok}
            if hi is not None:
                entry["max_mm"] = round(hi, 4)
            out["clearance"].append(entry)
    return out


def check_messages(report: dict) -> list[str]:
    msgs = []
    for hit in report.get("interference", []):
        a, b = hit["between"]
        msgs.append(f"{a} and {b} overlap by {hit['volume_mm3']:g} mm^3")
    for c in report.get("clearance", []):
        if not c["ok"]:
            a, b = c["between"]
            want = f"at least {c['min_mm']:g}" + (f", at most {c['max_mm']:g}" if "max_mm" in c else "")
            msgs.append(f"clearance {a} to {b} is {c['distance_mm']:g} mm, wanted {want}")
    return msgs
