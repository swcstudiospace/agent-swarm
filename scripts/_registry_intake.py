#!/usr/bin/env python3
"""Merge registry intake fragments into agents.json (v2.2 D8, DESIGN 5.2).

Parallel authors never edit agents.json. They write fragments under registry/intake/:

  <owner>.tools.json   JSON array of complete tool entries; <owner> is `shared`, an agent slug (a05-backend) or id (A05)
  grants.json          JSON object {tool_name: [agent ids to ADD to permitted_callers]}

  python3 scripts/_registry_intake.py --check    # validate the registry as it would look after the merge
  python3 scripts/_registry_intake.py --apply    # re-check, merge into agents.json, delete the applied fragments

Both modes refuse on any error. --apply then changes nothing: no byte of agents.json and no fragment.
--apply holds an exclusive flock on <intake>/.lock from planning to the last unlink, deletes a fragment only while
its sha256 still equals the one planned (an edit made meanwhile is kept for the next run), and treats a fragment or
grant whose content the registry already holds (a crash between the agents.json write and the unlink) as already
applied: it is reported and removed, never an error.
agents.json is hand formatted (early tools use compact inline schemas), so a load-and-dump would rewrite
thousands of untouched lines. The merge splices text instead: untouched bytes stay identical, new tool entries are
rendered with indent 2 in canonical key order, and the result is verified to parse to exactly the merged document.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from swarm import tool_registry  # noqa: E402

# examples.unauthorized_caller once every agent is permitted: a name that is no agent at all
NO_AGENT_PROBE = "ghost-agent"
GRANTS_FILE = "grants.json"
TOOLS_SUFFIX = ".tools.json"
LOCK_FILE = ".lock"
_DECODER = json.JSONDecoder()
# canonical key order of a tool entry: the contract's required fields (the order agents.json uses), then the rest
_KEY_ORDER = [*tool_registry.REQUIRED_FIELDS]
_KEY_ORDER.insert(_KEY_ORDER.index("idempotency") + 1, "side_effect_tier")


@dataclass
class Plan:
    """What a merge would do. `errors` empty means it may be applied."""

    errors: list[str] = field(default_factory=list)
    merged: dict | None = None
    # (container id or "shared", tool entry) in application order
    adds: list[tuple[str, dict]] = field(default_factory=list)
    grants: dict[str, list[str]] = field(default_factory=dict)
    # tool name -> new examples.unauthorized_caller, when a grant made the old one an authorized caller
    probe_fixes: dict[str, str] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    # sha256 of each fragment's bytes as planned: apply deletes a file only while it still hashes the same
    digests: dict[Path, str] = field(default_factory=dict)
    # fragment content the registry already holds (reported, then removed with the rest)
    already: list[str] = field(default_factory=list)
    # fragments left on disk because they changed after planning
    kept: list[str] = field(default_factory=list)


def plan_merge(agents_file: Path, intake_dir: Path, root: Path | None = None) -> Plan:
    """Read the fragments, merge them in memory and validate the result with the live registry checker."""
    root = root or ROOT
    plan = Plan()
    try:
        document = tool_registry.load_document(agents_file)
    except (OSError, ValueError) as exc:
        plan.errors.append(f"{agents_file}: cannot read registry ({exc})")
        return plan
    if not isinstance(document, dict) or not isinstance(document.get("agents"), list):
        plan.errors.append(f"{agents_file}: agents must be a list")
        return plan
    containers = _containers(document)
    merged = copy.deepcopy(document)
    existing = {
        t["name"]: (c, t) for c, t in tool_registry.iter_entries(document) if isinstance(t, dict) and isinstance(t.get("name"), str)
    }
    taken: set = set()

    tool_files = sorted(intake_dir.glob(f"*{TOOLS_SUFFIX}")) if intake_dir.is_dir() else []
    for path in tool_files:
        plan.files.append(path)
        _read_tools(path, containers, merged, taken, existing, plan)
    grants_path = intake_dir / GRANTS_FILE
    if grants_path.is_file():
        plan.files.append(grants_path)
        _read_grants(grants_path, merged, plan)
    if plan.errors:
        return plan
    plan.merged = merged
    plan.errors.extend(_validate(merged, root))
    return plan


def apply_merge(agents_file: Path, intake_dir: Path, root: Path | None = None) -> Plan:
    """plan_merge, then write agents.json and delete the applied fragments. Nothing changes when errors exist.

    The whole run (plan, read, write, unlink) holds an exclusive flock on <intake>/.lock, so two applies can never
    interleave. A fragment is deleted only when its bytes still equal what was planned (an edit that landed
    meanwhile stays on disk for the next run), and an already-applied fragment is simply removed."""
    with _locked(intake_dir):
        plan = plan_merge(agents_file, intake_dir, root)
        if plan.errors or plan.merged is None or not plan.files:
            return plan
        if plan.adds or plan.grants:
            text = Path(agents_file).read_bytes().decode("utf-8")  # bytes: keep the file's own line endings
            try:
                text = _splice(text, plan)
                if json.loads(text) != plan.merged:
                    raise ValueError("the spliced text does not parse to the merged document")
            except (ValueError, KeyError, IndexError) as exc:
                plan.errors.append(f"merge refused, agents.json untouched: {exc}")
                return plan
            _atomic_write(Path(agents_file), text)
        for path in plan.files:
            try:
                unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == plan.digests.get(path)
            except FileNotFoundError:
                continue
            if unchanged:
                path.unlink(missing_ok=True)
            else:
                plan.kept.append(path.name)
        return plan


def _containers(document: dict) -> dict[str, str | None]:
    """Fragment owner names (shared, agent id, agent slug) -> container id."""
    names: dict[str, str | None] = {"shared": "shared"}
    for agent in document["agents"]:
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            names[agent["id"]] = agent["id"]
            if isinstance(agent.get("slug"), str):
                names[agent["slug"]] = agent["id"]
    return names


def _load_fragment(path: Path, plan: Plan, what: str):
    """Parse a fragment, recording the sha256 of the exact bytes read so apply deletes only what it merged."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        plan.errors.append(f"{path.name}: cannot read {what} ({exc})")
        return None
    plan.digests[path] = hashlib.sha256(data).hexdigest()
    try:
        value = json.loads(data.decode("utf-8"))
        json.dumps(value, ensure_ascii=False).encode("utf-8")  # an unpaired surrogate cannot be written to agents.json
    except (ValueError, RecursionError) as exc:
        plan.errors.append(f"{path.name}: cannot read {what} ({exc})")
        return None
    return value


def _same_tool(entry: dict, existing: dict) -> bool:
    """True when the registry already holds this fragment entry. Grants since applied may have widened
    permitted_callers and moved the refused-caller example, so those two fields compare loosely."""
    mine, theirs = copy.deepcopy(entry), copy.deepcopy(existing)
    callers, held = mine.get("permitted_callers"), theirs.get("permitted_callers")
    if not (isinstance(callers, list) and isinstance(held, list) and all(c in held for c in callers)):
        return False
    for tool in (mine, theirs):
        tool.pop("permitted_callers", None)
        if isinstance(tool.get("examples"), dict):
            tool["examples"].pop("unauthorized_caller", None)
    return mine == theirs


def _read_tools(path: Path, containers: dict, merged: dict, taken: set, existing: dict, plan: Plan) -> None:
    # `<owner>.tools.json`, or `<owner>.<shard>.tools.json` when one owner has several authors (e.g. shared.runtime)
    owner_name = path.name[: -len(TOOLS_SUFFIX)].split(".", 1)[0]
    container = containers.get(owner_name)
    if container is None:
        plan.errors.append(f"{path.name}: owner {owner_name!r} must be 'shared', an agent slug or an agent id")
        return
    entries = _load_fragment(path, plan, "fragment")
    if entries is None:
        return
    if not isinstance(entries, list):
        plan.errors.append(f"{path.name}: a fragment must be a JSON array of tool entries")
        return
    target = _tool_list(merged, container)
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            plan.errors.append(f"{path.name}[{position}]: tool entry must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            plan.errors.append(f"{path.name}[{position}]: tool name must be a string, got {type(name).__name__}")
            continue
        if entry.get("owner") != container:
            plan.errors.append(f"{path.name}[{position}] ({name}): owner must be {container!r}, the owner named by the file")
        if name in existing and name not in taken:
            held_by, held = existing[name]
            if held_by == container and _same_tool(entry, held):
                # a crash (or a restored fragment) left it behind after agents.json already took it: nothing to merge
                taken.add(name)
                plan.already.append(f"{path.name}: tool {name}")
                continue
        if name in taken or name in existing:
            plan.errors.append(f"{path.name}[{position}]: tool {name!r} already exists in the registry or another fragment")
            continue
        taken.add(name)
        entry = _canonical(entry)
        target.append(entry)
        plan.adds.append((container, copy.deepcopy(entry)))


def _read_grants(path: Path, merged: dict, plan: Plan) -> None:
    grants = _load_fragment(path, plan, "grants")
    if grants is None:
        return
    if not isinstance(grants, dict):
        plan.errors.append(f"{path.name}: grants must be a JSON object {{tool_name: [agent ids]}}")
        return
    agent_ids = {a.get("id") for a in merged["agents"] if isinstance(a, dict)}
    by_name = {t["name"]: (c, t) for c, t in tool_registry.iter_entries(merged) if isinstance(t, dict) and "name" in t}
    for name, ids in grants.items():
        if name not in by_name:
            plan.errors.append(f"{path.name}: tool {name!r} is not registered")
            continue
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            plan.errors.append(f"{path.name}: {name} must grant a non-empty list of agent ids")
            continue
        unknown = [i for i in ids if i not in agent_ids]
        if unknown:
            plan.errors.append(f"{path.name}: {name} grants unknown agent id(s) {', '.join(unknown)}")
            continue
        container, tool = by_name[name]
        callers = tool.get("permitted_callers")
        if not isinstance(callers, list):
            plan.errors.append(f"{path.name}: tool {name!r} has no permitted_callers list to extend")
            continue
        if len(set(ids)) == len(ids) and container not in ids and all(i in callers for i in ids):
            # every id is already a permitted caller: the grant landed before (crash, restored file); nothing to do
            plan.already.append(f"{path.name}: grant {name} -> {', '.join(ids)}")
            continue
        repeated = sorted({i for i in ids if ids.count(i) > 1} | {i for i in ids if i in callers})
        if repeated:
            plan.errors.append(f"{path.name}: {name} would list {', '.join(repeated)} twice in permitted_callers")
            continue
        callers.extend(ids)
        if container != "shared" and container not in callers:
            plan.errors.append(f"{path.name}: owner {container} must stay a permitted caller of {name}")
            continue
        plan.grants[name] = list(ids)
        examples = tool.get("examples")
        if isinstance(examples, dict) and examples.get("unauthorized_caller") in callers:
            # the refused-caller example must stay refused: pick the first agent that is still not permitted
            free = [a["id"] for a in merged["agents"] if isinstance(a, dict) and a.get("id") not in callers]
            examples["unauthorized_caller"] = free[0] if free else NO_AGENT_PROBE
            plan.probe_fixes[name] = examples["unauthorized_caller"]


def _tool_list(document: dict, container: str) -> list:
    if container == "shared":
        return document.setdefault("shared_tools", [])
    for agent in document["agents"]:
        if isinstance(agent, dict) and agent.get("id") == container:
            return agent.setdefault("registered_tools", [])
    raise KeyError(container)


def _canonical(entry: dict) -> dict:
    ordered = {key: entry[key] for key in _KEY_ORDER if key in entry}
    ordered.update({key: value for key, value in entry.items() if key not in ordered})
    return ordered


def _validate(merged: dict, root: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="registry-intake-") as tmp:
        probe = Path(tmp) / "agents.json"
        probe.write_text(json.dumps(merged), encoding="utf-8")  # ensure_ascii: never trips on odd code points
        return tool_registry.validate_registry(probe, root=root)


# ---- text splice -------------------------------------------------------------------------------------------


def _ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _members(text: str, start: int):
    """Yield (key or index, value_start, value_end) for the object or array whose value begins at `start`."""
    i = _ws(text, start)
    opener = text[i]
    closer = "}" if opener == "{" else "]"
    if opener not in "{[":
        raise ValueError(f"offset {start} is not a container")
    i = _ws(text, i + 1)
    index = 0
    while text[i] != closer:
        key: str | int = index
        if opener == "{":
            key, i = _DECODER.raw_decode(text, i)
            i = _ws(text, i)
            i = _ws(text, i + 1)  # skip ':'
        begin = i
        _value, i = _DECODER.raw_decode(text, i)
        yield key, begin, i
        index += 1
        i = _ws(text, i)
        if text[i] == ",":
            i = _ws(text, i + 1)


def _span(text: str, path: list) -> tuple[int, int]:
    begin = _ws(text, 0)
    end = _DECODER.raw_decode(text, begin)[1]
    for key in path:
        for found, child_begin, child_end in _members(text, begin):
            if found == key:
                begin, end = child_begin, child_end
                break
        else:
            raise KeyError(f"path {path!r}: no member {key!r}")
    return begin, end


def _line_indent(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    rest = text[line_start:]
    return rest[: len(rest) - len(rest.lstrip(" "))]


def _dump(value, indent: str, nl: str = "\n") -> str:
    return nl.join(indent + line for line in json.dumps(value, indent=2, ensure_ascii=False).split("\n"))


def _splice(text: str, plan: Plan) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"  # keep the file's own line ending; never mix
    grouped: dict[str, list[dict]] = {}
    for container, tool in plan.adds:
        grouped.setdefault(container, []).append(tool)
    for container, tools in grouped.items():
        text = _append_tools(text, container, tools, nl)
    for name, ids in plan.grants.items():
        text = _extend_callers(text, name, ids, nl)
    for name, probe in plan.probe_fixes.items():
        text = _replace_probe(text, name, probe)
    return text


def _append_tools(text: str, container: str, tools: list[dict], nl: str = "\n") -> str:
    document = json.loads(text)
    if container == "shared":
        path: list = ["shared_tools"]
    else:
        index = next(i for i, a in enumerate(document["agents"]) if isinstance(a, dict) and a.get("id") == container)
        path = ["agents", index, "registered_tools"]
    begin, end = _span(text, path)
    members = list(_members(text, begin))
    if not members:
        indent = _line_indent(text, begin)
        body = ("," + nl).join(_dump(tool, indent + "  ", nl) for tool in tools)
        return text[:begin] + "[" + nl + body + nl + indent + "]" + text[end:]
    indent = _line_indent(text, members[0][1])
    body = ("," + nl).join(_dump(tool, indent, nl) for tool in tools)
    last_end = members[-1][2]
    return text[:last_end] + "," + nl + body + text[last_end:]


def _tool_path(text: str, name: str) -> list:
    document = json.loads(text)
    for i, tool in enumerate(document.get("shared_tools") or []):
        if isinstance(tool, dict) and tool.get("name") == name:
            return ["shared_tools", i]
    for ai, agent in enumerate(document["agents"]):
        for ti, tool in enumerate(agent.get("registered_tools") or []):
            if isinstance(tool, dict) and tool.get("name") == name:
                return ["agents", ai, "registered_tools", ti]
    raise KeyError(name)


def _replace_probe(text: str, name: str, probe: str) -> str:
    begin, end = _span(text, [*_tool_path(text, name), "examples", "unauthorized_caller"])
    return text[:begin] + json.dumps(probe) + text[end:]


def _extend_callers(text: str, name: str, ids: list[str], nl: str = "\n") -> str:
    begin, end = _span(text, [*_tool_path(text, name), "permitted_callers"])
    current = json.loads(text[begin:end]) + ids
    if "\n" in text[begin:end]:
        indent = _line_indent(text, begin)
        rendered = "[" + nl + ("," + nl).join(f"{indent}  {json.dumps(c)}" for c in current) + nl + indent + "]"
    else:
        rendered = "[" + ", ".join(json.dumps(c) for c in current) + "]"
    return text[:begin] + rendered + text[end:]


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _locked(intake_dir: Path, exclusive: bool = True):
    """Serialise intake runs: flock on <intake>/.lock, held from planning through the last fragment unlink."""
    if not Path(intake_dir).is_dir():
        yield
        return
    fd = os.open(Path(intake_dir) / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or merge registry intake fragments into agents.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate the merged registry; change nothing")
    mode.add_argument("--apply", action="store_true", help="merge into agents.json and delete the applied fragments")
    parser.add_argument("--root", default=str(ROOT), help="repository root (handlers and entrypoints resolve here)")
    parser.add_argument("--agents-file", default=None, help="registry to merge into (default: the live agents.json)")
    parser.add_argument("--intake-dir", default=None, help="fragment directory (default: <root>/registry/intake)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    agents_file = Path(args.agents_file) if args.agents_file else root / "agents.json"
    intake_dir = Path(args.intake_dir) if args.intake_dir else root / "registry" / "intake"
    if args.apply:
        plan = apply_merge(agents_file, intake_dir, root)
    else:
        with _locked(intake_dir, exclusive=False):
            plan = plan_merge(agents_file, intake_dir, root)
    if plan.errors:
        print("\n".join(plan.errors), file=sys.stderr)
        print(f"registry intake refused: {len(plan.errors)} error(s); agents.json and fragments unchanged", file=sys.stderr)
        return 1
    if not plan.files:
        print("registry intake: no fragments")
        return 0
    verb = "applied" if args.apply else "would apply"
    print(f"registry intake {verb}: {len(plan.adds)} tool(s), {len(plan.grants)} grant(s) from {len(plan.files)} fragment(s)")
    for line in plan.already:
        print(f"  already applied (no change needed): {line}")
    for name in plan.kept:
        print(f"  kept {name}: it changed after planning, so it was not merged and not deleted; run --apply again", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
