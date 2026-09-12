"""Errors must name the feature and say something actionable."""

import pytest

from cadjson.build import build_document, parse_document
from cadjson.errors import CadjsonError


def _doc(features, params=None):
    return {
        "schema": "cadjson/0.1",
        "name": "t",
        "params": params or {},
        "features": features,
    }


BOX = {"id": "box", "type": "extrude", "distance": 10,
       "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": 20, "h": 10}]}}


def test_schema_error_lists_location():
    with pytest.raises(CadjsonError) as ei:
        parse_document(_doc([{"id": "b", "type": "extrude", "sketch": {"plane": "XY", "shapes": []}}]))
    assert "features.0" in str(ei.value)


def test_reference_to_later_feature_is_rejected():
    with pytest.raises(CadjsonError, match="not an earlier feature id"):
        parse_document(_doc([
            {"id": "f", "type": "fillet", "radius": 1, "edges": {"of": "box"}},
            BOX,
        ]))


def test_fillet_too_large_reports_feature_and_max():
    doc = parse_document(_doc([BOX, {"id": "big", "type": "fillet", "radius": 8, "edges": {"parallel_to": "Z"}}]))
    with pytest.raises(CadjsonError) as ei:
        build_document(doc)
    msg = str(ei.value)
    assert msg.startswith("[big]")
    assert "largest radius" in msg


def test_selector_matching_nothing_lists_candidates():
    doc = parse_document(_doc([BOX, {"id": "c", "type": "chamfer", "length": 1, "edges": {"parallel_to": "Z", "geom": "circle"}}]))
    with pytest.raises(CadjsonError) as ei:
        build_document(doc)
    msg = str(ei.value)
    assert "[c]" in msg and "matched nothing" in msg and "available:" in msg


def test_unknown_param_names_feature():
    doc = parse_document(_doc([{**BOX, "distance": "depth"}]))
    with pytest.raises(CadjsonError, match=r"\[box\].*unknown parameter 'depth'"):
        build_document(doc)


def test_cut_first_is_an_error():
    doc = parse_document(_doc([{**BOX, "op": "cut"}]))
    with pytest.raises(CadjsonError, match="no solid yet"):
        build_document(doc)


def test_legacy_schema_string_is_accepted():
    doc = parse_document(_doc([BOX]) | {"schema": "cadgen/0.1"})
    assert doc.schema_version == "cadgen/0.1"
    with pytest.raises(CadjsonError, match="schema must be"):
        parse_document(_doc([BOX]) | {"schema": "cadjson/9.9"})
