---
name: a05-backend--route-review
description: Use when a backend change adds or edits HTTP routes and the route shapes must be checked before review.
owner: a05-backend
tools: [be_route_norm, be_does_not_exist]
---

# Route review

## When to Use

- A change adds a route.

## Procedure

1. Normalize every changed path template with `be_route_norm`.
2. Check the result with `be_does_not_exist`.

## Outputs

- A list of normalized paths.

## Failure Handling

- Report BLOCKED when a tool is unavailable.

## Handover

- Hand the list to the reviewer.
