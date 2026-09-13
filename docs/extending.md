# Extending cadjson

Two mechanisms: `extends` for part variants (no code), and plugins for new feature types (Python).

## Variants with `extends`

A variant file names a base part and says only what differs:

```jsonc
{ "schema": "cadjson/0.1", "name": "board_profile_long",
  "extends": "board_profile.json",             // relative to this file; chains are fine
  "params": { "L": 150, "D": 130, "h": 24 },   // override or add params
  "drop": ["notch"],                           // leave out base features by id (optional)
  "features": [                                // same id as a base feature: replaces it in place
    { "id": "mount_holes", "type": "hole", "face": "top", "diameter": 4.5, "through": true,
      "at": [[-45, 0], [45, 0]] }              // new id: appended after the base features
  ],
  "outputs": { "step": true } }                // given: replaces the base's; omitted: inherited
```

Rules:

- `params` merge; the variant's values win. Base expressions see the new values, so `g = L - D - x`
  in the base follows the variant's `L`.
- `features`: a feature whose id exists in the base replaces it at the same position; others are
  appended in order. `drop` removes base features first. Later features may reference earlier ids
  from either file.
- `parts` (assembly placements) concatenate.
- `name`, `description`, `outputs` come from the variant when present, otherwise from the base.
  `units` must agree.
- Relative `file` and `font_path` references in the base keep pointing at the right files after
  the merge, wherever the variant lives.
- The merge happens before validation, so errors refer to the merged document. `report.json`
  lists the chain under `extends`.

Because the variant is a real document after merging, everything works on it: builds, drawings,
`export-fusion`, `export-python`.

## Plugins: new feature types

A plugin is a Python package (or a module on the path while developing) that registers feature
types with cadjson's registry. cadjson finds packages through the `cadjson.plugins` entry-point
group and modules through `CADJSON_PLUGINS=module_a,module_b`.

Minimal plugin (see `examples/plugins/cadjson_gear` for a complete one with packaging):

```python
from typing import Literal
from build123d import Circle
from cadjson.schema import Dim, FeatureBase, Op, PlaneRef

class Disc(FeatureBase):
    type: Literal["disc"]        # the JSON "type" value
    d: Dim
    thickness: Dim
    plane: PlaneRef = "XY"
    op: Op = "add"

def register(registry):
    @registry.feature(Disc)
    def build_disc(feat, api):
        api.extrude(Circle(api.length(feat.d) / 2), api.plane(feat.plane),
                    distance=api.length(feat.thickness), op=feat.op)
```

What the build function gets:

| `api.` | Purpose |
|---|---|
| `length(dim)`, `num(dim)`, `vec2(v)`, `vec3(v)` | resolve dims (params, expressions, units) |
| `plane(ref)` | a build123d Plane from any PlaneRef |
| `sketch(model)`, `located_sketch(model)` | build a Sketch model (2D, or placed on its plane) |
| `faces(sel)`, `edges(sel)` | run selectors on the current solid |
| `extrude(sketch, plane, distance=, through=, both=, taper=, op=)` | extrude and combine |
| `combine(solid, op)` | combine any build123d solid with the body |
| `builder` | the underlying Builder for fillet/chamfer/shell/... |
| `error(message, hints)` | a CadjsonError naming this feature |

A plugin feature must produce geometry (through `extrude` or `combine`) or the build reports it.
If the feature refers to other feature ids, give the model a `refs()` method returning them so
ordering is validated.

Optional exporters: `registry.fusion_emitter("disc")` and `registry.python_emitter("disc")`
decorate functions `(feat, exporter)` that emit code; without them the exporters report the
feature as unsupported. A Python emitter should write dimensions with `exporter.length(dim)` /
`exporter.num(dim)` so they stay named (see docs/python-export.md).

Plugin types appear in validation errors, in `cadjson plugins`, and in
`cadjson schema --with-plugins`. The committed `schema/cadjson-0.1.schema.json` is built-ins only.

Sketch shapes and selectors are not pluggable yet; a feature that needs a special 2D shape
builds it directly with build123d, as the gear does.
