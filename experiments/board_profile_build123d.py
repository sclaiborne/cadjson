# Board profile from Scott's sketch, extruded. Dimensions in mm (sample values).
from build123d import *
import resvg_py, re

L, D, h, b, x, y = 100, 80, 20, 4, 12, 6   # labelled in the sketch
e = 8                                        # right lip width (unlabelled in sketch)
g = L - D - x                                # groove / notch width, derived (=8 here)
depth = 40                                   # extrude distance

pts = [(0, 0), (L - e - g, 0), (L - e - g, y), (L - e, y), (L - e, 0), (L, 0),
       (L, h), (L - D, h), (L - D, y), (x, y), (x, h - b), (0, h - b), (0, 0)]

with BuildPart() as p:
    with BuildSketch(Plane.XZ):              # profile in XZ, extrude along -Y
        with BuildLine():
            Polyline(pts)
        make_face()
    extrude(amount=depth)

part = p.part
print("valid", part.is_valid, "volume", round(part.volume, 1))
export_step(part, "board.step"); export_stl(part, "board.stl")
for name, vp, up in [("front", (0, -1, 0), (0, 0, 1)), ("iso", (1, -1, 1), (0, 0, 1))]:
    c = part.center(); vis, hid = part.project_to_viewport(viewport_origin=c + Vector(*vp) * 1000, viewport_up=up, look_at=c)
    ex = ExportSVG(scale=3); ex.add_layer("vis", line_weight=0.5)
    ex.add_layer("hid", line_weight=0.25, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="vis"); ex.add_shape(hid, layer="hid"); ex.write(f"board_{name}.svg")
    s = re.sub(r'(width|height)="([\d.]+)mm"', r'\1="\2"', open(f"board_{name}.svg").read(), count=2)
    open(f"board_{name}.png", "wb").write(bytes(resvg_py.svg_to_bytes(svg_string=s, width=600, background="white")))
