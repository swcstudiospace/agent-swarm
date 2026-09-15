# Spec: Uplift AgentSwarm for Claude Code + Grok Build

**Status:** Binding for the 2026-09-15 implementation plan.
**Source request:** Uplift the 15 AgentSwarm prompts in Prompt-Uplift style; add per-agent Python and TypeScript scripts; add per-agent SKILL.md files; add an orchestration SKILL.md that autonomously forces subagents to run; ship as Claude Code and Grok Build subagents in the Claude folder.

## Problem

AgentSwarm already has 15 XML-tagged prompts (~90 lines each), Python tools, and generated Claude Code subagents under `agent-swarm/.claude/agents/`. Gaps:

1. Prompts are short role sheets, not Prompt-Uplift operational specs (no SYSTEM_ROLE / SCOPE / WORKFLOW / ACCEPTANCE_CRITERIA / GRAPH_OF_THOUGHT density).
2. No TypeScript tools, so Grok Build sessions that prefer Bun/TS have nothing to run.
3. No per-agent SKILL.md, so neither Claude Code nor Grok auto-invokes the right specialist.
4. No orchestration skill or UserPromptSubmit/Stop hook, so the parent agent does the work itself instead of spawning `a01-orchestrator` … `a15-docs`.
5. Generated agents exist only as Claude Code frontmatter (`tools:`, `model: sonnet|opus|haiku`). Grok Build discovers agents from `.grok/agents/` with a different frontmatter (`prompt_mode`, `permission_mode`, `agents_md`).
6. `swarm_run.py` only invokes `claude -p --agent`. Grok headless is `grok -p --agent <NAME> --yolo`.

## Goals

1. **Uplifted prompts.** Every file in `prompts/A??-*.md` remains a single `<agent>` root (existing tests) and gains Prompt-Uplift children listed under Required XML. Each prompt is an operational spec a senior would hand a teammate: procedures, states, failure modes, exact script invocations, spawn contracts. Target 250–450 lines per prompt, sourced from `03-agents/` plus the existing prompt — do not invent repo paths, package names, or APIs that are not in those files or `agents.json`.
2. **Dual subagents.** `scripts/build_agents.py` (and a TypeScript twin) generate:
   - Claude Code: YAML `name`, `description`, `tools`, `model` → `agent-swarm/.claude/agents/<slug>.md` and workspace `.claude/agents/<slug>.md`
   - Grok Build: YAML `name`, `description`, `prompt_mode: full`, `model: inherit`, `permission_mode: default`, `agents_md: true` → `agent-swarm/.grok/agents/<slug>.md` and workspace `.grok/agents/<slug>.md`
3. **Python + TypeScript tools.** Every existing `scripts/*.py` agent tool keeps working. Each has a TypeScript counterpart under `scripts/ts/` with the same CLI flags and JSON object keys. Shared contract: `--task-id`, `--correlation-id`, `--root`, `--json`, `--dry-run`; exit 0 ok / 1 finding-fail / 2 taxonomy error.
4. **Per-agent skills.** One SKILL.md per agent plus one orchestration skill. Source of truth: `agent-swarm/skills/<slug>/SKILL.md`. Installed copy: workspace `.claude/skills/agent-swarm/<slug>/SKILL.md` (Claude Code + Grok Claude-compat discovery).
5. **Autonomous orchestration.** Skill `agent-swarm-orchestrate` plus a fail-open UserPromptSubmit hook that, when the user prompt is SDLC-shaped, injects instructions to load the orchestration skill and spawn swarm subagents instead of doing domain work in the parent. Hook never blocks the user prompt (exit 0 always). `swarm_run.py` gains `--runtime claude|grok|auto`.

## Non-goals

- Replacing NATS/Postgres/etcd from the architecture docs with a real distributed bus.
- Raising autonomy ceilings or bypassing L3/L4 human gates.
- Rewriting `swarm/` (envelope, Task Store, gates) except as needed for grok runtime and dual install.
- Committing unrelated workspace dirt (`pumpgrok/`, zips, other untracked repos).

## Required XML (every `prompts/A??-*.md`)

Existing tags (must remain): `role`, `inputs`, `outputs`, `output_format`, `tools`, `decision_logic`, `autonomy`, `error_handling`, `metrics`, `security`, `constraints`.

New required tags (Prompt Uplift density):

| Tag | Purpose |
|---|---|
| `system_role` | Stance for this subagent (senior specialist, fail-closed, etc.) |
| `scope` | What this run is allowed to produce |
| `out_of_scope` | Adjacent work this agent must refuse (other agents' single-writer zones) |
| `workflow` | Numbered procedure with checkable completion criteria |
| `acceptance_criteria` | Observable, testable outcomes for a successful run |
| `states` | Empty / blocked / in-progress / in-review / failed / escalated handling |
| `graph_of_thought` | Nested decision nodes (understand → decompose → decide → act) |
| `graceful_degradation` | What to do when a tool/binary is missing (status skipped:tool-missing, never fabricate) |
| `security_and_validation` | Input validation, secrets, signed envelopes |

`<script path="...">` entries must list **both** `python3 scripts/<name>.py` and `bun scripts/ts/<name>.ts` (or `npx tsx` fallback documented in the skill).

## Dual agent frontmatter

Claude (`*.md` under `.claude/agents/`):

```yaml
name: <slug>
description: "<id> <code> — <one-line description from agents.json, trigger-rich>"
tools: <comma list from agents.json>
model: inherit
```

`model` is `inherit` for both harnesses (do not emit `opus`/`sonnet`/`haiku` — those are Claude-only and break Grok). A01/A03/A09/A10 stay "most capable" via the description, not a hardcoded Claude model id.

Grok (`*.md` under `.grok/agents/`):

```yaml
name: <slug>
description: >
  <id> <code> — <description>. Use when <triggers>. Spawn as subagent_type <slug>.
prompt_mode: full
model: inherit
permission_mode: default
agents_md: true
```

Body after frontmatter: swarm_runtime preamble + uplifted prompt. Grok preamble must mention `spawn_subagent` with `subagent_type: <slug>` and Grok tools (`read_file`, `grep`, `run_terminal_command`); Claude preamble keeps Claude tool names.

## Skill contract

Each `skills/<slug>/SKILL.md`:

- Frontmatter: `name`, `description` (trigger-rich, includes `/<slug>` and the agent id/code), `disable-model-invocation: false`
- Orchestration skill name: `agent-swarm-orchestrate`
- Body: When to Use, Prerequisites, How to Run (Python and TS), Procedure (numbered), Pitfalls, Verification
- Procedure must tell the agent to run the scripts, then (for A01) spawn the other 14 by slug
- Do not duplicate the full prompt; point at `prompts/A??-*.md` as the subagent body

## Hook contract

Command hook reads Claude UserPromptSubmit JSON on stdin (`prompt` or `user_prompt` string). Classifies SDLC-shaped work with these positive signals (case-insensitive): `implement`, `feature`, `bug`, `fix`, `refactor`, `release`, `deploy`, `hotfix`, `requirements`, `architecture`, `code review`, `security audit`, `run the swarm`, `agent-swarm`, `a01-orchestrator`. Negative signals skip injection: `/uplift`, `/think`, `what is`, `explain only`.

On match, stdout is Claude hook JSON:

```json
{ "additionalContext": "<markdown instructing the parent to load agent-swarm-orchestrate and spawn a01-orchestrator; do not implement domain work in the parent>" }
```

On no match or any error: stdout empty or `{}`. Exit code always 0.

Grok hook file `.grok/hooks/agent-swarm.json` runs the same command on UserPromptSubmit.

## Runtime

`swarm_run.py --runtime auto|claude|grok`:

- `claude`: existing `claude -p --agent <slug> --output-format json --permission-mode <mode>`
- `grok`: `grok -p --agent <slug> --output-format json --yolo --cwd <repo>`
- `auto`: use grok if `SWARM_RUNTIME=grok` or (`grok` on PATH and no `claude`); else claude

## Tests (must pass)

- Existing `pytest -q` in `agent-swarm/` stays green.
- New tests: uplifted XML tags present; Claude + Grok agent files exist and frontmatter keys match; every Python script has a TS twin; TS `--dry-run --json` returns `status` in `{ok,fail}` and the same top-level keys as Python on a shared fixture; skills exist for 15 slugs + orchestrate; hook injects on "implement a feature" and stays silent on "what is a monad"; `swarm_run --runtime grok --dry-run` still completes a feature plan.
- Do not assert live `claude`/`grok` network calls.

## Layout (source of truth)

```
agent-swarm/
  prompts/A??-*.md          # uplifted XML
  skills/<slug>/SKILL.md    # 15 agents + orchestrate
  scripts/*.py              # existing Python
  scripts/ts/*.ts           # TypeScript twins + script_base.ts + build_agents.ts
  hooks/user_prompt_submit.py
  .claude/agents/<slug>.md  # generated Claude
  .grok/agents/<slug>.md    # generated Grok
```

Workspace install (mega-repo `/root/src/repos`):

```
.claude/agents/<slug>.md
.claude/skills/agent-swarm/<slug>/SKILL.md
.claude/settings.json          # UserPromptSubmit hook (preserve enabledPlugins)
.grok/agents/<slug>.md
.grok/hooks/agent-swarm.json
```
