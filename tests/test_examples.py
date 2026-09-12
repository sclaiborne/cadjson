"""Build every example part and check the ones with known answers."""

from pathlib import Path

import pytest

from cadjson.build import build_document, load_document, write_outputs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ALL = sorted(p for p in EXAMPLES.glob("*.json"))


@pytest.mark.parametrize("path", ALL, ids=[p.stem for p in ALL])
def test_example_builds(path):
    result = build_document(load_document(path))
    assert result.part.is_valid
    assert result.part.volume > 0


def _volume(name: str) -> float:
    return build_document(load_document(EXAMPLES / f"{name}.json")).part.volume


def test_board_profile_volume_matches_hand_calculation():
    # profile area 2000 - 48 (tongue step) - 112 (groove) - 48 (notch) = 1792, extruded 40
    assert _volume("board_profile") == pytest.approx(71680.0, abs=1e-3)


def test_board_profile_path_matches_cut_version():
    assert _volume("board_profile_path") == pytest.approx(_volume("board_profile"), abs=1e-3)


def test_board_profile_filleted_matches_experiment():
    # value produced by experiments/board_profile_filleted_build123d.py
    assert _volume("board_profile_filleted") == pytest.approx(71439.6, abs=0.5)


def test_outputs_are_written(tmp_path):
    result = build_document(load_document(EXAMPLES / "board_profile.json"))
    files = write_outputs(result, tmp_path, views=["front", "iso"])
    names = {f.name for f in files}
    assert "board_profile.step" in names
    assert "board_profile.stl" in names
    assert "board_profile_front.svg" in names
    assert "board_profile_iso.png" in names
    for f in files:
        assert f.stat().st_size > 0
