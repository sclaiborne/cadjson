# Python export

`cadjson export-python part.json` writes `out/<name>/<name>_build123d.py`, a build123d script
that rebuilds the same solid. Use it when the format cannot express something: export, then
carry on in Python.

```
cadjson export-python part.json            # plain build123d, needs only `pip install build123d`
cadjson export-python part.json --cadgen   # a text-to-cad model: out/<name>/<name>.py
```

## What the script looks like

```python
# --- parameters (mm)
width = 60
base_len = 40
t = 4
hole_d = 5.5

def l_bracket():
    part = None

    # --- profile (extrude)
    sk = None
    piece = make_face(_path((0, 0), [('right', base_len), ('up', t), ('left', base_len - t), ...]))
    ...
    tool = extrude(Plane.YZ * sk, amount=width)
    ...
    return part

if __name__ == "__main__":
    part = l_bracket()
    export_step(part, "l_bracket.step")
```

- **Params are named constants** at the top, in an order where each is defined before it is
  used. A param that is an expression stays one (`inner = outer_d - 2 * wall`).
- **Every dimension written in the part file is the same expression in the script**: sketch
  sizes and positions, path segments, extrude distances, fillet radii, hole diameters and
  positions, pattern counts and spacings, offset planes. Change `width = 60` to `80` and run.
- **Inch parts** keep their params in inches; lengths are multiplied by build123d's `IN` where
  they are used.
- **Small helpers** (`_path`, `_linear`, `_polar`, `_grid`, `_edges`, ...) are included only when
  the part needs them; each mirrors the cadjson feature it replaces.
- A param whose name would clash with a build123d name, a Python keyword or the script's own
  variables gets a trailing underscore, with a comment naming the original.

## What stays a number

Values cadjson works out from the geometry are recorded when you export:

- faces and edges picked by selectors (fillets, chamfers, shell openings, threads) are found
  again by their bounding-box centre and length or area;
- planes on faces, `through` depths, revolve axes and feature mirror/pattern axes;
- standard hole sizes (`"standard": "M3"`), with a comment giving the designation.

So a large change can move an edge that a fillet was recorded against. The script then stops
with `no edge of length ... any more`; re-export from the JSON, or edit the selection.

## `--cadgen`: text-to-cad models

[text-to-cad](https://github.com/earthtojake/text-to-cad) (the `cadgen` package) runs build123d
scripts declared with decorators. With `--cadgen` the script becomes one of those models:

```python
from cadgen import build123d as bd
from cadgen import step, stl

@stl
@step
def l_bracket():
    ...
    tool = bd.extrude(bd.Plane.YZ * sk, amount=width)
    ...
    return part

if __name__ == "__main__":
    l_bracket()
```

- `@step` always; `@stl` and `@threemf` when the part's `outputs` ask for STL or 3MF. cadgen
  writes the files beside the script, so the script is named after the part.
- build123d names go through cadgen's lazy `bd` module, so re-running an unchanged model returns
  without importing the CAD kernel.
- Mesh tolerances are not carried over: cadgen's are relative to part size, cadjson's are in mm.
- Assemblies are not exported (the same limit as the plain script). Threads need `bd_warehouse`
  installed next to cadgen.

cadjson itself does not need cadgen. To run the models in the same environment:

```
pip install "cadjson[cadgen] @ git+https://github.com/sclaiborne/CAD-Generator"
```

The extra pins `cadgen<0.6` because text-to-cad does not keep backwards compatibility between
minor versions. Checked against cadgen 0.5.1 on build123d 0.10 and 0.11.

You do not need `--cadgen` to use text-to-cad's inspection, printability or slicing tools on a
cadjson part: they read STEP and STL, which `cadjson build` already writes.

## For plugin authors

A plugin's `python_emitter` receives the exporter. Emit lines with `exporter.emit(...)`; they are
indented into the model function. Use `exporter.length(dim)` and `exporter.num(dim)` to write a
field as its expression over the param names (lengths are scaled for inch parts), rather than
formatting the resolved value.
