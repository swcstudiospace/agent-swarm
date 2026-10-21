---
name: a05-backend--route-surface-hygiene
description: Use when routes were added or renamed and a PR is near; prove no collisions, valid path params, allowed methods and statuses, and auth on every new route.
owner: a05-backend
tools: [be_route_collision, be_openapi_path_params, be_method_status_check, be_route_auth_coverage, shared_scratchpad_get]
---

## When to Use
- Your diff adds, renames or re-methods HTTP routes, after the code works and before the self-gate (A05 prompt, be_route_collision WHEN_TO_USE: before opening a PR that adds or renames routes).
- Routes only. Whole-patch readiness is `a05-backend--pre-review-self-gate`; the pre-code gap list is `a05-backend--contract-gap-baseline`.

## Procedure
1. List every route you added or changed with `bash` (`git diff <base>...HEAD`) and `grep` over the changed files: `{method, path}` plus whether an auth middleware or guard wraps it. Include existing routes of the same API surface so collisions with them show.
2. Call `be_route_collision` with `{routes: [{method, path}, ...]}`. `collision_count` must be 0; each `collisions` entry reads `METHOD shape <- path, path`.
3. For each new path with parameters call `be_openapi_path_params` with `{template}` written in OpenAPI form (`/users/{id}`; it counts only `{name}` parameters, so convert `:id` or `<int:id>` first). `duplicates` must be empty and `param_count` must equal what the handler reads.
4. For each new handler call `be_method_status_check` with `{method, status, allowed_methods, allowed_statuses}`, the allowlists copied from that path in the A03 contract and `status` each success or error code the handler returns. `verdict` must be PASS.
5. Call `be_route_auth_coverage` with `{routes: [{method, path, auth}], public: [...]}`. `public` lists only paths the contract declares public (OpenAPI `security: []`) or the task names. `unprotected` must be empty (A05 spec section 7: auth middleware present on new routes). `public_used` above 0 means you rely on a declared exemption: say so in the PR notes.
6. Fix every failing route in the source with `edit`, then rerun the failed tool (`be_route_collision`, `be_method_status_check` or `be_route_auth_coverage`) until clean. If a fix would change the contract, stop and use `a05-backend--contract-deviation-request`.
7. Call `shared_scratchpad_get` with `{correlation_id, key: "a05.contract-gaps"}` (the key `a05-backend--contract-gap-baseline` writes). When `found` is true, confirm that none of `value.missing` is still open; `found: false` means no baseline was recorded, say so in the verdict line.

## Outputs
- One verdict line per tool: collisions, params, method/status, auth coverage, each pass or fail with the offending route strings.

## Failure Handling
- INVALID_INPUT (route without a leading slash, empty `routes` list, `status` outside 100 to 599): fix the named field once, then BLOCKED needs=input.
- Auth marker unknown for a route you did not write: report it as unprotected rather than assuming protection.
- DEPENDENCY_UNAVAILABLE: retry twice, then record the check as `skipped:tool-missing`; the self-gate treats a missing route check as not ready.

## Handover
Verdicts go into `verification.checks` of the draft handover (for example `route_collision: pass`, `route_auth_coverage: fail POST /orders`). The self-gate sends it with `shared_handover_write`; this skill sends nothing.
