"""Pydantic models for the cadgen document format (docs/schema-v0.md).

These are the single source of truth for validation, the published JSON Schema, and docs.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cadgen import SCHEMA_VERSION

Dim = Union[int, float, str]
Vec2 = tuple[Dim, Dim]
Vec3 = tuple[Dim, Dim, Dim]
AxisName = Literal["X", "Y", "Z"]
PlaneName = Literal["XY", "XZ", "YZ"]
Op = Literal["add", "cut", "intersect"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- selectors -----------------------------------------------------------------------------


class Range(Model):
    min: Dim | None = None
    max: Dim | None = None


class FaceSelector(Model):
    of: str | None = Field(None, description="feature id: faces created or modified by it")
    normal: str | None = Field(None, pattern=r"^[+-][XYZ]$", description='"+Z", "-Y", ...')
    geom: Literal["plane", "cylinder", "cone", "sphere", "torus"] | None = None
    nth: int | None = Field(None, description="index after sorting along sort_by (or the normal axis)")
    sort_by: AxisName | None = None
    near: Vec3 | None = Field(None, description="the single face whose centre is closest")
    area: Range | None = None


FaceShortcut = Literal["top", "bottom", "left", "right", "front", "back"]
FaceSel = Union[FaceShortcut, FaceSelector]


class EdgeSelector(Model):
    of: str | None = None
    of_face: FaceSel | None = Field(None, description="edges bounding the selected faces")
    parallel_to: AxisName | None = None
    convex: bool | None = Field(None, description="true: outer corners, false: inner corners")
    geom: Literal["line", "circle", "arc", "ellipse", "spline"] | None = None
    radius: Range | None = None
    length: Range | None = None
    near: Vec3 | None = None
    nth: int | None = None
    sort_by: AxisName | None = None


# --- planes --------------------------------------------------------------------------------


class OffsetPlane(Model):
    base: PlaneName
    offset: Dim = 0
    origin: Vec2 | None = Field(None, description="optional sketch-origin shift [u, v] on the plane")


class FacePlane(Model):
    face: FaceSel


class ExplicitPlane(Model):
    origin: Vec3
    normal: Vec3
    x_dir: Vec3 | None = None


PlaneRef = Union[PlaneName, OffsetPlane, FacePlane, ExplicitPlane]


# --- sketch patterns and shapes ------------------------------------------------------------


class LinearPattern(Model):
    type: Literal["linear"]
    count: Dim
    spacing: Dim
    direction: Literal["u", "v"] | Vec2 = "u"
    centered: bool = Field(True, description="centre the row on the shape (true) or start at it (false)")


class PolarPattern(Model):
    type: Literal["polar"]
    count: Dim
    radius: Dim
    center: Vec2 = (0, 0)
    start_angle: Dim = 0
    angle: Dim = 360
    rotate: bool = Field(True, description="rotate each instance with its angle")


class GridPattern(Model):
    type: Literal["grid"]
    count: tuple[int, int]
    spacing: tuple[Dim, Dim]
    centered: bool = True


Pattern = Annotated[Union[LinearPattern, PolarPattern, GridPattern], Field(discriminator="type")]


class ShapeBase(Model):
    mode: Literal["add", "subtract"] = "add"
    pattern: Pattern | None = None


class RectShape(ShapeBase):
    type: Literal["rect"]
    w: Dim
    h: Dim
    radius: Dim = Field(0, description="corner radius (0 = sharp)")
    angle: Dim = 0
    center: Vec2 | None = None
    corner: Vec2 | None = Field(None, description="bottom-left corner")
    top_left: Vec2 | None = None
    top_right: Vec2 | None = None
    bottom_right: Vec2 | None = None

    @model_validator(mode="after")
    def _one_placement(self):
        given = [k for k in ("center", "corner", "top_left", "top_right", "bottom_right") if getattr(self, k) is not None]
        if len(given) > 1:
            raise ValueError(f"rect: give only one of center/corner/top_left/top_right/bottom_right, got {given}")
        return self


class CircleShape(ShapeBase):
    type: Literal["circle"]
    d: Dim | None = None
    r: Dim | None = None
    center: Vec2 = (0, 0)

    @model_validator(mode="after")
    def _d_or_r(self):
        if (self.d is None) == (self.r is None):
            raise ValueError("circle: give exactly one of d or r")
        return self


class SlotShape(ShapeBase):
    type: Literal["slot"]
    length: Dim = Field(description="overall end-to-end length")
    width: Dim
    center: Vec2 = (0, 0)
    angle: Dim = 0


class PolygonShape(ShapeBase):
    type: Literal["polygon"]
    sides: int = Field(ge=3)
    d: Dim | None = Field(None, description="across corners")
    flat: Dim | None = Field(None, description="across flats")
    center: Vec2 = (0, 0)
    angle: Dim = 0

    @model_validator(mode="after")
    def _d_or_flat(self):
        if (self.d is None) == (self.flat is None):
            raise ValueError("polygon: give exactly one of d or flat")
        return self


class PointsShape(ShapeBase):
    type: Literal["points"]
    points: list[Vec2] = Field(min_length=3)


class PathShape(ShapeBase):
    type: Literal["path"]
    start: Vec2 = (0, 0)
    segments: list[str] = Field(min_length=2)


class TextShape(ShapeBase):
    type: Literal["text"]
    text: str = Field(min_length=1)
    size: Dim = Field(description="font size (cap height-ish) in document units")
    center: Vec2 = (0, 0)
    angle: Dim = 0
    font: str = "Arial"
    bold: bool = False


Shape = Annotated[
    Union[RectShape, CircleShape, SlotShape, PolygonShape, PointsShape, PathShape, TextShape],
    Field(discriminator="type"),
]


class OpenPath(Model):
    """An open 2D path on a plane, used as a sweep path."""

    plane: PlaneRef
    start: Vec2 = (0, 0)
    segments: list[str] = Field(min_length=1)


class Sketch(Model):
    plane: PlaneRef
    shapes: list[Shape] = Field(min_length=1)


# --- features ------------------------------------------------------------------------------


class FeatureBase(Model):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class Extrude(FeatureBase):
    type: Literal["extrude"]
    sketch: Sketch
    distance: Dim | None = None
    through: bool = False
    both: bool = False
    taper: Dim = 0
    op: Op = "add"

    @model_validator(mode="after")
    def _extent(self):
        if self.through == (self.distance is not None):
            raise ValueError("extrude: give exactly one of distance or through")
        if self.through and self.op == "add":
            raise ValueError('extrude: "through" only makes sense with op "cut" or "intersect"')
        return self


class AxisRef2(Model):
    point: Vec2
    dir: Vec2


class Revolve(FeatureBase):
    type: Literal["revolve"]
    sketch: Sketch
    axis: Literal["u", "v"] | AxisRef2 = "v"
    angle: Dim = 360
    op: Op = "add"


class Fillet(FeatureBase):
    type: Literal["fillet"]
    radius: Dim
    edges: EdgeSelector


class Chamfer(FeatureBase):
    type: Literal["chamfer"]
    length: Dim
    length2: Dim | None = None
    edges: EdgeSelector


class Shell(FeatureBase):
    type: Literal["shell"]
    thickness: Dim
    remove: FaceSel | None = None


class Counterbore(Model):
    diameter: Dim
    depth: Dim


class Countersink(Model):
    diameter: Dim
    angle: Dim = 90


class Hole(FeatureBase):
    type: Literal["hole"]
    face: FaceSel
    at: list[Vec2] = Field(min_length=1)
    diameter: Dim | None = None
    standard: str | None = Field(None, description='thread designation, e.g. "M3", "M8x1", "#6-32", "1/4-20"')
    fit: Literal["tap", "close", "medium"] = Field("medium", description="hole size for a standard thread")
    through: bool = False
    depth: Dim | None = None
    counterbore: Counterbore | None = None
    countersink: Countersink | None = None

    @model_validator(mode="after")
    def _extent(self):
        if self.through == (self.depth is not None):
            raise ValueError("hole: give exactly one of depth or through")
        if self.counterbore and self.countersink:
            raise ValueError("hole: counterbore and countersink are mutually exclusive")
        if (self.diameter is None) == (self.standard is None):
            raise ValueError("hole: give exactly one of diameter or standard")
        return self


class Thread(FeatureBase):
    type: Literal["thread"]
    size: str = Field(description='thread designation, e.g. "M6", "M8x1", "1/4-20"')
    face: FaceSel = Field(description="the cylindrical face to thread (a shank or a hole wall)")
    kind: Literal["external", "internal"] = "external"
    length: Dim | None = Field(None, description="threaded length; whole face if omitted")
    near: Vec3 | None = Field(None, description="thread starts at the face end nearest this point")
    hand: Literal["right", "left"] = "right"


class Loft(FeatureBase):
    type: Literal["loft"]
    sections: list[Sketch] = Field(min_length=2, description="one closed shape per section, each on its own plane")
    ruled: bool = False
    op: Op = "add"


class Sweep(FeatureBase):
    type: Literal["sweep"]
    profile: Sketch = Field(description="closed profile on a plane at the path start, perpendicular to it")
    path: OpenPath
    op: Op = "add"


class PartRef(FeatureBase):
    type: Literal["part"]
    file: str = Field(description="another cadgen part file, relative to this file")
    at: Vec3 = (0, 0, 0)
    rotate: Vec3 = Field((0, 0, 0), description="degrees about X, Y, Z applied before translation")
    params: dict[str, Dim] = Field({}, description="override the imported part's params")
    op: Op = "add"


class Mirror(FeatureBase):
    type: Literal["mirror"]
    plane: PlaneRef
    features: list[str] | None = Field(None, description="feature ids to mirror; omit to mirror the whole body")


class AxisRef3(Model):
    point: Vec3
    dir: Vec3


class PatternFeature(FeatureBase):
    type: Literal["pattern"]
    features: list[str] = Field(min_length=1)
    kind: Literal["linear", "polar"]
    count: Dim
    spacing: Dim | None = None
    direction: AxisName | Vec3 | None = None
    axis: AxisName | AxisRef3 | None = None
    angle: Dim = 360

    @model_validator(mode="after")
    def _fields(self):
        if self.kind == "linear" and (self.spacing is None or self.direction is None):
            raise ValueError("linear pattern needs spacing and direction")
        if self.kind == "polar" and self.axis is None:
            raise ValueError("polar pattern needs axis")
        return self


Feature = Annotated[
    Union[Extrude, Revolve, Fillet, Chamfer, Shell, Hole, Mirror, PatternFeature, Thread, Loft, Sweep, PartRef],
    Field(discriminator="type"),
]


class Placement(Model):
    """A part positioned in an assembly."""

    file: str
    name: str | None = Field(None, description="defaults to the file's part name")
    at: Vec3 = (0, 0, 0)
    rotate: Vec3 = (0, 0, 0)
    params: dict[str, Dim] = {}


# --- outputs -------------------------------------------------------------------------------


class StlOptions(Model):
    tolerance: Dim = 0.01
    angular_tolerance: Dim = 0.1


ViewName = Literal["front", "back", "top", "bottom", "left", "right", "iso"]


class TitleBlock(Model):
    title: str | None = Field(None, description="defaults to the document name")
    number: str = "DWG-001"
    revision: str = "A"
    material: str = ""
    tolerance: str | None = Field(None, description='e.g. "ISO 2768-m"; omitted means unspecified')
    drawn_by: str = ""
    company: str = ""
    date: str = ""


class SectionSpec(Model):
    plane: PlaneRef
    name: str | None = Field(None, description="label; defaults to A, B, C ...")
    flip: bool = Field(False, description="keep the other half and look from the other side")
    hidden_lines: bool = False


class DrawingOptions(Model):
    views: list[ViewName] = ["front", "top", "right", "iso"]
    hidden_lines: bool = True
    format: list[Literal["svg", "dxf", "pdf"]] = ["svg"]
    scale: Literal["auto"] | Dim = "auto"
    sections: list[SectionSpec] = Field([], description="section views, one file each (own renderer)")
    sheet: bool = Field(False, description="full annotated drawing sheet via draftwright")
    dimensions: bool = Field(False, description="automatic dimensions on the sheet")
    projection: Literal["third", "first"] = "third"
    page: str | None = Field(None, description='sheet size, e.g. "A4", "A3"; automatic if omitted')
    title_block: TitleBlock = TitleBlock()


class ThreeMfOptions(Model):
    tolerance: Dim = 0.01
    angular_tolerance: Dim = 0.1
    part_number: str | None = None
    name: str | None = None


class Outputs(Model):
    step: bool = True
    stl: bool | StlOptions = False
    three_mf: bool | ThreeMfOptions = Field(False, alias="3mf")
    drawing: bool | DrawingOptions = False
    png: bool = True


class Document(Model):
    schema_uri: str | None = Field(None, alias="$schema", exclude=True,
                                   description="optional editor hint pointing at the JSON Schema file")
    schema_version: str = Field(alias="schema")
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    description: str = ""
    units: Literal["mm", "in"] = "mm"
    params: dict[str, Dim] = {}
    parts: list[Placement] = Field([], description="assembly: other part files placed in this one")
    features: list[Feature] = []
    outputs: Outputs = Outputs()

    @model_validator(mode="after")
    def _check(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f'schema must be "{SCHEMA_VERSION}", got "{self.schema_version}"')
        if not self.features and not self.parts:
            raise ValueError("a document needs at least one feature or one placed part")
        seen: set[str] = set()
        for f in self.features:
            if f.id in seen:
                raise ValueError(f"duplicate feature id {f.id!r}")
            for ref in _feature_refs(f):
                if ref not in seen:
                    raise ValueError(f"feature {f.id!r} references {ref!r}, which is not an earlier feature id")
            seen.add(f.id)
        return self


def _feature_refs(f: FeatureBase) -> list[str]:
    """Feature ids referenced by a feature (selectors' `of`, mirror/pattern `features`)."""
    refs: list[str] = []

    def from_face(sel) -> None:
        if isinstance(sel, FaceSelector) and sel.of:
            refs.append(sel.of)

    def from_edge(sel: EdgeSelector) -> None:
        if sel.of:
            refs.append(sel.of)
        if sel.of_face is not None:
            from_face(sel.of_face)

    def from_plane(p) -> None:
        if isinstance(p, FacePlane):
            from_face(p.face)

    if isinstance(f, (Extrude, Revolve)):
        from_plane(f.sketch.plane)
    elif isinstance(f, Loft):
        for s in f.sections:
            from_plane(s.plane)
    elif isinstance(f, Sweep):
        from_plane(f.profile.plane)
        from_plane(f.path.plane)
    elif isinstance(f, Thread):
        from_face(f.face)
    elif isinstance(f, (Fillet, Chamfer)):
        from_edge(f.edges)
    elif isinstance(f, Shell):
        if f.remove is not None:
            from_face(f.remove)
    elif isinstance(f, Hole):
        from_face(f.face)
    elif isinstance(f, Mirror):
        from_plane(f.plane)
        refs.extend(f.features or [])
    elif isinstance(f, PatternFeature):
        refs.extend(f.features)
    return refs


def json_schema() -> dict:
    return Document.model_json_schema(by_alias=True)
