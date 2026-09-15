"""swarm.envelope.v1 — build, validate, sign and verify inter-agent messages.

Signing: ed25519 via `cryptography` when SWARM_ED25519_KEY (hex seed) is set;
otherwise HMAC-SHA256 with SWARM_SIGNING_KEY (default dev key). Signatures are
REQUIRED for task.assign, gate verdicts and promote/rollback commands.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from .errors import SwarmError, ErrorCode

SCHEMA_PREFIX = "swarm.v1."
PRIORITIES = {"P0", "P1", "P2", "P3"}
RISK_CLASSES = {"low", "medium", "high"}
SIGNATURE_REQUIRED = {
    "task.assign", "gate.verdict", "quality.gate.verdict", "review.verdict",
    "security.gate.verdict", "promote.command", "rollback.command", "conflict.arbitration",
}
REQUIRED_FIELDS = ("msg_id", "correlation_id", "trace_id", "source", "target", "type",
                   "schema", "ts", "ttl_s", "priority", "risk_class", "payload")


def _uuid7() -> str:
    """UUIDv7-style id (time-ordered) without external deps."""
    ms = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    val = (ms << 80) | (0x7 << 76) | (rand >> 2 & ((1 << 12) - 1)) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=val & ((1 << 128) - 1)))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def build_envelope(*, source: str, target: str, msg_type: str, payload: dict,
                   correlation_id: str | None = None, causation_id: str | None = None,
                   trace_id: str | None = None, ttl_s: int = 300, priority: str = "P2",
                   risk_class: str = "low") -> dict:
    env = {
        "msg_id": _uuid7(),
        "correlation_id": correlation_id or str(uuid.uuid4()),
        "trace_id": trace_id or uuid.uuid4().hex,
        "causation_id": causation_id,
        "source": source,
        "target": target,
        "type": msg_type,
        "schema": SCHEMA_PREFIX + msg_type,
        "ts": _now_iso(),
        "ttl_s": ttl_s,
        "priority": priority,
        "risk_class": risk_class,
        "payload": payload,
        "sig": None,
    }
    validate_envelope(env, require_sig=False)
    return env


def validate_envelope(env: dict, *, require_sig: bool = True) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in env]
    if missing:
        raise SwarmError(ErrorCode.E_CONTRACT, f"envelope missing fields {missing}")
    if env["priority"] not in PRIORITIES:
        raise SwarmError(ErrorCode.E_CONTRACT, f"bad priority {env['priority']!r}")
    if env["risk_class"] not in RISK_CLASSES:
        raise SwarmError(ErrorCode.E_CONTRACT, f"bad risk_class {env['risk_class']!r}")
    if not env["schema"].startswith(SCHEMA_PREFIX) or env["schema"] != SCHEMA_PREFIX + env["type"]:
        raise SwarmError(ErrorCode.E_CONTRACT, "schema must equal swarm.v1.<type>")
    if "." not in env["type"]:
        raise SwarmError(ErrorCode.E_CONTRACT, "type must be namespaced <domain>.<name>")
    if not isinstance(env["payload"], dict):
        raise SwarmError(ErrorCode.E_CONTRACT, "payload must be an object")
    if require_sig and env["type"] in SIGNATURE_REQUIRED and not env.get("sig"):
        raise SwarmError(ErrorCode.E_POLICY, f"{env['type']} requires a signature")


def _canonical(env: dict) -> bytes:
    body = {k: v for k, v in env.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _ed25519_key():
    seed = os.environ.get("SWARM_ED25519_KEY")
    if not seed:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed))
    except Exception:  # pragma: no cover - library absent or bad key
        return None


def sign_envelope(env: dict) -> dict:
    key = _ed25519_key()
    if key is not None:
        sig = key.sign(_canonical(env))
        env["sig"] = "ed25519:" + base64.b64encode(sig).decode()
    else:
        secret = os.environ.get("SWARM_SIGNING_KEY", "dev-insecure-key").encode()
        mac = hmac.new(secret, _canonical(env), hashlib.sha256).digest()
        env["sig"] = "hmac:" + base64.b64encode(mac).decode()
    return env


def verify_envelope(env: dict) -> bool:
    sig = env.get("sig") or ""
    if sig.startswith("hmac:"):
        secret = os.environ.get("SWARM_SIGNING_KEY", "dev-insecure-key").encode()
        expected = hmac.new(secret, _canonical(env), hashlib.sha256).digest()
        return hmac.compare_digest(expected, base64.b64decode(sig[5:]))
    if sig.startswith("ed25519:"):
        key = _ed25519_key()
        if key is None:
            return False
        try:
            key.public_key().verify(base64.b64decode(sig[8:]), _canonical(env))
            return True
        except Exception:
            return False
    return False
