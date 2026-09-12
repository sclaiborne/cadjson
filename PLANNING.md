# CAD-Generator: options survey and plan

Date: 2026-09-12. Status: planning, no decisions locked yet.

## 1. Goal

Text as the source of truth for mechanical parts, so that parts can live in git and be
written or edited by an AI. From that text, produce:

- a real CAD file (B-rep) that opens in Fusion 360 and other CAD packages;
- STL (and 3MF) for 3D printing;
- 2D drawing views (top / front / side / isometric) with hidden lines, as SVG / DXF / PDF;
- a documented interface covering extrude, cut, revolve, fillet, chamfer, patterns, mirror,
  shell, with sketches placed on named planes, offset planes, faces, and axes.

## 2. Options surveyed (Sept 2026)

### 2.1 Geometry engines

| Engine | Kernel | STEP | STL/3MF | Fillet/chamfer | Patterns | Hidden-line 2D views | Install on Windows | Verdict |
|---|---|---|---|---|---|---|---|---|
| build123d 0.11.1 | OCCT B-rep (OCP) | yes | yes/yes | yes | Grid/Polar/Hex | yes, SVG and DXF | `pip install build123d`, Py 3.10-3.14 | **Recommended** |
| CadQuery 2.8 | OCCT B-rep (OCP) | yes | yes/yes | yes | yes | SVG only | pip, Py >= 3.11, pulls VTK | Close second; string selectors are nice for JSON |
| replicad 1.1 | OCCT via WASM (JS) | yes | STL only | yes | manual loops | SVG only | npm | Pick if it must run in a browser |
| FreeCAD 1.1 headless | OCCT B-rep | yes | yes | yes | yes | partial (TechDraw page export is GUI-only) | 500 MB installer or conda | Heavy; `.FCStd` not diff-friendly |
| OpenSCAD (Manifold) | mesh CSG | **no** | yes | **no** | loops | silhouette only | nightly builds | Fails the STEP and fillet requirements |
| JSCAD | mesh CSG | no | yes | no | loops | no | npm | Same as OpenSCAD |
| Zoo KCL | proprietary, cloud | yes | yes | yes | yes | roadmap only | API, 20 free min/month then $0.50/min | Not self-hostable; engine is closed |
| Fornjot | Rust B-rep | no | - | - | - | - | archived June 2026 | Dead |

Verified locally on this machine (Python 3.12, `experiments/smoke_build123d.py`): build123d
installed with pip in one step, and a box + corner fillets + polar hole pattern + side boss +
chamfer built and exported to STEP, STL, four hidden-line SVG views and a DXF in 0.25 s.

### 2.2 Getting into Fusion 360

- **STEP import**: Fusion gets a dumb body with no timeline. You can still fillet, chamfer,
  cut, and pattern on it afterwards (as a Base Feature with design history on, or with direct
  modeling), but the original sketches and extrudes are not editable.
- **Fusion Python API**: creates genuine timeline features (sketch, extrude, revolve, fillet,
  chamfer, shell, patterns, mirror, user parameters). Requires Fusion to be running; no local
  headless mode. The paid Fusion Automation API runs scripts in Autodesk's cloud.
- **Official Fusion MCP** (April 2026): local server, executes scripts and serves API docs;
  it is not a typed feature vocabulary. Several community add-ins do the same over a socket.
- **Drawings**: Fusion's July 2026 Drawing API can auto-create a drawing from a design but
  cannot yet place views or dimensions programmatically.

Implication: one JSON source can have two Fusion paths. STEP export gives an editable body
today. A JSON-to-Fusion-API exporter (a script run inside Fusion) gives a fully native
parametric timeline later. Both are compatible with the same schema.

### 2.3 Input format: JSON vs code vs DSL

- Prior art for JSON feature trees: Autodesk's Fusion 360 Gallery dataset and DeepCAD both use
  an entity dictionary plus an ordered timeline, profiles referenced by id, enumerated boolean
  and extent types. Both stop at sketch + extrude. We would extend with fillet, chamfer,
  revolve, pattern, mirror, shell.
- No maintained general-purpose "JSON feature tree to B-rep" library exists; every project
  defines its own schema. The ecosystem (build123d-mcp, agentcad, Zoo KCL, Onshape
  FeatureScript MCP) has converged on *code* as the LLM target plus a render/measure/validate
  feedback loop.
- Evidence on LLM accuracy: Text2CAD-Bench (2026) found frontier models produce far more
  invalid output when asked for DeepCAD-style command sequences than for CadQuery code
  (one model's invalid rate went from 13% to 67%). Fine-tuned models (CADmium) close the gap.
  Mitigations that work: a strict schema with rich error messages, the schema in the model's
  context, structured output decoding, and a validate/preview loop the model can iterate in.
- No evidence found that YAML beats JSON for this. YAML is nicer to hand-edit and allows
  comments; JSON has the better schema tooling. Both can share one schema.

### 2.4 Drawings, previews, STL quality

- 2D views: build123d `project_to_viewport` returns visible and hidden edges (OCCT HLR);
  `ExportSVG`/`ExportDXF` support layers and ISO/ANSI dashed line types. Section views via
  `section()`. No built-in dimensions or title block.
  Gotcha: `viewport_origin` is a camera *position*, not a direction. For a part not centred
  on the origin, place the camera at `part.center() + direction * 1000` and pass
  `look_at=part.center()`, otherwise "front" comes out oblique.
- Auto sheet layout with dimensions and title block: `draftwright` (alpha, **AGPL-3**, built on
  build123d) and `build123d-drafting-helpers` (dimensions, leaders, centrelines, title block).
- Preview PNG without a GPU: rasterise the SVG views (resvg-py worked here) or use
  `tcv_screenshots` (headless Chromium) for shaded renders. Avoid PyVista/pyrender on headless
  Windows.
- STL/3MF: build123d `Mesher` with `linear_deflection` and `angular_deflection`; validate
  watertightness with `trimesh`. STEP and STL are build artifacts, not diffable; commit the
  JSON plus a rendered SVG/PNG per part so reviews show visual diffs.

## 3. Recommended architecture

```
part.json  --validate-->  feature IR  --build123d-->  B-rep solid
   |                                                    |-- STEP / BREP
   |                                                    |-- STL / 3MF (+ watertight check)
   |                                                    |-- SVG / DXF / PDF views (top, front, side, iso, sections)
   |                                                    |-- PNG preview
   |-- (later) Fusion API script exporter  --> native timeline inside Fusion
   |-- (optional) build123d Python exporter --> escape hatch when the schema is not enough
```

- Language: Python 3.12, build123d engine, Pydantic models for the schema (one source for
  validation, JSON Schema export, and generated docs).
- CLI: `cadgen build part.json --step --stl --views top,front,side,iso --png`,
  `cadgen validate`, `cadgen schema`, `cadgen docs`.
- Error messages written for an LLM to act on: which feature id failed, why (for example
  "fillet radius 3 too large on edges selected by ... ; max feasible 2.4"), and the state
  of the model before that feature.
- Every build writes a preview so a human or agent can see what it got, because feature
  ordering mistakes are silent (the smoke test's side boss refilled two of the holes).

## 4. Draft schema shape (v0, to be argued over)

```jsonc
{
  "schema": "cadgen/0.1",
  "units": "mm",
  "params": { "w": 60, "h": 40, "t": 10, "hole_d": 5 },
  "features": [
    { "id": "base", "type": "extrude",
      "sketch": { "plane": "XY", "shapes": [ { "type": "rect", "w": "w", "h": "h" } ] },
      "distance": "t", "op": "add" },
    { "id": "corners", "type": "fillet", "radius": 5,
      "edges": { "of": "base", "parallel_to": "Z" } },
    { "id": "holes", "type": "extrude", "op": "cut", "through": true,
      "sketch": { "plane": { "face": { "of": "base", "normal": "+Z" } },
                  "shapes": [ { "type": "circle", "d": "hole_d",
                                "pattern": { "type": "polar", "count": 6, "radius": 15 } } ] } },
    { "id": "boss", "type": "extrude", "distance": 15,
      "sketch": { "plane": { "name": "XZ", "offset": 20 }, "shapes": [ { "type": "rect", "w": 20, "h": 10 } ] } },
    { "id": "top_break", "type": "chamfer", "length": 1,
      "edges": { "of_face": { "of": "boss", "normal": "+Z" }, "geom": "line" } }
  ],
  "outputs": { "step": true, "stl": { "tolerance": 0.01 },
               "drawing": { "views": ["top", "front", "right", "iso"], "hidden_lines": true } }
}
```

Open design points:

- **Selectors** are the hard part of any declarative CAD format. Proposed: reference topology
  by the feature that created it (`of: "base"`) combined with geometric filters (normal,
  parallel_to, geom type, nth by axis). Never by raw index.
- **Plane references**: named plane (`XY`, `XZ`, `YZ`) with optional offset and rotation,
  a face of a prior feature, or a custom origin + normal + x-direction.
- **Parameters and expressions**: plain numbers, or strings evaluated against `params`
  (a small safe expression evaluator, not Python `eval`).
- **Feature vocabulary v1**: extrude (add/cut/intersect, distance, through, symmetric, taper),
  revolve, fillet, chamfer, shell, mirror, linear/polar pattern (of sketch shapes or of
  features), hole (simple / counterbore / countersink, a 3D-printing convenience),
  boolean with another part.
- **Later**: loft, sweep, threads, text, assemblies (multi-part placement), constraints-based
  sketches.

## 5. Phased plan

| Phase | Deliverable | Notes |
|---|---|---|
| 0 | Repo skeleton, 3 to 5 target example parts written by hand, schema v0 agreed | The example parts drive the schema; pick things you actually want to print |
| 1 | MVP CLI: validate, build, STEP, STL, PNG preview | Pydantic schema, build123d backend, pytest over the example parts, generated schema docs |
| 2 | 2D drawings: multi-view SVG/DXF/PDF sheet, sections | Decide on dimensions and title block (AGPL draftwright vs own layout) |
| 3 | Fusion native exporter: JSON to Fusion API script (or add-in) | Gives an editable timeline; STEP remains the portable path |
| 4 | AI ergonomics: MCP server / Claude Code skill wrapping validate + build + render, LLM-oriented error messages, regression corpus | This is where the "AI generation" goal is won or lost |
| 5 | Assemblies, expressions library, threads/text, 3MF metadata | As needed |

## 6. Questions to settle before Phase 0

1. Fusion: is a STEP body (editable, but no sketch/extrude history) acceptable for the MVP,
   with the native-timeline exporter as a later phase?
2. Who writes the files: mostly an AI in Claude Code, you by hand, or both? This decides how
   terse the format is and whether YAML with comments is worth supporting alongside JSON.
3. Parameters and expressions in v1 (for example `"w - 2*wall"`), or fixed numbers only?
4. Single parts only for v1, or assemblies of positioned parts?
5. Drawings: views with hidden lines only, or also dimensions, sections, and a title block?
   Is an AGPL dependency acceptable if it saves the drafting work?
6. Python (build123d) is the recommendation. Any reason to prefer TypeScript (replicad) such
   as a browser viewer or the rest of your tooling?
7. Distribution: CLI only, or also an MCP server so Claude/Cursor can call it directly?
8. Units default to mm and the first example parts: what are the first three things you want
   to make with this?

## 7. Decisions (2026-09-12)

| Question | Decision |
|---|---|
| Fusion feature history | Wanted eventually. STEP body for the MVP; JSON-to-Fusion-API exporter later. This rules out "AI writes Python" as the stored format, because a feature tree is needed to regenerate a timeline. |
| Who writes the files | Mostly AI, but Scott wants to read, understand, and edit. Readability is a hard requirement. |
| Parameters / expressions | Revised 2026-09-12: an optional `params` block with names and tiny arithmetic is in v1 after all. Scott's real requirement is that the labelled sketch dimensions (L, D, h, b, x, y) are the values in the file and are easy to change; point coordinates are not intuitive. Names in one block plus feature fields that reference them is the cheapest way to get that. Literal numbers remain valid everywhere. |
| Assemblies | Single parts in Phase 1. |
| Drawings | Dimensions must be an option. AGPL dependency (draftwright) is acceptable. |
| Language | Python + build123d (no objection raised to Python). |
| Distribution | CLI only for now. |
| First part | A board profile (stepped tongue on the left, groove, bottom notch on the right), extruded. Sketch labels: L, D, h, b, x, y. Unlabelled: groove/notch width (derived as L - D - x) and the right lip width. See `examples/board_profile.json` (draft schema) and `experiments/board_profile_build123d.py` (working code version). |

Schema consequence of the first part: the board is modelled as a rectangle plus three named
cuts so each sketch label appears exactly once (`examples/board_profile.json`). A closed
`path` of relative moves is offered as the alternative (`examples/board_profile_path.json`).
Full draft in `docs/schema-v0.md`.

Escape route if JSON is abandoned: the interpreter will be written as a Python builder API
(one function per feature type) that the JSON merely calls. If Scott switches to scripts,
that API becomes the script format and nothing is thrown away.

## 8. Phase 0 status

- [x] git repo, package skeleton (`cadgen/`, `pyproject.toml`, stub CLI), venv
- [x] schema v0 draft: `docs/schema-v0.md`
- [x] example parts: board_profile (two styles), board_profile_filleted, l_bracket, spacer, enclosure
- [x] schema open questions: rect + cuts for examples (decided); the rest defaulted as documented
- [x] Phase 1 started 2026-09-12

## 9. Phase 1 status

- [x] Pydantic schema (`cadgen/schema.py`), `cadgen schema` prints JSON Schema
- [x] expression evaluator with params (`cadgen/expr.py`)
- [x] planes: named, offset, face, explicit (`cadgen/planes.py`)
- [x] sketches: rect, circle, slot, polygon, points, path; add/subtract; linear/polar/grid patterns
- [x] features: extrude, revolve, fillet, chamfer, shell, hole, mirror, feature-level pattern
- [x] selectors with feature tracking (`of`), convex/concave, and candidate listings on failure
- [x] outputs: STEP, STL, 3MF, hidden-line SVG/DXF views, PNG previews
- [x] CLI: validate, build, info, schema; all six examples build; 28 tests
- [ ] `cadgen export --python` (emit the equivalent build123d script)
- [ ] auto line weight / scale for small parts in SVG views
- [ ] more example parts from real use, to shake out selector ergonomics

## 10. Phase 2 status (2026-09-12)

- [x] annotated drawing sheet via draftwright 0.4.28: third-angle views + iso, automatic
      dimensions, hole/slot/radius/chamfer callouts, bolt-circle notes, title block; SVG/PDF/DXF
- [x] section views (own renderer): cut at any PlaneRef, cut faces filled grey, PNG preview
- [x] `title_block`, `projection`, `page`, `sheet`, `dimensions`, `sections` in `outputs.drawing`
- [x] `--sheet/--no-sheet` CLI override; line weights scale with part size
- [x] 32 tests; all examples build with sheets and sections

Costs accepted: draftwright is AGPL-3 and pins build123d to 0.10 on Python 3.12 (0.11 only on
Python 3.13+). All cadgen tests pass on 0.10. Moving the venv to Python 3.13 lifts the pin.

Known gaps: sheet dimensions are automatic only (no way to say "dimension L here"); sections
are separate files rather than placed on the sheet; no hatching, grey fill instead; draftwright
takes 3 to 5 s per sheet.

## 11. Phase 3 status (2026-09-12)

- [x] `cadgen export-fusion part.json` writes `out/<name>_fusion/` (script + manifest)
- [x] user parameters with expressions and inferred units (mm vs unitless)
- [x] sketches on named, offset, face and explicit planes; profiles matched by area + centroid
- [x] extrude, revolve, fillet, chamfer, shell, hole (simple/counterbore/countersink), mirror,
      rectangular and circular patterns as native timeline features
- [x] edges/faces matched at runtime by bounding-box centre and length/area recorded at export
- [x] tests execute every example's script against a fake `adsk` API (`tests/fake_adsk.py`)
- [ ] verified inside Fusion itself (needs a person to run the script; Fusion has no headless mode)
- [ ] sketch dimensional constraints driven by parameters (sketch geometry is numeric today)
- [ ] extrude taper

See `docs/fusion-export.md`.

## 12. Phase 4 status (2026-09-12)

- [x] Claude Code skill `.claude/skills/cadgen/SKILL.md`: workflow (validate, build, read
      report.json, look at the PNGs), one-page format reference, recipes, gotchas
- [x] JSON Schema committed at `schema/cadgen-0.1.schema.json` (`cadgen schema -o`), test keeps
      it current; parts may carry `"$schema"` for editor validation
- [x] `out/<name>/report.json` on every build: volume, bbox, params, per-feature timings, files
- [x] `cadgen info --json`
- [x] `cadgen export-python`: standalone build123d script; tested to rebuild every example to
      the same volume
- [x] regression corpus grown to 9 examples (knob: revolve with arc + polar feature pattern;
      plate_inch: inch units + countersinks; hex_standoff: blind and counterbored holes from
      opposite faces)
- [ ] MCP server (not requested; the CLI + skill is the agent interface for now)
