---
name: cadgen
description: Write, edit, build and check cadgen part files (JSON feature trees that become STEP, STL, 2D drawings and Fusion 360 timelines). Use whenever the user asks to model a part, change a dimension, add a feature, fix a build error, or produce CAD/STL/drawings from a description or sketch.
---

# cadgen: CAD as text

A part is one JSON file: named dimensions in `params`, an ordered list of `features`, and
`outputs`. `cadgen` validates it, builds it with the OpenCascade kernel, and writes STEP,
STL, per-view SVGs, section views, an annotated drawing sheet, PNG previews and a Fusion
script. Full format: `docs/schema-v0.md`. Machine-readable schema: `schema/cadgen-0.1.schema.json`.

## Workflow (always)

1. Write or edit the part in `parts/<name>.json` (or `examples/`). Put every dimension the
   user named in `params` and reference it by name. Never write derived coordinates by hand.
2. Validate, then build:
   ```
   .venv\Scripts\cadgen validate parts\<name>.json
   .venv\Scripts\cadgen build parts\<name>.json
   ```
3. Read `out/<name>/report.json` (volume, bbox, feature timings, files) and **look at**
   `out/<name>/<name>_iso.png` and `<name>_front.png` with the Read tool. Feature-order
   mistakes are silent; a picture catches them. Compare the volume with a hand estimate.
4. If a selector or fillet fails, the error names the feature and lists candidates. Run
   `.venv\Scripts\cadgen info parts\<name>.json` to see every face and edge with normal,
   centre and size, then fix the selector. Do not switch to raw indices.
5. Report to the user: what was built, the volume and bbox, which files exist, and anything
   you assumed (unlabelled sizes, hole positions).

## The format in one page

```jsonc
{ "schema": "cadgen/0.1", "name": "bracket", "units": "mm",
  "params": { "L": 60, "t": 4, "hole_d": 5.5, "g": "L - 2 * t" },
  "features": [
    { "id": "base", "type": "extrude", "distance": "t",
      "sketch": { "plane": "XY", "shapes": [ { "type": "rect", "w": "L", "h": 40, "corner": [0, 0] } ] } },
    { "id": "holes", "type": "hole", "face": "top", "at": [[-15, 0], [15, 0]], "diameter": "hole_d", "through": true },
    { "id": "round", "type": "fillet", "radius": 5, "edges": { "of": "base", "parallel_to": "Z" } }
  ],
  "outputs": { "step": true, "stl": true, "drawing": { "views": ["front", "top", "right", "iso"], "sheet": true, "dimensions": true } } }
```

- **Dims** are numbers or expressions over params: `+ - * /`, parentheses, `min max abs sqrt`.
- **Planes**: `"XY"`, `"XZ"`, `"YZ"`; `{ "base": "XZ", "offset": 10 }`; `{ "face": <face selector> }`;
  `{ "origin": [..], "normal": [..] }`. Z is up. **`XZ` has normal -Y** (build123d convention), so
  a positive extrude from `XZ` goes toward -Y. Face planes point out of the material, so cuts
  from a face use `"through": true` or a negative distance; `hole` always drills inward.
- **Sketch shapes**: `rect` (w, h; place with `center` | `corner` | `top_left` | `top_right` |
  `bottom_right`), `circle` (d or r), `slot` (length, width), `polygon` (sides, d or flat),
  `points`, `path` (`"right L"`, `"up h"`, `"line du, dv"`, `"arc du, dv, r"`, `"to u, v"`, `"close"`).
  Any shape takes `"mode": "subtract"` and a `pattern` (linear / polar / grid; linear and grid are
  centred on the shape by default).
- **Features**: `extrude` (distance | through, both, op add/cut/intersect), `revolve` (axis u/v or
  point+dir, angle), `fillet`, `chamfer` (length, length2), `shell` (thickness, remove), `hole`
  (face, at, diameter | `standard` "M3"/"#6-32" + `fit` tap/close/medium, through | depth,
  counterbore, countersink), `mirror` (plane, features), `pattern` (features, kind linear/polar,
  count, spacing+direction | axis+angle), `thread` (size "M6", kind external/internal, face,
  length, near), `loft` (sections), `sweep` (profile, path), `part` (file, op, at, rotate, params).
- **Assemblies**: a top-level `parts` list places other part files (`file`, `at`, `rotate`,
  `params` overrides); they stay separate solids in the STEP.
- **Text**: a `text` sketch shape (text, size, center); cut with a negative distance to engrave.
- **Selectors** (all filters AND together; never indices):
  faces: `of`, `normal` `"+Z"`, `geom`, `nth` + `sort_by`, `near`, `area`; shortcuts `"top"`,
  `"bottom"`, `"left"`, `"right"`, `"front"`, `"back"`.
  edges: `of`, `of_face`, `parallel_to`, `convex` (true = outer corners), `geom`, `radius`,
  `length`, `near`, `nth`. `of` means "created by that feature", tracked through later trimming.

## Recipes

- Rectangular block with rounded vertical corners: `fillet` with `{ "of": "block", "parallel_to": "Z" }`.
- Round only the outer corners of a profile, smaller radius inside: two fillets with
  `{ "parallel_to": "<extrude axis>", "convex": true }` and `"convex": false`.
- Holes on the top face: `hole` with `"face": "top"`; positions are [u, v] from the face centre,
  u along +X.
- Cut a pocket from a face: `extrude` with `"op": "cut"`, `"sketch": { "plane": { "face": "top" } }`
  and `"distance": -5`.
- Prismatic profile from a sketch with labelled dimensions: model it as a rect plus named cuts,
  one per label (see `examples/board_profile.json`), or one `path` of relative moves.
- Chamfer every edge of the top face: `{ "of_face": "top" }`.
- Vents on both sides: cut the pattern on one side, then `mirror` with `"features": ["vents"]`.
- Tapped hole: `hole` with `"standard": "M6", "fit": "tap"`, then `thread` kind `internal` on
  `{ "of": "<hole id>", "geom": "cylinder" }`. Bolt: circle shank at the major diameter, then
  `thread` kind `external` on `{ "of": "<shank id>", "geom": "cylinder" }`.
- Screw clearance holes: `"standard": "M3", "fit": "medium"` (no need to look up 3.4 mm).

## Gotchas

- The first feature must add material. A cut with no solid yet is an error.
- Fillet radius must fit: the error reports the largest radius that works.
- A selector that matches more than you meant is the most common bug (two faces with
  normal +Y, say). Add `nth`, `near`, `of`, or use a shortcut like `"back"`.
- `through: true` cuts in both directions through everything.
- Keep `params` to what the user would write on a sketch. Derived values get an expression.
- Units default to mm; `"units": "in"` scales every length.

## Other commands

- `cadgen export-fusion parts\<name>.json` writes a Fusion 360 script (native timeline) into
  `out/<name>_fusion/`; the user runs it from Fusion's Scripts dialog.
- `cadgen export-python parts\<name>.json` writes the equivalent standalone build123d script,
  for when the schema cannot express something.
- `cadgen schema` prints the JSON Schema; `cadgen build --sheet` forces the drawing sheet.
- `cadgen compare parts\<name>.json reference.stl` checks a recreation against an existing mesh:
  volume, bbox, and surface distance both ways. Recreating from an STL: measure it with trimesh
  (bounds, volume, `section` slices at a few heights, sharp edges), model it, then compare.
  See `parts/zoom_light/` for a worked example with its reference meshes.
