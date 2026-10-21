---
name: a30-custody-compliance--policy-repo-check
description: Use when a repository needs custody policy files validated and a key-material scan that reports locations and lengths only, with scan gaps named.
owner: a30-custody-compliance
tools: [custody_secret_scan, custody_multisig_policy, shared_open_findings, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Policy and key-material repository check

## When to Use

- A task wants every custody policy file in a directory validated and the tree scanned for private-key material before a merge, release or audit.
- Read-only analysis: the script writes nothing. A hit is a possible compromise: you never open the matched line, copy it or test the key.
- A single transaction is `skill://a30-custody-compliance--tx-policy-evaluation`; a confirmed leak continues as `skill://a30-custody-compliance--key-compromise-playbook`; general credential gating is A10's.

## Procedure

1. Run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --dry-run --json` once to confirm the script starts; its canned output has `dry_run: true` and proves nothing about the repository. Then run it without `--dry-run`. Exit 0 means `status: ok`, exit 1 means `status: fail` (a major or blocker finding exists, including `scan/incomplete`; not a tool error), exit 2 is a taxonomy error (`--path` outside the root or missing; the A30 tools not registered).
2. Read the output fields: `status`, `counts` (`blocker`, `major`, `minor`, `info`), `files_scanned`, `policy_files`, `secret_findings`, `findings` (each with `id`, `severity`, `summary`, `location`), `files_skipped` (`binary`, `symlink`, `too_large`, `unreadable`), `skipped`, `truncated`. Policy files are JSON or YAML whose path contains `custody`, `multisig` or `timelock`, with sections `multisig`, `timelock`, `signer_policy`, `wallet_tiers`.
3. Name the coverage gaps from the `scripts/custody_policy_check.py` output before any conclusion. Each gap is a major `scan/incomplete` finding; `status` stays `fail` until every in-scope input is analysed: `truncated: true` (only 2000 files visited), any non-zero `files_skipped` (files over 1 MiB, binary files, symlinks, including a symlink given as `--path`, and unreadable files), a `skipped` entry `yaml-policy-files` with `skipped:tool-missing` (PyYAML absent: YAML policies were not checked), and a policy-named file with no recognised section. Narrow `--path` and rerun to remove each gap you can; the rest stays unscanned.
4. Classify findings by id. Policy: `<section>:<rule>` come from the matching tool (`custody_multisig_policy`, `custody_timelock_check`, `custody_signer_policy_lint`, `custody_wallet_tier_caps`); `policy-unparseable`, `policy-not-an-object`, `<section>:policy-section-malformed`, `<section>:policy-invalid` and `<section>:check-failed` are file defects, each major. Key material: `secret:<kind>:<path>:<line>` with `kind` `pem-private-key`, `extended-private-key`, `wif-private-key`, `hex-private-key`, `mnemonic-phrase` (blockers), `encrypted-keystore` or an encrypted PEM (major). A path that is itself secret-shaped (a token or credentialed URL as a file name) appears as `<redacted-path-N>`, N being the file's position in the sorted walk.
5. Cross-check the secrets: `secret_findings` equals the number of `secret:` findings plus any omitted ones; rerun the script with `--path <file>` for up to 5 files that carry findings and require the same ids and `length` (the scan is deterministic). Run a positive control before trusting a clean result: call `custody_secret_scan` with `files: [{path: "canary.txt", content: "signing_key = \"" + "ab" x 32 + "\""}]` (a synthetic 64-digit value, not a key); it must return one `hex-private-key` finding of `length` 64 and `verdict: fail`, otherwise the scanner is broken: E-CONTRACT, no clean result. Never print or `read` a matched line.
6. For one policy file with a `multisig` section, call `custody_multisig_policy` with that section's `threshold` and `signers` and compare `verdict` with the script's `multisig:` findings (`fail` exactly when a major or blocker is listed). A mismatch is E-CONTRACT.
7. Decide: any secret finding in a path that is not obviously a test fixture is treated as real (fail closed, even for an encrypted keystore); a fixture-looking path is still reported and the owner decides. Recommendation to the owner, in order: treat the key as compromised and replace it through the human ceremony path, remove it from the tree and history (history rewrite needs a human), rerun this check. Call `shared_open_findings` with `{task_id}` to cite any verdict that already covers it.
8. For any blocker call `shared_escalate` with `{task_id, reason_code: "policy", summary: "key material found in repository", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the report you `write` to `custody/evidence/repo-check.md` (counts, finding ids with `path:line`, kind and length, gaps), then see Handover.

## Outputs

- `custody.evidence.pack` section: counts, finding ids with locations and lengths, coverage gaps; never a value.

## Failure Handling

- Exit 2 with E-INPUT: fix `--path` once, then BLOCKED needs=input. E-DEP (tools not registered): retry once, then `skipped:tool-missing` and BLOCKED needs=tool.
- INVALID_INPUT from `custody_secret_scan` (more than 8 files, duplicate path): split the batch. PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- A clean scan is never proof of absence: formats outside the listed kinds, skipped files and unscanned history remain unknown; say so.

## Handover

- `to_agent`: `A10` for any secret finding; otherwise the assigning agent (`A01` unless named). `goal`: remove and replace; `completed`: scan and gaps; `decisions`: classification basis; `artifacts`: report with digest; `blockers`: `human-approval`; `verification`: partial while gaps remain.
- End with `shared_handover_validate`, then `shared_handover_write`.
