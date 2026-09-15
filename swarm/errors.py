"""Shared error taxonomy — every agent MUST map exceptions onto one of these codes."""
from __future__ import annotations
from enum import Enum


class ErrorCode(str, Enum):
    E_INPUT = "E-INPUT"          # invalid/missing input artifact
    E_TIMEOUT = "E-TIMEOUT"      # budget/lease/SLA exceeded
    E_DEP = "E-DEP"              # upstream dependency unavailable
    E_CAPACITY = "E-CAPACITY"    # overload / WIP cap hit
    E_CONTRACT = "E-CONTRACT"    # output rejected by consumer/schema
    E_POLICY = "E-POLICY"        # blocked by security/compliance policy (fail-closed)
    E_INTERNAL = "E-INTERNAL"    # unexpected agent fault

    @property
    def standard_handling(self) -> str:
        return _HANDLING[self]


_HANDLING = {
    ErrorCode.E_INPUT: "Nack + task.status FAILED; notify artifact producer",
    ErrorCode.E_TIMEOUT: "Checkpoint, release lease, requeue once, then ESCALATED",
    ErrorCode.E_DEP: "Exponential backoff 1s→60s (jitter, max 5), then degraded mode",
    ErrorCode.E_CAPACITY: "Refuse bid; backpressure in next heartbeat",
    ErrorCode.E_CONTRACT: "Bounded fix loop per agent spec",
    ErrorCode.E_POLICY: "Fail-closed; escalate to A10/A01",
    ErrorCode.E_INTERNAL: "Checkpoint + crash; A01 requeue; 3 strikes ⇒ ESCALATED",
}


class SwarmError(Exception):
    """Exception carrying a taxonomy code so scripts can report it uniformly."""

    def __init__(self, code: ErrorCode | str, message: str, **details):
        self.code = ErrorCode(code)
        self.details = details
        super().__init__(f"{self.code.value}: {message}")

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": str(self), "details": self.details,
                "handling": self.code.standard_handling}


def classify(exc: BaseException) -> ErrorCode:
    """Best-effort mapping of arbitrary exceptions onto the taxonomy."""
    if isinstance(exc, SwarmError):
        return exc.code
    if isinstance(exc, (FileNotFoundError, KeyError, ValueError)):
        return ErrorCode.E_INPUT
    if isinstance(exc, TimeoutError):
        return ErrorCode.E_TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)):
        return ErrorCode.E_DEP
    if isinstance(exc, PermissionError):
        return ErrorCode.E_POLICY
    return ErrorCode.E_INTERNAL
