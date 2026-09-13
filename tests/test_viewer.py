"""The HTML viewer: one file, meshes embedded, one entry per placed part."""

import base64
import json
import re
import struct
from pathlib import Path

import pytest

from cadjson.build import build_document, load_document, write_outputs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _data(html: str) -> dict:
    return json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1))


def test_single_part_viewer(tmp_path):
    res = build_document(load_document(EXAMPLES / "spacer.json"))
    files = write_outputs(res, tmp_path, views=[], png=False, sheet=False, viewer=True)
    html = (tmp_path / "spacer.html").read_text(encoding="utf-8")
    assert tmp_path / "spacer.html" in files
    data = _data(html)
    assert [p["name"] for p in data["parts"]] == ["spacer"]
    p = data["parts"][0]
    pos = struct.unpack(f"<{len(base64.b64decode(p['positions'])) // 4}f", base64.b64decode(p["positions"]))
    idx = base64.b64decode(p["indices"])
    assert len(idx) == 12 * p["triangles"] and p["triangles"] > 100
    assert max(pos) == pytest.approx(10, abs=0.05) and min(pos) == pytest.approx(-10, abs=0.05)
    assert p["volume"] == pytest.approx(res.part.volume, abs=1e-3)


def test_assembly_viewer_lists_every_part(tmp_path):
    res = build_document(load_document(EXAMPLES / "assembly_mated.json"))
    write_outputs(res, tmp_path, views=[], png=False, sheet=False, viewer=True)
    data = _data((tmp_path / "assembly_mated.html").read_text(encoding="utf-8"))
    assert [p["name"] for p in data["parts"]] == ["left", "right", "cap", "assembly_mated"]
    cap = data["parts"][2]
    assert cap["bbox"][0][2] == pytest.approx(15, abs=1e-3)  # placed pose is baked into the mesh


def test_viewer_off_by_default(tmp_path):
    res = build_document(load_document(EXAMPLES / "spacer.json"))
    write_outputs(res, tmp_path, views=[], png=False, sheet=False)
    assert not (tmp_path / "spacer.html").exists()
