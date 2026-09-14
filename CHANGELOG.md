# Changelog

## Unreleased

- Fix: the assembly interference check missed overlaps made of several separate pieces (four
  bosses through a cover plate). build123d returns such an intersection as a `ShapeList`, which
  has no `.volume`; the check swallowed the error and reported no overlap. The volume is now the
  sum over every piece, and a boolean that fails is reported as an error naming the two parts
  instead of being silently treated as no overlap.

## 0.3.2 (2026-09-13)

- Viewer: hidden-lines mode (edges behind material drawn dashed), x-ray, part checkboxes with
  show all / hide all / only selected, panel layout that fits.

## 0.3.1 (2026-09-13)

- HTML viewer: `outputs.viewer`, `cadjson build --viewer`, `cadjson view part.json`. One file
  with the meshes embedded; three.js from a CDN; orbit and zoom, one colour per placed part
  with hide/show, X/Y/Z section sliders, edges toggle, named views.

## 0.3.0 (2026-09-13)

- Assemblies positioned by **mates**: `coaxial`, `against`, `flush`, `parallel` between faces of
  placed parts, the host body or the XY/XZ/YZ/X/Y/Z datums. Mates apply in order and leave the
  rest of `at` / `rotate` alone; `report.json` lists the derived pose of every part.
- Assembly **checks**: interference between placed parts (warn, error or off) and named
  clearance limits, in `report.json` and the build summary.
- Face selector `radius` (cylinders and cones), for picking hole walls and shanks.
- Example `assembly_mated.json`.
- `export-python` keeps params by name: they are constants at the top of the script, and every
  dimension written in the part file is the same expression in the script (sketches, paths,
  patterns, holes, fillets, extrudes). The model is a function; helpers are included only when
  used. See docs/python-export.md.
- `export-python --cadgen` writes a text-to-cad model (`@step`, plus `@stl` / `@threemf` from the
  part's outputs) using cadgen's lazy build123d import. Optional extra: `cadjson[cadgen]`.

## 0.2.0 (2026-09-12)

- Renamed from cadgen to **cadjson** (package, CLI, skill, schema string `cadjson/0.1`).
  Files that still say `"schema": "cadgen/0.1"` are accepted.
- Dimensioned drawing sheets are an optional extra: `pip install cadjson[sheets]` (draftwright,
  AGPL-3). `--sheet` without it explains what to install.
- MIT license file, CI on Windows and Linux, contributor notes.

## 0.1.1 (2026-09-12)

- `compare` dependencies (trimesh, rtree) are runtime dependencies.

## 0.1.0 (2026-09-12)

First tagged release: feature tree (extrude, revolve, fillet, chamfer, shell, holes with
standard thread sizes, real ISO threads, mirror, patterns, loft, sweep, text, imported parts,
assemblies), STEP/STL/3MF output, per-view SVG/DXF with hidden lines, section views,
annotated drawing sheets, PNG previews, `report.json`, Fusion 360 script export, standalone
build123d script export, `compare` against a reference mesh, `extends` variants, plugin
registry, Claude Code skill, `init` for parts repositories.
