# CAD-Generator (cadgen)

CAD as text. A part is a JSON file describing a feature tree (sketch, extrude, cut, revolve,
fillet, chamfer, shell, pattern, mirror). `cadgen` validates it and builds:

- STEP (opens in Fusion 360, FreeCAD, anything);
- STL / 3MF for 3D printing;
- 2D drawings (front / top / right / iso, hidden lines, optional dimensions) as SVG / DXF / PDF;
- PNG previews for review and for AI feedback loops.

Engine: [build123d](https://build123d.readthedocs.io/) on the OpenCascade kernel.

## Status

Phases 1 and 2 done: the feature tree builds and exports, and drawings include per-view
SVG/DXF with hidden lines, section views, and a fully annotated sheet (dimensions, callouts,
title block) as PDF/SVG/DXF. See [PLANNING.md](PLANNING.md) for the option survey and plan,
and [docs/schema-v0.md](docs/schema-v0.md) for the format.

Note: the sheet generator (draftwright, AGPL-3) pins build123d to 0.10 on Python 3.12.

## Usage

```bash
.venv\Scripts\cadgen validate examples\board_profile.json
.venv\Scripts\cadgen build examples\board_profile.json
.venv\Scripts\cadgen build examples\*.json --views front,top,right,iso
.venv\Scripts\cadgen info examples\board_profile.json     # list faces and edges, to write selectors
.venv\Scripts\cadgen schema > cadgen.schema.json
```

`build` writes to `out/<name>/`: STEP, STL, one SVG per view, section views, the drawing
sheet, and PNG previews. Each part's `outputs` block sets the defaults; `--step/--no-step`,
`--stl/--no-stl`, `--png/--no-png`, `--sheet/--no-sheet`, `--views` and `-o` override them.

## Layout

```
cadgen/        package (schema models, build123d backend, CLI)
docs/          schema reference and design notes
examples/      hand-written example parts in the draft schema
experiments/   throwaway scripts used to verify the toolchain
tests/
```

## Setup (Windows, Python 3.11+)

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```
