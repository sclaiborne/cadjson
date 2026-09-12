"""Fusion 360 exporter: expression translation, script generation, and a dry run on a fake API."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cadjson.build import load_document
from cadjson.errors import CadjsonError
from cadjson.expr import UnitsError, to_fusion
from cadjson.fusion import FusionExporter, export_fusion, infer_param_kinds

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_adsk  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ALL = sorted(EXAMPLES.glob("*.json"))


def test_to_fusion_lengths_and_counts():
    kinds = {"L": "len", "D": "len", "x": "len", "wall": "len", "n": "none"}
    assert to_fusion("L - D - x", kinds, "len") == "L - D - x"
    assert to_fusion("L - 2 * wall", kinds, "len") == "L - 2 * wall"
    assert to_fusion("h - 4", {"h": "len"}, "len") == "h - 4 mm"
    assert to_fusion("wall / 2", kinds, "len") == "wall / 2"
    assert to_fusion(40, kinds, "len") == "40 mm"
    assert to_fusion("n * 2", kinds, "none") == "n * 2"
    assert to_fusion(6, kinds, "none") == "6"
    assert to_fusion("max(L, D) / 2", kinds, "len") == "max(L, D) / 2"


def test_to_fusion_rejects_bad_units():
    kinds = {"L": "len", "n": "none"}
    with pytest.raises(UnitsError):
        to_fusion("L * L", kinds, "len")
    with pytest.raises(UnitsError):
        to_fusion("L", kinds, "none")
    with pytest.raises(UnitsError):
        to_fusion("n + L", kinds, "len")


def test_param_kinds_from_usage():
    doc = load_document(EXAMPLES / "enclosure.json")
    kinds = infer_param_kinds(doc)
    assert kinds["vent_count"] == "none"
    assert kinds["wall"] == "len"
    assert kinds["len"] == "len"


UNSUPPORTED = {"thread", "loft", "sweep", "part"}


def fusion_unsupported(doc) -> str | None:
    if doc.parts:
        return "assembly"
    for f in doc.features:
        if f.type in UNSUPPORTED:
            return f.type
        for shape in getattr(getattr(f, "sketch", None), "shapes", []) or []:
            if shape.type == "text":
                return "text"
    return None


@pytest.mark.parametrize("path", ALL, ids=[p.stem for p in ALL])
def test_script_generates_compiles_and_dry_runs(path, tmp_path):
    doc = load_document(path)
    why = fusion_unsupported(doc)
    if why:
        with pytest.raises(CadjsonError, match="cannot be exported|assembl"):
            export_fusion(doc, tmp_path)
        pytest.skip(f"{why} is not exported to Fusion yet (error is explicit)")
    files = export_fusion(doc, tmp_path)
    script = files[0].read_text(encoding="utf-8")
    compile(script, str(files[0]), "exec")
    assert "userParameters" in script or not doc.params
    for feat in doc.features:
        assert f"F[{feat.id!r}]" in script

    adsk = fake_adsk.install()
    ns = {}
    exec(compile(script, str(files[0]), "exec"), ns)
    helpers = ns["Helpers"]
    # geometry matching needs real bodies; stub it so the feature calls themselves run
    def fake_coll(n):
        c = fake_adsk.ObjectCollection.create()
        for _ in range(n):
            c.add(MagicMock())
        return c

    helpers.profiles = lambda self, sk, keys: fake_coll(len(keys))
    helpers.edges = lambda self, keys: fake_coll(len(keys))
    helpers.faces = lambda self, keys: fake_coll(len(keys))
    ns["run"](None)
    ui = adsk._app.userInterface
    messages = [c.args[0] for c in ui.messageBox.call_args_list]
    assert messages and messages[-1].startswith("cadjson: built"), messages


def test_board_script_uses_parameters_not_numbers(tmp_path):
    doc = load_document(EXAMPLES / "board_profile.json")
    script = FusionExporter(doc).export()
    assert "H.param('L', '100 mm', 'mm')" in script
    assert "H.param('g', 'L - D - x', 'mm')" in script
    assert "distance='depth'" in script
    assert "'XZ'" in script
