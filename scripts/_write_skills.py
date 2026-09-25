#!/usr/bin/env python3
"""Generate per-agent SKILL.md files and the orchestration skill."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = json.loads((ROOT / "agents.json").read_text())["agents"]

DONT = {
    "A01": "Do not implement domain artifacts (code/docs/IaC).",
    "A02": "Do not design architecture or write product code.",
    "A03": "Do not write product source or schema DDL.",
    "A04": "Do not implement backend/frontend source.",
    "A05": "Do not edit frontend, schema, or deploy.",
    "A06": "Do not edit backend or schema.",
    "A07": "Do not write application business logic.",
    "A08": "Do not patch product code.",
    "A09": "Do not edit product code.",
    "A10": "Do not edit product code or accept risk.",
    "A11": "Do not write product business logic.",
    "A12": "Do not write application code.",
    "A13": "Do not ship features; you observe.",
    "A14": "Do not add unrelated features.",
    "A15": "Do not rewrite product code to match docs.",
}


def agent_skill(a: dict) -> str:
    stems = [Path(s).stem for s in a["scripts"]]
    cmds = "\n".join(
        f"- `python3 scripts/{s}.py --task-id $TASK --correlation-id $CORR --json`\n"
        f"- `bun scripts/ts/{s}.ts --task-id $TASK --correlation-id $CORR --json`"
        for s in stems
    )
    spawn = ""
    if a["id"] == "A01":
        spawn = """
6. For each ready task, lease it (`orch_status.py --repo <app> --transition <id> CLAIMED`, then `IN_PROGRESS`), then spawn `subagent_type` = slug (Claude Agent tool or Grok `spawn_subagent`). Do not implement domain work.
7. Ingest child JSON with `orch_status.py --repo <app> --ingest`. Apply fail-closed gates (max 2 rework loops); gate scripts, not you, record verdicts.
"""
    return f"""---
name: {a['slug']}
description: >
  {a['id']} {a['code']} {a['name']} — {a['description']}
  Use when the user runs /{a['slug']}, asks for {a['code']}, or the swarm assigns capability {', '.join(a['capabilities'][:3])}.
disable-model-invocation: false
---

# {a['name']} ({a['id']} {a['code']})

Subagent body: `prompts/{Path(a['prompt']).name}` (do not paste it here). Generated agents: `.claude/agents/{a['slug']}.md` and `.grok/agents/{a['slug']}.md`.

## When to Use

- The assignment capability is one of: {', '.join(a['capabilities'])}.
- The user names this agent, slug `{a['slug']}`, or code `{a['code']}`.
- Use when the user runs /{a['slug']}.

Don't use for: {DONT[a['id']]}

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

{cmds}

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: {', '.join(a['consumes'][:8])}.
3. Run the scripts above. Never fabricate JSON.
4. Write only {', '.join(a['produces'][:6])}.
5. Finish with markdown summary + one fenced json `task.result`.
{spawn}
## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
"""


def orch_skill() -> str:
    slugs = ", ".join(a["slug"] for a in AGENTS)
    return f"""---
name: agent-swarm-orchestrate
description: >
  Run the AgentSwarm of 15 SDLC subagents (a01-orchestrator through a15-docs).
  Use when the user says run the swarm, AgentSwarm, implement a feature with the 15 agents,
  a01-orchestrator, SDLC swarm, or /swarm. UserPromptSubmit hook injects this skill for SDLC-shaped prompts.
disable-model-invocation: false
---

# AgentSwarm orchestration

This skill is mandatory when the UserPromptSubmit hook injected it (`hooks/user_prompt_submit.py`).
The parent session must not implement domain work.

## When to Use

- Implement / fix / refactor / release / deploy / hotfix a product with the swarm.
- Explicit `/swarm` or "run the swarm" / "AgentSwarm".
- Hook additionalContext named this skill.

Don't use for: pure questions ("what is"), `/uplift`, `/think`.

## Prerequisites

- Agent-swarm checkout at `/root/src/repos/agent-swarm` (or `$SWARM_ROOT`).
- Target application repo (`--repo` / cwd).
- python3; bun optional; claude or grok CLI for unattended `swarm_run`.

## Procedure

1. Identify the target application repo (`--repo` or cwd).
2. `python3 /root/src/repos/agent-swarm/scripts/orch_plan.py --repo <app> --brief-text "<user request>" --pattern feature|hotfix|dependency --json`
   (or `bun scripts/ts/orch_plan.ts` with the same flags).
3. Spawn subagent `a01-orchestrator` (Claude Agent tool / Grok `spawn_subagent` `subagent_type=a01-orchestrator`) with the plan JSON and the user brief.
4. A01 plans and spawns: {slugs}.
5. Parent MUST NOT write application code, docs, or IaC.
6. Unattended alternative: `python3 scripts/swarm_run.py --repo <app> --runtime auto --json` (dry-run with `--dry-run`).

## Hook

`hooks/user_prompt_submit.py` is fail-open (always exit 0). On SDLC-shaped prompts it injects additionalContext telling the parent to load this skill and spawn a01-orchestrator.

## Verification

- A plan JSON exists under `<app>/.swarm/plans/` (the same `--repo` the run uses).
- Child slugs match agents.json.
- Parent transcript contains no product-code patches from the parent itself.
"""

def swarm_orchestrate_skill() -> str:
    """Phase 5: omp-native in-session driver (separate file, separate slug)."""
    return """---
name: swarm-orchestrate
description: "Drive AgentSwarm SDLC waves in-session via A01: status → tasks[] (explicit agent per task) → ingest → gates → DONE or ESCALATED. Parent never implements domain work."
disable-model-invocation: false
---

# swarm-orchestrate (in-session A01 driver)

## When to Use
- You are a01-orchestrator and a wave is ready, or the user explicitly asked for the full SDLC through the swarm.
- You were given a plan whose tasks carry `agent` fields.

Never use from a specialist session.

## Prerequisites
- Tools: swarm_status, swarm_ingest, task (and the normal read tools). You do **not** have the `swarm_gate` tool — you dispatch gate *agents* (a09 etc.) via `task`.
- A plan/correlation from swarm_plan or /swarm.
- Absolute SWARM_DIR for the target.

## Procedure

1. `swarm_status` (pass correlation if known) → obtain ready waves.
2. For a ready wave:
   - Emit **one** `task` call.
   - The `tasks` array contains one entry per plan task in the wave.
   - Every entry **must** contain the `agent` field (e.g. "a05-backend").
   - Gate agents (a08/a09/a10/a12) also receive `schemaMode: "strict"`.
3. After the children complete, `swarm_ingest` every `task.result`.
4. For gates: dispatch the corresponding gate *agent* (e.g. a09-reviewer) via another `task` call, passing the findings for that target. The gate agent's session will call its `swarm_gate` tool.
5. On CHANGES_REQUESTED: create rework tasks for the failed items and repeat the wave.
6. After two full rework loops on a task that still fails a required gate → ESCALATED + record `escalation.request`.
7. When the wave's gates are all green, move to the next wave or overall DONE.

## Parent contract
The session that loaded this skill (the human's top-level) **must never** do domain work. All implementation goes through A01 waves.

## Depth cap & plan mode
- If you have no `task` tool and are not in plan mode, you may only `yield {state: "BLOCKED", needs: "depth"}`.
- In plan mode: yield a plan; perform zero Task Store writes.

## Negatives you must honour
- Specialists never receive the `task` tool.
- A01 running inside a workpool child yields BLOCKED depth.
- Plan mode never mutates the Task Store.

## Post-run evidence
- `orch_status --history <corr>` shows the full transition chain and at least one signed gate verdict.
- Injected failure produces CHANGES_REQUESTED → rework → ESCALATED + escalation.request event.
"""


def yaml_str(text: str) -> str:
    """Encode text as a YAML double-quoted scalar (a JSON string literal is one)."""
    return json.dumps(text, ensure_ascii=False)


def omp_skill(a: dict) -> str:
    desc = yaml_str(
        f"{a['id']} {a['code']} {a['name']} — {a['description']} "
        f"Use when the swarm assigns capability {', '.join(a['capabilities'][:3])}."
    )
    return f"""---
name: {yaml_str(a['slug'])}
description: {desc}
---

# {a['name']} ({a['id']} {a['code']}) — omp

## When to Use

- The assignment capability is one of: {', '.join(a['capabilities'])}.
- The user names this agent, slug `{a['slug']}`, or code `{a['code']}`.

Don't use for: {DONT[a['id']]}

## Procedure

1. Dispatch via the omp `task` tool with `agent: {a['slug']}` (definition: `omp/agents/{a['slug']}.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: {DONT[a['id']]}
"""



def main() -> None:
    for a in AGENTS:
        d = ROOT / "skills" / a["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(agent_skill(a))
        print("wrote", d / "SKILL.md")
    # legacy root orchestrate (kept)
    od = ROOT / "skills" / "orchestrate"
    od.mkdir(parents=True, exist_ok=True)
    (od / "SKILL.md").write_text(orch_skill())
    print("wrote", od / "SKILL.md")

    # Phase 5: canonical omp swarm-orchestrate
    omp_orch = ROOT / "omp" / "skills" / "swarm-orchestrate"
    omp_orch.mkdir(parents=True, exist_ok=True)
    (omp_orch / "SKILL.md").write_text(swarm_orchestrate_skill())
    print("wrote", omp_orch / "SKILL.md")

    for a in AGENTS:
        d = ROOT / "omp" / "skills" / a["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(omp_skill(a), encoding="utf-8")
        print("wrote", d / "SKILL.md")

if __name__ == "__main__":
    main()
