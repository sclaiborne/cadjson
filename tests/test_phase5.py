"""Phase 5: threads, standard holes, text, loft, sweep, part imports, assemblies, expressions."""

import json
import math
from pathlib import Path

import pytest

from cadjson.build import build_document, load_document, parse_document, write_outputs
from cadjson.errors import CadjsonError
from cadjson.expr import evaluate, to_fusion
from cadjson.standards import thread_spec

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_thread_specs():
    m6 = thread_spec("M6")
    assert (m6.major, m6.pitch, m6.tap_drill, m6.clearance_medium) == (6, 1.0, 5.0, 6.6)
    fine = thread_spec("M8x1")
    assert (fine.pitch, fine.tap_drill) == (1.0, 7.0)
    unc = thread_spec("1/4-20")
    assert unc.major == pytest.approx(6.35)
    assert unc.pitch == pytest.approx(1.27)
    with pytest.raises(CadjsonError, match="unknown"):
        thread_spec("M7.5")
    with pytest.raises(CadjsonError, match="unknown thread"):
        thread_spec("3/4-99")


def test_standard_hole_diameter_is_in_mm_even_for_inch_documents():
    doc = parse_document({
        "schema": "cadjson/0.1", "name": "t", "units": "in",
        "features": [
            {"id": "b", "type": "extrude", "distance": 0.5,
             "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": 2, "h": 2}]}},
            {"id": "h", "type": "hole", "face": "top", "at": [[0, 0]], "standard": "M3", "fit": "tap", "through": True},
        ],
    })
    part = build_document(doc).part
    circles = [e for e in part.edges() if e.geom_type.name == "CIRCLE"]
    assert min(e.radius for e in circles) == pytest.approx(1.25, abs=1e-6)


def test_expression_functions():
    assert evaluate("sin(30)", {}) == pytest.approx(0.5)
    assert evaluate("2 * pi", {}) == pytest.approx(2 * math.pi)
    assert evaluate("floor(7 / 2) + ceil(0.2) + round(2.5)", {}) == 3 + 1 + 2
    assert evaluate("atan(1)", {}) == pytest.approx(45)
    assert to_fusion("sin(a)", {"a": "none"}, "none") == "sin((a) * 1 deg)"


def test_bolt_has_a_thread():
    result = build_document(load_document(EXAMPLES / "bolt.json"))
    part = result.part
    assert part.is_valid
    # threaded shank has far more faces than a plain cylinder
    assert len(part.faces()) > 20
    bb = part.bounding_box()
    assert bb.min.Z == pytest.approx(-20, abs=1e-6)


def test_tapped_block_thread_and_text():
    result = build_document(load_document(EXAMPLES / "tapped_block.json"))
    part = result.part
    assert part.is_valid
    plain = 30 * 20 * 12
    assert part.volume < plain - math.pi * 2.5**2 * 12  # at least the tap hole is gone
    # the engraved label adds spline edges on the top face
    assert any(e.geom_type.name in ("BSPLINE", "BEZIER") for e in part.edges())


def test_text_font_path(tmp_path):
    font = Path("C:/Windows/Fonts/verdanab.ttf")
    if not font.exists():
        pytest.skip("Verdana Bold not installed")
    doc = {
        "schema": "cadjson/0.1", "name": "t",
        "features": [
            {"id": "plate", "type": "extrude", "distance": 2,
             "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": 80, "h": 30}]}},
            {"id": "label", "type": "extrude", "distance": 1,
             "sketch": {"plane": {"face": "top"}, "shapes": [
                 {"type": "text", "text": "abc", "size": 12, "font_path": "verdanab.ttf"}]}},
        ],
    }
    (tmp_path / "t.json").write_text(json.dumps(doc))
    (tmp_path / "verdanab.ttf").write_bytes(font.read_bytes())
    part = build_document(load_document(tmp_path / "t.json")).part
    assert part.volume > 80 * 30 * 2
    bad = dict(doc)
    bad["features"] = [doc["features"][0], {**doc["features"][1], "sketch": {"plane": {"face": "top"}, "shapes": [
        {"type": "text", "text": "abc", "size": 12, "font_path": "missing.ttf"}]}}]
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(CadjsonError, match="font file not found"):
        build_document(load_document(tmp_path / "bad.json"))


def test_funnel_and_hook_volumes():
    funnel = build_document(load_document(EXAMPLES / "funnel.json")).part
    assert funnel.is_valid and 0 < funnel.volume < math.pi * 25**2 * 40
    hook = build_document(load_document(EXAMPLES / "hook.json")).part
    path_len = 20 + math.pi * 10 / 2 + 15
    assert hook.volume == pytest.approx(math.pi * 2**2 * path_len, rel=0.02)


def test_assembly_places_parts_with_overrides(tmp_path):
    result = build_document(load_document(EXAMPLES / "assembly_stack.json"))
    part = result.part
    solids = part.solids()
    assert len(solids) == 4  # two standoffs, one spacer, one plate
    bb = part.bounding_box()
    assert (bb.min.X, bb.max.X) == pytest.approx((-10, 30), abs=1e-6)  # plate spans pitch + 20 around x=10
    widths = sorted(round(s.bounding_box().size.X, 3) for s in solids)
    assert 30.0 in widths  # the spacer was built with outer_d overridden to 30
    files = write_outputs(result, tmp_path, views=["iso"], png=False, sheet=False)
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["features"][0]["id"] == "plate"
    assert (tmp_path / "assembly_stack.step").stat().st_size > 0


def test_part_feature_cuts_an_imported_part():
    doc = parse_document({
        "schema": "cadjson/0.1", "name": "mold",
        "features": [
            {"id": "block", "type": "extrude", "distance": 20,
             "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": 40, "h": 40}]}},
            {"id": "cavity", "type": "part", "file": "spacer.json", "op": "cut", "at": [0, 0, 5]},
        ],
    })
    from cadjson.build import SOURCE_PATHS

    SOURCE_PATHS[id(doc)] = EXAMPLES / "mold.json"  # resolve spacer.json relative to examples/
    spacer = build_document(load_document(EXAMPLES / "spacer.json")).part
    part = build_document(doc).part
    assert part.volume == pytest.approx(40 * 40 * 20 - spacer.volume, rel=1e-4)


def test_part_cycle_is_detected(tmp_path):
    a = {"schema": "cadjson/0.1", "name": "a", "parts": [{"file": "b.json"}]}
    b = {"schema": "cadjson/0.1", "name": "b", "parts": [{"file": "a.json"}]}
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    with pytest.raises(CadjsonError, match="cycle"):
        build_document(load_document(tmp_path / "a.json"))
