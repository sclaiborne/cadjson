from pathlib import Path

from cadjson import __version__
from cadjson.init import init_repo, skill_text

ROOT = Path(__file__).resolve().parent.parent


def test_packaged_skill_matches_repo_skill():
    repo = (ROOT / ".claude" / "skills" / "cadjson" / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text() == repo, "copy .claude/skills/cadjson/SKILL.md to cadjson/skill/SKILL.md"


def test_init_scaffolds_and_update_refreshes(tmp_path):
    target = tmp_path / "my-parts"
    written = init_repo(target)
    names = {p.relative_to(target).as_posix() for p in written}
    assert {".claude/skills/cadjson/SKILL.md", ".gitignore", "README.md", "requirements.txt", "CLAUDE.md"} <= names
    assert f"@v{__version__}" in (target / "requirements.txt").read_text(encoding="utf-8")
    # user edits survive, the skill is refreshed
    (target / "README.md").write_text("mine", encoding="utf-8")
    (target / ".claude/skills/cadjson/SKILL.md").write_text("stale", encoding="utf-8")
    init_repo(target, update=True)
    assert (target / "README.md").read_text(encoding="utf-8") == "mine"
    assert (target / ".claude/skills/cadjson/SKILL.md").read_text(encoding="utf-8") == skill_text()
