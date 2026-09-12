"""Phase 4: what an agent relies on. Schema file is current, reports are written, the python
export rebuilds the same solid, and the skill exists."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cadgen.build import build_document, load_document, write_outputs
from cadgen.pyexport import export_python
from cadgen.schema import json_schema

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
ALL = sorted(EXAMPLES.glob("*.json"))


def test_committed_schema_matches_models():
    committed = json.loads((ROOT / "schema" / "cadgen-0.1.schema.json").read_text(encoding="utf-8"))
    assert committed == json_schema(), "run: cadgen schema -o schema/cadgen-0.1.schema.json"


def test_dollar_schema_key_is_accepted():
    doc = load_document(EXAMPLES / "knob.json")
    assert doc.schema_uri.endswith("cadgen-0.1.schema.json")


def test_report_written(tmp_path):
    result = build_document(load_document(EXAMPLES / "spacer.json"))
    files = write_outputs(result, tmp_path, views=[], png=False, sheet=False)
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["name"] == "spacer"
    assert rep["volume_mm3"] == pytest.approx(result.part.volume, abs=1e-3)
    assert [f["id"] for f in rep["features"]] == ["ring", "outer_chamfers", "pin_holes"]
    assert rep["params"]["outer_d"] == 20
    assert any(p.endswith("report.json") for p in rep["files"]) or files


@pytest.mark.parametrize("path", ALL, ids=[p.stem for p in ALL])
def test_python_export_rebuilds_same_volume(path, tmp_path):
    doc = load_document(path)
    if doc.parts or any(f.type == "part" for f in doc.features):
        pytest.skip("assemblies are not exported to scripts")
    expected = build_document(doc).part.volume
    script = export_python(doc, tmp_path)
    proc = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    volume = float(proc.stdout.split("volume")[1].split()[0])
    assert volume == pytest.approx(expected, rel=1e-4)


def test_inch_plate_bbox_is_in_mm():
    result = build_document(load_document(EXAMPLES / "plate_inch.json"))
    size = result.part.bounding_box().size
    assert (round(size.X, 3), round(size.Y, 3), round(size.Z, 3)) == (76.2, 50.8, 6.35)


def test_skill_exists_and_mentions_workflow():
    text = (ROOT / ".claude" / "skills" / "cadgen" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: cadgen")
    for needle in ("cadgen validate", "cadgen build", "report.json", "cadgen info", "convex"):
        assert needle in text
