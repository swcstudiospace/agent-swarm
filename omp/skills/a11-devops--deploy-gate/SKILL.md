---
name: "a11-devops--deploy-gate"
description: "Use when a promote.command from A12 asks you to execute a deploy and you must confirm verdict, freeze, drift, provenance and tier first, failing closed."
disable-model-invocation: true
---

# Deploy gate

## When to Use

- A `promote.command` arrives for an environment. You execute releases, you never decide what ships (03-agents/A11-devops.md section 1) and deploys are gated on `security.gate.verdict` (section 3).
- Not for infrastructure changes: those are `skill://a11-devops--prod-infra-change`.

## Procedure

1. Call `shared_route_check` with `{"sender": "A12", "recipient": "A11", "message_type": "promote.command"}`. `allowed` false means refuse the command and report the `reason`.
2. Call `shared_handover_bundle` with `{task_id}`. The target environment name, the freeze calendar and the release id come from the `task.assign` `inputs[]`, the bundle `goal`, `acceptance` and `handover.document.constraints`; the build.artifact entries are in `artifacts`. Take the A10 verdict from the `verdicts[]` entry with `gate` `security`, `agent` A10, `signature` `valid` and `expired` false: its `verdict` `pass` or `fail` is `security_verdict`. No such entry, an unverified or absent signature, an expired entry or a `waive` means `security_verdict` is `missing`.
3. Call `devops_env_tier` with `{"env_name": "<name>"}` and keep `tier`.
4. Get the current instant with `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) and call `devops_freeze_window` with `{"now": "<that instant>", "windows": [{start, end}, ...]}`; `frozen` feeds the gate. A promote.command without a freeze calendar is BLOCKED `needs=input` (fail-closed convention of this skill).
5. Call `shared_scratchpad_get` with `{correlation_id, "key": "drift-freeze.<env_id>"}`; a stored `value.frozen` true, or a drift freeze named in the bundle `blockers` or `constraints`, makes `drift_frozen` true. If the read fails, pass true (unverified is blocked). `found` false means no freeze under this correlation: the scratchpad is per correlation, so record that limit in `open_questions`.
6. Build the provenance record: `read` the build.artifact file named in `artifacts`, and add `provenance.attestation` from the bundle artifact of kind `provenance.attestation` (the convention of `skill://a11-devops--build-provenance`). Call `shared_artifact_check` with `{task_id, outputs: [<the build.artifact file entry>]}` and `devops_provenance_gate` with `{"record": <record>}`. `provenance_ok` is true only when `failed` is 0 and `decision` is `accept`.
7. Call `devops_deploy_gate` with `{"security_verdict": "pass|fail|missing", "frozen": <bool>, "drift_frozen": <bool>, "provenance_ok": <bool>, "env_tier": "<tier>"}`.
8. Act on the `devops_deploy_gate` `decision`. `allow`: trigger the pipeline's deploy job; the pipeline identity applies it, never your shell (the guard refuses `helm upgrade`, prod `kubectl` changes and `terraform apply`). `needs-human-approval` (production): stop, BLOCKED `needs=human-approval`. `block`: do not deploy; copy `blockers[]` into the result.

## Outputs

- `go` or `blocked` with `blockers[]`, `tier`, the freeze `remaining_s` when frozen.

## Failure Handling

- Any gate tool DEPENDENCY_UNAVAILABLE or TIMEOUT: retry twice, then report blocked; an unchecked input never counts as clear.
- INVALID_INPUT from `devops_freeze_window` (timestamp without offset): fix once, then BLOCKED `needs=input`.
- Never override a freeze or a failed verdict; exceptions belong to A12 and humans.

## Handover

Fill `goal` (proceed or resolve blockers), `completed`, `blockers` (the `blockers[]`), `decisions` (gate `decision`), `constraints` (freeze `remaining_s`), `verification`. Call `shared_handover_validate`, then `shared_handover_write` to A12.

<!-- generated from prompts/skills/a11-devops/deploy-gate.md; edit the source and run scripts/build_agents.py -->
