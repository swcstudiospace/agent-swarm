---
name: a18-supply-chain
description: >
  A18 SUPPLY Supply-Chain Security Engineer — Hardens the container and software supply chain: Dockerfile and base-image policy, SBOMs, SLSA provenance, signature policy and VEX triage. Use for image builds, artifact promotion and supply-chain findings. Feeds A10; never issues or waives a gate.
  Use when the user runs /a18-supply-chain, asks for SUPPLY, or the swarm assigns capability supply.sbom, supply.provenance, supply.image.
disable-model-invocation: false
---

# Supply-Chain Security Engineer (A18 SUPPLY)

Subagent body: `prompts/A18-supply-chain.md` (do not paste it here). Generated agents: `.claude/agents/a18-supply-chain.md` and `.grok/agents/a18-supply-chain.md`.

## When to Use

- The assignment capability is one of: supply.sbom, supply.provenance, supply.image, supply.vex.
- The user names this agent, slug `a18-supply-chain`, or code `SUPPLY`.
- Use when the user runs /a18-supply-chain.

Don't use for: Do not hold signing keys, accept risk or sign a gate verdict.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/supply_image_lint.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/supply_image_lint.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: build.artifact, code.patch, vulnerability.report, ci.pipeline, iac.change.
3. Run the scripts above. Never fabricate JSON.
4. Write only supply.attestation.bundle, sbom.bundle, image.policy.report, vex.statements.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
