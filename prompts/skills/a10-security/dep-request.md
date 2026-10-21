---
name: a10-security--dep-request
description: Use when A05, A06 or A14 files a dependency.request for one package before code exists and you must approve or reject it on age, license and KEV.
owner: a10-security
tools: [shared_handover_read, sec_dependency_age, sec_license_risk, sec_kev_match_count, sec_cvss_band, shared_verdict, shared_handover_validate, shared_handover_write]
---

## When to Use
- A `dependency.request` names one package and version from A05, A06 or A14. It is reviewed at design time, the cheapest fix point (agents A10 section 4, rule 5).
- One package per run. A whole shipped artifact is `skill://a10-security--sbom-attest`.

## Procedure
1. Call `shared_handover_read` with the request `task_id`. Take the package, version, release date, license identifiers, known CVE ids with CVSS base scores, the requester's stated need and age limit, and `handover.from_agent` (the requester). Anything missing becomes an open question; never invent a limit, a date or a feed.
2. Call `sec_dependency_age` with `released` (the version's release date, `YYYY-MM-DD`), `as_of` (today, `date -u +%F` through `bash`, never a guess) and `max_age_days` (the limit from the request or policy, 1 to 3650). `stale` true means the release is older than the limit. No limit supplied: skip the call and finish BLOCKED needs=input; a missing check never approves.
3. Call `sec_license_risk` with `licenses` (the package's identifiers; at least one) and `allowlist` (the approved licenses from the policy or the request `constraints`). `license_ok` false lists `unapproved`. A package that declares no license cannot be passed to the tool: treat it as an unapproved license.
4. Call `sec_kev_match_count` with `cve_ids` (all CVE ids affecting this exact version) and `kev_ids` (the KEV feed the request supplies). Name the feed's `data_as_of` in the result; an outage keeps KEV blocking active from the cache (agents A10 section 5). Advisory ids that are not `CVE-YYYY-NNNN` are INVALID_INPUT: map them to their CVE alias first.
5. Call `sec_cvss_band` once per CVE with its `score` (0 to 10); `gate_blocking` is true from 7.0.
6. Map to severities (a convention of this skill) and call `shared_verdict`: KEV match `blocker`; band critical `critical`; band high `major`; unapproved license `major`; stale `minor`; band medium `minor`; bands low and none `info`. The gate fails from `major` upward.
7. Finish with `yield` carrying `{"package": "<name>@<version>", "verdict": "approved|rejected", "conditions": [], "kev_listed": <bool>, "license_ok": <bool>}` plus `task_id` and `correlation_id`. Approve only when `shared_verdict` is `pass`; put follow-ups in `conditions` (for example "re-scan each release", "pin exact version"). A rejection names the failing check and, where known, a safer version. Outside omp, emit it as one fenced `json` block after a short summary.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- One `dependency.request.verdict` object as in step 7, preceded by a short summary of the four checks and the feed date.

## Failure Handling
- Any registry tool DEPENDENCY_UNAVAILABLE after two retries: the verdict is `rejected` with condition `check skipped:tool-missing`; never approve on a missing check.
- INVALID_INPUT from `sec_dependency_age` (both dates must be valid 10-character ISO dates): fix once, then BLOCKED needs=input.
- No KEV feed supplied: BLOCKED needs=input; an empty `kev_ids` list is only valid when the feed itself was empty.
- You recommend; you never install or pin anything (A10 has no write or edit tool).

## Handover
`shared_handover_write` to the requesting agent (A05, A06 or A14, from step 1) with `goal` (decision on the package), `completed` (checks run), `remaining` (conditions to satisfy), `decisions` (verdict and the check that decided it), `constraints` (conditions), `open_questions` (missing limits or feeds), and `verification` {status `verified` when all four checks ran, else `partial`; checks `sec_dependency_age:stale=<bool>`, `sec_kev_match_count:<n>`}.
