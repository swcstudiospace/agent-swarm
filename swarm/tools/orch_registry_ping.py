"""A01 pilot tool. Echoes a token and can be driven to every contract terminal state.

`probe` exists so generated contract tests can reach dependency, partial, and timeout
without a task-store side effect. Callers that omit it get SUCCESS.
"""
from __future__ import annotations

import time


def call(payload: dict) -> dict:
    probe = payload.get("probe")
    if probe == "sleep":
        time.sleep(0.2)
    if probe == "dependency":
        raise OSError("pilot dependency is unavailable")
    if probe == "partial":
        return {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5, "token": payload["token"], "owner": "A01"}
    return {"state": "SUCCESS", "token": payload["token"], "owner": "A01"}
