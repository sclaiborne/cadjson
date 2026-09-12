# Changelog

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
