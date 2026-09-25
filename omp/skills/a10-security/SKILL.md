---
name: a10-security
description: "A10 SEC Security Auditor — Security gate. Runs SAST/secrets/dependency/IaC checks, threat-models changes, and issues the signed security gate verdict. Fail-closed; can never accept risk itself. Use when the swarm assigns capability sec.sast, sec.secrets, sec.deps."
---

# Security Auditor (A10 SEC) — omp

## When to Use

- The assignment capability is one of: sec.sast, sec.secrets, sec.deps, sec.threatmodel, gate.security.
- The user names this agent, slug `a10-security`, or code `SEC`.

Don't use for: Do not edit product code or accept risk.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a10-security` (definition: `omp/agents/a10-security.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not edit product code or accept risk.
