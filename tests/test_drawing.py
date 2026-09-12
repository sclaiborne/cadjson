"""Phase 2: section views and the annotated drawing sheet."""

from pathlib import Path

import pytest
from build123d import Plane

from cadgen import export
from cadgen.build import build_document, load_document, write_outputs
from cadgen.errors import CadgenError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_section_view_has_cut_faces_and_edges():
    part = build_document(load_document(EXAMPLES / "enclosure.json")).part
    vis, hid, faces = export.section_view(part, Plane.XZ)
    assert faces, "no cut faces found on the section plane"
    # the shelled box cut through the middle shows two wall cross-sections plus the floor
    assert sum(f.area for f in faces) == pytest.approx(2 * 2 * 28 + 80 * 2, rel=0.05)
    assert len(vis.edges()) > 0


def test_section_plane_missing_part_is_an_error():
    part = build_document(load_document(EXAMPLES / "board_profile.json")).part
    with pytest.raises(CadgenError, match="does not pass through"):
        export.section_view(part, Plane.XY.offset(500))


def test_enclosure_section_output(tmp_path):
    result = build_document(load_document(EXAMPLES / "enclosure.json"))
    files = write_outputs(result, tmp_path, views=[], png=True)
    names = {f.name for f in files}
    assert "enclosure_section_A.svg" in names and "enclosure_section_A.png" in names
    svg = (tmp_path / "enclosure_section_A.svg").read_text(encoding="utf-8")
    assert 'id="section"' in svg


@pytest.mark.slow
def test_sheet_is_written(tmp_path):
    result = build_document(load_document(EXAMPLES / "board_profile.json"))
    files = write_outputs(result, tmp_path, views=[], png=False, sheet=True)
    names = {f.name for f in files}
    assert "board_profile_drawing.pdf" in names
    assert "board_profile_drawing.svg" in names
    for f in files:
        assert f.stat().st_size > 0
