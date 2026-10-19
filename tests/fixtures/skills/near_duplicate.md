---
name: a05-backend--route-audit
description: Use when a backend change adds or edits HTTP routes and the route shapes must be checked before review.
owner: a05-backend
tools: [be_route_norm, be_route_collision]
---

# Route review

## When to Use

- A change adds, renames or removes an HTTP route.

## Procedure

1. Normalize every changed path template with `be_route_norm`.
2. Compare the normalized shapes pairwise with `be_route_collision` and list every collision.
3. Record the verdict in `notes.md` using the `write` tool.

## Outputs

- `notes.md` with one line per route and the collision list.

## Failure Handling

- If `be_route_collision` answers `DEPENDENCY_UNAVAILABLE`, report BLOCKED with the tool name.

## Handover

- Hand `notes.md` to the reviewer with its digest.
