---
name: a13-observability--escalation-timeline
description: Use when a sev 1 or 2 incident may have gone ten minutes without stabilizing and humans need a timeline bundle to take command.
owner: a13-observability
tools: [obs_escalation_due, obs_incident_mttd_mttr, shared_artifact_digest, shared_escalate, shared_handover_bundle, shared_handover_validate, shared_handover_write, shared_scratchpad_get]
---

## When to Use

- An incident declared at sev 1 or 2 is still open and the clock check is due (prompt decision_logic 4, spec section 4 item 4: 10 minutes without stabilization escalates to humans with a timeline bundle).
- Humans own severe incident command; you supply the data and execute approved actions. This skill only reports; executing mitigations is `skill://a13-observability--mitigation-run`.

## Procedure

1. Use `read` on `.swarm/incidents/<incident_id>.json` (the id the declaration cited) and take `sev` and `at` (the declaration time, UTC with `Z`).
2. Use `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) for `now`, then call `obs_escalation_due` with `sev`, `declared_at` (that `at`), `now` and `stabilized_at` when A12 or the mitigation log recorded stabilization. Leave `max_minutes` unset to use 10.
3. If `due` is false, read `reason`: `waiting` gives `remaining_s` for the next check; `stabilized` and `sev-below-threshold` end the skill with a one-line note. Do not escalate early.
4. If `due` is true, call `obs_incident_mttd_mttr` with `incidents` (objects with `started`, `detected`, `resolved`, ISO 8601 with timezone) taken from the task inputs, to give the humans typical MTTD and MTTR as context. Skip this step, and say so, when the task supplies no resolved history.
5. Call `shared_scratchpad_get` with the task `correlation_id` and `key: "mitigations.<incident id lowercased>"` for the actions already taken (written by the mitigation skill); `found: false` means none were run.
6. Call `shared_handover_bundle` with `task_id` to collect goal, handover chain, artifacts, verdicts and attempts. Use `write` to create the timeline file at the path the task names: incident record fields (`at`, `evidence`, `suspect_changes`), mitigation actions, handover chain, MTTD and MTTR context. Report `truncated: true` on the bundle as such.
7. Call `shared_artifact_digest` with that timeline file's `path` and `.swarm/incidents/<incident_id>.json`.
8. Call `shared_escalate` with `task_id`, `reason_code: "blocked"`, `summary` (incident id, sev, minutes elapsed, what was tried) and `needs: ["human incident command"]`. Its `owner` (A14 when a direct upstream task is an A14 work order, else A01) is the accountable agent; its `result_patch` is the BLOCKED result you return.
9. Call `shared_handover_validate`, then `shared_handover_write` to that `owner`, with the timeline and incident record in `artifacts`.

## Outputs

- Escalation payload: incident id, sev, `elapsed_s`, mitigations tried, timeline file with digest, MTTD and MTTR context when available, and the BLOCKED `result_patch`.

## Failure Handling

- `obs_escalation_due` INVALID_INPUT on `declared_at` or `now` (no timezone) or `stabilized_at` outside the declared-to-now range: fix from the incident record; never guess an offset.
- `shared_handover_bundle` DEPENDENCY_UNAVAILABLE: retry twice, then escalate without the bundle and list it under `open_questions`.
- Do not call `shared_escalate` twice for one incident; reuse the first `escalation_id`.

## Handover

- `to_agent`: the `owner` returned by `shared_escalate`. `goal`: take incident command.
- `completed`: actions so far. `remaining`: stabilization. `blockers`: awaiting humans. `artifacts`: `{kind, uri, digest, producer: "A13"}` for the incident record and timeline file. `verification`: partial (incident not stabilized).
- Tools: `shared_handover_validate` then `shared_handover_write`.
