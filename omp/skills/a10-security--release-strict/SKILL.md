---
name: "a10-security--release-strict"
description: "Use when a release candidate needs a security verdict no older than 24 h from a strict scan, with KEV re-checked and skipped DAST treated as fail-closed."
disable-model-invocation: true
---

## When to Use
- A12 (or A01) asks for the security leg of a release candidate, or the newest security verdict is older than 24 h, or none exists. A12 consumes only a fresh verdict (A10 prompt, decision logic item 4; 03-agents A10 section 4, rule 4).
- Not for per-PR gating (`skill://a10-security--pr-gate`). The difference is strict mode, KEV and DAST evidence, and the freshness proof.

## Procedure
1. Call `shared_handover_bundle` with the release `task_id`. Take `task.risk_class`, the candidate `artifacts[]`, the previous security verdict reference and whether a DAST window was planned.
2. Strict mode: `swarm_gate` has no `strict` parameter, so reproduce what `sec_gate --strict` does. For each manifest in the workspace (`requirements*.txt` or `pyproject.toml`, `package-lock.json` or `package.json`, `Cargo.lock`, `go.mod`) check its audit binary with `bash` (`command -v pip-audit`, `npm`, `cargo-audit`, `govulncheck`). Each missing one becomes a finding `{severity: "major", kind: "deps", summary: "<audit> unavailable (skipped:tool-missing); fail-closed under --strict"}`. Outside omp, run `SWARM_AGENT_SESSION=1 python3 scripts/sec_gate.py --task-id <id> --correlation-id <cid> --strict --json` instead (non-recording preview).
3. KEV: take `cve_ids` from the dependency audit (run `pip-audit --format json` or `npm audit --json` through `bash`, or use Grype/Trivy output the assignment supplies; CVE aliases only) and `kev_ids` from the KEV feed the assignment supplies. Call `sec_kev_match_count` with both and state the feed's `data_as_of`. A match is a `blocker` finding `{kind: "deps"}` naming the CVE id. A cached feed keeps KEV blocking active; list a fresh fetch as an open question. No feed supplied: BLOCKED needs=input.
4. DAST: `sec_gate` runs none. Take the staging DAST result from the assignment or A11/A12. None, or the staging target down (`skipped:dast-target`): the candidate needs a manual-window re-run, and for `task.risk_class` high that is fail-closed: add a `major` finding `{kind: "dast"}`. Never scan production.
5. Record the gate once: call `swarm_gate` with `gate` "security", `task_id`, `correlation_id` and `per_target_findings` carrying the findings of steps 2 to 4. The other recording rules are those of `skill://a10-security--pr-gate` step 3. Read `.swarm/verdicts/<gate task_id>.security.json` with `read` (`payload.verdict`, `payload.findings`, `payload.runs`, `payload.issued_at`, `payload.scan_digest`).
6. Call `shared_verdict` with the severities of all `payload.findings` and compare with `payload.verdict`; the stricter wins.
7. Call `rel_gate_freshness` with `verdicts` `[{gate: "security", verdict, issued_at}]` (`issued_at` is `payload.issued_at` as a string), `max_age_h` `{"security": 24}` and `now` (`date -u +%Y-%m-%dT%H:%M:%SZ` through `bash`; the tool never reads a clock). Require `ok` true. `fresh`, `expired`, `unwaived` and `failed` are comma-joined gate names, so `security` must not appear in `expired` or `failed`. A verdict from this run is fresh by construction; the call also proves an older reused verdict is not passed off.
8. Call `shared_artifact_digest` on the verdict path. Finish with `yield` carrying the result block (`gate`, `task_id`, `correlation_id`, `verdict`, `blocking`, `advisories`, `suppressed`, `scan_digest`, `expires_s` 86400), then `shared_handover_validate` and `shared_handover_write`.

## Outputs
- Signed `gate.verdict` (`gate="security"`) with `runs` showing no `skipped:*` entry the policy treats as required, a freshness line (issue time, `expires_s`) and whether KEV data came live or from cache, with `data_as_of`.

## Failure Handling
- `rel_gate_freshness` returns `ok` false or the verdict is expired: the security leg is `fail` and A12 must wait for a re-run.
- Any scanner `fail`, error or `swarm_gate` error: `fail`, never pass-by-default.
- `rel_gate_freshness` DEPENDENCY_UNAVAILABLE after two retries: compute the age by hand from `issued_at` and `expires_s` and say so; do not skip the proof.
- `swarm_gate` has no `allow_list`: no suppression applies in this run. Risk acceptance requests go to `skill://a10-security--risk-exception`; a release is never waived by you.

## Handover
`shared_handover_write` to A12 with `goal` (fresh security verdict for the candidate), `completed` (strict checks, freshness, KEV), `remaining` (manual DAST window or fresh KEV fetch if open), `decisions` (cache vs live feed, DAST outcome), `artifacts` (kind `gate.verdict`, uri `.swarm/verdicts/<gate task_id>.security.json`, digest, producer A10), `blockers` (finding ids, `verdict expired`), `constraints` (valid until issue time + 24 h), and `verification` {status `verified` only when every scanner ran and the freshness check passed, else `partial`; checks `strict:emulated`, `rel_gate_freshness:ok=<bool>`}.

<!-- generated from prompts/skills/a10-security/release-strict.md; edit the source and run scripts/build_agents.py -->
