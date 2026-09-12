"""Standard thread designations: ISO metric coarse and fine, and unified (UNC/UNF) sizes.

Values in mm. Clearance holes follow ISO 273 (close / medium) and ASME B18.2.8 (close /
normal); tap drills are the usual major minus pitch (metric) and standard tables (unified).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cadjson.errors import CadjsonError

IN = 25.4


@dataclass(frozen=True)
class ThreadSpec:
    designation: str
    major: float
    pitch: float
    tap_drill: float
    clearance_close: float
    clearance_medium: float

    @property
    def minor(self) -> float:
        # ISO basic minor diameter of the internal thread: D - 1.0825 P
        return self.major - 1.0825 * self.pitch

    def hole_diameter(self, fit: str) -> float:
        return {"tap": self.tap_drill, "close": self.clearance_close, "medium": self.clearance_medium}[fit]


# metric coarse: major -> (pitch, tap drill, close clearance, medium clearance)
_METRIC = {
    1.6: (0.35, 1.25, 1.7, 1.8),
    2.0: (0.4, 1.6, 2.2, 2.4),
    2.5: (0.45, 2.05, 2.7, 2.9),
    3.0: (0.5, 2.5, 3.2, 3.4),
    3.5: (0.6, 2.9, 3.7, 3.9),
    4.0: (0.7, 3.3, 4.3, 4.5),
    5.0: (0.8, 4.2, 5.3, 5.5),
    6.0: (1.0, 5.0, 6.4, 6.6),
    8.0: (1.25, 6.8, 8.4, 9.0),
    10.0: (1.5, 8.5, 10.5, 11.0),
    12.0: (1.75, 10.2, 13.0, 13.5),
    14.0: (2.0, 12.0, 15.0, 15.5),
    16.0: (2.0, 14.0, 17.0, 17.5),
    20.0: (2.5, 17.5, 21.0, 22.0),
    24.0: (3.0, 21.0, 25.0, 26.0),
}

# unified: name -> (major in, threads per inch, tap drill in, close clearance in, normal clearance in)
_UNIFIED = {
    "#2-56": (0.086, 56, 0.0700, 0.0890, 0.0960),
    "#4-40": (0.112, 40, 0.0890, 0.1160, 0.1285),
    "#6-32": (0.138, 32, 0.1065, 0.1440, 0.1495),
    "#8-32": (0.164, 32, 0.1360, 0.1695, 0.1770),
    "#10-24": (0.190, 24, 0.1495, 0.1960, 0.2010),
    "#10-32": (0.190, 32, 0.1590, 0.1960, 0.2010),
    "1/4-20": (0.250, 20, 0.2010, 0.2570, 0.2660),
    "1/4-28": (0.250, 28, 0.2130, 0.2570, 0.2660),
    "5/16-18": (0.3125, 18, 0.2570, 0.3230, 0.3320),
    "3/8-16": (0.375, 16, 0.3125, 0.3860, 0.3970),
    "1/2-13": (0.500, 13, 0.4219, 0.5156, 0.5312),
}

_M_RE = re.compile(r"^M(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?$", re.IGNORECASE)


def thread_spec(designation: str) -> ThreadSpec:
    d = designation.strip().replace(" ", "")
    m = _M_RE.match(d)
    if m:
        major = float(m.group(1))
        if major not in _METRIC:
            raise CadjsonError(f"unknown metric size {designation!r}; known: " + ", ".join(f"M{k:g}" for k in _METRIC))
        pitch, tap, close, medium = _METRIC[major]
        if m.group(2):  # fine pitch given
            pitch = float(m.group(2))
            tap = round(major - pitch, 2)
        return ThreadSpec(f"M{major:g}x{pitch:g}", major, pitch, tap, close, medium)
    if d in _UNIFIED:
        major, tpi, tap, close, normal = _UNIFIED[d]
        return ThreadSpec(d, major * IN, IN / tpi, tap * IN, close * IN, normal * IN)
    raise CadjsonError(
        f"unknown thread {designation!r}",
        hints=["metric: M3, M6, M8x1 (fine)", "unified: " + ", ".join(_UNIFIED)],
    )
