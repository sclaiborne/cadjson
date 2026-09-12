# CAD-Generator (cadgen)

CAD as text. A part is a JSON file describing a feature tree (sketch, extrude, cut, revolve,
fillet, chamfer, shell, pattern, mirror). `cadgen` validates it and builds:

- STEP (opens in Fusion 360, FreeCAD, anything);
- STL / 3MF for 3D printing;
- 2D drawings (front / top / right / iso, hidden lines, optional dimensions) as SVG / DXF / PDF;
- PNG previews for review and for AI feedback loops.

Engine: [build123d](https://build123d.readthedocs.io/) on the OpenCascade kernel.

## Status

Phase 0 (schema and examples). Nothing builds from JSON yet. See [PLANNING.md](PLANNING.md)
for the option survey and plan, and [docs/schema-v0.md](docs/schema-v0.md) for the format.

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
