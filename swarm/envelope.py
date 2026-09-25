"""swarm.envelope.v1 — build, validate, sign and verify inter-agent messages.

Signing: ed25519 via `cryptography` when SWARM_ED25519_KEY (hex seed) is set;
otherwise HMAC-SHA256 with SWARM_SIGNING_KEY (default dev key). Signatures are
REQUIRED for task.assign, gate verdicts and promote/rollback commands.

Every signing with the dev-insecure-key fallback emits a `security.dev_key` event.
Verification accepts the dev key only when neither SWARM_ED25519_KEY nor
SWARM_REQUIRE_KEY=1 is set; otherwise a dev-key `hmac:` signature never verifies.
SWARM_REQUIRE_KEY=1 makes APPROVED fail closed (E-POLICY) unless SWARM_ED25519_KEY
(loadable) or SWARM_SIGNING_KEY is configured, and `signing_config_error()` lets
verdict signing fail fast on a missing or unloadable key.
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


def real_key_configured() -> bool:
    """True when a non-dev signing key is configured (loadable Ed25519 seed or HMAC secret)."""
    return _ed25519_key() is not None or bool(os.environ.get("SWARM_SIGNING_KEY"))


def _dev_key_forbidden() -> bool:
    """The public dev key must not verify once a real key is configured (even an unloadable
    Ed25519 seed: misconfiguration fails closed) or required via SWARM_REQUIRE_KEY=1."""
    return bool(os.environ.get("SWARM_ED25519_KEY")) or os.environ.get("SWARM_REQUIRE_KEY") == "1"


def signing_config_error() -> str | None:
    """Why a verdict signed now could never verify (fail-closed key misconfiguration), else None."""
    if os.environ.get("SWARM_REQUIRE_KEY") == "1" and not real_key_configured():
        return "fail-closed: SWARM_REQUIRE_KEY=1 but no signing key configured"
    if (os.environ.get("SWARM_ED25519_KEY") and _ed25519_key() is None
            and not os.environ.get("SWARM_SIGNING_KEY")):
        return ("fail-closed: SWARM_ED25519_KEY is set but cannot be loaded "
                "(cryptography missing or seed is not 32-byte hex)")
    return None


def _warn_dev_key(env: dict) -> None:
    try:
        from .runlog import emit
        emit("security.dev_key", {"msg_type": env["type"], "source": env["source"]},
             source="swarm.envelope", correlation_id=env.get("correlation_id"))
    except Exception:  # signing never fails on logging
        pass


def sign_envelope(env: dict) -> dict:
    key = _ed25519_key()
    if key is not None:
        sig = key.sign(_canonical(env))
        env["sig"] = "ed25519:" + base64.b64encode(sig).decode()
    else:
        secret = os.environ.get("SWARM_SIGNING_KEY", "dev-insecure-key").encode()
        if not os.environ.get("SWARM_SIGNING_KEY"):
            _warn_dev_key(env)
        mac = hmac.new(secret, _canonical(env), hashlib.sha256).digest()
        env["sig"] = "hmac:" + base64.b64encode(mac).decode()
    return env


def verify_envelope(env: dict) -> bool:
    sig = env.get("sig") or ""
    if sig.startswith("hmac:"):
        secret = os.environ.get("SWARM_SIGNING_KEY")
        if not secret:
            if _dev_key_forbidden():
                return False
            secret = "dev-insecure-key"
        expected = hmac.new(secret.encode(), _canonical(env), hashlib.sha256).digest()
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
