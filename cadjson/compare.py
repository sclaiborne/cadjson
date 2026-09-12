"""Compare a built part against a reference mesh (STL/3MF/OBJ): volume, bounding box, and
surface distance both ways. Used to check a recreation of an existing part."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cadjson import export


@dataclass
class Comparison:
    volume_part: float
    volume_ref: float
    bbox_part: tuple
    bbox_ref: tuple
    part_to_ref: dict  # distances from points on the part to the reference surface
    ref_to_part: dict
    samples: int

    @property
    def volume_error_pct(self) -> float:
        return 100 * (self.volume_part - self.volume_ref) / self.volume_ref if self.volume_ref else float("nan")

    def summary(self) -> str:
        bp, br = self.bbox_part, self.bbox_ref
        lines = [
            f"volume   part {self.volume_part:.1f}  ref {self.volume_ref:.1f}  ({self.volume_error_pct:+.2f}%)",
            f"bbox     part {bp[0]} .. {bp[1]}",
            f"         ref  {br[0]} .. {br[1]}",
            f"distance part->ref  mean {self.part_to_ref['mean']:.3f}  p95 {self.part_to_ref['p95']:.3f}  max {self.part_to_ref['max']:.3f} mm",
            f"         ref->part  mean {self.ref_to_part['mean']:.3f}  p95 {self.ref_to_part['p95']:.3f}  max {self.ref_to_part['max']:.3f} mm",
            f"         ({self.samples} surface samples each way)",
        ]
        return "\n".join(lines)


def _stats(d: np.ndarray) -> dict:
    return {"mean": float(d.mean()), "p95": float(np.percentile(d, 95)), "max": float(d.max())}


def _bbox(m) -> tuple:
    lo, hi = m.bounds
    return (tuple(round(float(v), 2) for v in lo), tuple(round(float(v), 2) for v in hi))


def compare(part, reference: Path, samples: int = 20000, tolerance: float = 0.02) -> Comparison:
    import trimesh

    ref = trimesh.load(str(reference), force="mesh")
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "part.stl"
        export.write_stl(part, stl, tolerance, 0.1)
        mesh = trimesh.load(str(stl), force="mesh")
    pts_part, _ = trimesh.sample.sample_surface(mesh, samples)
    pts_ref, _ = trimesh.sample.sample_surface(ref, samples)
    _, d_pr, _ = trimesh.proximity.closest_point(ref, pts_part)
    _, d_rp, _ = trimesh.proximity.closest_point(mesh, pts_ref)
    return Comparison(
        volume_part=float(mesh.volume), volume_ref=float(ref.volume),
        bbox_part=_bbox(mesh), bbox_ref=_bbox(ref),
        part_to_ref=_stats(d_pr), ref_to_part=_stats(d_rp), samples=samples,
    )
