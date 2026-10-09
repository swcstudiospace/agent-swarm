"""swarm.envelope.v1 — build, validate, sign and verify inter-agent messages.

Signing: ed25519 via `cryptography` when SWARM_ED25519_KEY (hex seed) is set;
otherwise HMAC-SHA256 with SWARM_SIGNING_KEY. There is no implicit dev key.
Signatures are REQUIRED for task.assign, gate verdicts and promote/rollback commands.

The public dev key is used only when SWARM_ALLOW_INSECURE_DEV_KEY=1, and not while
SWARM_ED25519_KEY or SWARM_REQUIRE_KEY=1 is set. That signing emits a `security.dev_key`
event, except a gate-script preview inside a key-less agent session (SWARM_AGENT_SESSION=1),
which never records (WR-15). Without a real key and without the opt-in, sign_envelope
refuses, dev-key hmac signatures do not verify, and APPROVED fails closed.
SWARM_REQUIRE_KEY=1 makes APPROVED fail closed (E-POLICY) unless SWARM_ED25519_KEY
(loadable) or SWARM_SIGNING_KEY is configured, and `signing_config_error()` lets
verdict signing fail fast on a missing or unloadable key.
"""
from __future__ import annotations
import base64
import binascii
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

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
    if not isinstance(env, dict):
        raise SwarmError(ErrorCode.E_CONTRACT, "envelope must be an object")
    missing = [f for f in REQUIRED_FIELDS if f not in env]
    if missing:
        raise SwarmError(ErrorCode.E_CONTRACT, f"envelope missing fields {missing}")
    not_str = [f for f in ("type", "schema", "priority", "risk_class") if not isinstance(env[f], str)]
    if not_str:
        raise SwarmError(ErrorCode.E_CONTRACT, f"envelope fields must be strings: {not_str}")
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


# sha256 of the public dev key. Recognition only; the value itself is returned
# from insecure_dev_key() and only after the opt-in check.
_DEV_KEY_SHA256 = "0c324e01315f4b0cec3ac05a605b9463fb3550d2e1a95b707091328c56cf4ea7"


def _is_public_dev_value(value: str | None) -> bool:
    """True when `value` is the public dev key. Does not sign or verify."""
    if not value:
        return False
    return hashlib.sha256(value.encode()).hexdigest() == _DEV_KEY_SHA256


def insecure_dev_key() -> str | None:
    """The public dev key, or None.

    Returned only when SWARM_ALLOW_INSECURE_DEV_KEY=1 and neither SWARM_ED25519_KEY
    nor SWARM_REQUIRE_KEY=1 is set. Callers must not invent another fallback.
    """
    if os.environ.get("SWARM_ALLOW_INSECURE_DEV_KEY") != "1":
        return None
    if os.environ.get("SWARM_ED25519_KEY") or os.environ.get("SWARM_REQUIRE_KEY") == "1":
        return None
    return "dev-insecure-key"


def real_key_configured() -> bool:
    """True when a non-dev signing key is configured (loadable Ed25519 seed or HMAC secret)."""
    hmac_key = os.environ.get("SWARM_SIGNING_KEY")
    valid_hmac = bool(hmac_key) and not _is_public_dev_value(hmac_key)
    return _ed25519_key() is not None or valid_hmac


def _dev_key_forbidden() -> bool:
    """The public dev key must not verify once a real key is configured (even an unloadable
    Ed25519 seed: misconfiguration fails closed) or required via SWARM_REQUIRE_KEY=1."""
    return (bool(os.environ.get("SWARM_ED25519_KEY")) or os.environ.get("SWARM_REQUIRE_KEY") == "1"
            or _is_public_dev_value(os.environ.get("SWARM_SIGNING_KEY")))


def _signing_hmac() -> tuple[bytes, bool] | None:
    """(secret, implicit_dev_fallback), or None when nothing may sign or verify.

    A configured HMAC secret that is not the public dev key always wins. The dev key
    is reached only through insecure_dev_key(). implicit_dev_fallback is true only for
    the unset or empty SWARM_SIGNING_KEY opt-in path, which emits security.dev_key.
    """
    raw = os.environ.get("SWARM_SIGNING_KEY") or ""
    if raw and not _is_public_dev_value(raw):
        return raw.encode(), False
    dev = insecure_dev_key()
    if dev is None:
        return None
    return dev.encode(), not bool(raw)


def signing_config_error() -> str | None:
    """Why a verdict signed now could never verify (fail-closed key misconfiguration), else None."""
    if os.environ.get("SWARM_REQUIRE_KEY") == "1" and not real_key_configured():
        return "fail-closed: SWARM_REQUIRE_KEY=1 but no signing key configured"
    if (os.environ.get("SWARM_ED25519_KEY") and _ed25519_key() is None
            and not os.environ.get("SWARM_SIGNING_KEY")):
        return ("fail-closed: SWARM_ED25519_KEY is set but cannot be loaded "
                "(cryptography missing or seed is not 32-byte hex)")
    return None


def _warn_dev_key(env: dict, root: str | Path | None) -> None:
    try:
        from .runlog import emit
        emit("security.dev_key", {"msg_type": env["type"], "source": env["source"]},
             source="swarm.envelope", correlation_id=env.get("correlation_id"), root=root)
    except Exception:  # signing never fails on logging
        pass


def sign_envelope(env: dict, *, root: str | Path | None = None, audit: bool = True) -> dict:
    """Sign in place. `root` is the caller's --root: the dev-key audit event lands in its state dir (D-10).
    audit=False skips that event for a key-less agent-session preview that can never record (WR-15)."""
    err = signing_config_error()
    if err and _dev_key_forbidden() and not real_key_configured():
        raise SwarmError(ErrorCode.E_POLICY, err)
    key = _ed25519_key()
    if key is not None:
        sig = key.sign(_canonical(env))
        env["sig"] = "ed25519:" + base64.b64encode(sig).decode()
        return env
    material = _signing_hmac()
    if material is None:
        # Refuse rather than emit an hmac that can never verify. The dev-key literal
        # is reached only inside insecure_dev_key(), after the opt-in check.
        raise SwarmError(ErrorCode.E_POLICY, "fail-closed: no signing key configured")
    secret, implicit_dev = material
    if audit and implicit_dev:
        _warn_dev_key(env, root)
    mac = hmac.new(secret, _canonical(env), hashlib.sha256).digest()
    env["sig"] = "hmac:" + base64.b64encode(mac).decode()
    return env


def _b64(data: str) -> bytes | None:
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None


def verify_envelope(env: dict) -> bool:
    sig = env.get("sig") if isinstance(env, dict) else None
    if not isinstance(sig, str):
        return False
    if sig.startswith("hmac:"):
        material = _signing_hmac()
        if material is None:
            return False
        secret, _implicit = material
        given = _b64(sig[5:])
        if given is None:
            return False
        expected = hmac.new(secret, _canonical(env), hashlib.sha256).digest()
        return hmac.compare_digest(expected, given)
    if sig.startswith("ed25519:"):
        key = _ed25519_key()
        given = _b64(sig[8:])
        if key is None or given is None:
            return False
        try:
            key.public_key().verify(given, _canonical(env))
            return True
        except Exception:
            return False
    return False
