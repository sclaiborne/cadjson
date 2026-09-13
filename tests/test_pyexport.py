"""export-python: params stay named in the script, and the cadgen flavor is a valid text-to-cad model."""

import importlib.util
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cadjson.build import build_document, load_document, parse_document
from cadjson.expr import evaluate, resolve_params
from cadjson.pyexport import PythonExporter, export_python

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


BOX = {"id": "box", "type": "extrude", "distance": 5, "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": 10, "h": 10}]}}


def _doc(params, features=(BOX,), **extra):
    return parse_document({"schema": "cadjson/0.1", "name": "t", "params": params, "features": list(features), **extra})


PLATE = [
    {"id": "plate", "type": "extrude", "distance": "t",
     "sketch": {"plane": "XY", "shapes": [{"type": "rect", "w": "w", "h": "w / 2"}]}},
    {"id": "holes", "type": "extrude", "through": True, "op": "cut",
     "sketch": {"plane": {"face": "top"}, "shapes": [
         {"type": "circle", "d": "hole_d", "pattern": {"type": "polar", "count": "n", "radius": "w / 4 - hole_d"}}]}},
]


def _run(script: Path, function: str, extra_env=None) -> float:
    """Import the script (so its __main__ block stays out of it) and call the model function."""
    env = {**os.environ, **(extra_env or {})}
    code = f"import runpy; ns = runpy.run_path({str(script)!r}); print('volume', ns[{function!r}]().volume)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=script.parent, capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return float(proc.stdout.split("volume")[-1].split()[0])


@pytest.mark.parametrize("text", [
    "a - (b - c)", "a / (b * c)", "-(a + b) * 2", "a * -b", "(a + b) / (c - d)", "sin(30) * a",
    "max(a, b - c) / 2", "sqrt(a * a + b * b)", "atan(b / a)", "2 * pi * c", "- -a", "floor(a / 3) + ceil(b / 3)",
])
def test_expressions_render_to_the_same_value(text):
    params = {"a": 7.0, "b": 3.0, "c": 2.0, "d": 0.5}
    exporter = PythonExporter(_doc(params))
    code = exporter.expr(text)
    assert eval(code, {"math": math}, dict(params)) == pytest.approx(evaluate(text, params))


def test_params_are_constants_and_dimensions_use_them(tmp_path):
    text = export_python(load_document(EXAMPLES / "l_bracket.json"), tmp_path).read_text(encoding="utf-8")
    assert "\nwidth = 60\n" in text
    assert "amount=width)" in text
    assert "('left', base_len - t)" in text
    assert "Circle(hole_d / 2)" in text


def test_params_are_written_before_they_are_used():
    doc = _doc({"outer": "inner + 2 * wall", "inner": 10, "wall": 2})
    lines = PythonExporter(doc)._params_code()
    assert lines.index("outer = inner + 2 * wall") > lines.index("inner = 10")
    ns = {}
    exec("\n".join(lines), ns)
    assert ns["outer"] == resolve_params(doc.params)["outer"]


def test_clashing_param_names_are_renamed():
    doc = _doc({"Box": 5, "part": 2, "x": "Box + part"})
    lines = PythonExporter(doc)._params_code()
    assert "Box_ = 5  # Box in the part file" in lines
    assert "x = Box_ + part_" in lines


def test_editing_a_param_in_the_script_rebuilds_like_the_json(tmp_path):
    params = {"w": 40, "t": 3, "hole_d": 3, "n": 4}
    script = export_python(_doc(params, PLATE), tmp_path)
    text = script.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^w = 40$", "w = 60", text)
    text = re.sub(r"(?m)^n = 4$", "n = 6", text)
    script.write_text(text, encoding="utf-8")
    expected = build_document(_doc({**params, "w": 60, "n": 6}, PLATE)).part.volume
    assert _run(script, "t") == pytest.approx(expected, rel=1e-6)


# --- cadgen flavor -------------------------------------------------------------------------------

FAKE_CADGEN = '''import build123d

def _decorator(func):
    return func

step = stl = threemf = _decorator
'''


@pytest.fixture
def fake_cadgen(tmp_path):
    pkg = tmp_path / "fake" / "cadgen"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(FAKE_CADGEN, encoding="utf-8")
    return {"PYTHONPATH": str(pkg.parent)}


def test_cadgen_script_declares_outputs_and_uses_lazy_build123d(tmp_path):
    text = export_python(load_document(EXAMPLES / "enclosure.json"), tmp_path, cadgen=True).read_text(encoding="utf-8")
    assert "from cadgen import build123d as bd\nfrom cadgen import step, stl, threemf\n" in text
    assert "@threemf\n@stl\n@step\ndef enclosure():\n" in text
    assert "from build123d" not in text
    assert "bd.extrude(bd.Plane" in text
    assert re.search(r"(?<![.\w])(extrude|Plane|Rectangle|Vector)\(", text) is None


@pytest.mark.parametrize("name", ["spacer", "enclosure", "knob", "plate_inch", "board_profile_filleted"])
def test_cadgen_script_rebuilds_same_volume(name, tmp_path, fake_cadgen):
    doc = load_document(EXAMPLES / f"{name}.json")
    script = export_python(doc, tmp_path, cadgen=True)
    assert script.name == f"{name}.py"
    assert _run(script, name, fake_cadgen) == pytest.approx(build_document(doc).part.volume, rel=1e-4)


@pytest.mark.slow
@pytest.mark.skipif(importlib.util.find_spec("cadgen") is None, reason="cadgen not installed (pip install cadjson[cadgen])")
def test_real_cadgen_builds_the_model(tmp_path):
    # cadgen stays out of this process: importing it changes how later imports resolve
    from build123d import import_step

    doc = load_document(EXAMPLES / "spacer.json")
    script = export_python(doc, tmp_path, cadgen=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    probe = ("from pathlib import Path; from cadgen.metadata import parse_generator_metadata as p; "
             f"m = p(Path({str(script)!r})); print(m.entry_function, [d.fmt for d in m.mesh_exports])")
    meta = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, env=env)
    assert meta.stdout.strip().splitlines()[-1] == "spacer ['stl']", meta.stderr[-2000:]
    proc = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert (tmp_path / "spacer.stl").exists()
    volume = import_step(str(tmp_path / "spacer.step")).volume
    assert volume == pytest.approx(build_document(doc).part.volume, rel=1e-4)
