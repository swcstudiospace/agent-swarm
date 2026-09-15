# AgentSwarm — Claude Code project guide

This repo is both the **design spec** (`01-*.md` … `07-*.md`, `03-agents/`) and a **runnable
Claude Code subagent swarm** built from it. Fifteen agents (A01–A15) cover the SDLC; each one is
a Claude Code and Grok Build subagent whose Prompt-Uplift XML prompt lives in `prompts/` and whose tools are Python plus TypeScript twins (`scripts/ts/`). Skills: `skills/<slug>/SKILL.md`. Orchestration: `skills/orchestrate/SKILL.md` + `hooks/user_prompt_submit.py`.

## Layout

| Path | What it is |
|---|---|
| `03-agents/A??-*.md` | Original 7-section specs (source of truth for behaviour) |
| `prompts/A??-*.md` | XML-tagged system prompts derived from the specs (edit these, not `.claude/agents/`) |
| `agents.json` | Manifest: id, code, slug, capabilities, consumes/produces, tools, model, scripts |
| `.claude/agents/*.md` | **Generated** Claude Code subagents (`python3 scripts/build_agents.py`) |
| `.grok/agents/*.md` | **Generated** Grok Build subagents |
| `skills/<slug>/SKILL.md` | Per-agent + `orchestrate` skills (copy with `--install-workspace`) |
| `scripts/ts/` | TypeScript twins of every `scripts/*.py` tool |
| `hooks/user_prompt_submit.py` | Fail-open UserPromptSubmit classifier |
| `swarm/` | Runtime toolkit: envelope (signed `swarm.v1`), Task Store (SQLite state machine), gates, manifest, run log |
| `scripts/` | Per-agent tools (see table below) + orchestration (`orch_plan.py`, `orch_status.py`, `swarm_run.py`) |
| `.swarm/` | Runtime state (task DB, plans, verdicts, assignments, results, `events.jsonl`). Git-ignored. `SWARM_DIR` overrides. |
| `tests/` | `pytest -q` |

## Agent → subagent → scripts

| Agent | Subagent slug | Scripts |
|---|---|---|
| A01 ORCH | `a01-orchestrator` | `orch_plan.py`, `orch_status.py`, `swarm_run.py`, `build_agents.py` |
| A02 REQ | `a02-requirements` | `req_lint.py` |
| A03 ARCH | `a03-architect` | `arch_adr.py`, `arch_contract_check.py` |
| A04 UXD | `a04-ux-designer` | `ux_tokens.py` |
| A05 BE | `a05-backend` | `code_checks.py`, `be_contract_conformance.py` |
| A06 FE | `a06-frontend` | `code_checks.py`, `fe_a11y_check.py` |
| A07 DATA | `a07-data` | `data_migration_check.py` |
| A08 QA | `a08-qa` | `qa_gate.py` (quality gate verdict) |
| A09 REV | `a09-reviewer` | `rev_gate.py` (review gate verdict) |
| A10 SEC | `a10-security` | `sec_gate.py` (security gate verdict) |
| A11 DEVOPS | `a11-devops` | `devops_ci_check.py`, `devops_build_record.py` |
| A12 REL | `a12-release` | `rel_plan.py` (release gate + canary plan) |
| A13 OBS | `a13-observability` | `obs_slo.py` |
| A14 MAINT | `a14-maintenance` | `maint_deps.py` |
| A15 DOC | `a15-docs` | `docs_bundle.py` |

Every script shares one CLI contract (`swarm/script_base.py`): `--task-id`, `--correlation-id`,
`--root`, `--json`, `--dry-run`; exit 0 ok / 1 finding-fail / 2 taxonomy error; appends to
`.swarm/events.jsonl`. Gate scripts write signed verdicts to `.swarm/verdicts/` and the Task Store.

## Running the swarm

```bash
# 1. plan: brief → task DAG (patterns: feature | hotfix | dependency | custom --plan plan.json)
python3 scripts/orch_plan.py --brief brief.md --pattern feature --risk-class medium

# 2. run autonomously (headless claude -p --agent <slug> per task, parallel where the DAG allows)
python3 scripts/swarm_run.py --repo /path/to/codebase --max-parallel 3

# simulate without calling Claude
python3 scripts/swarm_run.py --dry-run

# 3. inspect
python3 scripts/orch_status.py
python3 scripts/orch_status.py --history T-be
```

In-session alternative: ask for the `a01-orchestrator` subagent (or say "run the swarm on …");
it plans with `orch_plan.py` and delegates each ready task via the Agent tool using the slugs above.

## Rules the runtime enforces (mirrors the spec)

- Only A01 writes task state; agents report `task.status`, illegal transitions are rejected.
- Fail-closed gates by risk class — low: review · medium: review+quality · high: +security+release.
- Most restrictive verdict wins; a failing gate ⇒ CHANGES_REQUESTED with findings, max 2 rework
  loops, then ESCALATED with an `escalation.request` event. Verdicts issued before a rework are stale.
- `task.assign` and gate verdicts are signed (`SWARM_ED25519_KEY` hex seed, or HMAC via `SWARM_SIGNING_KEY`).
- Destructive/L3+/L4 actions: agents stop and report `BLOCKED` with `needs: human-approval`.

## Editing

- Change behaviour in `prompts/` (keep the XML tag set — `tests/test_swarm.py` checks it), then run
  `python3 scripts/build_agents.py` and commit the regenerated `.claude/agents/`.
- New agent: add to `agents.json`, write `prompts/`, scripts, regenerate. See `07-scalability.md`.
- Keep scripts stdlib-only; optional tools must degrade to `skipped:tool-missing`.
