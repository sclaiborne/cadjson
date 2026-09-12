"""Machine-readable build report, for people and agents that want facts rather than pictures."""

from __future__ import annotations

import json
from pathlib import Path

from cadgen import SCHEMA_VERSION, __version__


def build_report(result, files: list[Path], notes: list[str] | None = None) -> dict:
    part, doc = result.part, result.document
    bb = part.bounding_box()
    c = part.center()
    return {
        "name": doc.name,
        "schema": SCHEMA_VERSION,
        "cadgen": __version__,
        "units": doc.units,
        "params": result_params(result),
        "volume_mm3": round(part.volume, 4),
        "bbox_mm": {
            "min": [round(bb.min.X, 4), round(bb.min.Y, 4), round(bb.min.Z, 4)],
            "max": [round(bb.max.X, 4), round(bb.max.Y, 4), round(bb.max.Z, 4)],
            "size": [round(bb.size.X, 4), round(bb.size.Y, 4), round(bb.size.Z, 4)],
        },
        "center_mm": [round(c.X, 4), round(c.Y, 4), round(c.Z, 4)],
        "faces": len(part.faces()),
        "edges": len(part.edges()),
        "features": [
            {"id": f.id, "type": f.type, "ms": round(result.timings.get(f.id, 0) * 1000, 1)}
            for f in doc.features
        ],
        "files": [str(p) for p in files],
        "notes": notes or [],
    }


def result_params(result) -> dict[str, float]:
    from cadgen.context import Context

    ctx = Context(result.document.params, result.document.units)
    return {k: round(v, 6) for k, v in ctx.params.items()}


def write_report(result, out_dir: Path, files: list[Path], notes: list[str] | None = None) -> Path:
    path = out_dir / "report.json"
    path.write_text(json.dumps(build_report(result, files, notes), indent=2), encoding="utf-8")
    return path
