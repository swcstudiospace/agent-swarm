---
name: "a07-data--registry-outage-defer"
description: "Use when the schema registry is unreachable at publish time, to pin the contract by digest, retry with backoff and defer the publish instead of forcing it."
disable-model-invocation: true
---

## When to Use
- A publish of a data.contract fails with DEPENDENCY_UNAVAILABLE or a registry timeout.
- Rule: contracts are cached immutably by digest, consumers pin digests, and the publish is deferred with E-DEP backoff (prompts/A07-data.md error_handling).
- This skill designs nothing; the contract itself came from `a07-data--contract-evolution`.

## Procedure
1. Call `shared_handover_read` with `task_id` to find the finished contract file and the publish command or endpoint the task named.
2. Call `shared_artifact_digest` with `path` set to the contract file; the returned `digest` is the immutable pin.
3. Call `shared_attempt_history` with `task_id`; if `retries_left` is 0, skip to step 6. Otherwise retry the publish command (with `bash`) at most twice, sleeping between tries with `bash` (convention: 5 s, then 20 s). No publish command named: skip to step 6.
4. After each failed retry call `shared_status_report` with `kind` progress and a `step` naming the retry number and the E-DEP cause.
5. If a retry succeeds, record the registry response and the digest it reports; compare it yourself with the pinned `digest` (the registry copy is not a local file, so `shared_artifact_check` reports it `unchecked`). Matching: finish without deferral. Different: treat as a conflict (Failure Handling).
6. Call `shared_artifact_check` with `task_id`, `outputs` (`kind`, `uri` the local cache file, `digest`, `producer` A07) and `agent` A07: `ok` true means the cache file is intact for consumers; record `unchecked` entries explicitly.
7. Call `shared_status_report` with `kind` blocked, `step` "publish deferred: registry unavailable", then write the deferral in the handover.

## Outputs
- Deferred publish record: contract id, version, pinned digest, retry count and last error.
- Cache location (local file) consumers may pin.

## Failure Handling
- Cached copy fails `shared_artifact_check`: do not tell consumers to pin it; BLOCKED `needs=input` and regenerate the contract.
- Digest differs between the registry and the cache: treat as conflict, stop and escalate; never overwrite a published digest.

## Handover
`to_agent` is the agent id in the task routing (default A01, which retries the publish when the registry recovers). Fill `goal`, `completed` (contract built, digest pinned), `remaining` (publish when the registry recovers), `blockers` (registry outage), `artifacts` (cache file with `digest`), `constraints` (pin digests, do not republish under the same version). Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/registry-outage-defer.md; edit the source and run scripts/build_agents.py -->
