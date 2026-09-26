"""G-1: the generated omp swarm-orchestrate skill is the renderer's output and binds the runtime constants A01 acts
on (strict-dispatch gate agents, rework cap, A01's swarm tool grants). Every expected value is derived from code."""
import re

from swarm.manifest import load_manifest
from swarm.taskstore import MAX_REWORK_LOOPS
from test_omp_agents import OMP_AGENTS, OMP_SKILLS, _fm, builder, skills_mod

SKILL = OMP_SKILLS / "swarm-orchestrate" / "SKILL.md"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Body of the `## <heading>` section, up to the next `## ` heading."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, heading
    return m.group(1)


def test_skill_frontmatter_loadable():
    text = _skill()
    assert text == skills_mod.swarm_orchestrate_skill()
    fm = _fm(text)
    assert fm["name"] == "swarm-orchestrate"
    assert fm["description"].strip()


def test_skill_procedure_binds_runtime_constants():
    text = _skill()
    slugs = {a["id"]: a["slug"] for a in load_manifest()}

    # every tasks[] item for a gate agent is dispatched strict, and only gate agents are named on that step
    gate_slugs = {slugs[aid] for aid in builder.GATES}
    strict = [ln for ln in text.splitlines() if re.search(r'schemaMode:\s*"strict"', ln)]
    assert strict
    for ln in strict:
        assert {s for s in slugs.values() if re.search(rf"\b{re.escape(s)}\b", ln)} == gate_slugs

    # the rework cap the skill states is the Task Store's
    caps = re.findall(r"MAX_REWORK_LOOPS\W{0,3}(\d+)", text)
    assert caps and set(caps) == {str(MAX_REWORK_LOOPS)}

    # the Prerequisites tool list is A01's grant: its swarm_* tools (never swarm_gate) plus task
    a01_swarm = set(builder.SWARM_TOOLS["A01"])
    assert "swarm_gate" not in a01_swarm
    a01_tools = set(_fm((OMP_AGENTS / f"{slugs['A01']}.md").read_text(encoding="utf-8"))["tools"].split(", "))
    assert {t for t in a01_tools if t.startswith("swarm_")} == a01_swarm and "task" in a01_tools
    tools_line = next(ln for ln in _section(text, "Prerequisites").splitlines() if re.match(r"-\s*Tools:", ln))
    listed = {t.strip(" `*") for t in re.split(r"[(.]", tools_line.split(":", 1)[1], maxsplit=1)[0].split(",")}
    assert {t for t in listed if t.startswith("swarm_")} == a01_swarm
    assert "task" in listed
