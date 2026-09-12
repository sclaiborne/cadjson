"""A minimal stand-in for the Fusion 360 `adsk` package, enough to execute generated scripts.

Geometry classes are real (points, vectors, value inputs, collections) so the script's own
arithmetic runs; everything else is a permissive recorder that accepts any call.
"""

from __future__ import annotations

import math
import sys
import types
from unittest.mock import MagicMock


class Point3D:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)

    def distanceTo(self, other):
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


class Vector3D:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    @classmethod
    def create(cls, x, y, z):
        return cls(x, y, z)

    def dotProduct(self, o):
        return self.x * o.x + self.y * o.y + self.z * o.z

    def crossProduct(self, o):
        return Vector3D(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)


class ValueInput:
    created: list[str] = []

    def __init__(self, text):
        self.text = text

    @classmethod
    def createByString(cls, text):
        cls.created.append(text)
        return cls(text)


class ObjectCollection:
    def __init__(self):
        self.items = []

    @classmethod
    def create(cls):
        return cls()

    def add(self, item):
        self.items.append(item)

    def item(self, i):
        return self.items[i]

    @property
    def count(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


class FakeSketch(MagicMock):
    """Identity mapping between model and sketch space, XY orientation."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.xDirection = Vector3D(1, 0, 0)
        self.yDirection = Vector3D(0, 1, 0)
        self.modelToSketchSpace = lambda p: p
        self.sketchToModelSpace = lambda p: p


class Recorder(MagicMock):
    """MagicMock whose attribute access is recorded so tests can assert which features ran."""

    calls: list[str] = []

    def _mock_call(self, *args, **kwargs):
        Recorder.calls.append(self._extract_mock_name())
        return super()._mock_call(*args, **kwargs)


def install() -> types.ModuleType:
    """Install fake adsk, adsk.core, adsk.fusion into sys.modules and return adsk."""
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    core.Point3D = Point3D
    core.Vector3D = Vector3D
    core.ValueInput = ValueInput
    core.ObjectCollection = ObjectCollection
    core.Plane = MagicMock()
    core.InfiniteLine3D = MagicMock()
    core.DocumentTypes = MagicMock()

    root = Recorder(name="root")
    root.sketches.add = lambda plane: FakeSketch(name="sketch")
    root.bRepBodies = []
    design = Recorder(name="design")
    design.rootComponent = root
    app = Recorder(name="app")
    app.activeProduct = design
    core.Application = MagicMock()
    core.Application.get.return_value = app
    fusion.Design = MagicMock()
    fusion.Design.cast = lambda product: product
    fusion.FeatureOperations = MagicMock()
    fusion.ExtentDirections = MagicMock()
    fusion.PatternDistanceType = MagicMock()
    fusion.CalculationAccuracy = MagicMock()
    fusion.DesignTypes = MagicMock()

    adsk.core = core
    adsk.fusion = fusion
    sys.modules["adsk"] = adsk
    sys.modules["adsk.core"] = core
    sys.modules["adsk.fusion"] = fusion
    Recorder.calls.clear()
    ValueInput.created.clear()
    adsk._app = app
    return adsk
