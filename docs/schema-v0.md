# cadjson schema v0

Status: implemented (Phases 1 and 2, 2026-09-12). Examples in `examples/` build with
`cadjson build`.
`cadjson schema` prints the machine-readable JSON Schema generated from the same models.

Design rule: **the numbers a person would write on a sketch are the numbers in the file.**
Every labelled dimension appears once, in `params`, and features refer to it by name.
Coordinates of individual points should almost never appear.

## 1. Document

```jsonc
{
  "schema": "cadjson/0.1",      // required, exact string
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
minus, the constant `pi`, and the functions `min`, `max`, `abs`, `sqrt`, `floor`, `ceil`,
`round`, and `sin`, `cos`, `tan`, `asin`, `acos`, `atan` (degrees). Params may reference
each other in any order. Nothing else: no Python, no conditionals. If the grammar ever needs
to grow beyond this, that is the signal to reconsider "script instead of JSON".

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

### 5.8 `hole` with a standard thread size

```jsonc
{ "id": "tap", "type": "hole", "face": "top", "at": [[0, 0]], "standard": "M6", "fit": "tap", "through": true }
{ "id": "clr", "type": "hole", "face": "top", "at": [[10, 0]], "standard": "#6-32", "fit": "medium", "depth": 8 }
```

`standard` replaces `diameter`: metric (`M3`, `M8x1` for a fine pitch) or unified (`#4-40`,
`1/4-20`). `fit` is `tap` (tap drill), `close` or `medium` (clearance). Sizes are always in
mm regardless of the document units.

### 5.9 `thread`

Real ISO thread geometry (via bd_warehouse), for printing or for a faithful model.

```jsonc
{ "id": "thread", "type": "thread", "size": "M6", "kind": "external",
  "face": { "of": "shank", "geom": "cylinder" }, "length": 15, "near": [0, 0, -20], "hand": "right" }
{ "id": "tapped", "type": "thread", "size": "M6", "kind": "internal", "face": { "of": "tap_hole", "geom": "cylinder" } }
```

`face` is the cylindrical face to thread. External threads expect a shank at the major
diameter (it is cut to the root and the thread fused on); internal threads expect a hole at
the tap-drill size (it is opened to the major diameter and the thread fused in). `length`
defaults to the whole face; `near` picks which end the thread starts from.

### 5.10 `loft`

```jsonc
{ "id": "body", "type": "loft", "ruled": false, "sections": [
    { "plane": "XY", "shapes": [ { "type": "circle", "d": 12 } ] },
    { "plane": { "base": "XY", "offset": 40 }, "shapes": [ { "type": "circle", "d": 50 } ] } ] }
```

Each section is a sketch with exactly one closed shape, in order along the loft.

### 5.11 `sweep`

```jsonc
{ "id": "rod", "type": "sweep",
  "profile": { "plane": "XY", "shapes": [ { "type": "circle", "d": 4 } ] },
  "path": { "plane": "XZ", "start": [0, 0], "segments": [ "up 20", "arc 10, 10, -10", "right 15" ] } }
```

The path is an open `path` (same segment grammar, no `close`) on a plane. Put the profile at
the path start, perpendicular to it. Bend radii must exceed the profile's half-width.

### 5.12 `part` (import another part file)

```jsonc
{ "id": "cavity", "type": "part", "file": "spacer.json", "op": "cut", "at": [0, 0, 5],
  "rotate": [0, 0, 90], "params": { "outer_d": 22 } }
```

Builds the referenced file (relative to this one, with optional param overrides), places it,
and combines it with the body. Use `op: "add"` to merge a library part in, `cut` for a cavity
or a clearance.

### 5.13 Not yet

Sketch constraints, variable fillets, drafts on faces, helix/coil, surfaces.

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
| `text` | `text`, `size`, optional `font` (installed font name, default Arial) or `font_path` (a .ttf/.otf file relative to the part), `bold` | `center`, optional `angle`; emboss with extrude add, engrave with a negative-distance cut |

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

`cadjson info part.json` prints every face and edge of the finished part with its normal,
centre, and size, which is the quickest way to work out a selector.

## 7b. Assemblies: `parts`

```jsonc
{ "schema": "cadjson/0.1", "name": "stack",
  "params": { "pitch": 20 },
  "parts": [
    { "file": "hex_standoff.json", "name": "left",  "at": [0, 0, 0] },
    { "file": "hex_standoff.json", "name": "right", "at": ["pitch", 0, 0], "rotate": [0, 0, 30] },
    { "file": "spacer.json", "at": ["pitch / 2", 0, 12], "params": { "outer_d": 30 } } ],
  "features": [ ...optional, model the host part here... ] }
```

Placed parts stay separate, named solids in the STEP file (and merge in the STL). The
document's own `features` model a host body; they do not see the placed parts. To combine
geometry, use the `part` feature instead. Param overrides let one library file serve many
sizes. References are relative to the file; cycles are errors.

### Mates: positions from relationships

Typing `at` works for a stack; for parts that fit into each other, say how they fit and let
cadjson work out the numbers:

```jsonc
"parts": [
  { "file": "plate.json" },
  { "file": "hex_standoff.json", "name": "left", "mates": [
      { "type": "coaxial", "this": { "of": "clear_hole", "geom": "cylinder", "radius": { "max": 2 } },
        "to": "plate", "face": { "of": "mount_holes", "geom": "cylinder", "nth": 0, "sort_by": "X" } },
      { "type": "against", "this": "bottom", "to": "plate", "face": "top" } ] },
  { "file": "spacer.json", "name": "cap", "mates": [
      { "type": "coaxial", "this": { "of": "ring", "geom": "cylinder", "radius": { "max": 5 } },
        "to": "left", "face": { "of": "tap_hole", "geom": "cylinder" } },
      { "type": "against", "this": "bottom", "to": "left", "face": "top" } ] } ]
```

| type | faces | fixes |
|---|---|---|
| `against` | two flat faces, touching, normals opposed; `offset` leaves a gap along the target normal | one direction, one translation |
| `flush` | two flat faces coplanar with the same normal; `offset` shifts along it | one direction, one translation |
| `coaxial` | cylinder, cone or hole walls on one axis; `angle` turns about the axis afterwards | one direction, two translations |
| `parallel` | normals aligned, no movement | one direction |

- `this` selects a face of the placed part in that part's own coordinates (run `cadjson info` on
  the part file to see them). `to` names an earlier placed part (`name`, or the file stem), the
  assembly's own `name` for faces of its `features`, or a datum: `XY`, `XZ`, `YZ` (planes, normals
  as for sketch planes) and `X`, `Y`, `Z` (axes). A datum target takes no `face`.
- Mates apply in order. Each fixes some of the six degrees of freedom; what stays free keeps the
  value from `at` / `rotate`, so `at: [0, 0, 20]` plus one `against` mate is fine.
- A `coaxial` mate does not care which way the axis points; a later `against` or `flush` may
  turn the part end for end to be met. `flip` reverses the direction a mate would choose.
- A selector may match several faces as long as they lie on one plane (a ledge and the boss
  tops level with it) or one axis; otherwise narrow it with `nth`, `near`, `area` or `radius`.
- A mate that cannot be met without undoing an earlier one is an error naming both, with the
  most common cause: a selector that matched the wrong face.
- `report.json` lists every placed part with its derived `at` and `rotate`, so a mated assembly
  can be turned into typed coordinates when wanted.

### Checks

```jsonc
"checks": { "interference": "error",
            "clearance": [ { "between": ["cap", "right"], "min": 1 }, { "between": ["lid", "box"], "min": 0.2, "max": 0.5 } ] }
```

Interference between every pair of placed parts (and the host body) is always computed and
reported; `"warn"` (default) prints it, `"error"` fails the build, `"off"` skips it. Each
`clearance` entry checks the shortest distance between two parts against `min` (and `max`).
Results go to `report.json` under `assembly` and to the build summary.

## 8. Outputs

```jsonc
"outputs": {
  "step": true,
  "stl":  { "tolerance": 0.01, "angular_tolerance": 0.1 },   // or true for defaults
  "3mf":  { "part_number": "CG-010", "name": "Board" },       // or true; metadata lands in the 3MF
  "drawing": {
    "views": ["front", "top", "right", "iso"],   // also "left", "back", "bottom"; one file per view
    "hidden_lines": true,
    "format": ["svg", "dxf", "pdf"],             // svg/dxf apply to views; pdf applies to the sheet
    "scale": "auto",
    "sections": [ { "name": "A", "plane": "XZ" },
                  { "name": "B", "plane": { "base": "YZ", "offset": 10 }, "flip": true } ],
    "sheet": true,                               // full annotated drawing sheet (draftwright)
    "dimensions": true,                          // automatic dimensions and callouts on the sheet
    "projection": "third",                       // or "first"
    "page": "A4",                                // optional; automatic if omitted
    "title_block": { "title": "Board", "number": "CG-001", "revision": "A",
                     "material": "PLA", "tolerance": "ISO 2768-m", "drawn_by": "", "company": "" }
  },
  "png": true                    // line-art preview per view and per section
}
```

Outputs go to `out/<name>/`:

| File | What |
|---|---|
| `<name>.step`, `.stl`, `.3mf` | the solid |
| `<name>_<view>.svg` / `.dxf` / `.png` | one orthographic view, hidden lines dashed, no dimensions |
| `<name>_section_<label>.svg` / `.png` | the part cut at the plane, half on the normal side removed, cut faces filled grey |
| `<name>_drawing.svg` / `.pdf` / `.dxf` | the sheet: third-angle views + iso, dimensions, hole and radius callouts, title block |

Section convention: the half on the plane's normal side is discarded and you look at the cut
face from that side; `flip` does the opposite. Remember `XZ` has normal -Y (section 3).

The sheet is produced by [draftwright](https://pypi.org/project/draftwright/) (AGPL-3). Its
automatic dimensioning picks envelope sizes, hole and slot callouts, radii and chamfers; it
does not know your parameter names. Manual dimensions on the sheet are a later phase.

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
