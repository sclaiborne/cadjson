"""Example cadgen plugin: a `gear` feature (straight-sided spur gear, good enough for prints).

Install it (`pip install -e examples/plugins/cadgen_gear`) or point CADGEN_PLUGINS at it while
developing, then write:

    { "id": "g", "type": "gear", "teeth": 20, "module": 2, "thickness": 6, "bore": 5 }
"""

from __future__ import annotations

import math
from typing import Literal

from build123d import Circle, Location, Polygon
from pydantic import Field

from cadgen.schema import Dim, FeatureBase, Op, PlaneRef


class Gear(FeatureBase):
    type: Literal["gear"]
    teeth: int = Field(ge=6)
    module: Dim = Field(description="gear module in document units (pitch diameter = module x teeth)")
    thickness: Dim
    bore: Dim = Field(0, description="centre hole diameter, 0 for none")
    plane: PlaneRef = "XY"
    op: Op = "add"


def _gear_sketch(teeth: int, m: float, bore: float):
    rp = m * teeth / 2          # pitch radius
    ro = rp + m                 # outer (tip) radius
    rr = rp - 1.25 * m          # root radius
    tooth_pitch = math.pi * m
    base_w, tip_w = 0.65 * tooth_pitch, 0.35 * tooth_pitch
    tooth = Polygon((rr - 0.2, -base_w / 2), (ro, -tip_w / 2), (ro, tip_w / 2), (rr - 0.2, base_w / 2), align=None)
    sketch = Circle(rr)
    for i in range(teeth):
        sketch = sketch + Location((0, 0, 0), 360 * i / teeth) * tooth
    if bore > 0:
        sketch = sketch - Circle(bore / 2)
    return sketch


def register(registry):
    @registry.feature(Gear)
    def build_gear(feat: Gear, api):
        m = api.length(feat.module)
        bore = api.length(feat.bore)
        if bore >= (m * feat.teeth / 2 - 1.25 * m) * 2:
            raise api.error(f"bore {bore:g} is larger than the root diameter")
        sketch = _gear_sketch(feat.teeth, m, bore)
        api.extrude(sketch, api.plane(feat.plane), distance=api.length(feat.thickness), op=feat.op)
