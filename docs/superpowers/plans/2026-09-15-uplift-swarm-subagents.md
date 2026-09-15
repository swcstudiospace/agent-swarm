# Uplift AgentSwarm Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uplift the 15 AgentSwarm prompts into Prompt-Uplift operational specs, add TypeScript twins of every agent script, add per-agent plus orchestration SKILL.md files, generate Claude Code and Grok Build subagents into the Claude (and Grok) folders, and hook UserPromptSubmit so SDLC work autonomously spawns swarm subagents.

**Architecture:** Keep `swarm/` (envelope, Task Store, gates, Python `AgentScript`) as the runtime kernel. Expand `scripts/build_agents.py` to emit two frontmatter dialects and install into workspace `.claude/` and `.grok/`. Add `scripts/ts/` with a Bun `script_base.ts` that matches the Python CLI/JSON contract. Skills live in `agent-swarm/skills/` and are copied to `.claude/skills/agent-swarm/`. A fail-open hook classifies the user prompt and injects orchestration instructions.

**Tech Stack:** Python 3.12 stdlib + pytest (existing); TypeScript via Bun (`bun` on PATH, `bun test`); Claude Code subagent markdown; Grok Build agent markdown; Claude/Grok UserPromptSubmit hooks.

**Spec:** `agent-swarm/docs/superpowers/specs/uplift-swarm-subagents.md`

## Global Constraints

- Work only under `agent-swarm/` plus workspace install targets `.claude/agents/a*.md`, `.claude/skills/agent-swarm/`, `.claude/settings.json` (preserve `enabledPlugins`), `.grok/agents/`, `.grok/hooks/agent-swarm.json`. Do not add or commit unrelated repos (`pumpgrok/`, zips, other untracked trees).
- Every `prompts/A??-*.md` keeps root `<agent id="A\d\d"` and existing tags: `role`, `inputs`, `outputs`, `output_format`, `tools`, `decision_logic`, `autonomy`, `error_handling`, `metrics`, `security`, `constraints`. Add required uplift tags: `system_role`, `scope`, `out_of_scope`, `workflow`, `acceptance_criteria`, `states`, `graph_of_thought`, `graceful_degradation`, `security_and_validation`.
- Do not invent repository-specific facts (paths, libraries, APIs) that are not in `03-agents/`, `prompts/`, `agents.json`, or `scripts/`.
- Python scripts stay stdlib-only. TypeScript scripts depend only on Bun (no npm packages). Optional binaries degrade to `skipped:tool-missing`.
- Shared CLI on every agent script (py and ts): `--task-id`, `--correlation-id`, `--root`, `--json`, `--dry-run`. Exit 0 ok / 1 finding-fail / 2 taxonomy error.
- Claude agent `model:` is `inherit` (never `opus`/`sonnet`/`haiku`). Grok agents use `prompt_mode: full`, `model: inherit`, `permission_mode: default`, `agents_md: true`.
- Hooks are fail-open: always exit 0; never block the user prompt.
- Fail-closed gates, single-writer ownership, L3/L4 human-approval, max 2 rework loops — unchanged.
- Tests: `cd agent-swarm && python3 -m pytest -q` must stay green. New tests do not call live Claude/Grok APIs.
- Commits: conventional messages (`feat(swarm): ...`). Stage only the files this task lists.

---

### Task 1: Dual-runtime generator, TypeScript script_base, package.json

**Files:**
- Create: `agent-swarm/package.json`
- Create: `agent-swarm/tsconfig.json`
- Create: `agent-swarm/scripts/ts/script_base.ts`
- Create: `agent-swarm/scripts/ts/build_agents.ts`
- Create: `agent-swarm/tests/test_uplift_harness.py`
- Create: `agent-swarm/tests/ts/script_base.test.ts`
- Modify: `agent-swarm/scripts/build_agents.py`
- Modify: `agent-swarm/tests/test_swarm.py` (generated-agent assertions for both dialects and `model: inherit`)
- Test: `agent-swarm/tests/test_uplift_harness.py`, `agent-swarm/tests/test_swarm.py`, `agent-swarm/tests/ts/script_base.test.ts`

**Interfaces:**
- Consumes: `agents.json`, existing `PREAMBLE` in `build_agents.py`, Python `AgentScript` flags
- Produces: `render_claude(agent, defaults) -> str`, `render_grok(agent, defaults) -> str`, `install_targets() -> list[Path]`; TS `runAgentScript({ agentId, name, run, addArgs })`; generated files under `agent-swarm/.claude/agents/` and `agent-swarm/.grok/agents/`

- [ ] **Step 1: Write the failing tests**

`tests/test_uplift_harness.py`:

```python
"""Harness tests for dual Claude/Grok agent generation and TS script_base."""
import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def test_build_agents_writes_grok_and_claude(tmp_path, monkeypatch):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_agents.py"), "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for slug in ("a01-orchestrator", "a15-docs"):
        claude = (ROOT / ".claude" / "agents" / f"{slug}.md").read_text()
        grok = (ROOT / ".grok" / "agents" / f"{slug}.md").read_text()
        assert claude.startswith("---\n")
        assert "model: inherit" in claude.split("---", 2)[1]
        assert "prompt_mode: full" in grok
        assert "agents_md: true" in grok
        assert "model: opus" not in claude and "model: sonnet" not in claude

def test_ts_script_base_dry_run_json():
    ts = ROOT / "scripts" / "ts" / "script_base.ts"
    assert ts.exists()
```

`tests/ts/script_base.test.ts`:

```typescript
import { expect, test } from "bun:test";
import { parseAgentArgs, EXIT } from "../../scripts/ts/script_base.ts";

test("parseAgentArgs reads shared flags", () => {
  const a = parseAgentArgs(["--task-id", "T-1", "--json", "--dry-run", "--root", "/tmp"]);
  expect(a.taskId).toBe("T-1");
  expect(a.json).toBe(true);
  expect(a.dryRun).toBe(true);
  expect(EXIT.OK).toBe(0);
  expect(EXIT.FAIL).toBe(1);
  expect(EXIT.ERROR).toBe(2);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_uplift_harness.py -q`
Expected: FAIL (missing `.grok/agents` and/or `model: inherit`)

Run: `cd /root/src/repos/agent-swarm && bun test tests/ts/script_base.test.ts`
Expected: FAIL (file missing) or bun missing → install bun is out of scope; if bun is absent, skip TS tests with pytest.importorskip-style `shutil.which("bun")` skip in Python and still create the TS files.

- [ ] **Step 3: Write package.json, tsconfig, script_base.ts**

`package.json`:

```json
{
  "name": "agent-swarm",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "bun test",
    "build-agents": "bun scripts/ts/build_agents.ts"
  }
}
```

`tsconfig.json`: `{ "compilerOptions": { "target": "ES2022", "module": "ES2022", "moduleResolution": "bundler", "strict": true, "noEmit": true, "types": ["bun-types"] }, "include": ["scripts/ts/**/*.ts", "tests/ts/**/*.ts"] }`

`scripts/ts/script_base.ts` must:

- Export `EXIT = { OK: 0, FAIL: 1, ERROR: 2 }`
- Export `parseAgentArgs(argv: string[]): { taskId?: string; correlationId?: string; json: boolean; dryRun: boolean; root: string; rest: string[] }`
- Parse `--task-id`, `--correlation-id`, `--json`, `--dry-run`, `--root` (default `.`)
- Export `async function runAgentScript(opts: { agentId: string; name: string; run: (args: ReturnType<typeof parseAgentArgs>) => Promise<Record<string, unknown>> | Record<string, unknown> }): Promise<number>`
- On success print JSON if `--json`, else a one-line `[agent/name] STATUS`; return FAIL when `status === "fail"`
- On throw, print `{ status: "error", error: { code, message } }` and return 2
- Append a line to `$SWARM_DIR/events.jsonl` or `root/.swarm/events.jsonl` when the dir exists (create if SWARM_DIR set)

- [ ] **Step 4: Extend build_agents.py**

Add `render_grok` and write both dialects. Claude preamble stays XML `<swarm_runtime>` but `model: inherit`. Grok preamble is markdown that says: you are a Grok Build subagent; spawn siblings with `spawn_subagent` `subagent_type` = slug; run tools via `python3 scripts/<name>.py` or `bun scripts/ts/<name>.ts`.

CLI: keep `--check` and `--only`. Add `--install-workspace PATH` (optional). Task 1 writes **in-repo** `.claude/agents` and `.grok/agents` only; workspace install is Task 5.

`scripts/ts/build_agents.ts` may call the Python generator (`bun` spawning `python3 scripts/build_agents.py`) — do not duplicate render logic in two languages. If you implement render in TS, Python `--check` must still pass.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/src/repos/agent-swarm && python3 scripts/build_agents.py && python3 -m pytest tests/test_swarm.py tests/test_uplift_harness.py -q`
Expected: PASS

Run: `cd /root/src/repos/agent-swarm && bun test tests/ts/script_base.test.ts`
Expected: PASS (or skip documented in the report if bun is missing — then still emit the TS sources)

- [ ] **Step 6: Commit**

```bash
git add agent-swarm/package.json agent-swarm/tsconfig.json agent-swarm/scripts/ts/script_base.ts agent-swarm/scripts/ts/build_agents.ts agent-swarm/scripts/build_agents.py agent-swarm/tests/test_uplift_harness.py agent-swarm/tests/ts/script_base.test.ts agent-swarm/.claude/agents agent-swarm/.grok/agents agent-swarm/tests/test_swarm.py
git commit -m "feat(swarm): dual Claude/Grok agent generator and TS script_base"
```

---

### Task 2: Uplift all 15 agent prompts

**Files:**
- Modify: `agent-swarm/prompts/A01-orchestrator.md` … `agent-swarm/prompts/A15-docs.md` (all 15)
- Modify: `agent-swarm/tests/test_swarm.py` (`test_prompts_are_xml_tagged` — assert new tags)
- Modify: `agent-swarm/scripts/build_agents.py` only if preamble must mention TS scripts
- Create/overwrite (generated): `agent-swarm/.claude/agents/*.md`, `agent-swarm/.grok/agents/*.md`
- Test: `agent-swarm/tests/test_swarm.py`

**Interfaces:**
- Consumes: `03-agents/A??-*.md` (7-section specs), current prompts, `agents.json` scripts lists, Task 1 generator
- Produces: uplifted `<agent>` documents; regenerated dual agent files

- [ ] **Step 1: Expand `test_prompts_are_xml_tagged`**

In `tests/test_swarm.py`, the required tag tuple becomes:

```python
REQUIRED_TAGS = (
    "role", "inputs", "outputs", "output_format", "tools", "decision_logic",
    "autonomy", "error_handling", "metrics", "security", "constraints",
    "system_role", "scope", "out_of_scope", "workflow", "acceptance_criteria",
    "states", "graph_of_thought", "graceful_degradation", "security_and_validation",
)
```

Also assert each prompt contains `scripts/ts/` and `python3 scripts/`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_swarm.py::test_prompts_are_xml_tagged -q`
Expected: FAIL missing new tags

- [ ] **Step 3: Uplift every prompt**

For each `prompts/A??-*.md`, keep the existing content and **append** (inside `</agent>`) the new tags. Expand from the matching `03-agents/` spec. Density bar: each prompt 250–450 lines. Do not delete existing `<script>` blocks; add a sibling TS invocation.

Template to fill per agent (replace AGENT-SPECIFIC with facts from `03-agents/` and the current prompt only):

```xml
<system_role>Senior AGENT-NAME. Execute the assignment using repository evidence. Never invent paths or APIs. Fail closed. Single-writer for your artifact class.</system_role>
<scope>Only the artifacts listed in <outputs> for this task.assign. Echo task_id and correlation_id on every script invocation.</scope>
<out_of_scope>Other agents' single-writer zones. Raising autonomy. Unsigned task.assign. Live production writes at L3/L4 without human-approval BLOCKED.</out_of_scope>
<workflow>
1. Validate the signed task.assign (reject unsigned).
2. Read upstream artifacts listed in inputs[].
3. Run python3 scripts/NAME.py --json and/or bun scripts/ts/NAME.ts --json (same flags). Never fabricate script output.
4. Produce artifacts in your zone.
5. Finish with markdown summary + one fenced json task.result as in <output_format>.
</workflow>
<acceptance_criteria>Scripts ran (or dry-run); JSON result block present; state IN_REVIEW|FAILED|BLOCKED; correlation_id echoed; no writes outside <outputs>.</acceptance_criteria>
<states>
empty: missing inputs → BLOCKED needs=input
blocked: L3/L4 or missing tool → BLOCKED needs=human-approval|tool
in-progress: scripts running
in-review: JSON result emitted, gates pending
failed: taxonomy error in JSON
escalated: third gate failure — do not retry; report for A01
</states>
<graph_of_thought>
  <node id="understand">What is the task.assign capability and acceptance list?</node>
  <node id="decompose">Which scripts and upstream artifacts are required?</node>
  <node id="decide">Does autonomy ceiling allow this action?</node>
  <node id="act">Run scripts, write artifacts, emit task.result.</node>
</graph_of_thought>
<graceful_degradation>If a binary is missing, record skipped:tool-missing in JSON and continue other checks. Never invent scan results.</graceful_degradation>
<security_and_validation>Reject unsigned task.assign. No long-lived credentials in output. Minimise PII. Do not log secrets.</security_and_validation>
```

A01 `<workflow>` must include in-session spawning: Claude `Agent` tool with `subagent_type=<slug>`; Grok `spawn_subagent` with `subagent_type=<slug>`. Never do domain work in A01.

A09 and A10 remain read-only on product code.

- [ ] **Step 4: Regenerate agents and run tests**

Run: `cd /root/src/repos/agent-swarm && python3 scripts/build_agents.py && python3 -m pytest tests/test_swarm.py tests/test_uplift_harness.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent-swarm/prompts agent-swarm/.claude/agents agent-swarm/.grok/agents agent-swarm/tests/test_swarm.py
git commit -m "feat(swarm): uplift A01-A15 prompts to Prompt-Uplift operational specs"
```

---

### Task 3: TypeScript twins of every agent script

**Files:**
- Create: `agent-swarm/scripts/ts/<same-stem>.ts` for every `scripts/*.py` except none — include `orch_plan.ts`, `orch_status.ts`, `swarm_run.ts`, `req_lint.ts`, `arch_adr.ts`, `arch_contract_check.ts`, `ux_tokens.ts`, `code_checks.ts`, `be_contract_conformance.ts`, `fe_a11y_check.ts`, `data_migration_check.ts`, `qa_gate.ts`, `rev_gate.ts`, `sec_gate.ts`, `devops_ci_check.ts`, `devops_build_record.ts`, `rel_plan.ts`, `obs_slo.ts`, `maint_deps.ts`, `docs_bundle.ts`. `build_agents.ts` already exists from Task 1.
- Create: `agent-swarm/tests/test_ts_scripts.py`
- Modify: `agent-swarm/tests/test_swarm.py` (`test_all_scripts_dry_run` analog for TS)
- Test: `agent-swarm/tests/test_ts_scripts.py`

**Interfaces:**
- Consumes: Task 1 `runAgentScript` / `parseAgentArgs`; Python scripts' extra flags and JSON keys
- Produces: TS CLIs with identical extra flags (table below) and JSON keys `agent`, `script`, `status` plus each Python script's result keys on `--dry-run --json`

Extra flags to port (from the Python files — read each file and copy argparse):

| Script | Extra flags (must match Python) |
|---|---|
| orch_plan | `--brief`, `--brief-text`, `--pattern`, `--risk-class`, `--prefix`, `--plan` |
| orch_status | `--transition`, `--reason`, `--ingest`, `--history` |
| swarm_run | `--repo`, `--max-parallel`, `--dry-run` (shared), `--once`, `--runtime` (`claude`/`grok`/`auto`, default `auto`) |
| req_lint | `--file` |
| arch_adr | `--title`, `--status`, `--out` |
| arch_contract_check | `--path` |
| ux_tokens | `--path` |
| code_checks | `--path` |
| be_contract_conformance | `--path` |
| fe_a11y_check | `--path` |
| data_migration_check | `--path` |
| qa_gate | `--path` |
| rev_gate | `--path` |
| sec_gate | `--path` |
| devops_ci_check | `--path` |
| devops_build_record | `--out` |
| rel_plan | `--path` |
| obs_slo | `--path` |
| maint_deps | `--path` |
| docs_bundle | `--path` |
| build_agents | `--check`, `--only`, `--install-workspace` |

`--runtime` on `swarm_run.py` **and** `swarm_run.ts` is required here (Python must gain the flag this task if Task 5 has not landed it — add it in Python in this task so both twins match). `claude` keeps current argv; `grok` uses `grok -p --agent <slug> --output-format json --yolo --cwd <repo>`; `auto` picks grok when `SWARM_RUNTIME=grok` or (`which grok` and not `which claude`), else claude. Dry-run ignores runtime and uses canned results (existing behaviour).

- [ ] **Step 1: Write failing test**

```python
# tests/test_ts_scripts.py
import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
BUN = shutil.which("bun")

@pytest.mark.skipif(not BUN, reason="bun not installed")
def test_every_py_script_has_ts_twin():
    skip = set()
    py = {p.stem for p in (ROOT / "scripts").glob("*.py")}
    ts = {p.stem for p in (ROOT / "scripts" / "ts").glob("*.ts")} - {"script_base"}
    assert py <= ts, f"missing ts twins: {sorted(py - ts)}"

@pytest.mark.skipif(not BUN, reason="bun not installed")
def test_ts_dry_run_json(tmp_path, monkeypatch):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    skip = {"build_agents", "swarm_run", "orch_status", "script_base"}
    for ts in sorted((ROOT / "scripts" / "ts").glob("*.ts")):
        if ts.stem in skip:
            continue
        r = subprocess.run([BUN, str(ts), "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True, env=env)
        assert r.returncode in (0, 1), ts.name + r.stderr
        data = json.loads(r.stdout)
        assert data["status"] in ("ok", "fail")
        assert "agent" in data and "script" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_ts_scripts.py -q`
Expected: FAIL missing twins (or skip if no bun — still implement files)

- [ ] **Step 3: Implement TS twins**

Each file: shebang `#!/usr/bin/env bun`, import `runAgentScript` from `./script_base.ts`, implement `run` using the Python file as the behaviour spec. `--dry-run` must return deterministic canned JSON with `status: "ok"` (or `fail` only when the Python dry-run fails). Prefer calling into the same algorithms: for lint/scan scripts, reimplement the regex/file walk in TS; for Task Store mutations (`orch_plan`, `orch_status`, `swarm_run`, gates), **spawn the Python script with the same argv** and pass through stdout/exit code — do not reimplement SQLite state in TS. Document that split in a 4-line comment at the top of each pass-through file.

`req_lint.ts`, `code_checks.ts`, `fe_a11y_check.ts`, `sec_gate.ts` (secrets regex), `ux_tokens.ts` should be native TS reimplementations so Grok sessions can run them without Python logic drift tests: for those, `--dry-run --json` keys must include `findings` (list) and `status`.

- [ ] **Step 4: Run tests**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_swarm.py tests/test_ts_scripts.py tests/test_uplift_harness.py -q`
Expected: PASS (ts tests skipped only if bun missing)

- [ ] **Step 5: Commit**

```bash
git add agent-swarm/scripts/ts agent-swarm/scripts/swarm_run.py agent-swarm/tests/test_ts_scripts.py
git commit -m "feat(swarm): TypeScript twins for every agent script plus grok runtime flag"
```

---

### Task 4: Per-agent SKILL.md files and orchestration skill

**Files:**
- Create: `agent-swarm/skills/a01-orchestrator/SKILL.md` … `agent-swarm/skills/a15-docs/SKILL.md` (15)
- Create: `agent-swarm/skills/orchestrate/SKILL.md` (frontmatter `name: agent-swarm-orchestrate`)
- Create: `agent-swarm/tests/test_skills.py`
- Modify: `agent-swarm/scripts/build_agents.py` to copy `skills/` → `--install-workspace/.claude/skills/agent-swarm/` (copy only; Task 5 wires workspace). This task writes source skills only.
- Test: `agent-swarm/tests/test_skills.py`

**Interfaces:**
- Consumes: `agents.json` slugs/descriptions/scripts; uplifted prompts as the subagent body (do not paste them)
- Produces: 16 SKILL.md files meeting the spec skill contract

- [ ] **Step 1: Write failing test**

```python
# tests/test_skills.py
from pathlib import Path
import re
ROOT = Path(__file__).resolve().parent.parent

def test_all_agent_skills_exist():
    import json
    agents = json.loads((ROOT / "agents.json").read_text())["agents"]
    for a in agents:
        p = ROOT / "skills" / a["slug"] / "SKILL.md"
        assert p.exists(), p
        text = p.read_text()
        assert text.startswith("---\n")
        assert f"name: {a['slug']}" in text.split("---", 2)[1]
        assert "disable-model-invocation: false" in text
        assert "## When to Use" in text
        assert "## Procedure" in text
        assert "python3 scripts/" in text
        assert "scripts/ts/" in text

def test_orchestrate_skill():
    p = ROOT / "skills" / "orchestrate" / "SKILL.md"
    text = p.read_text()
    fm = text.split("---", 2)[1]
    assert "name: agent-swarm-orchestrate" in fm
    assert "disable-model-invocation: false" in fm
    assert "a01-orchestrator" in text
    assert "spawn_subagent" in text
    assert "UserPromptSubmit" in text or "hook" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_skills.py -q`
Expected: FAIL missing skills

- [ ] **Step 3: Write 15 agent skills**

Each skill frontmatter description MUST include: agent id, code, slug, when-to-use triggers from `agents.json` description, and `Use when the user runs /<slug>`.

Body sections in order: `# <Name>`, When to Use (bullets + Don't use for), Prerequisites (`SWARM_DIR`, repo root, bun or python3), How to Run (exact commands for every script in `agents.json` `scripts` plus TS twins), Procedure (numbered, last step = emit task.result JSON), Pitfalls, Verification.

A01 skill Procedure: run `orch_plan.py`, then for each ready task spawn `subagent_type` = slug (Claude Agent tool or Grok `spawn_subagent`). Do not implement domain work.

A08/A09/A10 skills: never modify product code; run gate scripts; write signed verdicts.

- [ ] **Step 4: Write orchestrate skill**

`skills/orchestrate/SKILL.md`:

- `name: agent-swarm-orchestrate`
- description must trigger on: run the swarm, AgentSwarm, SDLC, implement a feature with the 15 agents, a01-orchestrator, `/swarm`
- Procedure: (1) identify the target application repo (`--repo` / cwd), (2) `python3 agent-swarm/scripts/orch_plan.py --brief-text "..." --pattern feature|hotfix|dependency --json` (path relative to agent-swarm root), (3) spawn `a01-orchestrator` with the plan JSON, (4) parent MUST NOT write application code, (5) if hook injected this skill, treat it as mandatory
- Mention the UserPromptSubmit hook path `hooks/user_prompt_submit.py`

- [ ] **Step 5: Run tests**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_skills.py tests/test_swarm.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent-swarm/skills agent-swarm/tests/test_skills.py
git commit -m "feat(swarm): per-agent skills and agent-swarm-orchestrate"
```

---

### Task 5: Autonomous hook, workspace Claude/Grok install, docs

**Files:**
- Create: `agent-swarm/hooks/user_prompt_submit.py`
- Create: `agent-swarm/tests/test_hook.py`
- Create: `agent-swarm/.grok/hooks/agent-swarm.json` (in-repo example)
- Modify: `agent-swarm/scripts/build_agents.py` (`--install-workspace`)
- Modify: `/root/src/repos/.claude/settings.json` (add UserPromptSubmit command hook; keep `enabledPlugins`)
- Create: `/root/src/repos/.grok/hooks/agent-swarm.json`
- Copy via generator: workspace `.claude/agents/a*.md`, `.claude/skills/agent-swarm/**`, `.grok/agents/a*.md`
- Modify: `agent-swarm/CLAUDE.md`, `agent-swarm/README.md` (document dual runtime, skills, hook)
- Test: `agent-swarm/tests/test_hook.py`, `agent-swarm/tests/test_uplift_harness.py` (install-workspace)

**Interfaces:**
- Consumes: Task 4 skills; Task 3 `--runtime`; Claude UserPromptSubmit stdin JSON
- Produces: hook stdout contract from the spec; workspace install layout from the spec

- [ ] **Step 1: Write failing hook tests**

```python
# tests/test_hook.py
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "user_prompt_submit.py"

def run_hook(prompt: str) -> dict:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"prompt": prompt}),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout or "{}")

def test_injects_on_feature():
    out = run_hook("implement a new billing feature in the app")
    ctx = out.get("additionalContext", "")
    assert "agent-swarm-orchestrate" in ctx
    assert "a01-orchestrator" in ctx

def test_silent_on_explain():
    out = run_hook("what is a monad")
    assert not out.get("additionalContext")

def test_never_blocks_on_bad_stdin():
    p = subprocess.run([sys.executable, str(HOOK)], input="not-json", capture_output=True, text=True)
    assert p.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/src/repos/agent-swarm && python3 -m pytest tests/test_hook.py -q`
Expected: FAIL missing hook

- [ ] **Step 3: Implement hook**

`hooks/user_prompt_submit.py`: stdlib only. Read stdin. Parse JSON. Prompt from `prompt` or `user_prompt` or nested `inputs.prompt`. Classify per spec positive/negative signals. On match print JSON with `additionalContext` markdown:

```
## AgentSwarm orchestration (mandatory)

Load skill `agent-swarm-orchestrate` (path: agent-swarm/skills/orchestrate/SKILL.md or .claude/skills/agent-swarm/orchestrate/SKILL.md).
Do not implement domain work in the parent session.
Spawn subagent `a01-orchestrator` (Claude Agent tool / Grok spawn_subagent subagent_type=a01-orchestrator) with the user request as the brief.
A01 plans with orch_plan.py and spawns a02–a15 by slug.
```

Always `sys.exit(0)`. Wrap main in try/except that exits 0.

In-repo and workspace Grok hook JSON:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT:-/root/src/repos/agent-swarm}/hooks/user_prompt_submit.py" }
        ]
      }
    ]
  }
}
```

Do **not** use `${CLAUDE_PLUGIN_ROOT}` if that env is unset in Grok. Use an absolute path derived at install time by `build_agents.py --install-workspace`. The generator writes the concrete `python3 /root/src/repos/agent-swarm/hooks/user_prompt_submit.py` into both `.claude/settings.json` and `.grok/hooks/agent-swarm.json`. Merge settings.json: load existing JSON, set `hooks.UserPromptSubmit` to the command matcher (Claude Code schema: matcher optional, hooks array with type command). Preserve `enabledPlugins`.

`--install-workspace /root/src/repos` copies:

- `.claude/agents/<slug>.md` from agent-swarm generated Claude files
- `.grok/agents/<slug>.md` from agent-swarm generated Grok files
- `.claude/skills/agent-swarm/<slug>/SKILL.md` from `agent-swarm/skills/`
- `.claude/skills/agent-swarm/orchestrate/SKILL.md`
- writes hook files as above

- [ ] **Step 4: Run install and tests**

Run:

```
cd /root/src/repos/agent-swarm && python3 scripts/build_agents.py --install-workspace /root/src/repos
python3 -m pytest -q
```

Expected: all PASS; workspace files exist for 15 slugs + orchestrate skill; `.claude/settings.json` still has `enabledPlugins`.

- [ ] **Step 5: Update README.md and CLAUDE.md**

Document: dual agents, skills location, `bun scripts/ts/...`, `--runtime grok`, hook behaviour, `--install-workspace`.

- [ ] **Step 6: Commit**

```bash
git add agent-swarm/hooks agent-swarm/tests/test_hook.py agent-swarm/scripts/build_agents.py agent-swarm/CLAUDE.md agent-swarm/README.md agent-swarm/.grok/hooks .claude/settings.json .claude/agents .claude/skills/agent-swarm .grok/agents .grok/hooks
git commit -m "feat(swarm): autonomous orchestration hook and workspace Claude/Grok install"
```

---

## Self-review

1. Spec coverage: uplifted XML, dual subagents, Python+TS scripts, per-agent skills, orchestrate skill, hook, swarm_run grok, workspace Claude folder — each has a task.
2. Placeholder scan: no TBD; extra flags table is explicit; hook additionalContext text is explicit.
3. Type consistency: slugs from `agents.json`; CLI flags shared; install path `--install-workspace`.
