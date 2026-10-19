---
name: a05-backend--new-dependency-request
description: Use when a task needs a package absent from the manifest; classify it L3, check range and license, file the dependency.request for A10, do not import.
owner: a05-backend
tools: [be_dependency_class, be_semver_range_class, be_license_allowlist_check, shared_escalate, shared_handover_write]
---

## When to Use
- The implementation would import a third-party package that the manifest does not list (A05 spec section 4 rule 3: a new dependency is L3).
- A package already in the manifest is `a05-backend--dependency-bump-l2`. The package decision belongs to A10 policy, not to you.

## Procedure
1. Call `be_dependency_class` with `{manifest: {<package>: <MAJOR.MINOR.PATCH>, ...}, proposed: [{package, version}], allowlist: [<allowlisted package names>]}`. `manifest` is every current package with its resolved version (from the lockfile) and `version` is the exact target. The item must read `change: new`, `level: L3`; `level: L2` means the package exists, switch to the bump skill.
2. Call `be_semver_range_class` with `{range}` set to the range you would write into the manifest. `upper_bounded` must be true; a `wildcard` or open `comparator` range is not acceptable.
3. Call `be_license_allowlist_check` with `{packages: [{name, license}], allowlist: [<allowed license ids>]}`. A non-zero `violation_count` goes into the request as a stated risk.
4. Build the `dependency.request` from the results of `be_dependency_class`, `be_semver_range_class` and `be_license_allowlist_check`, as in prompts/A05-backend.md: `ecosystem`, `package` as `name@version`, `reason_md`, `risk` (`cves`, `license`, `maintainers`, `weekly_downloads`), `state: "pending"`. Fill `risk` only from registry or OSV data you actually have; omit what you lack and say so in `reason_md`. Never invent scan results. No ACP route carries this type (A05 does not produce it in agents.json), so the request travels as one JSON string in the handover `completed` list and in `dependency_requests` of the `code.patch`.
5. Do not import, install or add the package to any manifest or lockfile; confirm with `grep` that no import of it exists in your diff. Implement what you can without it and mark the dependent part unfinished.
6. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "new dependency <package>@<version> needs A10 approval", needs: ["human-approval: dependency.request for <package>@<version>"]}` and use the returned `result_patch` as the BLOCKED result (needs=human-approval).
7. Call `shared_handover_write` with `{task_id, to_agent: "A10", goal, completed: [<request string>], blockers: ["awaiting approval of <package>"], decisions, constraints: ["do not import <package> before approval"], verification}`.

## Outputs
- The `dependency.request` object, the classification, a BLOCKED needs=human-approval result.

## Failure Handling
- INVALID_INPUT from `be_dependency_class` (version not `MAJOR.MINOR.PATCH`, duplicate package, empty `proposed`): correct the named field once, then BLOCKED needs=input.
- Range or license tool unavailable: retry twice, then say `skipped:tool-missing` in `reason_md`; the request still goes out.
- Approval arrives in a later handover from A10: only then add the package, and run the tests.

## Handover
Fields: `completed` (the request), `blockers` (awaiting approval), `decisions` (why this package, alternatives rejected), `constraints` (do not import before approval), `verification` (`unverified`, with the three tool results in `checks`). Sent to A10 with `shared_handover_write`.
