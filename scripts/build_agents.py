#!/usr/bin/env python3
"""Generate Claude Code and Grok Build subagent definitions from agents.json + prompts/.

Claude: .claude/agents/<slug>.md  (name, description, tools, model: inherit)
Grok:   .grok/agents/<slug>.md    (prompt_mode, permission_mode, agents_md)

Run after editing any prompt or the manifest:  python3 scripts/build_agents.py [--check]
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from swarm.manifest import load_manifest  # noqa: E402

CLAUDE_DIR = ROOT / ".claude" / "agents"
GROK_DIR = ROOT / ".grok" / "agents"

CLAUDE_PREAMBLE = """<swarm_runtime>
You are running as a Claude Code subagent inside the AgentSwarm (see README.md, 01-architecture.md, 02-message-protocol.md).
- Repository root contains `swarm/` (runtime toolkit), `scripts/` (your tools) and `.swarm/` (task store, verdicts, event log).
- The assignment you receive is a `task.assign` payload: task_id, correlation_id, capability, inputs[], acceptance[], budget, risk_class. Echo task_id and correlation_id in every script call (`--task-id`, `--correlation-id`) and in your final JSON.
- Run your scripts with `python3 scripts/<script>.py … --json` or `bun scripts/ts/<script>.ts … --json`, read the JSON, then act. Never fabricate script output.
- Only write inside your single-writer artifact zone (see <outputs>). To change anything else, describe the request in your final report for A01 to route.
- Finish with: (1) a short markdown summary, (2) exactly one fenced ```json block that is your `task.result` (or gate verdict) payload as defined in <output_format>. Set "state" to IN_REVIEW when work is complete, FAILED with an "error" {code,message} from the shared taxonomy when it is not, or BLOCKED with "needs" when an input is missing.
- Fail closed. Respect autonomy ceilings: for anything at L3/L4, stop and report `"state": "BLOCKED", "needs": "human-approval: …"`.
- Spawn sibling agents with the Agent tool, `subagent_type` = their slug (a02-requirements … a15-docs).
</swarm_runtime>
"""

GROK_PREAMBLE = """You are running as a Grok Build subagent inside the AgentSwarm (see README.md, 01-architecture.md, 02-message-protocol.md).

- Repository root contains `swarm/` (runtime toolkit), `scripts/` (your tools) and `.swarm/` (task store, verdicts, event log).
- The assignment you receive is a `task.assign` payload: task_id, correlation_id, capability, inputs[], acceptance[], budget, risk_class. Echo task_id and correlation_id in every script call (`--task-id`, `--correlation-id`) and in your final JSON.
- Run tools via `python3 scripts/<name>.py --json` or `bun scripts/ts/<name>.ts --json`. Never fabricate script output.
- Spawn sibling agents with `spawn_subagent` and `subagent_type` set to the agent slug (a01-orchestrator … a15-docs).
- Grok tools: `read_file`, `grep`, `run_terminal_command`. Do not invent Claude-only tool names.
- Only write inside your single-writer artifact zone (see <outputs>). To change anything else, describe the request in your final report for A01 to route.
- Finish with: (1) a short markdown summary, (2) exactly one fenced json block that is your `task.result` (or gate verdict) payload as defined in <output_format>. Set "state" to IN_REVIEW when work is complete, FAILED with an "error" {code,message} from the shared taxonomy when it is not, or BLOCKED with "needs" when an input is missing.
- Fail closed. Respect autonomy ceilings: for anything at L3/L4, stop and report `"state": "BLOCKED", "needs": "human-approval: …"`.
"""


def _body(agent: dict) -> str:
    prompt_path = ROOT / agent["prompt"]
    if not prompt_path.exists():
        raise FileNotFoundError(f"{agent['id']}: prompt missing at {agent['prompt']}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _desc(agent: dict) -> str:
    return agent["description"].replace('"', "'")


def render_claude(agent: dict, defaults: dict) -> str:
    del defaults  # model is always inherit for dual-runtime files
    tools = ", ".join(agent["tools"])
    desc = _desc(agent)
    fm = [
        "---",
        f"name: {agent['slug']}",
        f'description: "{agent["id"]} {agent["code"]} — {desc}"',
        f"tools: {tools}",
        "model: inherit",
        "---",
    ]
    return "\n".join(fm) + "\n\n" + CLAUDE_PREAMBLE + "\n" + _body(agent) + "\n"


def render_grok(agent: dict, defaults: dict) -> str:
    del defaults
    desc = _desc(agent)
    fm = [
        "---",
        f"name: {agent['slug']}",
        "description: >",
        f"  {agent['id']} {agent['code']} — {desc} Spawn as subagent_type {agent['slug']}.",
        "prompt_mode: full",
        "model: inherit",
        "permission_mode: default",
        "agents_md: true",
        "---",
    ]
    return "\n".join(fm) + "\n\n" + GROK_PREAMBLE + "\n" + _body(agent) + "\n"


def install_targets() -> list[Path]:
    return [CLAUDE_DIR, GROK_DIR]


def _write_or_check(target: Path, content: str, check: bool, changed: list, written: list) -> None:
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return
    changed.append(str(target.relative_to(ROOT)))
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target.relative_to(ROOT)))


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def merge_claude_settings(settings_path: Path, hook_cmd: str) -> None:
    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            data = {}
    data.setdefault("enabledPlugins", data.get("enabledPlugins", {}))
    hooks = data.setdefault("hooks", {})
    hooks["UserPromptSubmit"] = [
        {"hooks": [{"type": "command", "command": hook_cmd}]}
    ]
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")


def write_grok_hooks(path: Path, hook_cmd: str) -> None:
    payload = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": hook_cmd}]}]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def install_workspace(workspace: Path) -> None:
    hook_py = ROOT / "hooks" / "user_prompt_submit.py"
    hook_cmd = f"python3 {hook_py}"
    for agent in load_manifest():
        slug = agent["slug"]
        _copy_file(CLAUDE_DIR / f"{slug}.md", workspace / ".claude" / "agents" / f"{slug}.md")
        _copy_file(GROK_DIR / f"{slug}.md", workspace / ".grok" / "agents" / f"{slug}.md")
        skill_src = ROOT / "skills" / slug / "SKILL.md"
        if skill_src.exists():
            _copy_file(skill_src, workspace / ".claude" / "skills" / "agent-swarm" / slug / "SKILL.md")
    orch = ROOT / "skills" / "orchestrate" / "SKILL.md"
    if orch.exists():
        _copy_file(orch, workspace / ".claude" / "skills" / "agent-swarm" / "orchestrate" / "SKILL.md")
    merge_claude_settings(workspace / ".claude" / "settings.json", hook_cmd)
    write_grok_hooks(workspace / ".grok" / "hooks" / "agent-swarm.json", hook_cmd)
    write_grok_hooks(ROOT / ".grok" / "hooks" / "agent-swarm.json", hook_cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if generated output differs from disk")
    ap.add_argument("--only", help="comma list of agent ids/slugs")
    ap.add_argument("--install-workspace", help="copy generated agents, skills, and hooks into this workspace root")
    args = ap.parse_args()
    manifest_raw = json.loads((ROOT / "agents.json").read_text())
    defaults = manifest_raw.get("defaults", {})
    only = {s.strip().lower() for s in args.only.split(",")} if args.only else None
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    GROK_DIR.mkdir(parents=True, exist_ok=True)
    changed, written = [], []
    for agent in load_manifest():
        if only and agent["id"].lower() not in only and agent["slug"] not in only:
            continue
        _write_or_check(CLAUDE_DIR / f"{agent['slug']}.md", render_claude(agent, defaults), args.check, changed, written)
        _write_or_check(GROK_DIR / f"{agent['slug']}.md", render_grok(agent, defaults), args.check, changed, written)
    if args.check:
        print("stale:" if changed else "up-to-date", ", ".join(changed))
        return 1 if changed else 0
    print(f"wrote {len(written)} agent file(s): {', '.join(written) or '(none changed)'}")
    if args.install_workspace:
        install_workspace(Path(args.install_workspace).resolve())
        print(f"installed into {args.install_workspace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
