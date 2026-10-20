---
name: "a24-cluster-security--policy-as-code-testing"
description: "Use when a policy set needs a regression suite of must-fail and must-pass manifests run through the scan script, the lint and the engine's own test runner."
disable-model-invocation: true
---

# Policy-as-code testing

## When to Use

- A policy set (`cluster.policy.set` draft, or policies in `gitops.manifests` from A22) must be proven by fixtures before it merges or flips mode, or a policy change needs a regression check.
- Test authoring and running on files and non-prod dry-runs (L2). Writing the policy is `skill://a24-cluster-security--admission-policy-authoring`; changing a mode is L3 and not done here. The gate verdict is A10's, never yours.

## Procedure

1. List the policies in scope with `glob` and `read`, and write down the matrix: one row per rule, with at least one must-fail and one must-pass fixture; RBAC rules get a wildcard and a scoped Role; Pod Security rules one violating pod per control. A rule without both fixtures is a gap, not a pass.
2. Store fixtures under a `tests/policy/<policy>/{fail,pass}/` directory with `write`, as plain manifests. Keep them small: one violation per must-fail file.
3. Run the engine's own runner where available through `bash`: `kyverno test <dir>` for Kyverno, `gator verify <suite>` for Gatekeeper suites, and server-side dry-run (`kubectl --context <ctx> apply --dry-run=server -f <file>`) on a non-prod cluster for ValidatingAdmissionPolicy. Record command, exit code and failing cases. A missing binary is `skipped:tool-missing`, not a pass.
4. For Pod Security, RBAC and service-account rules run `python3 scripts/kpol_manifest_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path tests/policy/<policy>/fail --level restricted --json`: exit 1 (`status: fail`, a major or worse finding) is EXPECTED; exit 0 on a must-fail directory means the fixtures do not violate what you claim. Run the same on `.../pass`: exit 0 and `findings` empty is expected, so must-pass pods also need a dedicated `serviceAccountName` and `automountServiceAccountToken: false` (the `sa.*` rules fail a default-account pod). Exit 2 is an input error. The script does not read Kyverno, Gatekeeper or VAP fixtures: step 3 proves those.
5. Cross-check the script with the tools on the same files: `kpol_pss_check` with `workloads` and `level` `restricted` must give `verdict` fail on every must-fail pod fixture and pass on the must-pass ones; `kpol_rbac_risk_scan` with `roles` and `bindings` likewise for RBAC fixtures. Any case where script and tool disagree is E-CONTRACT.
6. Lint the policies themselves: call `kpol_admission_policy_lint` with `policies`; `admission.fail-open`, `admission.policy-unbound` and `admission.api-removed` (v1beta1 webhooks removed in 1.22) must be absent. Mutation testing check (this skill's definition): weaken one fixture rule and confirm that some case fails; a suite that cannot fail is invalid.
7. Aggregate: take the `severity` of every unexpected result (a must-fail that passed, a must-pass that failed, lint findings) and call `shared_verdict` with `severities`. Its `verdict` is the suite verdict; `pass` requires no major or blocker. Write the matrix, commands and results to `cluster-reports/<cluster>/policy-tests.md` with `write` and digest it, and the fixture archive, with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (test variant): fixtures, matrix, run commands, suite verdict.
- `cluster.security.findings`: failing cases and coverage gaps.

## Failure Handling

- INVALID_INPUT (`workloads` empty, a non-manifest fixture): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; untested rules are reported as gaps. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The script reads JSON natively and YAML only with PyYAML (`tools.yaml` says `skipped:tool-missing` otherwise): an unscanned directory is not clean. It caps at 2000 files and 1 MiB per file.
- Fixtures must never contain real secrets or credentialed URLs. Never retry an apply against a cluster; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A22 (wires the suite into the delivery pipeline); A10 gets findings only. Fill `goal`, `completed`, `decisions`, `artifacts` with digests, `open_questions` (untested rules), `verification` (commands, exit codes, `shared_verdict` result). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/policy-as-code-testing.md; edit the source and run scripts/build_agents.py -->
