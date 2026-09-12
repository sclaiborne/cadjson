# cadgen schema v0

Status: implemented in Phase 1 (2026-09-12) for everything below except `dimensions`, `pdf`,
and `section` views, which are Phase 2. Examples in `examples/` build with `cadgen build`.
`cadgen schema` prints the machine-readable JSON Schema generated from the same models.

Design rule: **the numbers a person would write on a sketch are the numbers in the file.**
Every labelled dimension appears once, in `params`, and features refer to it by name.
Coordinates of individual points should almost never appear.

## 1. Document

```jsonc
{
  "schema": "cadgen/0.1",      // required, exact string
  "name": "board_profile",     // used for output file names
  "description": "...",        // free text, optional
  "units": "mm",               // "mm" (default) or "in"; all lengths are in this unit
  "params": { ... },           // optional named dimensions, see section 2
  "features": [ ... ],         // ordered feature tree, see section 5
  "outputs": { ... }           // what to produce, see section 8
}
```

Features run in order. Each one takes the current solid (or nothing, for the first) and
returns the new solid. Any feature can be referenced later by its `id`.

## 2. Dimensions and `params`

Any field documented as a *dim* accepts either a number or a string expression:

```jsonc
"params": { "L": 100, "h": 20, "x": 12, "g": "L - D - x" }
"w": "L"            // reference
"h": "h - y"        // arithmetic
"corner": [0, "h"]  // inside vectors too
```

Expression grammar, deliberately tiny: numbers, param names, `+ - * /`, parentheses, unary
minus, and the functions `min`, `max`, `abs`, `sqrt`. Params may reference earlier params.
Nothing else: no Python, no conditionals. If the grammar ever needs to grow beyond this, that
is the signal to reconsider "script instead of JSON".

`params` is optional. A part with only literal numbers is valid.

## 3. Coordinate conventions

- Z is up. Right-handed. Units per document.
- Named planes and their sketch axes (u = sketch horizontal, v = sketch vertical, n = normal):

| Plane | u | v | n |
|---|---|---|---|
| `XY` | +X | +Y | +Z |
| `XZ` | +X | +Z | -Y |
| `YZ` | +Y | +Z | +X |

These match build123d. Fusion 360 defaults to Y-up; the Fusion exporter will convert.
The XZ normal being -Y is a build123d quirk worth knowing: extruding a positive distance
from `XZ` goes toward -Y. For a single part it rarely matters.

## 4. Planes

A `plane` field accepts:

```jsonc
"XY"                                              // named plane through the origin
{ "base": "XZ", "offset": "wid / 2" }             // named plane moved along its normal
{ "face": <face selector> }                       // the plane of an existing planar face
{ "origin": [0, 0, 10], "normal": [0, 0, 1], "x_dir": [1, 0, 0] }   // explicit
```

For a face plane: origin is the face centre and the normal points **out of the material**.
If the face is horizontal, u is +X (and v is +Y on a top face, -Y on a bottom face). Otherwise
v points up (+Z) and u is horizontal, to the right as seen by someone looking at the face.
So a cut sketched on a face with `"through": true` goes into the part with no sign to think
about; a blind cut uses a negative distance, and a `hole` always drills inward.

An `OffsetPlane` may also carry `"origin": [u, v]` to shift the sketch origin within the plane.

## 5. Features

Common fields: `id` (required, unique), `type` (required), `op` for features that add or
remove material: `"add"` (default), `"cut"`, `"intersect"`.

### 5.1 `extrude`

```jsonc
{ "id": "body", "type": "extrude",
  "sketch": <sketch>,
  "distance": "depth",     // dim, along the plane normal; negative goes the other way
  "through": true,         // cut only: through everything, both directions (instead of distance)
  "both": true,            // symmetric about the plane (optional)
  "taper": 0,              // degrees, optional
  "op": "add" }
```

Exactly one of `distance` or `through`.

### 5.2 `revolve`

```jsonc
{ "id": "ring", "type": "revolve",
  "sketch": <sketch>,
  "axis": "v",             // "u" or "v" (a sketch axis), or { "point": [u, v], "dir": [du, dv] }
  "angle": 360,            // dim, degrees, default 360
  "op": "add" }
```

### 5.3 `fillet` and `chamfer`

```jsonc
{ "id": "corners", "type": "fillet", "radius": 5, "edges": <edge selector> }
{ "id": "break", "type": "chamfer", "length": 1, "edges": <edge selector> }
{ "id": "break2", "type": "chamfer", "length": 1, "length2": 2, "edges": <edge selector> }
```

If the kernel cannot make the fillet, the error names the feature id and the largest radius
that would have worked.

### 5.4 `shell`

```jsonc
{ "id": "hollow", "type": "shell", "thickness": "wall", "remove": <face selector> }
```

`remove` is optional; without it the solid becomes a closed hollow shell. Negative thickness
grows outward.

### 5.5 `hole`

A convenience for the most common 3D-printing operation. Places one or more holes on a face.

```jsonc
{ "id": "wall_holes", "type": "hole",
  "face": <face selector>,
  "at": [[-15, 22], [15, 22]],          // list of [u, v] on that face plane
  "diameter": "hole_d",
  "through": true,                     // or "depth": 10
  "counterbore": { "diameter": 9, "depth": 3 },   // optional
  "countersink": { "diameter": 9, "angle": 90 } } // optional
```

### 5.6 `mirror`

```jsonc
{ "id": "vents_other_side", "type": "mirror", "features": ["vents"], "plane": "XZ" }
```

Repeats the listed features (their material change) reflected across the plane. Without
`features`, mirrors the whole body and unions it.

### 5.7 `pattern` (feature level)

```jsonc
{ "id": "more_holes", "type": "pattern", "features": ["hole1"],
  "kind": "linear", "count": 4, "spacing": 10, "direction": "X" }
{ "id": "ring_holes", "type": "pattern", "features": ["hole1"],
  "kind": "polar", "count": 6, "axis": "Z", "angle": 360 }
```

Sketch-level patterns (section 6) cover most cases and are simpler; feature-level patterns
are for repeating fillets, chamfers, or multi-feature groups. Phase 1 implements sketch-level
first.

### 5.8 Later (not v0)

`loft`, `sweep`, `thread`, `text`, boolean with another part file, assemblies.

## 6. Sketches

```jsonc
"sketch": { "plane": <plane>, "shapes": [ <shape>, ... ] }
```

Shapes are unioned into one profile unless a shape says `"mode": "subtract"` (a hole in a
profile). Every shape may carry a `pattern`. All 2D coordinates are `[u, v]` on the plane.

| Shape | Fields | Placement |
|---|---|---|
| `rect` | `w`, `h` | one of `center` (default `[0,0]`), `corner` (bottom-left), `top_left`, `top_right`, `bottom_right`; optional `angle` |
| `circle` | `d` or `r` | `center` |
| `slot` | `length` (end to end), `width` | `center`, optional `angle` |
| `polygon` | `sides`, `d` (across corners) or `flat` (across flats) | `center`, optional `angle` |
| `points` | `points: [[u,v], ...]` closed polyline | absolute |
| `path` | `start: [u,v]`, `segments: [...]` | see below |

`path` segments are strings, one move each, so a profile reads like a sketch walk-through:

```
"right L"        "left g"        "up h"        "down y"     // axis-aligned, dim length
"line du, dv"                                               // relative straight move
"arc du, dv, r"                                             // relative arc, r signed for side
"to u, v"                                                   // absolute straight move
"close"                                                     // straight line back to start
```

Pattern on a shape:

```jsonc
"pattern": { "type": "linear", "count": 4, "spacing": 5, "direction": "v" }   // "u", "v", or [du, dv]
"pattern": { "type": "polar",  "count": 6, "radius": 15, "start_angle": 0, "angle": 360 }
"pattern": { "type": "grid",   "count": [3, 2], "spacing": [10, 8] }
```

Linear and grid patterns are centred on the shape's own position by default, so a row of four
vents with a shape centred at mid-height sits symmetrically about mid-height. Set
`"centered": false` to start at the shape and step forward instead. A polar pattern places the
shape on a circle of `radius` around `center` (default the sketch origin) and rotates each
instance with its angle (`"rotate": false` keeps them upright).

## 7. Selectors

Selectors pick faces or edges of the *current* solid. All given filters must match (AND).
A selector that matches nothing is an error, and the message lists what was available.
Raw indices are deliberately not supported: they change whenever an earlier feature changes.

Face selector fields:

| Field | Meaning |
|---|---|
| `of` | feature id: only faces created or modified by that feature |
| `normal` | `"+Z"`, `"-Y"`, ... planar faces whose normal points that way |
| `geom` | `"plane"`, `"cylinder"`, `"cone"`, `"sphere"` |
| `nth` | after sorting along `normal` (or `sort_by`), pick index; `-1` is the farthest along the axis |
| `near` | `[x, y, z]`: the single face whose centre is closest |
| `area` | `{ "min": .., "max": .. }` |

Edge selector fields:

| Field | Meaning |
|---|---|
| `of` | feature id |
| `of_face` | face selector: edges bounding those faces |
| `parallel_to` | `"X"`, `"Y"`, `"Z"`: straight edges along that axis |
| `convex` | `true`: outer corners (material angle < 180°); `false`: inner corners. Lets "round the body more than the grooves" be two fillet features with no coordinates. |
| `geom` | `"line"`, `"circle"`, `"arc"`, `"ellipse"`, `"spline"` |
| `radius`, `length` | `{ "min": .., "max": .. }` |
| `near` | `[x, y, z]`: the single edge whose centre is closest |
| `nth`, `sort_by` | as for faces |

Shortcuts (sugar, expand to the above): `"top"` = `{ "normal": "+Z", "nth": -1 }`, likewise
`"bottom"`, `"left"` (-X), `"right"` (+X), `"front"` (-Y), `"back"` (+Y).

`of` works by comparing topology before and after the named feature: the faces (and edges)
that feature *created*, identified by the surface or curve they lie on. Later cuts, fillets,
and chamfers may trim those faces, and they still count as belonging to the feature. Faces
that merely got trimmed by a cut do not belong to the cut; the cut's own walls do.

`cadgen info part.json` prints every face and edge of the finished part with its normal,
centre, and size, which is the quickest way to work out a selector.

## 8. Outputs

```jsonc
"outputs": {
  "step": true,
  "stl":  { "tolerance": 0.01, "angular_tolerance": 0.1 },   // or true for defaults
  "3mf":  true,
  "drawing": {
    "views": ["front", "top", "right", "iso"],   // also "left", "back", "bottom", "section:XZ"
    "hidden_lines": true,
    "dimensions": true,          // overall envelope + hole callouts (Phase 2)
    "format": ["svg", "dxf", "pdf"],
    "scale": "auto"
  },
  "png": true                    // shaded or line preview for review
}
```

Outputs go to `out/<name>/`. The CLI can override any of this.

## 9. Errors

Every error names the feature id, says what was being attempted, and what the model looked
like just before (bounding box, volume, face and edge counts). Selector errors list the
candidate faces or edges with their normals, centres, and lengths so the fix is obvious
to a person or an LLM.

## 10. Open questions for review

1. Decided 2026-09-12: examples use `rect` + cuts; `path` stays in the schema as an
   alternative for profiles that are not rectangular. Both remain supported.
2. Are the corner keywords (`top_left`, `bottom_right`, ...) enough, or do you want
   `"anchor": "top_left"` plus `"at"`?
3. Should `through` be the default for cuts sketched on a face?
4. Face plane orientation: normal out of the material. Agree?
5. Is `hole` worth having as its own feature, or is `circle` + `cut` enough?
