"""Toolchain sanity checks. These do not test cadgen itself yet (Phase 0)."""

import json
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_build123d_builds_a_solid():
    from build123d import Box, BuildPart

    with BuildPart() as p:
        Box(10, 20, 30)
    assert p.part.is_valid
    assert abs(p.part.volume - 6000) < 1e-6


def test_examples_are_valid_json_with_required_keys():
    files = sorted(EXAMPLES.glob("*.json"))
    assert files, "no example parts found"
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["schema"] == "cadgen/0.1", f.name
        assert isinstance(doc["features"], list) and doc["features"], f.name
        ids = [feat["id"] for feat in doc["features"]]
        assert len(ids) == len(set(ids)), f"duplicate feature ids in {f.name}"
