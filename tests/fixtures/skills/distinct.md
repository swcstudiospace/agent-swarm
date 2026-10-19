---
name: a05-backend--coverage-gate
description: Use when a backend run reports coverage and the change must be rejected on a regression.
owner: a05-backend
tools: [be_coverage_delta]
---

# Coverage gate

## When to Use

- A backend test run finished and its coverage figure is available.

## Procedure

1. Compute the percentage-point change against the previous run with `be_coverage_delta`.
2. When the change is negative, read the coverage report with `read`, list the files whose coverage fell and stop.

## Outputs

- One line stating the delta and whether the gate passed.

## Failure Handling

- Treat a missing previous run as BLOCKED and ask for the baseline.

## Handover

- Pass the delta line and the failing file list onward.
