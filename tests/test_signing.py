"""CORE-07 / D-15 signing trust: the public dev key never verifies under a configured or required real key."""
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, run_script

KEY_VARS = ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY")
ED_SEED = "11" * 32


def _clear_keys(monkeypatch):
    for k in KEY_VARS:
        monkeypatch.delenv(k, raising=False)


def _in_review(ts, tid, gates=("review",)):
    ts.create(task_id=tid, correlation_id="c", capability="code.backend", notes={"gates": list(gates)})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition(tid, s)


def _dev_key_verdict(ts, tid):
    """Record a review pass signed with the public dev key (opt-in, no real key configured)."""
    from swarm.gates import make_verdict
    os.environ.pop("SWARM_SIGNING_KEY", None)
    os.environ["SWARM_ALLOW_INSECURE_DEV_KEY"] = "1"
    ts.record_verdict(tid, make_verdict(gate="review", task_id=tid, agent_id="A09", correlation_id="c"))


# ---------------------------------------------------------------- CR-01: dev-key HMAC forgery
def test_dev_key_hmac_rejected_under_ed25519_and_require_key(swarm_dir, monkeypatch):
    pytest.importorskip("cryptography")
    from swarm.taskstore import TaskStore
    from swarm.errors import SwarmError, ErrorCode
    _clear_keys(monkeypatch)
    ts = TaskStore()
    _in_review(ts, "K-1")
    _dev_key_verdict(ts, "K-1")
    monkeypatch.setenv("SWARM_ED25519_KEY", ED_SEED)
    monkeypatch.setenv("SWARM_REQUIRE_KEY", "1")
    assert ts.missing_gate_reasons("K-1") == {"review": "bad-sig"}
    with pytest.raises(SwarmError) as ei:
        ts.transition("K-1", "APPROVED")
    assert ei.value.code is ErrorCode.E_POLICY
    assert ts.get("K-1")["state"] == "IN_REVIEW"
    r = run_script("orch_status.py", "--history", "K-1", "--json", env={"SWARM_DIR": str(swarm_dir)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["missing_gate_reasons"]["review"] == "bad-sig"


def test_dev_key_hmac_rejected_under_ed25519_only(swarm_dir, monkeypatch):
    from swarm.taskstore import TaskStore
    _clear_keys(monkeypatch)
    ts = TaskStore()
    _in_review(ts, "K-1")
    _dev_key_verdict(ts, "K-1")
    monkeypatch.setenv("SWARM_ED25519_KEY", ED_SEED)
    assert ts.missing_gate_reasons("K-1") == {"review": "bad-sig"}


def test_dev_key_hmac_rejected_when_ed25519_seed_unloadable(swarm_dir, monkeypatch):
    from swarm.envelope import build_envelope, sign_envelope, verify_envelope
    _clear_keys(monkeypatch)
    monkeypatch.setenv("SWARM_ALLOW_INSECURE_DEV_KEY", "1")
    env = sign_envelope(build_envelope(source="A09", target="A01", msg_type="gate.verdict",
                                       payload={"gate": "review"}, correlation_id="c"))
    assert env["sig"].startswith("hmac:") and verify_envelope(env)
    monkeypatch.setenv("SWARM_ED25519_KEY", "zz")
    assert verify_envelope(env) is False


def test_real_hmac_and_ed25519_still_verify(swarm_dir, monkeypatch):
    pytest.importorskip("cryptography")
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    _clear_keys(monkeypatch)
    ts = TaskStore()
    # HMAC under a configured secret keeps verifying once an Ed25519 key is also configured
    _in_review(ts, "K-2")
    monkeypatch.setenv("SWARM_SIGNING_KEY", "k")
    env = make_verdict(gate="review", task_id="K-2", agent_id="A09", correlation_id="c")
    assert env["sig"].startswith("hmac:")
    ts.record_verdict("K-2", env)
    monkeypatch.setenv("SWARM_ED25519_KEY", ED_SEED)
    assert ts.missing_gate_reasons("K-2") == {}
    # a genuine Ed25519 verdict still approves under SWARM_REQUIRE_KEY=1
    monkeypatch.delenv("SWARM_SIGNING_KEY")
    monkeypatch.setenv("SWARM_REQUIRE_KEY", "1")
    _in_review(ts, "K-3")
    env = make_verdict(gate="review", task_id="K-3", agent_id="A09", correlation_id="c")
    assert env["sig"].startswith("ed25519:")
    ts.record_verdict("K-3", env)
    assert ts.transition("K-3", "APPROVED")["state"] == "APPROVED"


@pytest.mark.parametrize("extra, rc, needle", [
    ({"SWARM_REQUIRE_KEY": "1"}, 2, "SWARM_REQUIRE_KEY=1 but no signing key configured"),
    ({"SWARM_ED25519_KEY": "zz"}, 2, "SWARM_ED25519_KEY"),
    ({"SWARM_REQUIRE_KEY": "1", "SWARM_SIGNING_KEY": "k"}, 0, None),
], ids=["require-key-no-key", "ed25519-unloadable", "require-key-with-hmac"])
def test_gate_script_key_config_error(swarm_dir, monkeypatch, extra, rc, needle):
    _clear_keys(monkeypatch)
    r = run_script("qa_gate.py", "--dry-run", "--task-id", "Q-none", "--json",
                   env={"SWARM_DIR": str(swarm_dir), **extra})
    assert r.returncode == rc, r.stdout + r.stderr
    assert "verdict signature invalid" not in r.stdout
    if needle:
        err = json.loads(r.stdout)["error"]
        assert err["code"] == "E-POLICY"
        assert needle in err["message"]


# ---------------------------------------------------------------- WR-07: malformed envelopes read as bad-sig
def _raw_row(ts, tid, gate, verdict, envelope_json):
    import time
    ts.conn.execute("INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts, envelope_json)"
                    " VALUES (?,?,?,?,?,?,?,?)", (tid, gate, verdict, "A09", "[]", time.time() + 999, time.time(), envelope_json))
    ts.conn.commit()


def _tampered(env, **changes):
    return json.dumps({**env, **changes})


def test_malformed_envelope_rows_are_bad_sig(swarm_dir, monkeypatch):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    from swarm.results import reconcile
    # the throwaway HMAC key from the fixture signs the envelope; this test is not the dev-key path
    monkeypatch.delenv("SWARM_ED25519_KEY", raising=False)
    monkeypatch.delenv("SWARM_REQUIRE_KEY", raising=False)
    ts = TaskStore()
    _in_review(ts, "W-1")
    env = make_verdict(gate="review", task_id="W-1", agent_id="A09", correlation_id="c")
    rows = {"int-sig": _tampered(env, sig=123), "null-schema": _tampered(env, schema=None),
            "bad-base64": _tampered(env, sig="hmac:!!!not-base64"), "non-object": "[]"}
    for name, envelope_json in rows.items():
        _raw_row(ts, "W-1", "review", "pass", envelope_json)
        assert ts.missing_gate_reasons("W-1") == {"review": "bad-sig"}, name
        reconcile(ts, "c", lambda *a, **k: None)
        assert ts.get("W-1")["state"] == "IN_REVIEW", name


@pytest.mark.parametrize("sig", ["hmac:!!!", "hmac:abc", "hmac:!!!not-base64", 123])
def test_record_verdict_bad_base64_is_taxonomy_error(swarm_dir, monkeypatch, sig):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    from swarm.errors import SwarmError, ErrorCode
    monkeypatch.delenv("SWARM_ED25519_KEY", raising=False)
    monkeypatch.delenv("SWARM_REQUIRE_KEY", raising=False)
    ts = TaskStore()
    _in_review(ts, "W-2")
    env = {**make_verdict(gate="review", task_id="W-2", agent_id="A09", correlation_id="c"), "sig": sig}
    with pytest.raises(SwarmError) as ei:
        ts.record_verdict("W-2", env)
    assert ei.value.code in (ErrorCode.E_POLICY, ErrorCode.E_CONTRACT)


# ---------------------------------------------------------------- IN-02 / D-10: dev_key event follows --root
def test_dev_key_event_follows_root(tmp_path):
    app, other = tmp_path / "app", tmp_path / "other"
    app.mkdir()
    other.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("SWARM_DIR", *KEY_VARS)}
    env["SWARM_ALLOW_INSECURE_DEV_KEY"] = "1"
    # cwd=other on purpose (not conftest.run_script, whose cwd is the repo and its real .swarm)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rev_gate.py"), "--root", str(app), "--dry-run",
                        "--task-id", "R-rev", "--json"], capture_output=True, text=True, cwd=other, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    events = app / ".swarm" / "events.jsonl"
    assert events.exists() and '"security.dev_key"' in events.read_text()
    assert not (other / ".swarm").exists()


def test_empty_signing_key_is_not_an_implicit_dev_key(swarm_dir, monkeypatch):
    """An exported but empty SWARM_SIGNING_KEY is unset. Without SWARM_ALLOW_INSECURE_DEV_KEY it is not the
    dev key. With the opt-in, empty still signs and verifies as the dev key, and unsetting the opt-in stops that."""
    from swarm.envelope import verify_envelope
    from swarm.errors import SwarmError
    from swarm.gates import make_verdict
    _clear_keys(monkeypatch)
    monkeypatch.delenv("SWARM_ALLOW_INSECURE_DEV_KEY", raising=False)
    monkeypatch.setenv("SWARM_SIGNING_KEY", "")
    with pytest.raises(SwarmError, match="fail-closed: no signing key configured"):
        make_verdict(gate="review", task_id="K-9", agent_id="A09", correlation_id="c")
    monkeypatch.setenv("SWARM_ALLOW_INSECURE_DEV_KEY", "1")
    env = make_verdict(gate="review", task_id="K-9", agent_id="A09", correlation_id="c")
    assert verify_envelope(env)
    monkeypatch.delenv("SWARM_SIGNING_KEY")
    assert verify_envelope(env)
    monkeypatch.delenv("SWARM_ALLOW_INSECURE_DEV_KEY")
    assert verify_envelope(env) is False
