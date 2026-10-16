"""A11 DevOps / Platform Engineer lane tools.

Pure checks over payload data for the A11 decision logic: environment tier and
autonomy, production change gating, GitOps drift, cost overrun against the
120 percent rule, image reference immutability, and freeze windows. Nothing
here reads files, the network, the clock, or the environment.

Tools:
- devops_env_tier: classify an environment name as ephemeral, staging, or production.
- devops_change_gate: decide whether a change may proceed given its tier and evidence.
- devops_drift_count: count drifted keys and choose the reconcile action.
- devops_cost_overrun: compare actual cost with modeled cost under the 120 percent rule.
- devops_image_tag_check: flag image references not pinned by digest or version tag.
- devops_freeze_window: test whether a UTC instant falls inside a freeze window.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from swarm.tools.support import respond

_PRODUCTION_WORDS = frozenset({"prod", "production", "prd"})
_STAGING_WORDS = frozenset({"staging", "stg", "stage", "preprod"})
_EPHEMERAL_WORDS = frozenset({"ephemeral", "preview", "pr"})
_OVERRUN_PERCENT = 120
_FREEZE_RECONCILES = 3
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SEMVER = re.compile(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_MUTABLE_TAGS = frozenset(
    {
        "latest", "main", "master", "develop", "dev", "edge", "nightly", "stable",
        "head", "trunk", "current", "canary", "staging", "prod", "production",
    }
)


def env_tier(payload: dict) -> dict:
    def work() -> dict:
        words = set(re.findall(r"[a-z]+", payload["env_name"].lower()))
        # Production wins over staging, and staging over ephemeral, so a mixed name fails closed.
        if words & _PRODUCTION_WORDS:
            tier = "production"
        elif words & _STAGING_WORDS:
            tier = "staging"
        elif words & _EPHEMERAL_WORDS:
            tier = "ephemeral"
        else:
            return {
                "state": "INVALID_INPUT",
                "field": "env_name",
                "message": "name has no production, staging, or ephemeral marker",
            }
        return {"state": "SUCCESS", "tier": tier, "autonomy": "L3" if tier == "production" else "L2"}

    return respond(payload, work)


def change_gate(payload: dict) -> dict:
    def work() -> dict:
        if payload["env_tier"] != "production":
            return {"state": "SUCCESS", "decision": "allowed", "missing": ""}
        evidence = {
            "change_ticket": payload.get("change_ticket", ""),
            "blast_radius": payload.get("blast_radius", ""),
            "rollback_plan": payload.get("rollback_plan", ""),
        }
        missing = [name for name, text in evidence.items() if not text.strip()]
        if missing:
            return {"state": "SUCCESS", "decision": "blocked", "missing": ", ".join(missing)}
        return {"state": "SUCCESS", "decision": "needs-human-approval", "missing": ""}

    return respond(payload, work)


def _state_map(pairs: list) -> dict | None:
    state: dict[str, str] = {}
    for pair in pairs:
        if pair["key"] in state:
            return None
        state[pair["key"]] = pair["value"]
    return state


def drift_count(payload: dict) -> dict:
    def work() -> dict:
        want = _state_map(payload["desired"])
        if want is None:
            return {"state": "INVALID_INPUT", "field": "desired", "message": "keys must be unique"}
        have = _state_map(payload["actual"])
        if have is None:
            return {"state": "INVALID_INPUT", "field": "actual", "message": "keys must be unique"}
        missing = [key for key in want if key not in have]
        unexpected = [key for key in have if key not in want]
        changed = [key for key in want if key in have and want[key] != have[key]]
        drifted = sorted(set(missing) | set(unexpected) | set(changed))
        # drift_streak counts prior consecutive reconciles that still reported drift.
        if not drifted:
            action = "none"
        elif payload["drift_streak"] + 1 >= _FREEZE_RECONCILES:
            action = "freeze-and-escalate"
        else:
            action = "auto-reconcile"
        return {
            "state": "SUCCESS",
            "drift_count": len(drifted),
            "drifted_keys": ", ".join(drifted),
            "action": action,
        }

    return respond(payload, work)


def cost_overrun(payload: dict) -> dict:
    def work() -> dict:
        modeled = payload["modeled_cost"]
        actual = payload["actual_cost"]
        if modeled <= 0:
            return {"state": "INVALID_INPUT", "field": "modeled_cost", "message": "must be above zero"}
        # Decimal(repr(...)) compares the decimal values the caller wrote, not binary float residue.
        overrun = Decimal(repr(actual)) * 100 > Decimal(repr(modeled)) * _OVERRUN_PERCENT
        return {
            "state": "SUCCESS",
            "ratio": round(actual / modeled, 4),
            "overrun": overrun,
            "action": "auto-downscale" if overrun else "none",
        }

    return respond(payload, work)


def image_tag_check(payload: dict) -> dict:
    def work() -> dict:
        ref = payload["image_ref"]
        if any(ch.isspace() for ch in ref):
            return {"state": "INVALID_INPUT", "field": "image_ref", "message": "must not contain whitespace"}
        if ref.startswith("oci://"):
            ref = ref[len("oci://"):]
        name, at, digest = ref.partition("@")
        if at and not _DIGEST.fullmatch(digest):
            return {
                "state": "INVALID_INPUT",
                "field": "image_ref",
                "message": "digest must be sha256 followed by 64 lowercase hex characters",
            }
        repo, _colon, tag = name.rsplit("/", 1)[-1].partition(":")
        if not repo:
            return {"state": "INVALID_INPUT", "field": "image_ref", "message": "repository name is empty"}
        # Version and commit tags pass on convention; the registry must enforce tag immutability for that to hold.
        if at:
            verdict, reason = "pass", "digest-pinned"
        elif not tag:
            verdict, reason = "fail", "no-tag"
        elif tag.lower() in _MUTABLE_TAGS:
            verdict, reason = "fail", "mutable-tag"
        elif _SEMVER.fullmatch(tag):
            verdict, reason = "pass", "version-tag"
        elif _COMMIT.fullmatch(tag):
            verdict, reason = "pass", "commit-sha-tag"
        else:
            verdict, reason = "fail", "unrecognized-tag"
        return {"state": "SUCCESS", "verdict": verdict, "reason": reason, "tag": tag}

    return respond(payload, work)


def _instant(text: str) -> datetime | None:
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.utcoffset() is not None else None


def freeze_window(payload: dict) -> dict:
    def work() -> dict:
        now = _instant(payload["now"])
        if now is None:
            return {
                "state": "INVALID_INPUT",
                "field": "now",
                "message": "needs an ISO 8601 timestamp with a UTC offset or Z",
            }
        index = -1
        remaining = 0
        for position, window in enumerate(payload["windows"]):
            start = _instant(window["start"])
            end = _instant(window["end"])
            if start is None or end is None or end <= start:
                return {
                    "state": "INVALID_INPUT",
                    "field": "windows",
                    "message": f"window {position} needs offset timestamps with end after start",
                }
            # Start is inclusive and end is exclusive; the first matching window wins.
            if index == -1 and start <= now < end:
                index = position
                remaining = int((end - now).total_seconds())
        return {"state": "SUCCESS", "frozen": index != -1, "window_index": index, "remaining_s": remaining}

    return respond(payload, work)
