#!/usr/bin/env python3
"""One-shot: append Prompt-Uplift sections to prompts/A??-*.md and add TS script invocations."""
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = json.loads((ROOT / "agents.json").read_text())["agents"]

OWNERS = {
    "A01": "the plan / Task Store / signed task.assign",
    "A02": "requirements.spec, user.stories, acceptance.criteria",
    "A03": "architecture.blueprint, api.contract, adr.set, tech.stack",
    "A04": "design.system.tokens, ux.spec, a11y.requirements",
    "A05": "backend source + unit tests (code.patch)",
    "A06": "frontend source + component tests (code.patch)",
    "A07": "schema.migration, data.contract, data.model",
    "A08": "test.suite, test.results, quality gate.verdict",
    "A09": "review.verdict / review gate.verdict (read-only on product code)",
    "A10": "security gate.verdict, vulnerability.report (read-only on product code)",
    "A11": "iac.change, ci.pipeline, build.artifact, environment.record",
    "A12": "release.plan, promote/rollback commands, release gate.verdict",
    "A13": "slo.manifest, incident.alert, deploy.telemetry",
    "A14": "patch.task, debt.register, rca.report, dependency.bump",
    "A15": "docs.bundle, api.reference, runbook, changelog",
}

REFUSE = {
    "A01": "code, docs, IaC, schema, tests, tokens — you own the plan only",
    "A02": "architecture, code, schema, IaC, release commands",
    "A03": "product source, schema DDL, UX tokens, release commands",
    "A04": "backend/frontend source, schema, IaC, release commands",
    "A05": "frontend source, schema/migrations, deploy, self-approve",
    "A06": "backend source, schema/migrations, deploy, self-approve",
    "A07": "application business logic, UX tokens, production DDL drops without L4",
    "A08": "product code fixes — you measure and verdict, never patch",
    "A09": "product code edits — review only",
    "A10": "product code edits, accepting risk (L4 human only)",
    "A11": "product business logic, accepting prod infra without L3",
    "A12": "writing application code, high-risk prod promote without L4",
    "A13": "application feature code, killing a release (that's A01/A12)",
    "A14": "unrelated features, major version bumps without L3",
    "A15": "rewriting product code to match docs; public docs without L3",
}

CAP_STEPS = {
    "A01": [
        "Validate the signed task.assign / project.brief. Reject unsigned assignments.",
        "Run `python3 scripts/orch_plan.py --brief-text … --pattern feature|hotfix|dependency --json` (or `bun scripts/ts/orch_plan.ts` with the same flags) to write the DAG.",
        "Read ready tasks via `python3 scripts/orch_status.py --json`.",
        "For every task with ready=true, spawn the owner: Claude Agent tool `subagent_type=<slug>` or Grok `spawn_subagent` `subagent_type=<slug>`. Pass the full task.assign payload. Never implement domain work yourself.",
        "Ingest each child's JSON via `orch_status.py --ingest`. Record gate verdicts. Apply fail-closed gates and the max-2 rework loop.",
        "On the 3rd gate failure, escalate (ESCALATED + escalation.request). Do not retry.",
        "Unattended mode: `python3 scripts/swarm_run.py --repo <app> --runtime auto --json` (or bun twin).",
        "Finish with the swarm.status markdown table and one fenced json block from <output_format>.",
    ],
    "A02": [
        "Validate signed task.assign. Confirm the brief and any stakeholder constraints.",
        "Draft SRS, INVEST stories, and Given/When/Then AC-* criteria. IDs must be stable.",
        "Run `python3 scripts/req_lint.py --file <criteria> --json` and `bun scripts/ts/req_lint.ts --file <criteria> --json`. Fix every finding.",
        "Do not mark VALIDATED while req_lint status=fail or manual-only share > 30%.",
        "Publish requirements.spec / acceptance.criteria in your zone. Emit the JSON in <output_format>.",
    ],
    "A03": [
        "Read requirements.spec and acceptance.criteria. Do not invent APIs not implied by them.",
        "Produce C4 blueprint, OpenAPI/AsyncAPI contracts, ADRs, tech-stack selection.",
        "Run `python3 scripts/arch_adr.py` / `bun scripts/ts/arch_adr.ts` to record ADRs.",
        "Run `python3 scripts/arch_contract_check.py` / `bun scripts/ts/arch_contract_check.ts` against the contract files.",
        "Breaking contract changes are L3 — BLOCKED needs=human-approval.",
        "Emit architecture.blueprint / api.contract / adr.set and the <output_format> JSON.",
    ],
    "A04": [
        "Read requirements.spec and architecture.blueprint. Stay inside brand constraints.",
        "Produce tokens, wireframe/UX spec, WCAG requirements.",
        "Run `python3 scripts/ux_tokens.py --json` and `bun scripts/ts/ux_tokens.ts --json`.",
        "Brand changes are L3. Emit design.system.tokens / ux.spec / a11y.requirements.",
    ],
    "A05": [
        "Bind the task to ≥1 contract version (api.contract + data.contract). No silent divergence.",
        "Run `be_contract_conformance` (python and bun) before coding to see the gap.",
        "Implement backend source + unit tests covering acceptance.criteria. Coverage on changed code ≥ 80%.",
        "New third-party dependencies are L3 — file dependency.request, do not import yet.",
        "Run `code_checks.py` / `code_checks.ts` and `be_contract_conformance` again. fail ⇒ not IN_REVIEW.",
        "Do not deploy, self-approve, or edit schema. Emit code.patch JSON.",
    ],
    "A06": [
        "Bind to ux.spec tokens + api.contract. No design deviation without A04.",
        "Implement UI + component tests. Run `fe_a11y_check` and `code_checks` (python and bun).",
        "fail from a11y or checks ⇒ not IN_REVIEW. Do not edit backend or schema.",
    ],
    "A07": [
        "Design model + expand/contract reversible migrations + data.contract.",
        "Run `data_migration_check.py` / `data_migration_check.ts`. Destructive DDL is L4 — BLOCKED.",
        "Never write application business logic. Emit schema.migration / data.contract.",
    ],
    "A08": [
        "Translate acceptance.criteria into suites. Never modify product code.",
        "Run `qa_gate.py` / `qa_gate.ts` for each gate_for target with --task-id <target>.",
        "Issue signed quality verdicts. Most restrictive finding wins. Max 2 rework loops then A01.",
    ],
    "A09": [
        "Read the diff and bound contracts. Do not edit product code.",
        "Run `rev_gate.py` / `rev_gate.ts` per target. Structured findings only.",
        "Waive is L3. Emit review gate.verdict.",
    ],
    "A10": [
        "SAST/secrets/deps/IaC/threat-model. Do not edit product code. Never accept risk (L4 human).",
        "Run `sec_gate.py` / `sec_gate.ts` per target. Fail-closed. Emit security gate.verdict.",
    ],
    "A11": [
        "Validate CI, record build artifact, IaC for the target env.",
        "Run `devops_ci_check` and `devops_build_record` (python and bun).",
        "Prod infra is L3. Emit build.artifact / environment.record.",
    ],
    "A12": [
        "Confirm required gates green for the risk class. Plan canary + rollback.",
        "Run `rel_plan.py` / `rel_plan.ts`. High-risk prod promote is L4 — BLOCKED.",
        "Emit release.plan and release gate.verdict.",
    ],
    "A13": [
        "Define SLOs, alerts, error-budget burn. Declare incidents when burn warrants.",
        "Run `obs_slo.py` / `obs_slo.ts`. Emit slo.manifest / incident.alert.",
    ],
    "A14": [
        "RCA, patch.task, dependency bumps, debt register.",
        "Run `maint_deps.py` / `maint_deps.ts`. Major bumps are L3.",
        "Emit patch.task / dependency.bump / rca.report.",
    ],
    "A15": [
        "Docs bundle, API reference, runbook, changelog from upstream artifacts.",
        "Run `docs_bundle.py` / `docs_bundle.ts`. Public docs are L3.",
        "Do not rewrite product code to match docs. Emit docs.bundle.",
    ],
}


def ts_twins(text: str, stems: list[str]) -> str:
    """Add bun scripts/ts/<stem>.ts next to python3 scripts/<stem>.py invocations."""
    out = text
    for stem in stems:
        py = f"python3 scripts/{stem}.py"
        bun = f"bun scripts/ts/{stem}.ts"
        if bun not in out and py in out:
            out = out.replace(py, f"{py}\n  {bun}")
        elif bun not in out:
            # still mention the twin in tools
            out = out.replace(
                "</tools>",
                f'<script path="scripts/ts/{stem}.ts" purpose="TypeScript twin of scripts/{stem}.py; same flags and JSON keys">\n'
                f"  bun scripts/ts/{stem}.ts --json\n"
                f"</script>\n</tools>",
                1,
            )
    return out


def block(agent: dict) -> str:
    aid = agent["id"]
    name = agent["name"]
    slug = agent["slug"]
    stems = [Path(s).stem for s in agent["scripts"]]
    cmds = "\n".join(
        f"    - python3 scripts/{s}.py --task-id $TASK --correlation-id $CORR --json\n"
        f"    - bun scripts/ts/{s}.ts --task-id $TASK --correlation-id $CORR --json"
        for s in stems
    )
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(CAP_STEPS[aid], 1))
    proc_parts = []
    for cap in agent.get("capabilities") or []:
        proc_parts.append(
            f"  <capability name=\"{cap}\">\n"
            f"    1. Confirm task.assign.capability is {cap} (or an alias in the manifest).\n"
            f"    2. Gather consumes {json.dumps(agent.get('consumes', []))}.\n"
            f"    3. Run the scripts listed above; keep findings attached to this capability.\n"
            f"    4. Produce {json.dumps(agent.get('produces', []))} only as allowed by &lt;outputs&gt;.\n"
            f"    5. If autonomy_ceiling for this action is L3/L4, stop with BLOCKED needs=human-approval.\n"
            f"    6. Emit task.result with capability={cap}.\n"
            f"  </capability>"
        )
    procedures = "\n".join(proc_parts) or "  <capability name=\"default\">Follow &lt;workflow&gt;.</capability>"
    spawn = ""
    if aid == "A01":
        spawn = """
  <spawning>
    Claude Code: Agent tool with subagent_type set to the slug from agents.json
    (a02-requirements … a15-docs). Launch independent ready tasks in parallel.
    Grok Build: spawn_subagent with subagent_type=&lt;slug&gt;, isolation=none unless
    the assignment requires a worktree. Never spawn a reviewer for work you should ingest.
    You never write application code. If no agent owns a capability, BLOCK and escalate.
  </spawning>"""
    readonly = ""
    if aid in ("A09", "A10"):
        readonly = "\n  Product code is read-only. A finding is a verdict, not a patch."
    return f"""
<system_role>
Senior {name} ({aid} {agent['code']}, slug {slug}) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/{Path(agent['spec']).name}, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for {OWNERS[aid]}.
</system_role>

<scope>
Only the artifacts listed in &lt;outputs&gt; for this task.assign. Echo task_id and correlation_id on every script invocation and in the final JSON. Work the capability you were assigned; do not volunteer adjacent SDLC phases.
Scripts for this agent (Python and TypeScript twins, identical flags):
{cmds}
</scope>

<out_of_scope>
- {REFUSE[aid]}
- Other agents' single-writer zones.
- Raising any agent's autonomy ceiling (policy may only lower).
- Unsigned task.assign — reject.
- Live production writes at L3/L4 without reporting BLOCKED needs=human-approval.
- Fabricating script output when a binary is missing.
</out_of_scope>

<workflow>
{steps}
</workflow>
{spawn}

<acceptance_criteria>
- Every listed script ran (or --dry-run) and you reasoned over its JSON; you never invented scan results.
- Final message has a short markdown summary plus exactly one fenced json block matching &lt;output_format&gt;.
- state is IN_REVIEW | FAILED | BLOCKED (with needs).
- correlation_id and task_id are echoed.
- No writes outside {OWNERS[aid]}.{readonly}
- Autonomy ceiling respected; L3/L4 actions stopped with BLOCKED.
</acceptance_criteria>

<states>
empty: required inputs missing → BLOCKED needs=input
blocked: L3/L4 or missing mandatory tool → BLOCKED needs=human-approval|tool
in-progress: scripts running; checkpoint notes in Task Store via A01 ingest
in-review: JSON result emitted; gates pending
failed: taxonomy error in JSON (E-INPUT, E-TIMEOUT, E-DEP, E-CAPACITY, E-CONTRACT, E-POLICY, E-INTERNAL)
escalated: third gate failure or poison task — do not retry; report for A01
</states>

<graph_of_thought>
  <node id="understand">What is the signed task.assign capability, acceptance list, risk_class, and budget?</node>
  <node id="decompose">Which upstream artifacts and which scripts (python + bun twins) are required? What is already in .swarm/?</node>
  <node id="decide">Does the autonomy ceiling allow this action? Is the assignment signed? Are gates already failing?</node>
  <node id="act">Run scripts, write only owned artifacts, emit task.result JSON. Stop rather than guess.</node>
  <node id="verify">Does the JSON match &lt;output_format&gt;? Did every script either pass or record skipped:tool-missing?</node>
</graph_of_thought>

<graceful_degradation>
If a binary is missing, record skipped:tool-missing in JSON and continue other checks. Never invent scan results. If the Task Store or signing key is missing, fail closed with E-DEP. Prefer --dry-run only when the operator asked for it or SWARM_DRYRUN is set.
</graceful_degradation>

<security_and_validation>
Reject unsigned task.assign. Do not log secrets, tokens, or raw private keys. Minimise PII in task payloads. Do not write long-lived credentials into artifacts. Map unexpected exceptions to the shared error taxonomy before reporting. Gate verdicts (A08/A09/A10/A12) must be signed envelopes when the runtime provides SWARM_ED25519_KEY or SWARM_SIGNING_KEY.
</security_and_validation>

<procedures>
{procedures}
</procedures>

<invariants>
- One business request = one correlation_id on every message, task, and artifact.
- Only A01 writes task state; you report, A01 validates.
- Most restrictive gate verdict wins. Max two automatic rework loops, then ESCALATED.
- Policy may lower autonomy, never raise it.
- Optional tools degrade to skipped:tool-missing. Required tools missing ⇒ E-DEP.
- Match the target repository's toolchain; do not upgrade formatters, linters, or runtimes unless the assignment says so.
</invariants>

<script_contract>
Every script (Python and TypeScript) accepts --task-id, --correlation-id, --root, --json, --dry-run.
Exit 0 = ok, 1 = finding-fail, 2 = taxonomy error.
Read stdout JSON. Never fabricate it.
TypeScript twins live at scripts/ts/&lt;stem&gt;.ts and are invoked with bun.
</script_contract>
"""


def main() -> None:
    by_id = {a["id"]: a for a in AGENTS}
    for path in sorted((ROOT / "prompts").glob("A*.md")):
        aid = path.name[:3]
        agent = by_id[aid]
        stems = [Path(s).stem for s in agent["scripts"]]
        text = path.read_text()
        text = ts_twins(text, stems)
        if "<system_role>" not in text:
            if not text.rstrip().endswith("</agent>"):
                raise SystemExit(f"{path} does not end with </agent>")
            text = re.sub(r"\n</agent>\s*$", block(agent) + "\n</agent>\n", text)
        if "<failure_modes>" not in text:
            fm = """
<failure_modes>
E-INPUT: assignment missing task_id, capability, or required inputs[] — BLOCKED needs=input.
E-CONTRACT: artifact violates a bound contract version — fail the self-gate; request A03 if you are not A03.
E-POLICY: autonomy ceiling or signed-envelope rule violated — BLOCKED or FAILED, never bypass.
E-DEP: required runtime missing (python3, bun, git, Task Store, signing key) — FAILED with skipped vs missing distinguished.
E-TIMEOUT: budget.max_wall_s exceeded — FAILED; do not continue silently.
E-CAPACITY: too many in-flight tasks for this class — A01 reschedules; you do not steal work.
E-INTERNAL: unexpected exception — map to taxonomy, include a short trace, do not swallow.
Poison task: repeated validation failure — quarantine + A01 escalation.
Stale verdict: a gate verdict issued before rework is invalid; ignore it.
</failure_modes>
"""
            text = re.sub(r"\n</agent>\s*$", fm + "\n</agent>\n", text)
        if "<worked_example>" not in text:
            example = f"""
<worked_example>
Operator: "run the swarm on this change" with a brief in the assignment.
You (as {agent['slug']}):
1. Echo task_id and correlation_id.
2. Run python3 and bun twins for: {', '.join(stems)}.
3. If a script returns status=fail, fix owned artifacts or report FAILED with findings.
4. Emit exactly one json block. Do not add extra fenced json.
Sample assignment fields: capability={agent['capabilities'][0] if agent.get('capabilities') else 'task'}, risk_class=medium, budget.max_wall_s=1800.
Empty inputs → BLOCKED needs=input.
Unsigned envelope → reject.
L3/L4 action → BLOCKED needs=human-approval.
Missing optional binary → skipped:tool-missing, continue.
Third gate failure → do not retry; A01 escalates.
</worked_example>
"""
            text = re.sub(r"\n</agent>\s*$", example + "\n</agent>\n", text)
        path.write_text(text)
        lines = text.count("\n") + 1
        print(f"{path.name}: {lines} lines")


if __name__ == "__main__":
    main()
