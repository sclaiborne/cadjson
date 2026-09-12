# Board profile with rounded corners: convex (outer) corners r=2, concave (inner) corners r=1.
from build123d import *
import resvg_py, re

L, D, h, b, x, y, e = 100, 80, 20, 4, 12, 6, 8
g = L - D - x
depth = 40
outer_r, inner_r = 2, 1

pts = [(0, 0), (L - e - g, 0), (L - e - g, y), (L - e, y), (L - e, 0), (L, 0),
       (L, h), (L - D, h), (L - D, y), (x, y), (x, h - b), (0, h - b)]

def convex(i):  # CCW polygon: positive cross product at vertex i means a convex corner
    (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
    return (bx - ax) * (cy - by) - (by - ay) * (cx - bx) > 0

with BuildPart() as p:
    with BuildSketch(Plane.XZ):
        with BuildLine():
            Polyline(pts + [pts[0]])
        make_face()
    extrude(amount=depth)
    along_y = p.edges().filter_by(Axis.Y)
    def edge_at(u, v):  # extrusion runs toward -Y, so edge centres sit at y = -depth/2
        return along_y.sort_by_distance((u, -depth / 2, v))[0]
    outer = [edge_at(*pt) for i, pt in enumerate(pts) if convex(i)]
    inner = [edge_at(*pt) for i, pt in enumerate(pts) if not convex(i)]
    print(len(outer), "convex edges,", len(inner), "concave edges")
    fillet(outer, radius=outer_r)
    inner = [p.edges().filter_by(Axis.Y).sort_by_distance((u, -depth / 2, v))[0]
             for i, (u, v) in enumerate(pts) if not convex(i)]   # reselect: topology changed
    fillet(inner, radius=inner_r)

part = p.part
print("valid", part.is_valid, "volume", round(part.volume, 1))
c = part.center()
vis, hid = part.project_to_viewport(viewport_origin=c + Vector(0, -1, 0) * 1000, viewport_up=(0, 0, 1), look_at=c)
ex = ExportSVG(scale=4); ex.add_layer("vis", line_weight=0.4); ex.add_shape(vis, layer="vis"); ex.write("out/board_profile_filleted_front.svg")
s = re.sub(r'(width|height)="([\d.]+)mm"', r'\1="\2"', open("out/board_profile_filleted_front.svg").read(), count=2)
open("out/board_profile_filleted_front.png", "wb").write(bytes(resvg_py.svg_to_bytes(svg_string=s, width=800, background="white")))
