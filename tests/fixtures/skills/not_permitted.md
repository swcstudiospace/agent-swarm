---
name: a05-backend--layout-review
description: Use when a backend change touches a page layout and the component tree must be checked.
owner: a05-backend
tools: [fe_component_tree]
---

# Layout review

## When to Use

- A change touches a page layout.

## Procedure

1. Read the component tree with `fe_component_tree`.

## Outputs

- A list of components.

## Failure Handling

- Report BLOCKED when the tool is unavailable.

## Handover

- Hand the list to the reviewer.
