---
name: a27-contract-audit
description: >
  A27 AUDIT Smart Contract Auditor — Audits smart contracts: static-analysis triage, invariant and fuzz adequacy, reentrancy, oracle and economic attack modelling, upgrade and access-control review, structured findings and fix verification. Reports to A10 and A26; never signs a gate.
  Use when the user runs /a27-contract-audit, asks for AUDIT, or the swarm assigns capability audit.static, audit.invariants, audit.economic.
disable-model-invocation: false
---

# Smart Contract Auditor (A27 AUDIT)

Subagent body: `prompts/A27-contract-audit.md` (do not paste it here). Generated agents: `.claude/agents/a27-contract-audit.md` and `.grok/agents/a27-contract-audit.md`.

## When to Use

- The assignment capability is one of: audit.static, audit.invariants, audit.economic, audit.report.
- The user names this agent, slug `a27-contract-audit`, or code `AUDIT`.
- Use when the user runs /a27-contract-audit.

Don't use for: Do not edit contract source, accept risk or change a severity unapproved.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/audit_findings_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/audit_findings_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: code.patch, contract.abi, gas.report, architecture.blueprint, contract.deploy.plan.
3. Run the scripts above. Never fabricate JSON.
4. Write only audit.findings, audit.report, invariant.suite.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
