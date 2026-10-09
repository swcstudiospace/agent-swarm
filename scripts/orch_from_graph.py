#!/usr/bin/env python3
"""A01 — turn an Ultrathink graph summary into an orch_plan custom plan.

The summary is the Desk Lead export: graphId, nodes (id, kind, title, steps,
depends_on) and optional waves. This writes a plan orch_plan.py accepts as
``--pattern custom --plan <file> --graph-id <graphId>``.

Kind map (through agents.json, never an agent outside the fifteen):

- understand → A02 ``req.spec``
- synthesize → A01 ``plan.decompose``
- critique → A09 ``review.code`` and A10 ``sec.threatmodel`` (one task each)
- decompose, generate, and refine → the owning implementer, or A03
  ``design.blueprint`` when no step names an owned file

refine is mapped like generate. A node stays one task unless it is a critique
or its steps name files owned by more than one agent. Steps that name no file
stay on every split of that node; a step that names a file stays with that
file's owner. Owned-file rules, first match:

- ``*.md`` → A15 docs
- ``*.sql`` or a ``migrations/`` path → A07 data
- ``*.tsx`` / ``*.jsx`` / ``*.css`` or a components, frontend or ui directory → A06
- ``infra/``, ``.github/workflows/``, ``*.tf``, ``*.sh`` → A11
- ``*.py`` ``*.go`` ``*.rs`` ``*.ts`` ``*.js`` ``*.toml`` ``*.yml`` ``*.yaml`` → A05

Implementation agents (A05, A06, A07, A11) are medium risk with review and
quality gates. Every other agent is low risk with a review gate. The plan
records ``summary_sha256`` of the summary file's raw bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.manifest import load_manifest  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.substrate_tee import is_graph_id  # noqa: E402

_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")
_FILE = re.compile(
    r"(?<![\w@./-])"
    r"((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|md|sh|yml|yaml|toml|tf|css|html))"
    r"(?![\w.-])"
)
_DESIGN_KINDS = frozenset({"decompose", "generate", "refine"})
_SINGLE = {
    "understand": ("A02", "req.spec"),
    "synthesize": ("A01", "plan.decompose"),
}
_CRITIQUE = (("A09", "review.code"), ("A10", "sec.threatmodel"))
_DESIGN_DEFAULT = ("A03", "design.blueprint")
_OWNER_CAPABILITY = {
    "A05": "code.backend",
    "A06": "code.frontend",
    "A07": "data.migration",
    "A11": "iac.change",
    "A15": "docs.bundle",
}
_MEDIUM = frozenset({"A05", "A06", "A07", "A11"})
_UI_DIRS = frozenset({"components", "frontend", "ui"})


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _owner(path: str) -> str | None:
    lower = path.lower()
    base = lower.rsplit("/", 1)[-1]
    parts = lower.split("/")
    if base.endswith(".md"):
        return "A15"
    if base.endswith(".sql") or "migrations" in parts[:-1]:
        return "A07"
    if base.endswith((".tsx", ".jsx", ".css")) or any(part in _UI_DIRS for part in parts[:-1]):
        return "A06"
    if "infra" in parts[:-1] or ".github" in parts[:-1] or base.endswith((".tf", ".sh")):
        return "A11"
    if base.endswith((".py", ".go", ".rs", ".ts", ".js", ".toml", ".yml", ".yaml")):
        return "A05"
    return None


def _files(step: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _FILE.findall(step):
        if match in seen:
            continue
        seen.add(match)
        agent = _owner(match)
        if agent is not None:
            found.append((match, agent))
    return found


def _read_summary(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SwarmError(ErrorCode.E_INPUT, f"cannot read summary {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SwarmError(ErrorCode.E_INPUT, f"summary is not UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SwarmError(ErrorCode.E_INPUT, f"summary is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SwarmError(ErrorCode.E_INPUT, "summary must be a JSON object")
    return data, hashlib.sha256(raw).hexdigest()


def _nodes(data: dict) -> list[dict]:
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise SwarmError(ErrorCode.E_INPUT, "summary nodes must be a non-empty list")
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise SwarmError(ErrorCode.E_INPUT, "each node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or _NODE_ID.fullmatch(node_id) is None:
            raise SwarmError(ErrorCode.E_INPUT, f"invalid node id {node_id!r}")
        if node_id in seen:
            raise SwarmError(ErrorCode.E_INPUT, f"duplicate node id {node_id}")
        seen.add(node_id)
        kind = node.get("kind")
        known = isinstance(kind, str) and (kind in _SINGLE or kind in _DESIGN_KINDS or kind == "critique")
        if not known:
            raise SwarmError(ErrorCode.E_INPUT, f"unknown kind {kind!r} on {node_id}")
        title = node.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SwarmError(ErrorCode.E_INPUT, f"node {node_id} needs a title")
        steps = node.get("steps")
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise SwarmError(ErrorCode.E_INPUT, f"node {node_id} steps must be a list of strings")
        deps = node.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
            raise SwarmError(ErrorCode.E_INPUT, f"node {node_id} depends_on must be a list of node ids")
    return nodes


def _dependencies(nodes: list[dict]) -> dict[str, list[str]]:
    ids = {node["id"] for node in nodes}
    deps: dict[str, list[str]] = {}
    for node in nodes:
        cleaned: list[str] = []
        for dep in node["depends_on"]:
            if dep not in ids:
                raise SwarmError(ErrorCode.E_INPUT, f"node {node['id']} depends on unknown node {dep}")
            if dep not in cleaned:
                cleaned.append(dep)
        deps[node["id"]] = cleaned
    return deps


def _assert_acyclic(deps: dict[str, list[str]]) -> None:
    white, grey, black = 0, 1, 2
    color = {node_id: white for node_id in deps}
    stack: list[str] = []

    def visit(node_id: str) -> None:
        color[node_id] = grey
        stack.append(node_id)
        for dep in deps[node_id]:
            if color[dep] == grey:
                cycle = stack[stack.index(dep):] + [dep]
                raise SwarmError(ErrorCode.E_INPUT, f"dependency cycle: {' -> '.join(cycle)}")
            if color[dep] == white:
                visit(dep)
        stack.pop()
        color[node_id] = black

    for node_id in deps:
        if color[node_id] == white:
            visit(node_id)


def _waves(data: dict, deps: dict[str, list[str]]) -> list | None:
    if "waves" not in data:
        return None
    waves = data["waves"]
    if not isinstance(waves, list) or not waves:
        raise SwarmError(ErrorCode.E_INPUT, "waves must be a non-empty list when present")
    seen: dict[str, int] = {}
    numbers: set[int] = set()
    for wave in waves:
        if not isinstance(wave, dict):
            raise SwarmError(ErrorCode.E_INPUT, "each wave must be an object")
        number = wave.get("wave")
        if not _int(number):
            raise SwarmError(ErrorCode.E_INPUT, f"wave number {number!r} is not an integer")
        if number in numbers:
            raise SwarmError(ErrorCode.E_INPUT, f"duplicate wave number {number}")
        numbers.add(number)
        if not isinstance(wave.get("parallel"), bool):
            raise SwarmError(ErrorCode.E_INPUT, f"wave {number} parallel must be a boolean")
        ids = wave.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
            raise SwarmError(ErrorCode.E_INPUT, f"wave {number} ids must be a non-empty list of node ids")
        for node_id in ids:
            if node_id not in deps:
                raise SwarmError(ErrorCode.E_INPUT, f"wave {number} names unknown node {node_id}")
            if node_id in seen:
                raise SwarmError(ErrorCode.E_INPUT, f"node {node_id} appears in more than one wave")
            seen[node_id] = number
    missing = [node_id for node_id in deps if node_id not in seen]
    if missing:
        raise SwarmError(ErrorCode.E_INPUT, f"nodes missing from waves: {', '.join(missing)}")
    for node_id, node_deps in deps.items():
        for dep in node_deps:
            if seen[dep] >= seen[node_id]:
                raise SwarmError(
                    ErrorCode.E_INPUT,
                    f"node {node_id} (wave {seen[node_id]}) depends on {dep} (wave {seen[dep]})",
                )
    return waves


def _check_totals(data: dict, nodes: list[dict]) -> None:
    if "root" in data and not isinstance(data["root"], str):
        raise SwarmError(ErrorCode.E_INPUT, "root must be a string")
    if "totalSteps" not in data:
        return
    if not _int(data["totalSteps"]):
        raise SwarmError(ErrorCode.E_INPUT, "totalSteps must be an integer")
    counted = sum(len(node["steps"]) for node in nodes)
    if data["totalSteps"] != counted:
        raise SwarmError(ErrorCode.E_INPUT, f"totalSteps is {data['totalSteps']} but the nodes have {counted} steps")


def _agents_for(node: dict) -> list[tuple[str, str]]:
    kind = node["kind"]
    if kind in _SINGLE:
        return [_SINGLE[kind]]
    if kind == "critique":
        return list(_CRITIQUE)
    owners: list[str] = []
    for step in node["steps"]:
        for _path, agent in _files(step):
            if agent not in owners:
                owners.append(agent)
    if not owners:
        return [_DESIGN_DEFAULT]
    owners.sort(key=lambda agent: int(agent[1:]))
    return [(agent, _OWNER_CAPABILITY[agent]) for agent in owners]


def _acceptance(node: dict, agent: str, multi: bool) -> list[str]:
    steps: list[str] = node["steps"]
    if not multi or node["kind"] == "critique":
        return list(steps)
    kept: list[str] = []
    for step in steps:
        owners = [owner for _path, owner in _files(step)]
        if not owners or agent in owners:
            kept.append(step)
    return kept


def _risk_gates(agent: str) -> tuple[str, list[str]]:
    if agent in _MEDIUM:
        return "medium", ["review", "quality"]
    return "low", ["review"]


def _manifest() -> dict[str, dict]:
    agents = load_manifest()
    if len(agents) != 15:
        raise SwarmError(ErrorCode.E_CONTRACT, f"agents.json has {len(agents)} agents; the swarm has 15")
    return {agent["id"]: agent for agent in agents}


def convert(data: dict, summary_sha256: str) -> dict:
    """Return the custom plan for a validated summary. ``summary_sha256`` is the raw-file digest."""
    graph_id = data.get("graphId")
    if not is_graph_id(graph_id):
        raise SwarmError(ErrorCode.E_INPUT, f"invalid graphId {graph_id!r}: expected ut-<base36>-<8 hex>")
    nodes = _nodes(data)
    deps = _dependencies(nodes)
    _assert_acyclic(deps)
    waves = _waves(data, deps)
    _check_totals(data, nodes)
    manifest = _manifest()

    chosen = [(node, _agents_for(node)) for node in nodes]
    id_of: dict[str, list[str]] = {}
    emitted: list[str] = []
    for node, agents in chosen:
        multi = len(agents) > 1
        ids = [f"{node['id']}-{agent.lower()}" if multi else node["id"] for agent, _cap in agents]
        id_of[node["id"]] = ids
        emitted.extend(ids)
    if len(emitted) != len(set(emitted)):
        raise SwarmError(ErrorCode.E_INPUT, "task ids collide after splitting nodes")

    tasks = []
    for node, agents in chosen:
        multi = len(agents) > 1
        expanded: list[str] = []
        for dep in deps[node["id"]]:
            expanded.extend(id_of[dep])
        for (agent, capability), task_id in zip(agents, id_of[node["id"]]):
            spec = manifest.get(agent)
            if spec is None:
                raise SwarmError(ErrorCode.E_INPUT, f"agent {agent} is outside the 15")
            if capability not in spec["capabilities"]:
                raise SwarmError(ErrorCode.E_CONTRACT, f"{agent} does not offer {capability}")
            risk, gates = _risk_gates(agent)
            title = node["title"] if not multi else f"{node['title']} [{agent}]"
            tasks.append({
                "id": task_id,
                "node_id": node["id"],
                "kind": node["kind"],
                "capability": capability,
                "agent": agent,
                "title": title,
                "depends_on": expanded,
                "gates": gates,
                "risk_class": risk,
                "acceptance": _acceptance(node, agent, multi),
            })

    plan = {"graph_id": graph_id, "summary_sha256": summary_sha256, "tasks": tasks}
    if "root" in data:
        plan["root"] = data["root"]
    if waves is not None:
        plan["waves"] = waves
    return plan


def _dump(plan: dict) -> str:
    return json.dumps(plan, indent=2, ensure_ascii=False) + "\n"


def run(args, ctx) -> dict:
    if ctx.dry_run and not args.summary:
        return {"status": "ok", "tasks": [],
                "summary": "dry-run: would convert an Ultrathink graph summary into a custom plan"}
    if not args.summary:
        raise SwarmError(ErrorCode.E_INPUT, "provide --summary FILE")
    data, digest = _read_summary(Path(args.summary))
    plan = convert(data, digest)
    written = None
    if args.out and not ctx.dry_run:
        dest = Path(args.out)
        if not dest.parent.exists():
            raise SwarmError(ErrorCode.E_INPUT, f"output directory does not exist: {dest.parent}")
        dest.write_text(_dump(plan), encoding="utf-8")
        written = str(dest.resolve())
    return {
        "status": "ok",
        "graph_id": plan["graph_id"],
        "summary_sha256": plan["summary_sha256"],
        "task_count": len(plan["tasks"]),
        "plan": plan,
        "out": written,
        "summary": f"converted {plan['graph_id']} into {len(plan['tasks'])} tasks",
    }


def add_args(parser) -> None:
    parser.add_argument("--summary", help="Ultrathink graph summary JSON (graphId, nodes, waves)")
    parser.add_argument("--out", help="write the orch_plan custom plan JSON to this path")


if __name__ == "__main__":
    sys.exit(AgentScript(
        "A01", "orch_from_graph", run,
        description=__doc__, add_args=add_args,
    ).main())
