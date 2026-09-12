import time
from build123d import *
t0=time.time()
with BuildPart() as p:
    Box(60, 40, 10)
    fillet(p.edges().filter_by(Axis.Z), radius=5)            # round the 4 corners
    with BuildSketch(p.faces().sort_by(Axis.Z)[-1]):         # top face
        with PolarLocations(15, 6):
            Circle(2.5)
    extrude(amount=-10, mode=Mode.SUBTRACT)                  # cut through
    with BuildSketch(Plane.XZ.offset(-20)):                  # boss on side plane
        Rectangle(20, 10)
    extrude(amount=15)
    chamfer(p.faces().sort_by(Axis.Z)[-1].edges().filter_by(GeomType.LINE), length=1)
part=p.part
print("volume", round(part.volume,1), "valid", part.is_valid, "build", round(time.time()-t0,2),"s")
export_step(part, "smoke.step")
export_stl(part, "smoke.stl", tolerance=0.01, angular_tolerance=0.1)
for name, vp, up in [("top",(0,0,1),(0,1,0)), ("front",(0,-1,0),(0,0,1)), ("side",(1,0,0),(0,0,1)), ("iso",(1,-1,1),(0,0,1))]:
    vis, hid = part.project_to_viewport(viewport_origin=vp, viewport_up=up)
    ex=ExportSVG(scale=2)
    ex.add_layer("vis", line_weight=0.5)
    ex.add_layer("hid", line_weight=0.25, line_type=LineType.HIDDEN)
    ex.add_shape(vis, layer="vis"); ex.add_shape(hid, layer="hid")
    ex.write(f"view_{name}.svg")
dx=ExportDXF(); dx.add_layer("vis"); dx.add_shape(part.project_to_viewport((0,0,1))[0], layer="vis"); dx.write("view_top.dxf")
print("total", round(time.time()-t0,2),"s")
