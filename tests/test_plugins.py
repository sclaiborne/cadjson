"""Plugin registry: the example gear plugin loaded through CADJSON_PLUGINS."""

import json
import sys
from pathlib import Path
from typing import Literal

import pytest

from cadjson.build import build_document, load_document, parse_document
from cadjson.errors import CadjsonError
from cadjson.plugins import Registry, registry
from cadjson.schema import FeatureBase, json_schema

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "examples" / "plugins" / "cadjson_gear"


@pytest.fixture
def gear_plugin(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    monkeypatch.setenv("CADJSON_PLUGINS", "cadjson_gear")
    registry.reset()
    yield registry
    registry.reset()


def test_gear_plugin_builds(gear_plugin, tmp_path):
    doc = load_document(ROOT / "examples" / "plugins" / "gear_demo.json")
    assert doc.features[0].type == "gear"
    part = build_document(doc).part
    assert part.is_valid
    ro = 2 * (24 + 2) / 2  # tip radius
    assert part.bounding_box().size.X == pytest.approx(2 * ro, abs=0.05)
    assert part.bounding_box().size.Z == pytest.approx(12)


def test_plugin_type_in_schema_and_errors(gear_plugin):
    assert "gear" in json.dumps(json_schema(with_plugins=True))
    assert "gear" not in json.dumps(json_schema())
    with pytest.raises(CadjsonError) as ei:
        parse_document({"schema": "cadjson/0.1", "name": "x", "features": [{"id": "a", "type": "sprocket"}]})
    assert "plugin feature types available: gear" in str(ei.value)


def test_unknown_type_without_plugins():
    registry.reset()
    with pytest.raises(CadjsonError, match="does not match schema"):
        parse_document({"schema": "cadjson/0.1", "name": "x", "features": [{"id": "a", "type": "gear", "teeth": 8, "module": 1, "thickness": 1}]})


def test_plugin_must_produce_geometry():
    reg = Registry()

    class Nothing(FeatureBase):
        type: Literal["nothing"]

    @reg.feature(Nothing)
    def build_nothing(feat, api):
        pass

    from cadjson import build as build_mod

    monkey = build_mod.registry if hasattr(build_mod, "registry") else None
    import cadjson.plugins as plugins_mod

    saved = plugins_mod.registry
    plugins_mod.registry = reg
    reg.loaded = True
    try:
        doc = parse_document({"schema": "cadjson/0.1", "name": "x", "features": [{"id": "n", "type": "nothing"}]})
        with pytest.raises(CadjsonError, match="did not produce geometry"):
            build_document(doc)
    finally:
        plugins_mod.registry = saved


def test_registry_rejects_builtin_clash():
    reg = Registry()

    class Fake(FeatureBase):
        type: Literal["extrude"]

    with pytest.raises(CadjsonError, match="clashes"):
        reg.feature(Fake)(lambda f, a: None)
