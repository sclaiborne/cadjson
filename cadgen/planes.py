"""Resolve PlaneRef models to build123d Planes (docs/schema-v0.md sections 3 and 4)."""

from __future__ import annotations

from build123d import Face, Plane, Vector

from cadgen.context import Context
from cadgen.errors import CadgenError
from cadgen.schema import ExplicitPlane, FacePlane, OffsetPlane, PlaneRef

NAMED = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}

AXIS = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}


def plane_from_normal(origin: Vector, normal: Vector, x_dir: Vector | None = None) -> Plane:
    """Plane with the given outward normal; u follows +X when possible, otherwise v points up."""
    n = normal.normalized()
    if x_dir is None:
        if abs(n.Z) > 0.999:
            x_dir = Vector(1, 0, 0)
        else:
            x_dir = Vector(0, 0, 1).cross(n).normalized()
    return Plane(origin=origin, x_dir=x_dir, z_dir=n)


def face_plane(face: Face) -> Plane:
    return plane_from_normal(face.center(), face.normal_at())


def resolve_plane(ref: PlaneRef, ctx: Context, select_faces) -> Plane:
    """`select_faces(FaceSel) -> list[Face]` is supplied by the builder for face planes."""
    if isinstance(ref, str):
        return NAMED[ref]
    if isinstance(ref, OffsetPlane):
        plane = NAMED[ref.base].offset(ctx.length(ref.offset))
        if ref.origin is not None:
            u, v = ctx.vec2(ref.origin)
            plane = plane.shift_origin(plane.from_local_coords((u, v, 0)))
        return plane
    if isinstance(ref, FacePlane):
        faces = select_faces(ref.face)
        if len(faces) != 1:
            raise CadgenError(
                f"a face plane needs exactly one face, selector matched {len(faces)}",
                ctx.feature_id,
                ["add nth, near, or of to narrow it down"],
            )
        face = faces[0]
        if face.geom_type.name != "PLANE":
            raise CadgenError(f"cannot sketch on a {face.geom_type.name.lower()} face; only planar faces", ctx.feature_id)
        return face_plane(face)
    if isinstance(ref, ExplicitPlane):
        x_dir = ctx.dir3(ref.x_dir) if ref.x_dir is not None else None
        return plane_from_normal(ctx.vec3(ref.origin), ctx.dir3(ref.normal), x_dir)
    raise CadgenError(f"unknown plane reference {ref!r}", ctx.feature_id)
