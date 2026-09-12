"""`extends`: variants that inherit a base part."""

import json
from pathlib import Path

import pytest

from cadjson.build import build_document, load_document, load_raw, merge_documents, write_outputs
from cadjson.errors import CadjsonError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _write(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


BOX = {
    "schema": "cadjson/0.1", "name": "box",
    "params": {"w": 20, "h": 10, "t": 5},
    "features": [
        {"id": "body", "type": "extrude", "distance": "t",
         "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": "w", "h": "h"}]}},
        {"id": "corners", "type": "fillet", "radius": 1, "edges": {"of": "body", "parallel_to": "Z"}},
    ],
    "outputs": {"step": False, "png": False},
}


def test_variant_overrides_params(tmp_path):
    _write(tmp_path / "box.json", BOX)
    child = _write(tmp_path / "box_tall.json", {"schema": "cadjson/0.1", "name": "box_tall", "extends": "box.json",
                                                  "params": {"t": 20}})
    doc = load_document(child)
    assert doc.name == "box_tall"
    assert [f.id for f in doc.features] == ["body", "corners"]
    assert doc.outputs.step is False  # inherited
    part = build_document(doc).part
    assert part.bounding_box().size.Z == pytest.approx(20)


def test_replace_drop_and_append(tmp_path):
    _write(tmp_path / "box.json", BOX)
    child = _write(tmp_path / "box2.json", {
        "schema": "cadjson/0.1", "name": "box2", "extends": "box.json",
        "drop": ["corners"],
        "features": [
            {"id": "body", "type": "extrude", "distance": "t",
             "sketch": {"plane": "XY", "shapes": [{"type": "circle", "d": "w"}]}},
            {"id": "hole", "type": "hole", "face": "top", "at": [[0, 0]], "diameter": 4, "through": True},
        ],
    })
    doc = load_document(child)
    assert [f.id for f in doc.features] == ["body", "hole"]
    assert doc.features[0].sketch.shapes[0].type == "circle"


def test_drop_unknown_and_cycle(tmp_path):
    _write(tmp_path / "box.json", BOX)
    bad = _write(tmp_path / "bad.json", {"schema": "cadjson/0.1", "name": "bad", "extends": "box.json", "drop": ["nope"]})
    with pytest.raises(CadjsonError, match="drop names 'nope'"):
        load_document(bad)
    _write(tmp_path / "a.json", {"schema": "cadjson/0.1", "name": "a", "extends": "b.json"})
    _write(tmp_path / "b.json", {"schema": "cadjson/0.1", "name": "b", "extends": "a.json"})
    with pytest.raises(CadjsonError, match="cycle"):
        load_document(tmp_path / "a.json")


def test_chain_and_relative_refs_are_rebased(tmp_path):
    base_dir = tmp_path / "lib"
    base_dir.mkdir()
    _write(base_dir / "box.json", BOX)
    _write(base_dir / "with_cavity.json", {
        "schema": "cadjson/0.1", "name": "with_cavity", "extends": "box.json",
        "params": {"t": 12},
        "features": [{"id": "cavity", "type": "part", "file": "box.json", "op": "cut", "at": [0, 0, 6]}],
    })
    variants = tmp_path / "variants"
    variants.mkdir()
    child = _write(variants / "v.json", {"schema": "cadjson/0.1", "name": "v", "extends": "../lib/with_cavity.json",
                                          "params": {"w": 30}})
    raw, chain = load_raw(child)
    assert raw["features"][-1]["file"] == "../lib/box.json"
    assert [Path(p).name for p in chain] == ["with_cavity.json", "box.json"]
    part = build_document(load_document(child)).part
    assert part.is_valid and part.volume < 30 * 10 * 12


def test_merge_keeps_child_outputs_and_checks_units():
    base = {"schema": "cadjson/0.1", "name": "b", "units": "mm", "features": [], "outputs": {"stl": True}}
    merged = merge_documents(base, {"schema": "cadjson/0.1", "name": "c", "outputs": {"step": False}})
    assert merged["outputs"] == {"step": False}
    with pytest.raises(CadjsonError, match="units"):
        merge_documents(base, {"schema": "cadjson/0.1", "name": "c", "units": "in"})


def test_example_variant_builds_and_reports_chain(tmp_path):
    result = build_document(load_document(EXAMPLES / "board_profile_long.json"))
    bb = result.part.bounding_box().size
    assert (round(bb.X, 3), round(bb.Z, 3)) == (150, 24)
    assert [f.id for f in result.document.features][-1] == "mount_holes"
    write_outputs(result, tmp_path, views=[], png=False, sheet=False, step=False, stl=False)
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["extends"] and rep["extends"][0].endswith("board_profile.json")
