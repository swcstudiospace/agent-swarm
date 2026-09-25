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
description: "Drive AgentSwarm SDLC waves in-session via A01: status → lease → one tasks[] call (explicit agent per task) → ingest (runs reconcile) → DONE or ESCALATED. Parent never implements domain work."
disable-model-invocation: false
---

# swarm-orchestrate (in-session A01 driver)

## When to Use
- You are a01-orchestrator and a wave is ready, or the user explicitly asked for the full SDLC through the swarm.
- You were given a plan whose tasks carry `agent` fields.

Never use from a specialist session.

## Prerequisites
- Tools: swarm_plan, swarm_status, swarm_transition, swarm_ingest, task (plus read tools). You do **not** have `swarm_gate`: the gate agents (a08-qa, a09-reviewer, a10-security, a12-release) run it themselves.
- A correlation from swarm_plan or /swarm.

## Procedure

1. `swarm_status` for the correlation → the ready tasks, each with its `agent` and gate notes (`gate`, `gate_for`).
2. Lease every task of the ready wave with `swarm_transition`: PLANNED → CLAIMED, then CLAIMED → IN_PROGRESS. Lease gate tasks before their gate agent runs: `swarm_gate` refuses a gate task that is not IN_PROGRESS.
3. Make **one** `task` call for the wave:
   - `tasks[]` holds one item per task, each with `agent: <slug>` and a task.assign payload: `task_id`, `correlation_id`, `capability`, `inputs`, `acceptance`, `risk_class`.
   - Add `schemaMode: "strict"` to every item for a08-qa, a09-reviewer, a10-security and a12-release.
   - Gate agents run `swarm_gate` themselves. Never pass them findings or a verdict.
4. `swarm_ingest` every child's task.result before the next wave. Ingest applies the result and runs reconcile itself; there is no separate reconcile call. Reconcile:
   - moves a target failing a required gate CHANGES_REQUESTED → IN_PROGRESS (rework, feedback in its notes) and creates the gate rerun task `<gate-task-id>.r<N>`;
   - moves a target passing every required gate APPROVED → DONE;
   - after MAX_REWORK_LOOPS=2 rework loops, moves a still-failing target to ESCALATED and emits `escalation.request`.
5. Missing yield (D-07): a child whose output ends with `SUBAGENT_WARNING_MISSING_YIELD`, or has no parseable task.result, is ingested as FAILED with `error: {code: "E-CONTRACT", message: "missing yield"}`, or as BLOCKED with `needs` when it stated a need. Never IN_REVIEW.
6. Refusals and blocks:
   - Ingest refuses a gate result with E-CONTRACT `review verdict mismatch:` or `gate script not run`: nothing transitions and the gate task stays leased (IN_PROGRESS). Re-dispatch that gate agent with the error text. Never transition around it.
   - A BLOCKED task: escalate to the human. Never self-release it.
7. FAILED tasks: nothing in-session retries them (only the headless runner does). For each task FAILED after ingest, read `attempt` from `swarm_status` (`max_attempts` defaults to 3):
   - `attempt` < `max_attempts`: `swarm_transition` FAILED → RETRY, re-lease it RETRY → CLAIMED → IN_PROGRESS, and re-dispatch it to its agent with the error in the payload;
   - otherwise: FAILED → ESCALATED, and report it as an escalation.
8. Loop: re-dispatch rework tasks (IN_PROGRESS after reconcile, already leased) to their agent with the feedback from notes. Repeat from step 1 until every task is DONE, ESCALATED, CANCELLED or BLOCKED awaiting a human, then yield the swarm.status summary listing every ESCALATED, FAILED and BLOCKED task (with its escalation or need).

## Parent contract
The session that loaded this skill (the human's top-level) **must never** do domain work. All implementation goes through A01 waves.

## Depth cap & plan mode
- If you have no `task` tool and are not in plan mode, you may only `yield {state: "BLOCKED", needs: "depth"}`.
- In plan mode: yield a plan; perform zero Task Store writes.

## Negatives you must honour
- Specialists never receive the `task` tool.
- A01 running inside a workpool child yields BLOCKED depth.
- Plan mode never mutates the Task Store.
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
