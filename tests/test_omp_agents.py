"""omp agent + skill generation contract (AGENT-01..06, ORCH-01)."""
import importlib.util
from pathlib import Path

from swarm.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
OMP_AGENTS = ROOT / "omp" / "agents"
OMP_SKILLS = ROOT / "omp" / "skills"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


skills_mod = _load("_write_skills")


def _fm(text):
    fm = {}
    for line in text.split("---", 2)[1].strip().splitlines():
        k, v = line.split(": ", 1)
        fm[k] = v
    return fm


def test_omp_agent_files_match_manifest():
    slugs = {a["slug"] for a in load_manifest()}
    assert {p.stem for p in OMP_AGENTS.glob("*.md")} == slugs
    for slug in slugs:
        assert _fm((OMP_AGENTS / f"{slug}.md").read_text(encoding="utf-8"))["name"] == slug


def test_omp_skill_files_exist():
    for a in load_manifest():
        p = OMP_SKILLS / a["slug"] / "SKILL.md"
        fm = _fm(p.read_text(encoding="utf-8"))
        assert fm["name"] == a["slug"]
        assert fm["description"].strip('"').strip()


def test_omp_skill_matches_renderer():
    for a in load_manifest():
        p = OMP_SKILLS / a["slug"] / "SKILL.md"
        assert skills_mod.omp_skill(a) == p.read_text(encoding="utf-8"), a["slug"]
