---
name: "a10-security--vuln-flood"
description: "Use when one new CVE or base-image weakness hits many findings at once and you must batch them into a single advisory and remediation plan for A14."
disable-model-invocation: true
---

## When to Use
- A mass-finding event: a new CVE in a base image or a widely used library lights up hundreds of components, images or repositories (A10 prompt, error_handling: flood control).
- The goal is one advisory and one remediation list, not thousands of per-finding verdicts. For a few overlapping findings in one gate run use `skill://a10-security--scanner-conflict`.

## Procedure
1. Call `shared_status_report` with the task `task_id`, `kind` `progress` and `step` "flood batching started" so the live bar shows the batch as one unit of work.
2. With `read` over the scan outputs, collect the raw findings into `findings` with `id`, `tool`, `rule` (set to the CVE id or advisory id for all of them), `severity` (gate scale: `info`, `minor`, `major`, `critical`, `blocker`; unique `id` per finding, at most 5000 findings per call) and `location` (image, package or repo path).
3. Call `sec_finding_dedup` with `group_by` `rule`. Each group is one advisory; `ids` lists the affected findings and `tools` the scanners that saw it. `group_count` is the number of distinct issues, `finding_count` the raw volume.
4. Call `sec_kev_match_count` with `cve_ids` (the group keys that are `CVE-YYYY-NNNN` ids; map an advisory id to its CVE alias first, other ids are INVALID_INPUT) and `kev_ids` (the cached feed; state its `data_as_of`). A KEV hit lifts the whole group to a blocking advisory with the shortest deadline.
5. Call `sec_cvss_band` once per distinct CVE with its base `score`; use the band for the severity of the advisory and `gate_blocking` for the blocking flag.
6. Write the advisory: CVE id, band, KEV status, number of affected locations, the ten highest-exposure locations (the number is this skill's convention), and the shared fix (base image tag, library version). Targets from the stack metrics: criticals under 24 h, highs under 72 h.
7. Build the remediation plan: one line per group, ordered by KEV first, then band, naming the fix version and the owner family (A05 for code, A11 for images). Do not open one task per finding.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- One `vulnerability.report` advisory plus a grouped remediation list. Gate verdicts for affected PRs stay per task and cite the advisory instead of repeating it.

## Failure Handling
- More than 5000 findings: run `sec_finding_dedup` in batches by `tool` and merge the group keys by hand.
- Feed outage: use the cached feed with its `data_as_of`; KEV blocking stays active; add a fresh-fetch follow-up to the handover.
- A group with differing severities: `conflicts` lists them; keep the maximum, as in `scanner-conflict`.
- `shared_status_report` failing never blocks the advisory.

## Handover
`shared_handover_write` to A14 (patch plan) with `goal` (remediate the flood advisory), `completed` (grouping and scoring), `remaining` (the ordered remediation list), `decisions` (severity and deadline per group), `constraints` (the 24 h and 72 h targets, KEV first), `open_questions` (affected components that could not be resolved), `artifacts` (kind `vulnerability.report`, uri, digest, producer A10), and `verification` {status `verified` or `partial` when the feed was a cache; checks `sec_finding_dedup:group_count`, `sec_kev_match_count:matched_count`}.

<!-- generated from prompts/skills/a10-security/vuln-flood.md; edit the source and run scripts/build_agents.py -->
