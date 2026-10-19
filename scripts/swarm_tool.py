#!/usr/bin/env python3
"""Registry call bridge: list what an agent may use, and call one registered tool as that agent.

  python3 scripts/swarm_tool.py catalog --caller A05 [--kind tools|skills|all] [--query text]
  python3 scripts/swarm_tool.py call --caller a05-backend --name be_route_norm --input '{"path": "/users/:id/"}'
  (call --plan-mode refuses every tool above tier READ with E-POLICY before the handler is imported)

The caller is an agent id (A05) or slug (a05-backend); an unknown caller is E-INPUT. `catalog` returns the tools the
caller may call (name, purpose, tier, timeout) and the caller's own plus shared skills. `call` goes through
swarm.tool_registry.call_tool, the only call path, and returns its result object unchanged under `result`.
Any state other than SUCCESS or PARTIAL_SUCCESS is status `fail` (exit 1): data the agent must read, not a crash.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm import tool_registry  # noqa: E402
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402

try:
    from swarm import skill_registry
except ImportError:  # the skills pipeline is optional for the tool bridge
    skill_registry = None

OK_STATES = ("SUCCESS", "PARTIAL_SUCCESS")
KINDS = ("tools", "skills", "all")
MAX_INPUT_BYTES = 100_000


def add_args(p) -> None:
    p.add_argument("command", nargs="?", choices=("catalog", "call"), default="catalog")
    p.add_argument("--caller", default=None, help="agent id (A05) or slug (a05-backend)")
    p.add_argument("--kind", choices=KINDS, default="all")
    p.add_argument("--query", default=None, help="catalog: case-insensitive words matched against name and purpose")
    p.add_argument("--name", default=None, help="call: registered tool name")
    p.add_argument("--input", default="{}", help="call: JSON object payload")
    p.add_argument("--plan-mode", action="store_true", help="call: the host is read-only; refuse any tool above tier READ")


def resolve_caller(data: dict, ident: str | None) -> dict:
    """The agents.json entry for an id or slug, else E-INPUT."""
    if not isinstance(ident, str) or not ident.strip():
        raise SwarmError(ErrorCode.E_INPUT, "--caller is required: an agent id (A05) or slug (a05-backend)")
    wanted = ident.strip()
    for agent in data.get("agents") or []:
        if isinstance(agent, dict) and wanted in (agent.get("id"), agent.get("slug")):
            return agent
    known = ", ".join(str(a.get("id")) for a in data.get("agents") or [] if isinstance(a, dict))
    raise SwarmError(ErrorCode.E_INPUT, f"unknown caller {wanted!r}; use one of {known} or an agent slug")


def _matches(query: str | None, *texts: str) -> bool:
    if not query:
        return True
    haystack = " ".join(texts).lower()
    return all(word in haystack for word in query.lower().split())


def catalog(data: dict, agent: dict, kind: str, query: str | None) -> dict:
    """Tools the agent may call and its skills (own plus shared), filtered by kind and query."""
    out: dict = {"caller": agent["id"], "caller_slug": agent["slug"], "kind": kind}
    if kind in ("tools", "all"):
        rows = []
        for _container, tool in tool_registry.iter_entries(data):
            if not isinstance(tool, dict) or agent["id"] not in (tool.get("permitted_callers") or []):
                continue
            if _matches(query, str(tool.get("name")), str(tool.get("purpose"))):
                rows.append({
                    "name": tool["name"],
                    "purpose": tool.get("purpose"),
                    "tier": tool_registry.tool_tier(tool),
                    "timeout_s": tool.get("timeout_s"),
                })
        out["tools"] = sorted(rows, key=lambda row: row["name"])
    if kind in ("skills", "all"):
        out["skills"], problem = _skills(agent["slug"], query)
        if problem:
            out["skills_unavailable"] = problem
    parts = [f"{len(out[k])} {k}" for k in ("tools", "skills") if k in out]
    out["summary"] = f"{agent['id']} ({agent['slug']}): " + ", ".join(parts)
    return out


def _skills(slug: str, query: str | None) -> tuple[list[dict], str | None]:
    if skill_registry is None:
        return [], None
    try:
        found = [*skill_registry.skills_for(slug), *skill_registry.skills_for(skill_registry.SHARED_OWNER)]
    except (OSError, ValueError) as exc:
        return [], f"skill sources unreadable: {exc}"
    rows = [
        {"name": s["name"], "description": s.get("description", ""), "owner": s.get("owner", ""), "tools": s.get("tools", [])}
        for s in found
        if _matches(query, str(s.get("name")), str(s.get("description")))
    ]
    return sorted(rows, key=lambda row: row["name"]), None


def _parse_payload(raw: str) -> dict:
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise SwarmError(ErrorCode.E_INPUT, f"--input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SwarmError(ErrorCode.E_INPUT, f"--input is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SwarmError(ErrorCode.E_INPUT, "--input must be a JSON object")
    return payload


def run(args, ctx) -> dict:
    if ctx.dry_run and args.caller is None:
        return {"status": "ok", "dry_run": True, "command": args.command, "summary": f"dry-run: swarm_tool {args.command}"}
    try:
        data = tool_registry.load_document()
    except (OSError, json.JSONDecodeError) as exc:
        raise SwarmError(ErrorCode.E_DEP, f"registry unreadable: {exc}") from exc
    agent = resolve_caller(data, args.caller)
    if args.command == "catalog":
        result = catalog(data, agent, args.kind, args.query)
        if ctx.dry_run:
            result["dry_run"] = True
        return {"status": "ok", **result}
    if not args.name:
        raise SwarmError(ErrorCode.E_INPUT, "call requires --name")
    payload = _parse_payload(args.input)
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "caller": agent["id"], "name": args.name,
                "summary": f"dry-run: {agent['id']} would call {args.name}"}
    if ctx.root.is_dir():
        os.chdir(ctx.root)  # handlers inspect the working directory, which is the workspace the agent works on
    if args.plan_mode:
        target = tool_registry.find_tool(data, args.name)
        tier = tool_registry.tool_tier(target) if target else tool_registry.DEFAULT_TIER
        if tier != tool_registry.DEFAULT_TIER:
            raise SwarmError(ErrorCode.E_POLICY, f"{args.name} is a {tier} tool; the host is in plan mode (read-only), "
                             "so it was not called. Leave plan mode to run it; READ tools still work")
    result = tool_registry.call_tool(args.name, agent["id"], payload, data=data)
    status = "ok" if result.get("state") in OK_STATES else "fail"
    return {"status": status, "caller": agent["id"], "name": args.name, "result": result,
            "summary": f"{args.name}: {result.get('state')}"}


if __name__ == "__main__":
    sys.exit(AgentScript("shared", "swarm_tool", run, description=__doc__, add_args=add_args).main())
