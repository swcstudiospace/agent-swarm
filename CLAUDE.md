# AgentSwarm — Claude Code project guide

This repo is both the **design spec** (`01-*.md` … `07-*.md`, `03-agents/`) and a **runnable
Claude Code subagent swarm** built from it. Fifteen agents (A01–A15) cover the SDLC; each one is
a Claude Code and Grok Build subagent, an omp task agent and a Trae SOLO prompt, generated from the Prompt-Uplift XML prompt in `prompts/`; its tools are Python plus TypeScript twins (`scripts/ts/`). Skills: `skills/<slug>/SKILL.md`. Orchestration: `skills/orchestrate/SKILL.md` + `hooks/user_prompt_submit.py` (Claude Code), or `/swarm <brief>` + the `before_agent_start` context hook (omp).

## Layout

| Path | What it is |
|---|---|
| `03-agents/A??-*.md` | Original 7-section specs (source of truth for behaviour) |
| `prompts/A??-*.md` | XML-tagged system prompts derived from the specs (edit these, not `.claude/agents/`) |
| `agents.json` | Manifest: id, code, slug, capabilities, consumes/produces, tools, model, scripts |
| `.claude/agents/*.md` | **Generated** Claude Code subagents (`python3 scripts/build_agents.py`) |
| `.grok/agents/*.md` | **Generated** Grok Build subagents |
| `omp/agents/*.md` | **Generated** omp task agents (`build_agents.py`; `--check` covers them). `tools:` adds the hidden swarm tools per agent (`SWARM_TOOLS`). Not a sandbox: the `tool_call` guard (`omp/src/guard.ts`) enforces the autonomy ceiling, swarm-state and depth rules in swarm sessions; read-only is unenforced |
| `omp/skills/<slug>/SKILL.md` | **Generated** omp skills (`build_agents.py`, `_write_skills.py`) |
| `omp/package.json`, `omp/src/` | **Hand-written** omp extension package: five typed `swarm_*` tools over one python bridge (`omp/src/bridge.ts`), the `tool_call` guard (`guard.ts`), the `before_agent_start` context hook (`hooks.ts`) and the `/swarm` command (`commands.ts`) |
| `omp/test/` | **Hand-written** `bun:test` suite for the package (`cd omp && bun run test`) |
| `.omp/config.yml` | Committed omp project wiring: `extensions:` → `- omp` (the only file in `.omp/`) |
| `.trae/` | **Generated** Trae SOLO kit (`python3 scripts/build_trae_agents.py`): 15 XML prompts ≤ 10,000 chars, `registration.json`, `commands/swarm.md` |
| `skills/<slug>/SKILL.md` | Per-agent + `orchestrate` skills (copy with `--install-workspace`) |
| `scripts/ts/` | TypeScript twins of every `scripts/*.py` tool |
| `hooks/user_prompt_submit.py` | Fail-open UserPromptSubmit classifier: injects swarm context only on SDLC-shaped prompts (`tests/fixtures/classifier_prompts.json` pins it together with the omp hook) |
| `hooks/autonomous_run.py` | Plan + run in one detached process (120 s dedupe lock, log at `$SWARM_DIR/autonomous.log`). The run cap (`SWARM_AUTONOMOUS_RUN_CAP_S`, default 3600) SIGTERMs the runner so it can end its sessions, and SIGKILLs only after `SWARM_AUTONOMOUS_RUN_GRACE_S` (default 15). Started by an external plugin; `--runtime` forwarded to `swarm_run.py` |
| `swarm/` | Runtime toolkit: envelope (signed `swarm.v1`), Task Store (SQLite state machine), gates, manifest, run log |
| `scripts/` | Per-agent tools (see table below) + orchestration (`orch_plan.py`, `orch_status.py`, `swarm_run.py`) + generators (`build_agents.py`, `build_trae_agents.py`, `_write_skills.py`, `_install_omp.py`) |
| `.swarm/` | Runtime state (task DB, plans, verdicts, assignments, results, `events.jsonl`). Resolved per call: `SWARM_DIR` (made absolute) → `<git toplevel of --root/--repo or cwd>/.swarm` → `<dir>/.swarm`. Created with its own `.gitignore` of `*`. |
| `tests/` | `pytest -q` |

## Agent → subagent → scripts

| Agent | Subagent slug | Scripts |
|---|---|---|
| A01 ORCH | `a01-orchestrator` | `orch_plan.py`, `orch_status.py`, `swarm_run.py` |
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
`--root`, `--json`, `--dry-run`, exact spellings only (`allow_abbrev=False`: `--ing` exits 2); exit 0 ok / 1 finding-fail / 2 taxonomy error; appends to
`.swarm/events.jsonl`. Gate scripts write signed verdicts to `.swarm/verdicts/` and the Task Store.

## Running the swarm

```bash
# 1. plan: brief → task DAG (patterns: feature | hotfix | dependency | parallel | custom --plan plan.json)
python3 scripts/orch_plan.py --repo /path/to/codebase --brief brief.md --pattern feature --risk-class medium
#    parallel: a blast-radii JSON block, --slices, or --slices-json. Same agent, several lanes,
#    each on its own worktree; join merges; one Greptile review of the merged branch.

# 2. run autonomously (one headless session per task, parallel where the DAG allows, including several of one agent)
#    --runtime auto (claude or grok) | claude | grok | omp; --runtime omp [--omp-bin omp] runs headless omp -p, never auto-picked (or set SWARM_RUNTIME=omp)
python3 scripts/swarm_run.py --repo /path/to/codebase --max-parallel 3 --runtime auto

# simulate without model calls (a bare --dry-run with no --repo runs the plan in the cwd's .swarm)
python3 scripts/swarm_run.py --repo /path/to/codebase --dry-run

# 3. inspect
python3 scripts/orch_status.py --repo /path/to/codebase
python3 scripts/orch_status.py --repo /path/to/codebase --history T7f3a-be
```

Pass the same `--repo` to all three (or export one `SWARM_DIR` first): state lives in `<repo>/.swarm`,
so a plan written to one repo's store is invisible to a run against another.

In-session alternative: ask for the `a01-orchestrator` subagent (or say "run the swarm on …");
it plans with `orch_plan.py` and delegates each ready task via the Agent tool using the slugs above.

## omp extension package

Start omp **at the repo root**: `.omp/config.yml` is project config, which omp reads from the cwd only,
and its `omp` entry resolves against the cwd. That makes `omp/` an extension root, so its tools, agents and
skills load together. Never also add `.omp/extensions/` or a symlink (the factory would load twice).
Swarm runs expect `task.isolation` off, so every session resolves the same Task Store.

Install into another workspace (omp targets: `omp/agents/`, `omp/skills/` and the `omp/` extension package):

```bash
python3 scripts/build_agents.py --install-workspace /path/to/ws                  # link (default)
python3 scripts/build_agents.py --install-workspace /path/to/ws --omp-mode copy  # agents + skills only
python3 scripts/build_agents.py --install-workspace /path/to/ws --dry-run        # print plan + config diff + env files, write nothing
python3 scripts/build_agents.py --install-workspace /path/to/ws --no-substrate   # skip the substrate-mcp wiring
python3 scripts/build_agents.py --install-workspace /path/to/ws --with-a01-complete-hook  # also register the A01-complete Stop hook (off by default)
```

- It regenerates, wires substrate-mcp, copies the Claude/Grok agents, skills and the UserPromptSubmit hook (the A01-complete Stop hook only with `--with-a01-complete-hook`), then runs the omp step. The install step writes only inside `<ws>` (never into this repo or `~/.omp`), except the per-agent substrate credential env files under `${XDG_CONFIG_HOME:-~/.config}/agent-swarm/agents/` (outside the workspace by design; skipped with `--no-substrate`); the regeneration pass may update this repo's tracked generated files. It refuses (exit 2, nothing written) a workspace inside this checkout, equal to `$HOME` or inside `~/.omp`, and any destination reached through a symlink (the file or a parent dir under `<ws>`).
- substrate-mcp ([docs/substrate-workspace.md](docs/substrate-workspace.md)): the `substrate` entry in `<ws>/.mcp.json` and `<ws>/.grok/config.toml` names `${SUBSTRATE_TOKEN}`, never a value; each agent's token goes to a 0600 env file outside the workspace, from `SUBSTRATE_TOKEN_<SURFACE>` in your environment. A missing token prints what to create and exits 2.
- `link` merges the package realpath into `<ws>/.omp/config.yml` `extensions:` (a host path by design). Start omp at `<ws>`: the config is read from the cwd only. If that file has no `extensions` key, the inherited list is carried over, because a project array replaces the user array: `<ws>/.omp/settings.json`, else `config.yml|config.yaml` in the omp user agent dir (profile dir via `OMP_PROFILE`/`PI_PROFILE`, else `PI_CODING_AGENT_DIR`, else `~/.omp/agent`), else that dir's `settings.json`. A user YAML without `extensions` suppresses the legacy `settings.json`.
- `copy` copies agents and skills into `<ws>/.omp/agents|skills`: no tools, no guard, no `/swarm`, no context hook. Re-run it after regenerating.
- Both modes print a `WARNING shadow:` line per same-`name` agent or skill that omp resolves first: project `.omp/agents|skills` in `<ws>` or its ancestors, the user `agents|skills` dirs (profile-aware), earlier `extensions:` entries, `skills.customDirectories`. Only files omp would load count (agents need `name` + `description`; skills need `description` and are skipped on `enabled: false`). `.claude/*` and `.agents/skills` do not shadow. Copy mode refuses (exit 2, nothing written) when a strict ancestor of `<ws>` shadows the package — the copies would run guard-less with shadowed skills; re-run with `--allow-shadowed-copy` to proceed anyway, or use link mode to keep the tools and guard.
- Start a fresh omp session after every install or `build_agents.py` regeneration.
- `task.maxRecursionDepth`: the default 2 is enough (main → `a01-orchestrator`, which keeps `task` → specialists, which never get `task` at any depth). Set 3 only when A01 is itself spawned by another subagent; otherwise it yields `BLOCKED needs: depth`. Never use a negative (unlimited) value.

  ```yaml
  # <ws>/.omp/config.yml or ~/.omp/agent/config.yml
  task:
    maxRecursionDepth: 3
  ```

Swarm-slug sessions get a `## AgentSwarm runtime` system-prompt part naming `Runtime root: <abs agent-swarm root>`; the omp preambles run `python3 <runtime root>/scripts/<script>.py … --root <repo> --json`. `omp/src` imports nothing outside `omp/` (`omp/src/paths.ts`); a relocated `omp/` needs `SWARM_ROOT`.

The five tools are `hidden` + `essential`; an agent only gets the ones its generated `tools:` names:

| Tool | Granted to | Runs |
|---|---|---|
| `swarm_plan` | A01 | `orch_plan.py` (feature / hotfix / dependency) |
| `swarm_status` | A01 | `orch_status.py` (read-only) |
| `swarm_ingest` | A01 | `orch_status.py --ingest` (task.result v1 object) |
| `swarm_transition` | A01 | `orch_status.py --transition` |
| `swarm_gate` | A08, A09, A10, A12 | `qa_gate` / `rev_gate` / `sec_gate` / `rel_plan` (findings in, signed verdict out) |

Mutating tools and `/swarm` refuse with E-POLICY in omp plan mode. Tests: `cd omp && bun run test` (package suite;
`extension.test.ts` runs in its own process) and `bun test tests/ts` (root TS suite).

Hooks and the guard (Phase 4; the full contract, rule ids and residuals are in `AGENTS.md` **Hooks**):

- `before_agent_start`: SDLC-shaped prompts in a top-level session get a `## AgentSwarm` part pointing at `/swarm` and `task` with `a01-orchestrator`; subagents, `SWARM_CHILD=1`/`SWARM_AGENT` sessions and non-SDLC prompts get nothing. An Ultrathink/Prompt-Uplift XML prompt is classified by its unescaped `<ORIGINAL>` text (else the whole prompt), matching the plugin's headless kickoff.
- `tool_call` (swarm sessions, identity from `session_init.agent` else `SWARM_AGENT`): the `RULES` table in `omp/src/guard.ts` blocks with `BLOCKED needs: human-approval (<capability>: <rule id>)`; `swarm_transition`/`swarm_ingest` from anyone but A01 block with `BLOCKED needs: human-approval (swarm-state)` in every session; A01 without `task` can only yield `BLOCKED needs: depth`; `eval`, MCP tools (`mcp__*` calls and `write` to `xd://mcp__*`, `(mcp: …)`) and writes into `.swarm/`, `.omp/`, `~/.omp` block.
- `/swarm <brief> [--pattern=feature|hotfix|dependency|parallel] [--risk=low|medium|high]`: plans through the bridge (same argv as `swarm_plan`, correlation id = uuid5 of pattern, risk and brief, so an identical brief reuses its plan), then sends a `[agent-swarm:dispatch]` prompt that calls `task` once with `a01-orchestrator` and `{correlation_id, capability: "plan.execute", ready_tasks}`, and holds the session on `isIdle()` until that turn ends (10 s start timeout; hold cap `SWARM_DISPATCH_HOLD_MS`, else 30 min with a UI and unbounded in `-p`/rpc; a cap expiry is a warning saying the plan was dispatched) before draining with `waitForIdle`. Usage, plan mode and `E-CONTRACT` conflicts never dispatch; without a UI they are reported on stderr as `[/swarm] …`.

## Rules the runtime enforces (mirrors the spec)

- Only A01 writes task state; agents report `task.status`, illegal transitions are rejected.
- Fail-closed gates by risk class — low: review · medium: review+quality · high: +security+release.
- Most restrictive verdict wins; a failing gate ⇒ CHANGES_REQUESTED with findings, max 2 rework
  loops, then ESCALATED with an `escalation.request` event. Verdicts issued before a rework are stale.
- `task.assign` and gate verdicts are signed (`SWARM_ED25519_KEY` hex seed, or HMAC via `SWARM_SIGNING_KEY`). With neither set, `SWARM_ALLOW_INSECURE_DEV_KEY=1` opts into the dev key; otherwise gates stay advisory, APPROVED fails closed, and `swarm_run` (including `--dry-run`) refuses before any task is claimed. A keyless gate still fails when a per-target finding is blocking, and records no verdict row.
- Destructive/L3+/L4 actions: agents stop and report `BLOCKED` with `needs: human-approval`.

## Editing

- Change behaviour in `prompts/` (keep the XML tag set — `tests/test_swarm.py` checks it), then run
  `python3 scripts/build_agents.py` (`.claude/agents/`, `.grok/agents/`, `omp/agents/`, `omp/skills/`) and
  `python3 scripts/build_trae_agents.py` (`.trae/`), and commit the regenerated files.
- New agent: add to `agents.json`, write `prompts/`, scripts, regenerate. See `07-scalability.md`.
- Keep scripts stdlib-only; optional tools must degrade to `skipped:tool-missing`.
