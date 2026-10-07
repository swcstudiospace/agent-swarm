"""ADR 0001 S2 criteria 7-9 / HAND-01..03: signed handoffs at accountable-agent boundaries, and a receiver's context
rebuilt from the ledger alone.

The substrate is test_substrate_lease's fake (lease rules on a clock the test moves) plus coord_handoff,
coord_handoff_list and events_query as substrate-mcp answers them: the sender is the caller's token, never an argument;
packets are HMAC-signed over everything but the signature value when a key is set and recorded unsigned when not; the
list replays a graph's packets with a verdict each; `release_lease` releases only after the packet is stored."""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from swarm import substrate_client, substrate_handoff as hand_mod, substrate_lease as lease_mod, substrate_tee as tee_mod
from swarm import workspace as ws_mod
from swarm.manifest import load_manifest
from swarm.results import reconcile
from swarm.taskstore import MAX_REWORK_LOOPS, TaskStore
from swarm.verdicts import record_gate_verdicts

from test_substrate_lease import CORR, GID, FakeSubstrate, _args, _iso, _load, _refusal, _store

_PINNED = {k: v for k, v in sys.modules.items() if k == "swarm" or k.startswith("swarm.")}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k, v in _PINNED.items():  # other modules' swarm_dir fixture reloads swarm.*; keep the objects patched here
        monkeypatch.setitem(sys.modules, k, v)
    for k in [k for k in os.environ if k.startswith(("SUBSTRATE_", "SWARM_")) or k.lower().endswith("_proxy")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    monkeypatch.setenv("SWARM_SIGNING_KEY", "runner-secret")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    substrate_client.reset()
    tee_mod.reset()
    yield
    substrate_client.reset()
    tee_mod.reset()


@pytest.fixture()
def runner():
    mod = _load("swarm_run")
    # a fixed, unsigned envelope so the test reads task.assign back out of the prompt
    mod.build_envelope = lambda **kw: {"msg_type": kw["msg_type"], "payload": kw["payload"]}
    mod.sign_envelope = lambda env, **kw: env
    return mod


KEY = "handoff-secret"


class FakeHandoffSubstrate(FakeSubstrate):
    def __init__(self):
        super().__init__()
        self.key: str | None = KEY
        self.handoffs: list[dict] = []  # the ledger's `handoff` events, in append order
        self.store_handoffs = True

    def _sign(self, packet: dict) -> str:
        body = {k: v for k, v in packet.items() if k != "signature"}
        body["signature"] = {"alg": "hmac-sha256", "key_id": "k1"}
        return hmac.new(self.key.encode(), json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
                        hashlib.sha256).hexdigest()

    def verdict(self, packet: dict) -> dict:
        sig = packet.get("signature")
        if not sig:
            return {"ok": False, "reason": "unsigned"}
        if not self.key:
            return {"ok": False, "reason": "no-keys-configured"}
        return {"ok": True, "key_id": "k1"} if hmac.compare_digest(sig["value"], self._sign(packet)) \
            else {"ok": False, "reason": "bad-signature"}

    def tool_coord_handoff(self, a, caller):
        if caller is None:
            return _refusal("unauthenticated")
        n = len(self.handoffs)  # append order, as the ledger's own timestamps would sort it
        packet = {"version": 1, "handoff_id": f"hof_{next(self._ids)}", "ts": "2026-10-07T00:%02d:%02d.000Z" % divmod(n, 60),
                  # the sender is the token's surface: coord_handoff has no argument for it
                  "from": {"surface": caller, "session_id": a.get("session_id")},
                  "to": {"surface": a["to"], "session_id": a.get("to_session_id")},
                  "graph_id": a.get("graph_id"), "node_id": a.get("node_id"), "goal": a["goal"],
                  "files": a.get("files", []), "dod": a.get("dod", []), "blockers": a.get("blockers", []),
                  "prev_event_hash": None, "lease_handover": bool(a.get("lease_id")), "notes": a.get("notes"),
                  "signature": None}
        if self.key:
            packet["signature"] = {"alg": "hmac-sha256", "key_id": "k1", "value": self._sign(packet)}
        reply = {"packet": packet, "signed": packet["signature"] is not None, "gaps": []}
        if not self.store_handoffs:
            return {**reply, "stored": False, "error": "ledger unavailable"}
        self.handoffs.append({"event_id": f"evt_{next(self._ids)}", "ts": packet["ts"], "packet": packet})
        self.trail.append({"kind": "handoff", "action": "handoff", "node": a.get("node_id"), "session": a.get("session_id"),
                           "lease_id": None})
        reply["stored"] = True
        if a.get("release_lease") and a.get("lease_id"):  # only now that the packet is durable
            got = self._drop({"graph_id": a["graph_id"], "node_id": a["node_id"], "lease_id": a["lease_id"]}, caller, False)
            reply["lease_released"] = got.get("action") == "released"
        return reply

    def tool_coord_handoff_list(self, a, caller):
        if caller is None:
            return _refusal("unauthenticated")
        return [{"event_id": h["event_id"], "ts": h["ts"], "packet": h["packet"], "verdict": self.verdict(h["packet"])}
                for h in self.handoffs if h["packet"]["graph_id"] == a["graph_id"]]

    def tool_events_query(self, a, caller):
        if caller is None:
            return _refusal("unauthenticated")
        rows = [{"id": f"evt_t{i}", "ts": _iso(self.now), "kind": e["kind"], "session_id": e["session"],
                 "graph_id": GID, "node_id": e["node"], "summary": e["action"]}
                for i, e in enumerate(self.trail) if a.get("node_id") in (None, e["node"])]
        rows = rows if a.get("order") == "asc" else rows[::-1]
        return {"events": rows[: a.get("limit", 100)], "count": len(rows)}


def _deliver(workspace) -> None:
    """Every agent's token where S4 puts it: its env file for `workspace`."""
    for agent in load_manifest():
        ws_mod.write_token(ws_mod.env_file(workspace, agent["slug"]), f"tok-{tee_mod.AGENT_SURFACES[agent['id']]}")


@pytest.fixture()
def fake(monkeypatch, tmp_path, tmp_path_factory):
    monkeypatch.setenv("SUBSTRATE_URL", "http://substrate.test:8787")
    monkeypatch.setenv("SUBSTRATE_TOKEN", "tok-swarm-a01-orch")  # the runner's own: it never signs for another agent
    # each agent's token in its env file for the workspace (tmp_path). The config dir lies outside tmp_path, so the
    # reconstruction guard below, which catches any read under tmp_path, sees only swarm state, never a credential.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    _deliver(tmp_path)
    f = FakeHandoffSubstrate()
    f.workspace = tmp_path
    monkeypatch.setattr(substrate_client, "_open", f)
    return f


# --- the guard that proves a reconstruction reads nothing local ------------------------------------------------------
_GUARD: dict = {"on": False, "roots": (), "seen": []}
_LOCAL_NAMES = ("tasks.db", "events.jsonl", "substrate-tee.db")


def _audit(event, args):
    if not _GUARD["on"] or event not in ("open", "sqlite3.connect", "os.listdir", "os.scandir"):
        return
    target = args[0] if args else None
    if isinstance(target, (str, bytes, os.PathLike)):
        path = os.fsdecode(target)
        if path.startswith(_GUARD["roots"]) or os.path.basename(path).startswith(_LOCAL_NAMES):
            _GUARD["seen"].append((event, path))


sys.addaudithook(_audit)  # process-wide and permanent, so it is inert unless a test switches it on


# --- helpers -----------------------------------------------------------------------------------------------------
def _task(store, tid, agent, *, deps=(), acceptance=(), gates=(), max_attempts=3, **notes) -> dict:
    store.create(task_id=tid, correlation_id=CORR, capability="code.backend", agent_id=agent, title=f"{tid} work",
                 depends_on=list(deps), acceptance=list(acceptance), max_attempts=max_attempts,
                 notes={"gates": list(gates), **notes})
    store.transition(tid, "VALIDATED")
    store.transition(tid, "PLANNED")
    return store.get(tid)


def _walk(store, tid, *states):
    for s in states:
        store.transition(tid, s)


def _bridges(store, events, env=None):
    emit = lambda t, p, **k: events.append((t, p))  # noqa: E731
    root = store.path.parent.parent  # the workspace whose agent env files the fixture wrote
    leases = lease_mod.LeaseBridge(store.path, CORR, emit=emit, root=root, env=env)
    return leases, hand_mod.HandoffBridge(store.path, CORR, emit=emit, root=root, env=env, leases=leases)


def _types(events):
    return [t for t, _ in events]


def _packets(fake, node=None):
    return [h["packet"] for h in fake.handoffs if node in (None, h["packet"]["node_id"])]


def _fail_quality(store, gate_task, target, summary):
    time.sleep(0.01)  # after the rework's verdicts_since, so the row counts
    record_gate_verdicts(store, gate_task_id=gate_task, gate="quality", agent_id="A08@local", verdict="fail", runs={},
                         findings=[{"id": "F", "severity": "major", "kind": "functional", "summary": summary,
                                    "file": "src/patch.py"}],
                         correlation_id=CORR, expires_s=600, emit=lambda *a, **k: None)


PATCH_DOD = ["Given the incident repro, when the patch runs, then the 500 is gone", "No new dependency"]


def _hotfix_escalated(tmp_path, fake):
    """The hotfix shape (A14 rca → A05 patch → A08 quality gate) taken through three failing quality gates: two reworks
    hand the findings to A05, and the rework cap makes A05 hand the patch to A14 with its lease."""
    fake.register("H-rca", "H-patch", "H-qa")
    store, events = _store(tmp_path), []
    leases, handoffs = _bridges(store, events)
    _task(store, "H-rca", "A14", gates=[])
    patch = _task(store, "H-patch", "A05", deps=["H-rca"], acceptance=PATCH_DOD, gates=["quality"])
    _task(store, "H-qa", "A08", deps=["H-patch"], gate="quality", gate_for=["H-patch"])
    _walk(store, "H-rca", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    assert leases.acquire(patch, "A05").status == "held"
    assert handoffs.dispatched(store.get("H-patch"), "A05") == ["sent"]  # A14's work order reaches A05
    _walk(store, "H-patch", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    gate = "H-qa"
    for n, summary in enumerate(["first failure", "second failure", "third failure"], start=1):
        _walk(store, gate, "CLAIMED", "IN_PROGRESS")
        _fail_quality(store, gate, "H-patch", summary)
        reconcile(store, CORR, lambda t, p, **k: events.append((t, p)), leases=leases, handoffs=handoffs)
        if n < 3:
            assert store.get("H-patch")["state"] == "IN_PROGRESS"
            store.transition("H-patch", "IN_REVIEW")
            gate = f"H-qa.r{n}"
    assert store.get("H-patch")["state"] == "ESCALATED"
    return store, leases, handoffs, events


# --- HAND-01: the sender signs, as itself ---------------------------------------------------------------------------
def test_each_boundary_is_written_by_its_sender_and_a05_cannot_sign_as_a14(tmp_path, fake):
    store, leases, handoffs, events = _hotfix_escalated(tmp_path, fake)
    # INST-03: the runner's environment holds only its own token; each sender signs with its env file's
    assert not [k for k in os.environ if k.startswith("SUBSTRATE_TOKEN_")]
    calls = [c for c in fake.at("coord_handoff")]
    assert [(c["caller"], c["args"]["to"]) for c in calls] == [
        ("swarm-a14-maint", "swarm-a05-be"),  # dependency: A14's rca → A05's patch
        ("swarm-a08-qa", "swarm-a05-be"),  # gate: rework 1
        ("swarm-a08-qa", "swarm-a05-be"),  # gate: rework 2
        ("swarm-a05-be", "swarm-a14-maint"),  # escalation: the rework cap hands the patch to A14
    ]
    assert all("from" not in c["args"] and "surface" not in c["args"] for c in calls)  # nothing to name a sender with
    escalation = _packets(fake)[-1]
    assert escalation["from"] == {"surface": "swarm-a05-be", "session_id": f"A05@r0:{GID}"}
    assert escalation["to"]["surface"] == "swarm-a14-maint" and escalation["node_id"] == "H-patch"
    assert fake.verdict(escalation) == {"ok": True, "key_id": "k1"}
    assert hand_mod.boundary_key(escalation) == "escalated-a1r2@H-patch/A05"
    # A05's packet re-labelled as A14's does not verify: the signature covers the sender
    forged = {**escalation, "from": {"surface": "swarm-a14-maint", "session_id": f"A14@r0:{GID}"}}
    assert fake.verdict(forged) == {"ok": False, "reason": "bad-signature"}
    assert not [t for t in _types(events) if t.startswith("handoff.")]


def test_a_sender_without_its_own_token_writes_nothing_rather_than_writing_as_the_runner(tmp_path, fake, monkeypatch):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    _walk(store, "T-arch", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    # A03's token was never delivered: no env file and no SUBSTRATE_TOKEN_SWARM_A03_ARCH. SUBSTRATE_TOKEN (A01's) is
    # still there to fall back on, and must not be used.
    ws_mod.env_file(fake.workspace, "a03-architect").unlink()
    assert not lease_mod.has_own_token("A03", fake.workspace, os.environ)
    assert lease_mod.has_own_token("A05", fake.workspace, os.environ)  # its env file: the same lookup as its session
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == ["refused"]
    assert fake.at("coord_handoff") == [] and fake.handoffs == []
    ((kind, payload),) = events
    assert kind == "handoff.refused" and payload["from"] == "A03" and "no token of its own" in payload["reason"]
    assert handoffs.flush() == 0 and fake.at("coord_handoff") == []  # not retried: it is configuration, not an outage


def test_a_token_configured_under_another_agents_name_is_reported(tmp_path, fake, monkeypatch):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    ws_mod.write_token(ws_mod.env_file(fake.workspace, "a03-architect"), "tok-swarm-a09-rev")
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == ["sent"]
    assert _packets(fake)[0]["from"]["surface"] == "swarm-a09-rev"  # the ledger says who really sent it
    assert [(t, p["expected"], p["recorded"]) for t, p in events] == [
        ("handoff.misattributed", "swarm-a03-arch", "swarm-a09-rev")]


# --- HAND-01: the fields ---------------------------------------------------------------------------------------------
def test_a_dependency_packet_carries_the_receivers_dod_from_acceptance_and_the_upstreams_files(tmp_path, fake):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    store.add_artifact("T-arch", kind="contract", uri="docs/api/openapi.yaml", producer="A03")
    store.add_artifact("T-arch", kind="adr", uri="docs/adr/0007-cache.md", producer="A03")
    _walk(store, "T-arch", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    store.set_notes("T-arch", result={"summary_md": "contract v2 with a cache header"})
    acceptance = ["GET /items answers 200 within 50 ms", {"given": "a cold cache", "then": "one upstream call"}]
    task = _task(store, "T-be", "A05", deps=["T-arch"], acceptance=acceptance)
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == ["sent"]
    (call,) = fake.at("coord_handoff")
    a = call["args"]
    assert a["dod"] == ["GET /items answers 200 within 50 ms", '{"given": "a cold cache", "then": "one upstream call"}']
    assert a["files"] == ["docs/api/openapi.yaml", "docs/adr/0007-cache.md"] and a["blockers"] == []
    assert (a["graph_id"], a["node_id"], a["to"]) == (GID, "T-be", "swarm-a05-be")
    assert (a["session_id"], a["to_session_id"]) == (f"A03@r0:{GID}", f"A05@r0:{GID}")
    assert a["goal"].startswith("T-be work (code.backend), building on T-arch")
    assert a["notes"].splitlines()[0] == "boundary: dispatch@T-be/dep:T-arch"
    assert "contract v2 with a cache header" in a["notes"]
    assert "lease_id" not in a and "release_lease" not in a  # A03's lease is its own node's; it is not handed over
    section = handoffs.context("T-be", "A05")
    assert "## Handoffs (from the substrate ledger)" in section and "- GET /items answers 200 within 50 ms" in section
    assert "signed and verified" in section and events == []


def test_a_same_agent_dependency_is_not_a_boundary(tmp_path, fake):
    fake.register("T-a", "T-b")
    store, events = _store(tmp_path), []
    _task(store, "T-a", "A05")
    task = _task(store, "T-b", "A05", deps=["T-a"])
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == [] and fake.at("coord_handoff") == []


def test_a_failing_gate_hands_its_findings_to_the_producer(tmp_path, fake):
    _hotfix_escalated(tmp_path, fake)
    first, second = [p for p in _packets(fake) if p["from"]["surface"] == "swarm-a08-qa"]
    assert hand_mod.boundary_key(first) == "rework1@H-patch/gate:quality"
    assert hand_mod.boundary_key(second) == "rework2@H-patch/gate:quality"
    assert first["blockers"] == ["quality [major] first failure (src/patch.py)"] and first["dod"] == PATCH_DOD
    assert "src/patch.py" in first["files"] and first["lease_handover"] is False


def test_the_rework_cap_hands_the_lease_over_with_the_packet(tmp_path, fake):
    store, leases, handoffs, events = _hotfix_escalated(tmp_path, fake)
    esc = _packets(fake)[-1]
    assert esc["lease_handover"] is True and "lease_id" not in json.dumps(esc)
    assert esc["dod"] == PATCH_DOD
    assert esc["blockers"] == ["quality [major] third failure (src/patch.py)",
                               "the rework cap was reached with gates ['quality'] failing"]
    # released by the handoff, after the packet: not a second time by settle
    assert fake.trail_of("H-patch")[-2:] == [("handoff", "handoff"), ("note", "released")]
    assert len(fake.at("graph_release")) == 2  # the two reworks' releases; the escalation's was the handoff's
    assert fake.node("H-patch")["lease_id"] is None and leases.held("H-patch") is None
    mirror = store.get("H-patch")["notes_json"]["lease"]
    assert mirror["state"] == "released" and mirror["action"] == "handover"
    # and A14 can now claim the node it was handed
    claim, _ = lease_mod.claim_node("A14", GID, "H-patch", ttl_s=900, workspace=fake.workspace)
    assert claim.status == "held"


@pytest.mark.parametrize("boundary_kind", ["rework-cap", "max-attempts"])
@pytest.mark.parametrize("delivery", ["stored", "not-stored", "refused", "unreachable"])
def test_escalation_keeps_the_lease_until_the_packet_attempt_finishes(
        tmp_path, fake, runner, monkeypatch, boundary_kind, delivery):
    """Pause after the transition but before handoff storage; a real keeper must wait on the transition lock."""
    fake.register("T-be", "T-qa")
    store, events = _store(tmp_path), []
    task = _task(store, "T-be", "A05", gates=["quality"], max_attempts=1)
    leases, handoffs = _bridges(store, events)
    assert leases.acquire(task, "A05").status == "held"
    lease_id = fake.node("T-be")["lease_id"]
    _walk(store, "T-be", "CLAIMED", "IN_PROGRESS")
    if boundary_kind == "rework-cap":
        store.transition("T-be", "IN_REVIEW")
        store.update("T-be", rework_loops=MAX_REWORK_LOOPS)
        _task(store, "T-qa", "A08", gate="quality", gate_for=["T-be"])
        _walk(store, "T-qa", "CLAIMED", "IN_PROGRESS")
        _fail_quality(store, "T-qa", "T-be", "last failure")
    else:
        store.transition("T-be", "FAILED", reason="last failure")
    fake.store_handoffs = delivery != "not-stored"
    if delivery == "refused":
        fake.forced["coord_handoff"] = _refusal("handoffs are closed")
    fake.down = delivery == "unreachable"
    paused, proceed, keeper_observed = threading.Event(), threading.Event(), threading.Event()
    task_lock = leases._task_lock("T-be")

    class ObservedLock:
        def __enter__(self):
            if not task_lock.acquire(blocking=False):
                if threading.current_thread().name.startswith("swarm-lease-beat"):
                    keeper_observed.set()  # keeper is blocked, not merely scheduled
                task_lock.acquire()
            return self

        def __exit__(self, *exc):
            task_lock.release()

    monkeypatch.setitem(leases._task_locks, "T-be", ObservedLock())
    beat, escalate = leases.beat, handoffs.escalated

    def observed_beat(tid):
        try:
            return beat(tid)
        finally:
            keeper_observed.set()  # old unlocked code reaches here after prematurely releasing

    def paused_escalation(*args, **kwargs):
        paused.set()
        assert proceed.wait(5)
        return escalate(*args, **kwargs)

    monkeypatch.setattr(leases, "beat", observed_beat)
    monkeypatch.setattr(handoffs, "escalated", paused_escalation)
    errors = []

    def transition():
        local = TaskStore(store.path)
        try:
            if boundary_kind == "rework-cap":
                reconcile(local, CORR, leases.emit, leases=leases, handoffs=handoffs)
            else:
                runner.dispatchable(local, CORR, leases.emit, handoffs=handoffs)
        except BaseException as exc:
            errors.append(exc)
        finally:
            local.conn.close()

    worker = threading.Thread(target=transition)
    worker.start()
    try:
        assert paused.wait(5)
        assert store.get("T-be")["state"] == "ESCALATED"
        held = leases.held("T-be")
        assert held is not None
        held.due = 0
        leases.start()
        assert keeper_observed.wait(5)
        assert fake.handoffs == [] and fake.at("graph_release") == []
        assert fake.node("T-be")["lease_id"] == lease_id
    finally:
        proceed.set()
        worker.join(5)
        leases.close()
    assert not worker.is_alive() and errors == []
    assert leases.held("T-be") is None
    if delivery == "stored":
        assert fake.trail_of("T-be")[-2:] == [("handoff", "handoff"), ("note", "released")]
        assert fake.at("graph_release") == []
        assert _packets(fake)[0]["lease_handover"] is True
        assert store.get("T-be")["notes_json"]["lease"]["action"] == "handover"
        assert fake.node("T-be")["lease_id"] is None
    else:
        assert fake.handoffs == []
        assert ("handoff.refused" if delivery == "refused" else "handoff.unsent") in _types(events)
        if delivery == "unreachable":
            assert fake.node("T-be")["lease_id"] == lease_id  # remote outage: the lease must lapse, not pretend released
            assert store.get("T-be")["notes_json"]["lease"]["state"] == "unsettled"
            assert "lease.unsettled" in _types(events)
            fake.down = False
            substrate_client.reset()
        else:
            assert [c["args"]["lease_id"] for c in fake.at("graph_release")] == [lease_id]
            assert store.get("T-be")["notes_json"]["lease"]["state"] == "released"
            assert fake.node("T-be")["lease_id"] is None
        fake.store_handoffs = True
        if delivery == "refused":
            assert handoffs.flush() == 0 and fake.handoffs == []  # deliberate refusals are not queued
        else:
            assert handoffs.flush() == 1 and handoffs.flush() == 0
            assert _packets(fake)[0]["lease_handover"] is False


def test_a_task_out_of_attempts_is_handed_to_a01(tmp_path, fake, runner):
    fake.register("T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-be", "A05", acceptance=["it builds"], max_attempts=1)
    _walk(store, "T-be", "CLAIMED")
    store.transition("T-be", "FAILED", reason="E-TIMEOUT: task_timeout exceeded")
    _, handoffs = _bridges(store, events)
    assert runner.dispatchable(store, CORR, lambda t, p, **k: events.append((t, p)),
                               handoffs=handoffs) == []
    assert store.get("T-be")["state"] == "ESCALATED"
    (packet,) = _packets(fake)
    assert (packet["from"]["surface"], packet["to"]["surface"]) == ("swarm-a05-be", "swarm-a01-orch")
    assert packet["blockers"] == ["max_attempts reached; last failure: E-TIMEOUT: task_timeout exceeded"]
    assert packet["dod"] == ["it builds"] and packet["lease_handover"] is False  # released at FAILED already


# --- HAND-02: the receiver rebuilds from the ledger alone -------------------------------------------------------------
def test_a14_rebuilds_the_patch_from_the_handoff_list_and_the_event_query_alone(tmp_path, fake, monkeypatch):
    store, *_ = _hotfix_escalated(tmp_path, fake)
    sdir = store.path.parent
    store.conn.close()
    # the Task Store and the run log are gone, and anything that still tried to open them is caught
    store.path.rename(tmp_path / "moved-away.db")
    for f in sdir.glob("*"):  # the run log, the Graph ID cache: nothing local is left to read
        if f.is_file():
            f.unlink()
    monkeypatch.setattr(TaskStore, "__init__", lambda *a, **k: pytest.fail("a Task Store was opened"))
    before = len(fake.calls)
    _GUARD.update(on=True, roots=(str(sdir), str(tmp_path)), seen=[])
    try:
        ctx = hand_mod.reconstruct(GID, "H-patch", agent="A14", workspace=fake.workspace)
    finally:
        _GUARD["on"] = False
    assert _GUARD["seen"] == []
    assert [(c["tool"], c["caller"]) for c in fake.calls[before:]] == [
        ("coord_handoff_list", "swarm-a14-maint"), ("events_query", "swarm-a14-maint")]
    assert ctx.status == "ok" and ctx.sender == "swarm-a05-be" and ctx.signed is True and ctx.lease_handover is True
    assert ctx.goal.startswith("Take over H-patch work: the rework cap was reached")
    assert ctx.dod == PATCH_DOD
    assert ctx.blockers == ["quality [major] third failure (src/patch.py)",
                            "the rework cap was reached with gates ['quality'] failing"]
    assert ctx.files == ["src/patch.py"]
    assert [(p["from"], p["to"], p["boundary"], p["trust"]) for p in ctx.packets] == [
        ("swarm-a14-maint", "swarm-a05-be", "dispatch@H-patch/dep:H-rca", "verified"),
        ("swarm-a08-qa", "swarm-a05-be", "rework1@H-patch/gate:quality", "verified"),
        ("swarm-a08-qa", "swarm-a05-be", "rework2@H-patch/gate:quality", "verified"),
        ("swarm-a05-be", "swarm-a14-maint", "escalated-a1r2@H-patch/A05", "verified")]
    assert all(p["verdict"] == {"ok": True, "key_id": "k1"} for p in ctx.packets)
    kinds = [e["kind"] for e in ctx.events]
    assert kinds[0] == "note" and kinds.count("handoff") == 4 and "claim" in kinds  # newest first: the release


def test_a_packet_that_does_not_verify_is_listed_but_never_shapes_the_context(tmp_path, fake):
    _hotfix_escalated(tmp_path, fake)
    fake.handoffs[-1]["packet"]["from"] = {"surface": "swarm-a14-maint", "session_id": None}  # tampered in storage
    ctx = hand_mod.reconstruct(GID, "H-patch", agent="A14", workspace=fake.workspace)
    assert ctx.packets[-1]["trust"] == "rejected" and ctx.packets[-1]["verdict"]["reason"] == "bad-signature"
    assert ctx.goal.startswith("Rework H-patch work") and ctx.blockers == ["quality [major] second failure (src/patch.py)"]
    assert "1 packet(s) failed verification and were ignored" in hand_mod.render(ctx)


def test_an_unanswered_ledger_rebuilds_nothing(tmp_path, fake):
    fake.down = True
    ctx = hand_mod.reconstruct(GID, "H-patch", agent="A14", workspace=fake.workspace)
    assert ctx.status == "unreachable" and ctx.packets == [] and hand_mod.render(ctx) == ""


# --- HAND-03: unsigned, said so, and the run completes ---------------------------------------------------------------
def test_with_no_handoff_key_packets_are_unsigned_say_so_and_the_run_completes(tmp_path, fake, runner, monkeypatch):
    fake.key = None
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    _task(store, "T-be", "A05", deps=["T-arch"], acceptance=["the endpoint answers"])
    prompts: dict[str, str] = {}

    def session(agent, prompt, repo, args, *, on_session=None):
        tid = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])["payload"]["task_id"]
        prompts[tid] = prompt
        return "```json\n" + json.dumps({"task_id": tid, "state": "IN_REVIEW", "summary_md": f"{tid} done"}) + "\n```", \
            {"returncode": 0}

    monkeypatch.setattr(runner, "run_agent_headless", session)
    repo = tmp_path / "work"
    repo.mkdir()
    _deliver(repo)  # the run's workspace is its --repo: the runner reads the agents' env files for it
    args = _args(repo=str(repo), max_parallel=2, max_rounds=10, once=False)
    args.runtime, args.grok_bin = "grok", sys.executable  # any executable: the sessions are stubbed
    ctx = SimpleNamespace(correlation_id=CORR, emit=lambda t, p, **k: events.append((t, p)))
    out = runner.run(args, ctx)
    assert out["complete"] is True and out["counts"] == {"DONE": 2}
    (packet,) = _packets(fake)
    assert packet["signature"] is None and fake.verdict(packet) == {"ok": False, "reason": "unsigned"}
    assert [(t, p["boundary"]) for t, p in events if t.startswith("handoff.")] == [
        ("handoff.unsigned", "dispatch@T-be/dep:T-arch")]
    assert "UNSIGNED (no handoff key on the substrate)" in prompts["T-be"]
    assert "- the endpoint answers" in prompts["T-be"] and "## Handoffs" not in prompts["T-arch"]
    ctx2 = hand_mod.reconstruct(GID, "T-be", agent="A05", workspace=repo)
    assert ctx2.signed is False and ctx2.packets[0]["trust"] == "unsigned" and ctx2.dod == ["the endpoint answers"]


# --- idempotency -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("signing_key", [KEY, None, "no-keys-configured"])
def test_a_rerun_writes_no_second_packet_for_one_boundary(tmp_path, fake, signing_key):
    fake.key = KEY if signing_key == "no-keys-configured" else signing_key
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    # another agent quoting the boundary does not suppress the real sender's packet
    lease_mod._call("coord_handoff", {"to": "swarm-a05-be", "goal": "noise", "graph_id": GID, "node_id": "T-be",
                                      "notes": "boundary: dispatch@T-be/dep:T-arch"}, "A09", fake.workspace, os.environ)
    _, first = _bridges(store, events)
    assert first.dispatched(task, "A05") == ["sent"]
    assert first.dispatched(task, "A05") == ["duplicate"]  # a rework or retry of the task
    if signing_key == "no-keys-configured":
        fake.key = None
    _, restarted = _bridges(store, events)  # a new runner process: the ledger, not memory, says it was written
    assert restarted.dispatched(task, "A05") == ["duplicate"]
    ours = [p for p in _packets(fake) if p["from"]["surface"] == "swarm-a03-arch"]
    assert len(ours) == 1
    assert _types(events) == ([] if signing_key else ["handoff.unsigned"])


@pytest.mark.parametrize("corruption", [
    "bad-signature", "missing-verdict", "malformed-verdict", "malformed-reason",
    "malformed-sender", "unhashable-sender", "missing-node", "wrong-graph",
    "missing-goal", "malformed-lists", "wrong-node",
])
def test_rejected_or_malformed_ledger_rows_cannot_suppress_a_real_boundary(tmp_path, fake, corruption):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    _, first = _bridges(store, events)
    assert first.dispatched(task, "A05") == ["sent"]
    if corruption == "bad-signature":
        fake.handoffs[0]["packet"]["goal"] = "tampered"
    else:
        entry = json.loads(json.dumps(fake.tool_coord_handoff_list({"graph_id": GID}, "swarm-a01-orch")[0]))
        if corruption == "missing-verdict":
            entry.pop("verdict")
        elif corruption == "malformed-verdict":
            entry["verdict"] = {"reason": "unsigned"}
        elif corruption == "malformed-reason":
            entry["verdict"] = {"ok": False, "reason": ["unsigned"]}
        elif corruption == "malformed-sender":
            entry["packet"]["from"] = "swarm-a03-arch"
        elif corruption == "unhashable-sender":
            entry["packet"]["from"]["surface"] = ["swarm-a03-arch"]
        elif corruption == "missing-node":
            entry["packet"].pop("node_id")
        elif corruption == "missing-goal":
            entry["packet"].pop("goal")
        elif corruption == "malformed-lists":
            entry["packet"]["files"] = "not a file list"
        elif corruption == "wrong-node":
            entry["packet"]["node_id"] = "T-other"
        else:
            entry["packet"]["graph_id"] = "ut-other-12345678"
        fake.forced["coord_handoff_list"] = [entry]
    _, restarted = _bridges(store, events)
    assert restarted.dispatched(task, "A05") == ["sent"]
    assert restarted.dispatched(task, "A05") == ["duplicate"]
    packet = _packets(fake)[-1]
    assert packet["goal"].startswith("T-be work")
    assert packet["from"]["surface"] == "swarm-a03-arch"
    assert hand_mod.boundary_key(packet) == "dispatch@T-be/dep:T-arch"
    assert len(fake.at("coord_handoff")) == 2


@pytest.mark.parametrize("signing_key", [KEY, None])
def test_large_multibyte_packets_preserve_distinct_boundary_keys_after_restart(tmp_path, fake, monkeypatch, signing_key):
    fake.key = signing_key
    monkeypatch.setenv("SWARM_REPLICA", "r" * 64)
    node = "節" * 300
    fake.register(node)
    store, events = _store(tmp_path), []
    _, first = _bridges(store, events)
    original = fake.tool_coord_handoff

    def server_notes_cap(args, caller):
        # The real server caps notes at 1200 UTF-16 units before signing; an oversized key used to be cut in half.
        notes = args["notes"].encode("utf-16-le")[:2400].decode("utf-16-le", errors="ignore")
        return original({**args, "notes": notes}, caller)

    monkeypatch.setattr(fake, "tool_coord_handoff", server_notes_cap)
    boundaries = [
        hand_mod.boundary(round_=f"dispatch@{node}", part="dep:" + "界" * 2000 + suffix,
                          sender="A03", receiver="A05", node_id=node, goal="目標" * 250,
                          files=[f"路{i}/" + "界" * 280 for i in range(50)],
                          dod=[f"合格{i} " + "試" * 280 for i in range(30)],
                          blockers=[f"問題{i} " + "難" * 280 for i in range(20)], body="説明" * 450)
        for suffix in ("first", "second")
    ]
    for boundary in boundaries:
        assert first.send(boundary) == "sent"
    packets = _packets(fake)
    assert [hand_mod.boundary_key(p) for p in packets] == [b.key for b in boundaries]
    assert boundaries[0].key != boundaries[1].key
    for packet in packets:
        assert len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 9000
        assert len(packet["notes"].encode("utf-16-le")) <= 2400
        assert "trimmed by the runner" in packet["notes"]
        assert len(packet["files"]) <= 40 and len(packet["dod"]) <= 24 and len(packet["blockers"]) <= 16
        assert fake.verdict(packet)["ok"] is bool(signing_key)
    _, restarted = _bridges(store, events)
    assert [restarted.send(b) for b in boundaries] == ["duplicate", "duplicate"]
    assert len(fake.at("coord_handoff")) == 2


def test_routing_that_cannot_fit_never_sends_a_packet_with_a_lost_key(tmp_path, fake):
    store, events = _store(tmp_path), []
    _, handoffs = _bridges(store, events)
    packet = hand_mod.boundary(round_="dispatch@large", part="dep:T", sender="A03", receiver="A05",
                               node_id="界" * 9000, goal="work", files=[], dod=[], blockers=[], body="context")
    assert handoffs.send(packet) == "refused"
    assert fake.at("coord_handoff") == []
    assert _types(events) == ["handoff.refused"]
    assert "packet budget" in events[0][1]["reason"]
    assert handoffs.flush() == 0


# --- fail-open ---------------------------------------------------------------------------------------------------------
def test_an_unreachable_substrate_warns_lets_the_task_run_and_sends_later(tmp_path, fake, runner, monkeypatch):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    _walk(store, "T-arch", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    leases, handoffs = _bridges(store, events)
    assert leases.acquire(task, "A05").status == "held"
    fake.down = True
    seen = {}

    def session(agent, prompt, repo, args, *, on_session=None):
        seen["prompt"] = prompt
        return "```json\n" + json.dumps({"task_id": "T-be", "state": "IN_REVIEW", "summary_md": "ok"}) + "\n```", \
            {"returncode": 0}

    monkeypatch.setattr(runner, "run_agent_headless", session)
    repo = tmp_path / "work"
    repo.mkdir()
    ctx = SimpleNamespace(emit=lambda t, p, **k: events.append((t, p)))
    _, _, outcome = runner.execute_one(store.path, task, {"id": "A05", "slug": "a05-backend"}, _args(), ctx, repo,
                                       leases, handoffs)
    assert outcome == "IN_REVIEW" and "## Handoffs" not in seen["prompt"]
    unsent = [p for t, p in events if t == "handoff.unsent"]
    assert [(p["boundary"], p["from"], p["to"]) for p in unsent] == [("dispatch@T-be/dep:T-arch", "A03", "A05")]
    assert handoffs.flush() == 0 and len([t for t, _ in events if t == "handoff.unsent"]) == 1  # warned once
    fake.down = False
    substrate_client.reset()
    assert handoffs.flush() == 1 and handoffs.flush() == 0
    assert [hand_mod.boundary_key(p) for p in _packets(fake)] == ["dispatch@T-be/dep:T-arch"]


def test_a_packet_the_ledger_did_not_store_is_retried(tmp_path, fake):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    fake.store_handoffs = False
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == ["unsent"]
    assert "not stored: ledger unavailable" in events[0][1]["reason"]
    fake.store_handoffs = True
    assert handoffs.flush() == 1 and len(_packets(fake)) == 1


def test_a_refused_packet_is_recorded_and_not_retried(tmp_path, fake):
    fake.register("T-arch", "T-be")
    store, events = _store(tmp_path), []
    _task(store, "T-arch", "A03")
    task = _task(store, "T-be", "A05", deps=["T-arch"])
    fake.forced["coord_handoff"] = _refusal("handoffs are closed")
    _, handoffs = _bridges(store, events)
    assert handoffs.dispatched(task, "A05") == ["refused"]
    assert [(t, p["reason"]) for t, p in events] == [("handoff.refused", "handoffs are closed")]
    del fake.forced["coord_handoff"]
    assert handoffs.flush() == 0 and fake.handoffs == []


# --- off -------------------------------------------------------------------------------------------------------------
def test_no_handoffs_without_leases(tmp_path, runner):
    ctx = SimpleNamespace(emit=lambda *a, **k: None)
    assert runner.handoff_bridge(tmp_path / "t.db", CORR, tmp_path, ctx, None) is None
