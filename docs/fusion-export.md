# Fusion 360 export

`cadjson export-fusion part.json` writes `out/<name>_fusion/` containing a Fusion 360 script
and its manifest. The script rebuilds the part inside Fusion with a **native parametric
timeline**: sketches, extrudes, revolves, fillets, chamfers, shells, holes, mirrors and
patterns, each editable in Fusion as if drawn by hand.

## Running it

1. In Fusion 360 open Utilities > Add-Ins > Scripts and Add-Ins (Shift+S).
2. On the Scripts tab press the green **+** next to "My Scripts" and choose the folder
   `out/<name>_fusion/`.
3. Select the script and press Run. A new design is created and built.

Fusion has no headless mode, so cadjson cannot run this for you. The generated script is
checked in cadjson's tests by executing it against a fake `adsk` API.

## What maps to what

| cadjson | Fusion |
|---|---|
| `params` | User parameters, with their expressions (`g = L - D - x` stays an expression) |
| named planes `XY`, `XZ`, `YZ` | the root construction planes |
| offset, face and explicit planes | a construction plane created from origin and normal |
| sketch shapes and patterns | sketch lines, arcs and circles (numeric, see below) |
| `extrude` | Extrude feature; `distance` expression, `through` as symmetric through-all |
| `revolve` | Revolve about a construction line drawn in the sketch |
| `fillet` / `chamfer` | Fillet / Chamfer features on the matched edges |
| `shell` | Shell feature (faces to remove matched, or the whole body) |
| `hole` | Hole feature per point; simple, counterbore or countersink |
| `mirror` | Mirror feature of the listed features (or the bodies) |
| `pattern` | Rectangular or Circular pattern of the listed features |

## Units and expressions

Fusion needs units. cadjson infers, for every parameter, whether it is a length (used for a
size, distance, radius, position) or a plain number (used as a count or an angle), and
creates it with unit `mm` or no unit. Expressions are rewritten so a bare literal added to a
length gets `mm` (`h - 4` becomes `h - 4 mm`) while a literal multiplier stays bare
(`2 * wall`). Where an expression cannot be typed (a length times a length, say) the script
uses the evaluated number and lists it under "Notes" at the top of the file.

## How edges and faces are found

cadjson resolves every selector while building the part with build123d, and records the
bounding-box centre and the length (edges) or area (faces) of what was selected. The script
matches those against the Fusion body at the same point in the timeline. Both kernels build
the same geometry from the same features, so this is reliable for prismatic parts; if a
match fails the script reports which feature and what it was looking for.

## Limits

- Sketch geometry is numeric. The user parameters drive feature values (extrude distances,
  radii, thicknesses, hole sizes) but not sketch dimensions, so changing `L` in Fusion does
  not resize the sketch rectangle. Sketch dimensional constraints are a later step.
- `taper` on extrude is not exported.
- Linear patterns along a custom vector create a construction axis; named axes use the root
  axes.
- Only features cadjson knows about are exported; there is no round trip from Fusion.
