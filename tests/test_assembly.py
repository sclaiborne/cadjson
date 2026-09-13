"""Assemblies: mates derive positions, checks find overlaps and clearances, poses round-trip."""

import json
import math

import numpy as np
import pytest

from cadjson.assembly import Pose, euler_to_matrix, matrix_to_euler
from cadjson.build import build_document, load_document, parse_document
from cadjson.errors import CadjsonError

from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

PEG = {
    "schema": "cadjson/0.1", "name": "peg", "params": {"d": 6, "h": 20, "head_d": 10, "head_t": 3},
    "features": [
        {"id": "shank", "type": "extrude", "distance": "h",
         "sketch": {"plane": "XY", "shapes": [{"type": "circle", "d": "d"}]}},
        {"id": "head", "type": "extrude", "distance": "head_t",
         "sketch": {"plane": {"face": "top"}, "shapes": [{"type": "circle", "d": "head_d"}]}},
    ],
}
BLOCK = {  # 40 x 40 x 25 block, a 6.2 mm socket 20 deep at (12, -12): its floor is at z = 5
    "schema": "cadjson/0.1", "name": "block", "params": {"w": 40, "t": 25, "hole_d": 6.2, "x": 12},
    "features": [
        {"id": "body", "type": "extrude", "distance": "t",
         "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": "w", "h": "w"}]}},
        {"id": "socket", "type": "hole", "face": "top", "diameter": "hole_d", "depth": 20, "at": [["x", "-x"]]},
    ],
}


@pytest.fixture
def lib(tmp_path):
    for doc in (PEG, BLOCK):
        (tmp_path / f"{doc['name']}.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def _assembly(lib, parts, checks=None, name="asm", features=None):
    raw = {"schema": "cadjson/0.1", "name": name, "parts": parts, "features": features or []}
    if checks is not None:
        raw["checks"] = checks
    path = lib / f"{name}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return build_document(load_document(path))


def _part(result, name):
    return next(p for p in result.assembly["parts"] if p["name"] == name)


# --- pose maths --------------------------------------------------------------------------------


@pytest.mark.parametrize("rot", [(0, 0, 0), (30, 0, 0), (0, 45, 0), (0, 0, 90), (10, 20, 30), (-80, 170, 5)])
def test_euler_round_trip(rot):
    R = euler_to_matrix(rot)
    assert np.allclose(euler_to_matrix(matrix_to_euler(R)), R, atol=1e-9)


def test_pose_apply_matches_rotate_then_translate():
    from build123d import Axis, Box

    box = Box(10, 4, 2).moved(__import__("build123d").Location((3, 0, 0)))
    expected = box.rotate(Axis.X, 30).rotate(Axis.Y, 40).rotate(Axis.Z, 50).translate((1, 2, 3))
    got = Pose.from_euler((1, 2, 3), (30, 40, 50)).apply(box)
    assert (got.center() - expected.center()).length < 1e-9
    assert got.bounding_box().min.to_tuple() == pytest.approx(expected.bounding_box().min.to_tuple(), abs=1e-9)


# --- mates ----------------------------------------------------------------------------------


def test_coaxial_and_against_derive_the_position(lib):
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "mates": [
            {"type": "coaxial", "this": {"of": "shank", "geom": "cylinder"},
             "to": "block", "face": {"of": "socket", "geom": "cylinder"}},
            {"type": "against", "this": {"of": "head", "normal": "-Z"}, "to": "block", "face": "top"},
        ]},
    ])
    peg = _part(res, "peg")
    # head underside on the block top (z = 25), shank 20 long down the socket: origin at the socket floor
    assert peg["at"] == pytest.approx([12, -12, 5], abs=1e-6)
    assert peg["rotate"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert res.assembly["interference"] == []


def test_against_turns_the_part_over_when_needed(lib):
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "mates": [{"type": "against", "this": "top", "to": "block", "face": "top"}]},
    ])
    peg = _part(res, "peg")
    assert any(abs(r) == pytest.approx(180, abs=1e-6) for r in peg["rotate"][:2])
    bb = res.part.bounding_box()
    assert bb.max.Z == pytest.approx(25 + 23, abs=1e-6)  # upside down on the block: head first, shank up


def test_coaxial_then_against_flips_end_for_end(lib):
    # the coaxial mate may line the shank up pointing either way; the face mate must still be met
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "rotate": [180, 0, 0], "mates": [
            {"type": "coaxial", "this": {"of": "shank", "geom": "cylinder"},
             "to": "block", "face": {"of": "socket", "geom": "cylinder"}},
            {"type": "against", "this": {"of": "head", "normal": "-Z"}, "to": "block", "face": "top"},
        ]},
    ])
    peg = _part(res, "peg")
    assert peg["at"] == pytest.approx([12, -12, 5], abs=1e-6)
    assert res.assembly["interference"] == []


def test_against_with_offset_and_free_axes_keep_at(lib):
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "at": [5, 7, 99], "mates": [
            {"type": "against", "this": "bottom", "to": "block", "face": "top", "offset": 2},
        ]},
    ])
    peg = _part(res, "peg")
    assert peg["at"] == pytest.approx([5, 7, 27], abs=1e-6)  # x, y untouched; z = block top 25 + gap 2
    assert peg["rotate"] == pytest.approx([0, 0, 0], abs=1e-6)


def test_datum_targets(lib):
    res = _assembly(lib, [
        {"file": "peg.json", "mates": [
            {"type": "coaxial", "this": {"of": "shank", "geom": "cylinder"}, "to": "X"},
            {"type": "flush", "this": "top", "to": "YZ", "offset": 5},
        ]},
    ])
    peg = _part(res, "peg")
    assert peg["at"][1:] == pytest.approx([0, 0], abs=1e-6)
    bb = res.part.bounding_box()
    assert bb.max.X == pytest.approx(5, abs=1e-6) and bb.size.X == pytest.approx(23, abs=1e-6)


def test_parallel_fixes_rotation_only(lib):
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "at": [1, 2, 3], "mates": [
            {"type": "parallel", "this": "top", "to": "block", "face": "right"},
        ]},
    ])
    peg = _part(res, "peg")
    assert peg["at"] == pytest.approx([1, 2, 3], abs=1e-6)
    assert peg["rotate"][1] == pytest.approx(90, abs=1e-6)


def test_conflicting_mates_are_an_error(lib):
    with pytest.raises(CadjsonError, match="cannot turn the part without undoing mate 0"):
        _assembly(lib, [
            {"file": "block.json"},
            {"file": "peg.json", "mates": [
                {"type": "against", "this": "top", "to": "block", "face": "top"},
                {"type": "flush", "this": "top", "to": "block", "face": "right"},
            ]},
        ])


def test_mate_face_must_be_the_right_kind(lib):
    with pytest.raises(CadjsonError, match="must be a cylindrical or conical face"):
        _assembly(lib, [
            {"file": "block.json"},
            {"file": "peg.json", "mates": [{"type": "coaxial", "this": "top", "to": "block", "face": "top"}]},
        ])


def test_unknown_target_rejected_at_validation():
    with pytest.raises(CadjsonError, match="not an earlier placed part"):
        parse_document({"schema": "cadjson/0.1", "name": "a", "parts": [
            {"file": "peg.json", "mates": [{"type": "against", "this": "top", "to": "block", "face": "top"}]},
            {"file": "block.json"},
        ]})


def test_duplicate_part_names_rejected():
    with pytest.raises(CadjsonError, match="two placed parts are named 'peg'"):
        parse_document({"schema": "cadjson/0.1", "name": "a", "parts": [{"file": "peg.json"}, {"file": "peg.json"}]})


# --- checks ---------------------------------------------------------------------------------


def test_interference_is_reported_and_can_fail_the_build(lib):
    parts = [{"file": "block.json"}, {"file": "peg.json", "at": [12, -12, 2]}]  # shank 3 mm below the socket floor
    res = _assembly(lib, parts)
    hit = res.assembly["interference"]
    assert hit and hit[0]["between"] == ["block", "peg"]
    # 3 mm of shank below the socket floor, and 3 mm of head sunk into the block around the hole
    assert hit[0]["volume_mm3"] == pytest.approx(math.pi * 9 * 3 + math.pi * (25 - 3.1**2) * 3, rel=1e-3)
    assert "overlap by" in res.summary()
    with pytest.raises(CadjsonError, match="block and peg overlap"):
        _assembly(lib, parts, checks={"interference": "error"})


def test_clearance_check(lib):
    res = _assembly(lib, [
        {"file": "block.json"},
        {"file": "peg.json", "at": [0, 0, 27.5]},
    ], checks={"clearance": [{"between": ["block", "peg"], "min": 2}, {"between": ["block", "peg"], "min": 3}]})
    a, b = res.assembly["clearance"]
    assert a["distance_mm"] == pytest.approx(2.5, abs=1e-6) and a["ok"]
    assert not b["ok"]
    assert any("wanted at least 3" in m for m in res.warnings())


def test_clearance_unknown_part_rejected():
    with pytest.raises(CadjsonError, match="not a placed part"):
        parse_document({"schema": "cadjson/0.1", "name": "a", "parts": [{"file": "peg.json"}],
                        "checks": {"clearance": [{"between": ["peg", "nope"], "min": 1}]}})


# --- the example ----------------------------------------------------------------------------


def test_mated_example_positions_and_report(tmp_path):
    from cadjson.build import write_outputs

    res = build_document(load_document(EXAMPLES / "assembly_mated.json"))
    by = {p["name"]: p for p in res.assembly["parts"]}
    assert by["left"]["at"] == pytest.approx([-15, 0, 3], abs=1e-6)
    assert by["right"]["rotate"] == pytest.approx([0, 0, 30], abs=1e-6)
    assert by["cap"]["at"] == pytest.approx([-15, 0, 15], abs=1e-6)
    assert res.assembly["interference"] == [] and res.assembly["clearance"][0]["ok"]
    write_outputs(res, tmp_path, views=[], png=False, sheet=False)
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["assembly"]["parts"][2]["name"] == "cap"
