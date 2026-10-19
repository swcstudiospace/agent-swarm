"""`python3 -m swarm.skill_registry docs`: deterministic output, drift detection naming the stale file."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from swarm import skill_registry as sr

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "skills"
AGENTS = {
    "capability_policy": {"skills": {"min": 0, "max": 15}},
    "agents": [
        {"id": "A05", "code": "BE", "name": "Backend Engineer", "slug": "a05-backend", "registered_tools": [
            {"name": "be_route_norm", "owner": "A05", "purpose": "x", "permitted_callers": ["A05"]},
            {"name": "be_route_collision", "owner": "A05", "purpose": "x", "permitted_callers": ["A05"]},
        ]},
        {"id": "A06", "code": "FE", "name": "Frontend Engineer", "slug": "a06-frontend", "registered_tools": []},
    ],
    "shared_tools": [],
}


def _root(tmp_path: Path) -> Path:
    dest = tmp_path / "prompts" / "skills" / "a05-backend"
    dest.mkdir(parents=True)
    (dest / "route-review.md").write_text((FIXTURES / "valid.md").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "agents.json").write_text(json.dumps(AGENTS), encoding="utf-8")
    return tmp_path


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "swarm.skill_registry", "docs", "--root", str(root), *args],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_render_is_deterministic_and_lists_every_skill_once():
    skills = sr.load_skills(ROOT)
    doc = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    first = sr.render_docs(skills, doc)
    assert first == sr.render_docs(list(reversed(skills)), doc)
    catalog = first[sr.CATALOG_PATH]
    for skill in skills:
        rows = [line for line in catalog.splitlines() if line.startswith(f"| `{skill.name}` |")]
        assert len(rows) == 1, f"{skill.name}: expected exactly one catalog row, found {len(rows)}"
        assert str(skill.size) in rows[0] and skill.owner_dir in rows[0]


def test_one_page_per_agent_plus_shared_with_index_tools_and_handover():
    skills = sr.load_skills(ROOT)
    doc = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    pages = sr.render_docs(skills, doc)
    expected = {f"docs/skills/agents/{a['slug']}.md" for a in doc["agents"]} | {"docs/skills/agents/shared.md"}
    assert set(pages) - {sr.CATALOG_PATH} == expected
    for rel in expected:
        owner = Path(rel).stem
        text = pages[rel]
        mine = sr.skills_for_owner(skills, owner)
        assert mine, f"{owner}: no skills to document"
        assert text.startswith(sr.DOCS_HEADER)
        assert "## Index" in text and "## Handover notes" in text
        for skill in mine:
            assert f"`{skill.name}`" in text, f"{rel}: skill {skill.name} missing from its owner page"
            assert skill.description in text.replace("\\|", "|"), f"{rel}: trigger for {skill.name} missing"


def test_committed_docs_are_current():
    result = _cli(ROOT, "--check")
    assert result.returncode == 0, result.stderr


def test_write_then_check_roundtrip_and_stale_files_are_named(tmp_path):
    root = _root(tmp_path)
    assert _cli(root, "--check").returncode == 1  # nothing written yet
    assert _cli(root).returncode == 0
    assert (root / sr.CATALOG_PATH).is_file() and (root / "docs/skills/agents/a06-frontend.md").is_file()
    ok = _cli(root, "--check")
    assert ok.returncode == 0 and "skill docs ok" in ok.stdout

    page = root / "docs/skills/agents/a05-backend.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")
    stale = _cli(root, "--check")
    assert stale.returncode == 1
    assert "docs/skills/agents/a05-backend.md" in stale.stderr
    assert "docs/skills/catalog.md" not in stale.stderr

    assert _cli(root).returncode == 0  # regenerating repairs the drift
    assert _cli(root, "--check").returncode == 0


def test_orphan_page_is_drift_and_removed_on_write(tmp_path):
    root = _root(tmp_path)
    assert _cli(root).returncode == 0
    orphan = root / "docs/skills/agents/a99-gone.md"
    orphan.write_text("old\n", encoding="utf-8")
    result = _cli(root, "--check")
    assert result.returncode == 1 and "docs/skills/agents/a99-gone.md" in result.stderr
    assert _cli(root).returncode == 0 and not orphan.exists()


def test_source_change_makes_docs_stale(tmp_path):
    root = _root(tmp_path)
    assert _cli(root).returncode == 0
    src = root / "prompts/skills/a05-backend/route-review.md"
    src.write_text(src.read_text(encoding="utf-8") + "\nExtra handover detail.\n", encoding="utf-8")
    result = _cli(root, "--check")
    assert result.returncode == 1 and "docs/skills/catalog.md" in result.stderr


def test_invalid_sources_refuse_to_render(tmp_path):
    root = _root(tmp_path)
    (root / "prompts/skills/a05-backend/bad-one.md").write_text((FIXTURES / "bad_name.md").read_text(encoding="utf-8"), encoding="utf-8")
    result = _cli(root)
    assert result.returncode == 1 and "prompts/skills/a05-backend/bad-one.md:" in result.stderr
    assert not (root / "docs").exists()
