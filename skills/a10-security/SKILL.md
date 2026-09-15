---
name: a10-security
description: >
  A10 SEC Security Auditor — Security gate. Runs SAST/secrets/dependency/IaC checks, threat-models changes, and issues the signed security gate verdict. Fail-closed; can never accept risk itself.
  Use when the user runs /a10-security, asks for SEC, or the swarm assigns capability sec.sast, sec.secrets, sec.deps.
disable-model-invocation: false
---

# Security Auditor (A10 SEC)

Subagent body: `prompts/A10-security.md` (do not paste it here). Generated agents: `.claude/agents/a10-security.md` and `.grok/agents/a10-security.md`.

## When to Use

- The assignment capability is one of: sec.sast, sec.secrets, sec.deps, sec.threatmodel, gate.security.
- The user names this agent, slug `a10-security`, or code `SEC`.
- Use when the user runs /a10-security.

Don't use for: Do not edit product code or accept risk.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/sec_gate.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/sec_gate.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: code.patch, schema.migration, build.artifact, iac.change, architecture.blueprint.
3. Run the scripts above. Never fabricate JSON.
4. Write only security.gate.verdict, gate.verdict, vulnerability.report, sbom.attestation.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
