"""`cadjson init`: scaffold a parts repository that uses cadjson, including the Claude Code skill."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from cadjson import __version__

GITIGNORE = """# Python
.venv/
__pycache__/

# cadjson build outputs (JSON is the source; STEP/STL/PDF are rebuilt on demand)
out/
*.step
*.stp
*.stl
*.3mf
*.dxf
*.pdf

# keep reference meshes and previews that are committed on purpose
!parts/**/*_ref.stl
!parts/**/previews/**
"""

README = """# {name}

Part designs as text, built with [cadjson](https://github.com/sclaiborne/cadjson).

```
python3.12 -m venv .venv                     # py -3.12 -m venv .venv on Windows
.venv/bin/python -m pip install -r requirements.txt    # .venv\\Scripts\\python on Windows
.venv/bin/cadjson build parts/<project>/<part>.json
```

Layout: `parts/<project>/<part>.json`, reference meshes as `*_ref.stl`, previews and
`report.json` committed beside the part so history shows what changed. Tag a commit when a
revision is printed (`<project>/<part>-A`).

Claude Code: the `cadjson` skill in `.claude/skills/` teaches Claude the format and workflow.
Refresh it after upgrading cadjson with `cadjson init . --update`.
"""

REQUIREMENTS = """# pin the tool so parts keep building the same way; bump the tag deliberately
cadjson @ git+https://github.com/sclaiborne/cadjson@v{version}
"""

CLAUDE_MD = """# {name}

This repository holds cadjson part files (JSON feature trees). Use the `cadjson` skill in
`.claude/skills/cadjson/SKILL.md` for the format and the validate / build / look-at-previews
workflow. The tool is installed in `.venv` (`.venv/bin/cadjson`, `.venv\\Scripts\\cadjson` on
Windows). Part files live under
`parts/<project>/`; keep reference meshes as `*_ref.stl` next to the part they were
recreated from, and commit previews plus `report.json` with each change.
"""


def skill_text() -> str:
    return resources.files("cadjson").joinpath("skill/SKILL.md").read_text(encoding="utf-8")


def init_repo(target: Path, *, update: bool = False, name: str | None = None) -> list[Path]:
    """Create (or refresh) the scaffold. Returns the files written."""
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    name = name or target.name
    written: list[Path] = []

    def put(rel: str, text: str, overwrite: bool) -> None:
        path = target / rel
        if path.exists() and not overwrite:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    put(".claude/skills/cadjson/SKILL.md", skill_text(), overwrite=True)
    put(".gitignore", GITIGNORE, overwrite=False)
    put("README.md", README.format(name=name), overwrite=False)
    put("requirements.txt", REQUIREMENTS.format(version=__version__), overwrite=update)
    put("CLAUDE.md", CLAUDE_MD.format(name=name), overwrite=False)
    (target / "parts").mkdir(exist_ok=True)
    keep = target / "parts" / ".gitkeep"
    if not any((target / "parts").iterdir()):
        keep.write_text("", encoding="utf-8")
        written.append(keep)
    return written
