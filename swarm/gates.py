"""Gate verdicts (quality / review / security / release) and conjunction rules."""
from __future__ import annotations
import time
from .errors import SwarmError, ErrorCode
from .envelope import build_envelope, sign_envelope

GATES = {"quality", "review", "security", "release"}
VERDICTS = {"pass", "fail", "waive"}
SEVERITIES = ["info", "minor", "major", "critical", "blocker"]
BLOCKING_SEVERITY = "major"   # any finding at or above this ⇒ fail


def make_finding(fid: str, severity: str, kind: str, summary: str, *, evidence: str = "",
                 ac_ref: str | None = None, owner_suggestion: str | None = None,
                 location: str | None = None) -> dict:
    if severity not in SEVERITIES:
        raise SwarmError(ErrorCode.E_CONTRACT, f"bad severity {severity}")
    return {"id": fid, "severity": severity, "kind": kind, "summary": summary,
            "evidence": evidence, "ac_ref": ac_ref, "owner_suggestion": owner_suggestion,
            "location": location}


def derive_verdict(findings: list[dict]) -> str:
    """fail on any finding ≥ BLOCKING_SEVERITY, otherwise pass."""
    idx = SEVERITIES.index(BLOCKING_SEVERITY)
    return "fail" if any(SEVERITIES.index(f["severity"]) >= idx for f in findings) else "pass"


def make_verdict(*, gate: str, task_id: str, agent_id: str, findings: list[dict] | None = None,
                 verdict: str | None = None, runs: dict | None = None, expires_s: int = 86400,
                 correlation_id: str | None = None, extra: dict | None = None) -> dict:
    """Return a signed envelope carrying a gate verdict. Waive requires explicit verdict."""
    if gate not in GATES:
        raise SwarmError(ErrorCode.E_CONTRACT, f"unknown gate {gate}")
    findings = findings or []
    verdict = verdict or derive_verdict(findings)
    if verdict not in VERDICTS:
        raise SwarmError(ErrorCode.E_CONTRACT, f"bad verdict {verdict}")
    if verdict == "waive" and not (extra or {}).get("waived_by"):
        raise SwarmError(ErrorCode.E_POLICY, "waive requires extra.waived_by (human, L3)")
    payload = {"gate": gate, "task_id": task_id, "verdict": verdict, "findings": findings,
               "runs": runs or {}, "expires_s": expires_s, "issued_at": time.time(), **(extra or {})}
    env = build_envelope(source=agent_id, target="A01", msg_type="gate.verdict", payload=payload,
                         correlation_id=correlation_id, priority="P1")
    return sign_envelope(env)


def validate_verdict(env: dict) -> dict:
    from .envelope import validate_envelope, verify_envelope
    validate_envelope(env)
    if env["type"] != "gate.verdict":
        raise SwarmError(ErrorCode.E_CONTRACT, "not a gate.verdict")
    if not verify_envelope(env):
        raise SwarmError(ErrorCode.E_POLICY, "verdict signature invalid")
    p = env["payload"]
    for k in ("gate", "task_id", "verdict", "findings", "expires_s"):
        if k not in p:
            raise SwarmError(ErrorCode.E_CONTRACT, f"verdict missing {k}")
    return p


def conjunction(verdicts: dict[str, str], required: list[str]) -> tuple[str, list[str]]:
    """Most-restrictive-wins: returns (overall, missing_or_failing_gates)."""
    problems = []
    for g in required:
        v = verdicts.get(g)
        if v is None:
            problems.append(f"{g}:missing")
        elif v == "fail":
            problems.append(f"{g}:fail")
    return ("pass" if not problems else "fail"), problems
