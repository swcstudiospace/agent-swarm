"""ADR 0001 S1: the swarm -> Agent Substrate tee, Graph ID binding and their fail-open guarantees. No real network."""
import ast
import io
import importlib.util
import sys
import json
import os
import re
import sqlite3
import time
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

from conftest import ROOT, run_script

from swarm import runlog, substrate_client, substrate_tee as tee_mod

GID_A = "ut-mabc123-0123abcd"
GID_B = "ut-mxyz789-89abcdef"
CORR = "corr-s1"
TOKEN = "sekrit-token-value"


# --- fake substrate -----------------------------------------------------------------------------------------
class _Resp(io.BytesIO):
    def __init__(self, body: str, status: int = 200):
        super().__init__(body.encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeSubstrate:
    """Stands in for urllib.request.urlopen: REST /events and a stateless MCP /mcp (graph_bind, graph_register)."""

    def __init__(self, bindings=None, sse=False, events_status=200):
        self.bindings = dict(bindings or {})
        self.sse, self.events_status = sse, events_status
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        call = {"path": urlparse(req.full_url).path, "headers": {k.lower(): v for k, v in req.header_items()},
                "body": json.loads(req.data), "timeout": timeout}
        self.calls.append(call)
        if call["path"] == "/events":
            if self.events_status >= 400:
                raise urllib.error.HTTPError(req.full_url, self.events_status, "no", {}, io.BytesIO(b'{"error":"refused"}'))
            return _Resp('{"ok":true}', self.events_status)
        assert call["path"] == "/mcp", call["path"]
        params = call["body"]["params"]
        assert call["body"]["method"] == "tools/call"
        out = getattr(self, "tool_" + params["name"])(params["arguments"])
        rpc = {"jsonrpc": "2.0", "id": call["body"]["id"], "result": {"content": [{"type": "text", "text": json.dumps(out)}]}}
        return _Resp(f"event: message\ndata: {json.dumps(rpc)}\n\n" if self.sse else json.dumps(rpc))

    def tool_graph_bind(self, a):
        corr, gid = a.get("correlation_id"), a.get("graph_id")
        if corr and gid:
            first = corr not in self.bindings
            winner = self.bindings.setdefault(corr, gid)
            return {"status": "created" if first else "existing",
                    "correlation_id": corr, "graph_id": winner, "conflict": winner != gid}
        if corr:
            g = self.bindings.get(corr)
            return {"status": "existing" if g else "unbound", "correlation_id": corr, "graph_id": g}
        return {"graph_id": gid, "correlation_ids": [c for c, g in self.bindings.items() if g == gid]}

    def tool_graph_register(self, a):
        return {"graph_id": a["graph_id"], "registered": len(a.get("nodes", []))}

    def at(self, path, tool=None):
        return [c for c in self.calls if c["path"] == path and (tool is None or c["body"]["params"]["name"] == tool)]

    @property
    def events(self):
        return [c["body"] for c in self.at("/events")]


# Other test modules reload every `swarm*` module (see the swarm_dir fixture); pin the ones imported here so that
# runlog -> substrate_tee -> substrate_client inside a test are the very objects the test patches and inspects.
_PINNED = {k: v for k, v in sys.modules.items() if k == "swarm" or k.startswith("swarm.")}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k, v in _PINNED.items():
        monkeypatch.setitem(sys.modules, k, v)
    for k in [k for k in os.environ if k.startswith(("SUBSTRATE_", "SWARM_"))]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # a tmp dir is never mistaken for a checkout's subdir
    substrate_client.reset()
    tee_mod.reset()
    yield
    substrate_client.reset()
    tee_mod.reset()


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv("SUBSTRATE_URL", "http://substrate.test:8787/")
    monkeypatch.setenv("SUBSTRATE_TOKEN", TOKEN)


@pytest.fixture()
def fake(monkeypatch):
    f = FakeSubstrate()
    monkeypatch.setattr(urllib.request, "urlopen", f)
    return f


def _no_urlopen(monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("socket")))
    return calls


def _emit(tmp_path, typ="task.claimed", source="A05@code_checks", corr=CORR, task_id="T1-be"):
    return runlog.emit(typ, {"task_id": task_id, "status": "ok"}, source=source, correlation_id=corr, task_id=task_id,
                       root=tmp_path)


def _db(tmp_path):
    return tmp_path / ".swarm" / "substrate-tee.db"


def _load_orch_plan():
    spec = importlib.util.spec_from_file_location("orch_plan_under_test", ROOT / "scripts" / "orch_plan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _orch_plan(capsys, tmp_path, *args):
    mod = _load_orch_plan()
    repo = tmp_path / "myrepo"
    repo.mkdir(exist_ok=True)
    capsys.readouterr()
    rc = mod.AgentScript("A01", "orch_plan", mod.run, description=mod.__doc__, add_args=mod.add_args).main(
        ["--json", "--root", str(repo), *args])
    out = capsys.readouterr()
    return rc, json.loads(out.out), out


# --- off switches: no socket, no sqlite ---------------------------------------------------------------------
def test_url_unset_is_a_pure_noop(tmp_path, monkeypatch):
    calls = _no_urlopen(monkeypatch)
    rec = _emit(tmp_path)
    assert calls == []
    assert not _db(tmp_path).exists()
    assert re.fullmatch(r"[0-9a-f]{32}", rec["msg_id"])
    line = json.loads((tmp_path / ".swarm" / "events.jsonl").read_text().splitlines()[0])
    assert line == rec and {"ts", "type", "source", "correlation_id", "task_id", "payload"} <= set(line)
    assert substrate_client.rest_post("/events", {}) is None and substrate_client.mcp_call("graph_bind", {}) is None
    assert tee_mod.tee(rec, tmp_path) is False and not _db(tmp_path).exists()


@pytest.mark.parametrize("url", ["", "   ", "file:///etc/passwd", "ftp://x"])
def test_blank_or_non_http_url_is_off(monkeypatch, url):
    calls = _no_urlopen(monkeypatch)
    assert not substrate_client.enabled({"SUBSTRATE_URL": url})
    assert substrate_client.rest_post("/events", {}, {"SUBSTRATE_URL": url}) is None
    assert calls == []


def test_substrate_disabled_wins(tmp_path, monkeypatch, on):
    monkeypatch.setenv("SUBSTRATE_DISABLED", "1")
    calls = _no_urlopen(monkeypatch)
    _emit(tmp_path)
    assert calls == [] and not _db(tmp_path).exists()
    assert not substrate_client.enabled()


# --- client -------------------------------------------------------------------------------------------------
def test_rest_post_headers_status_and_timeout(on, fake):
    got = substrate_client.rest_post("/events", {"a": 1})
    assert got == (200, '{"ok":true}')
    c = fake.calls[0]
    assert c["headers"]["authorization"] == f"Bearer {TOKEN}" and c["headers"]["content-type"] == "application/json"
    assert c["timeout"] == 1.5 and c["body"] == {"a": 1}


def test_rest_post_reports_http_errors_and_swallows_the_rest(on, fake, monkeypatch):
    fake.events_status = 403
    assert substrate_client.rest_post("/events", {})[0] == 403
    substrate_client.reset()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError(TOKEN)))
    assert substrate_client.rest_post("/events", {}) is None


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_call_json_and_sse(on, monkeypatch, sse):
    f = FakeSubstrate(sse=sse)
    monkeypatch.setattr(urllib.request, "urlopen", f)
    got = substrate_client.mcp_call("graph_bind", {"correlation_id": "c", "graph_id": GID_A})
    assert got == {"status": "created", "correlation_id": "c", "graph_id": GID_A, "conflict": False}
    c = f.calls[0]
    assert c["headers"]["accept"] == "application/json, text/event-stream"
    assert c["body"] == {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "graph_bind", "arguments": {"correlation_id": "c", "graph_id": GID_A}}}


@pytest.mark.parametrize("reply", [
    '{"jsonrpc":"2.0","id":1,"result":{"isError":true,"content":[{"type":"text","text":"{\\"x\\":1}"}]}}',
    '{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"no"}}',
    '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"not json"}]}}',
    "data: nonsense\n\n",
    "",
])
def test_mcp_call_failures_are_none(on, monkeypatch, reply):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(reply))
    assert substrate_client.mcp_call("graph_bind", {}) is None


def test_dead_host_costs_one_attempt_per_backoff(on, monkeypatch):
    n = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: n.append(1) or (_ for _ in ()).throw(OSError("down")))
    assert substrate_client.rest_post("/events", {}) is None and substrate_client.rest_post("/events", {}) is None
    assert len(n) == 1


# --- tables -------------------------------------------------------------------------------------------------
def test_surfaces_match_agents_json():
    agents = json.loads((ROOT / "agents.json").read_text())["agents"]
    assert {a["id"] for a in agents} == set(tee_mod.AGENT_SURFACES) and len(agents) == 15
    for a in agents:
        assert tee_mod.AGENT_SURFACES[a["id"]] == f"swarm-{a['id'].lower()}-{a['code'].lower()}"
    assert list(tee_mod.AGENT_SURFACES.values())[0] == "swarm-a01-orch" and list(tee_mod.AGENT_SURFACES.values())[-1] == "swarm-a15-doc"
    assert [i for i in tee_mod.AGENT_SURFACES] == [f"A{n:02d}" for n in range(1, 16)]


EVENT_KINDS = {"session.start", "prompt", "claim", "tool.call", "file.edit", "shell", "commit", "pr", "handoff", "session.end",
               "note", "warning"}
_SOURCES = [*sorted((ROOT / "swarm").glob("*.py")), *sorted((ROOT / "scripts").glob("*.py")), *sorted((ROOT / "hooks").glob("*.py"))]
_PASS_THROUGH = "swarm/script_base.py"  # Ctx.emit forwards its own `event_type` argument to runlog.emit


def _emit_type_arg(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    return next((kw.value for kw in call.keywords if kw.arg == "event_type"), None)


def _emit_sites():
    """(literal swarm types -> files, non-literal emit sites) over every `emit(...)`/`x.emit(...)` call in the AST."""
    literal, other = {}, []
    for path in _SOURCES:
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=rel)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not ((isinstance(f, ast.Name) and f.id == "emit") or (isinstance(f, ast.Attribute) and f.attr == "emit")):
                continue
            arg = _emit_type_arg(node)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal.setdefault(arg.value, []).append(path.name)
            elif isinstance(arg, ast.JoinedStr):
                text = "".join(p.value if isinstance(p, ast.Constant) else "x" for p in arg.values)
                literal.setdefault(text, []).append(path.name)
            elif not (rel == _PASS_THROUGH and isinstance(arg, ast.Name) and arg.id == "event_type"):
                other.append((rel, node.lineno, ast.unparse(node)[:60]))
    return literal, other


def test_every_emitted_type_is_mapped_and_nothing_is_stale():
    literal, other = _emit_sites()
    assert other == [], f"emit site with a non-literal type: register it explicitly in the test and the table: {other}"
    assert len(literal) >= 12, literal
    unmapped = sorted(t for t in literal if tee_mod.kind_for(t) is None)
    assert unmapped == [], f"unmapped swarm event types (add to substrate_tee.TYPE_KINDS): {unmapped}"
    assert set(tee_mod.TYPE_KINDS) <= set(literal), "TYPE_KINDS lists a type nothing emits"
    assert {"script.x", "script.x.error"} <= set(literal)


def test_kinds_are_valid_and_follow_the_documented_rules():
    assert set(tee_mod.TYPE_KINDS.values()) | {k for _, k in tee_mod.PREFIX_KINDS} <= EVENT_KINDS
    for t in ("gate.verdict.unrecorded", "gate.findings.coerced", "gate.findings.synthesized", "gate.findings.unattributed"):
        assert tee_mod.kind_for(t) == "warning"
    assert tee_mod.kind_for("plan.updated") == tee_mod.kind_for("task.transition") == "note"
    assert tee_mod.kind_for("script.orch_plan") == tee_mod.kind_for("script.qa_gate.error") == "tool.call"
    assert tee_mod.kind_for("task.claimed") == "claim"
    assert tee_mod.kind_for("made.up") is None and tee_mod.kind_for("script.") is None


def test_script_agent_ids_are_known():
    for path in (ROOT / "scripts").glob("*.py"):
        for aid in re.findall(r'AgentScript\(\s*"(A\d{2})"', path.read_text(encoding="utf-8")):
            assert aid in tee_mod.AGENT_SURFACES, path.name


@pytest.mark.parametrize("rec,agent", [
    ({"source": "A05@code_checks"}, "A05"),
    ({"source": "A15@docs_bundle"}, "A15"),
    ({"source": "swarm.envelope", "payload": {"source": "A08@local"}}, "A08"),
    ({"source": "swarm.envelope", "payload": {"source": "A99"}}, None),
    ({"source": "swarm.envelope", "payload": {}}, None),
    ({"source": "t"}, None),
    ({"source": "A16@x"}, None),
])
def test_agent_of(rec, agent):
    assert tee_mod.agent_of(rec) == agent


# --- Graph ID -----------------------------------------------------------------------------------------------
def test_mint_graph_id_format_and_clock():
    before = int(time.time() * 1000)
    gid = tee_mod.mint_graph_id()
    after = int(time.time() * 1000)
    m = re.fullmatch(r"ut-([0-9a-z]+)-([0-9a-f]{8})", gid)
    assert m and before <= int(m.group(1), 36) <= after
    assert tee_mod.mint_graph_id(0).startswith("ut-0-") and tee_mod.mint_graph_id(36).startswith("ut-10-")
    assert tee_mod.mint_graph_id() != tee_mod.mint_graph_id()


def test_resolve_order_explicit_env_brief_mint():
    brief = f'<ISSUES graphId="{GID_B}"> ... Graph ID: ut-zzzz-ffffffff'
    env = {"SUBSTRATE_GRAPH_ID": "ut-fromenv-00000001"}
    assert tee_mod.resolve_graph_id("c", explicit=GID_A, env=env, brief_text=brief) == GID_A
    assert tee_mod.resolve_graph_id("c", env=env, brief_text=brief) == "ut-fromenv-00000001"
    assert tee_mod.resolve_graph_id("c", env={}, brief_text=brief) == GID_B
    assert tee_mod.resolve_graph_id("c", env={}, brief_text="Graph ID: ut-q1-deadbeef.") == "ut-q1-deadbeef"
    assert tee_mod.resolve_graph_id("c", env={}, brief_text="no id here ut-bad-xyz").startswith("ut-")
    assert tee_mod.resolve_graph_id("c", explicit="garbage", env={"SUBSTRATE_GRAPH_ID": "junk"}, brief_text=None) != "garbage"


def test_bind_adopts_the_servers_winner_even_on_conflict(tmp_path, on, monkeypatch):
    f = FakeSubstrate(bindings={CORR: GID_A})
    monkeypatch.setattr(urllib.request, "urlopen", f)
    assert tee_mod.bind_graph(CORR, GID_B, root=tmp_path) == GID_A
    assert f.at("/mcp", "graph_bind")[0]["body"]["params"]["arguments"] == {"correlation_id": CORR, "graph_id": GID_B}
    assert tee_mod.cached_graph_id(CORR, tmp_path) == GID_A
    # a later process teeing for this correlation uses the winner without asking again
    mcp_before = len(f.at("/mcp"))
    rec = _emit(tmp_path)
    assert rec["type"] == "task.claimed"
    assert [e["graph_id"] for e in f.events] == [GID_A] and f.events[0]["session_id"] == f"A05@r0:{GID_A}"
    assert len(f.at("/mcp")) == mcp_before  # emit added only the /events request


def test_bind_without_an_answer_caches_nothing(tmp_path, on, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert tee_mod.bind_graph(CORR, GID_A, root=tmp_path) is None
    assert tee_mod.lookup_graph_id(CORR, root=tmp_path) is None
    assert tee_mod.cached_graph_id(CORR, tmp_path) is None


# --- the tee ------------------------------------------------------------------------------------------------
def _fake_git(monkeypatch, stdout="", returncode=0, exc=None):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    monkeypatch.setattr(tee_mod.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("remote,slug", [
    ("https://github.com/owner/name.git\n", "owner/name"),
    ("https://github.com/owner/name\n", "owner/name"),
    ("git@github.com:o/r.git\n", "o/r"),
])
def test_repo_slug_parses_the_origin_remote(tmp_path, monkeypatch, remote, slug):
    calls = _fake_git(monkeypatch, stdout=remote)
    assert tee_mod.repo_slug(tmp_path) == slug
    assert tee_mod.repo_slug(tmp_path) == slug and len(calls) == 1  # memoized per root
    cmd, kw = calls[0]
    assert cmd == ["git", "-C", str(tmp_path.resolve()), "remote", "get-url", "origin"] and kw["timeout"] == 2


@pytest.mark.parametrize("kw", [{"returncode": 2}, {"stdout": "\n"}, {"exc": FileNotFoundError("git")},
                                {"exc": subprocess.TimeoutExpired("git", 2)}])
def test_repo_slug_falls_back_to_the_directory_name(tmp_path, monkeypatch, kw):
    _fake_git(monkeypatch, **kw)
    assert tee_mod.repo_slug(tmp_path) == tmp_path.resolve().name


def test_repo_slug_with_no_remote_is_the_directory_name(tmp_path):
    repo = tmp_path / "plainrepo"
    repo.mkdir()
    assert tee_mod.repo_slug(repo) == "plainrepo"


def test_build_event_carries_the_repo_slug(tmp_path, monkeypatch):
    _fake_git(monkeypatch, stdout="git@github.com:o/r.git\n")
    rec = {"type": "task.claimed", "source": "A05@x", "correlation_id": CORR, "msg_id": "m", "task_id": "T1", "payload": {}}
    assert tee_mod.build_event(rec, GID_A, root=tmp_path)["repo"] == "o/r"
    assert "repo" not in tee_mod.build_event(rec, GID_A)


def test_tee_event_fields(tmp_path, on, fake, monkeypatch):
    monkeypatch.setenv("SWARM_REPLICA", "r7")
    fake.bindings[CORR] = GID_A
    rec = _emit(tmp_path)
    (body,) = fake.events
    assert body == {
        "kind": "claim", "summary": "task.claimed T1-be", "surface": "swarm-a05-be", "session_id": f"A05@r7:{GID_A}",
        "graph_id": GID_A, "node_id": "T1-be", "actor": "agent", "repo": tmp_path.name,
        "payload": {"correlation_id": CORR, "msg_id": rec["msg_id"], "swarm_type": "task.claimed", "status": "ok"},
    }
    call = fake.at("/events")[0]
    assert call["headers"]["authorization"] == f"Bearer {TOKEN}" and TOKEN not in json.dumps(body)


def test_tee_default_replica_and_optional_ids(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    rec = {"type": "plan.updated", "source": "A01@orch_plan", "correlation_id": CORR, "task_id": None, "payload": {},
           "msg_id": "m1", "trace_id": "tr", "causation_id": "ca"}
    assert tee_mod.tee(rec, tmp_path) is True
    (body,) = fake.events
    assert body["session_id"] == f"A01@r0:{GID_A}" and body["kind"] == "note" and "node_id" not in body
    assert body["payload"] == {"correlation_id": CORR, "msg_id": "m1", "swarm_type": "plan.updated", "trace_id": "tr", "causation_id": "ca"}


def test_tee_script_exit_is_tool_call_with_swarm_type(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    _emit(tmp_path, typ="script.qa_gate", source="A08@qa_gate", task_id=None)
    _emit(tmp_path, typ="script.qa_gate.error", source="A08@qa_gate", task_id=None)
    assert [(e["kind"], e["surface"], e["payload"]["swarm_type"]) for e in fake.events] == [
        ("tool.call", "swarm-a08-qa", "script.qa_gate"), ("tool.call", "swarm-a08-qa", "script.qa_gate.error")]


@pytest.mark.parametrize("rec", [
    {"type": "made.up.type", "source": "A05@x", "correlation_id": CORR, "msg_id": "m"},   # unmapped: never a silent note
    {"type": "task.claimed", "source": "t", "correlation_id": CORR, "msg_id": "m"},       # no agent identity
    {"type": "task.claimed", "source": "A05@x", "correlation_id": None, "msg_id": "m"},   # no correlation -> no graph
    {"type": "task.claimed", "source": "A05@x", "correlation_id": CORR},                  # no msg_id
])
def test_tee_skips_what_it_cannot_attribute(tmp_path, on, fake, rec):
    fake.bindings[CORR] = GID_A
    assert tee_mod.tee({"task_id": None, "payload": {}, **rec}, tmp_path) is False
    assert fake.events == []


def test_unbound_correlation_costs_one_lookup_across_tees(tmp_path, on, fake):
    _emit(tmp_path)
    _emit(tmp_path, typ="plan.updated", source="A01@orch_plan", task_id=None)
    assert fake.events == [] and len(fake.at("/mcp", "graph_bind")) == 1
    assert tee_mod.cached_graph_id(CORR, tmp_path) is None  # the negative result is never persisted


def test_unbound_negative_cache_expires_and_bind_clears_it(tmp_path, on, fake):
    _emit(tmp_path)
    _emit(tmp_path)
    assert len(fake.at("/mcp", "graph_bind")) == 1
    tee_mod._unbound[CORR] = time.monotonic() - 1  # TTL elapsed
    _emit(tmp_path)
    assert len(fake.at("/mcp", "graph_bind")) == 2
    _emit(tmp_path)
    assert len(fake.at("/mcp", "graph_bind")) == 2
    assert tee_mod.bind_graph(CORR, GID_A, root=tmp_path) == GID_A  # a bind clears the negative entry
    assert CORR not in tee_mod._unbound
    _emit(tmp_path)
    assert [e["graph_id"] for e in fake.events] == [GID_A]


def test_unbound_correlation_is_skipped_after_one_forward_lookup(tmp_path, on, fake):
    _emit(tmp_path)
    assert fake.events == []
    (lookup,) = fake.at("/mcp", "graph_bind")
    assert lookup["body"]["params"]["arguments"] == {"correlation_id": CORR}
    assert tee_mod.cached_graph_id(CORR, tmp_path) is None


def test_forward_lookup_is_cached_across_emits(tmp_path, on, fake):
    fake.bindings[CORR] = GID_B
    _emit(tmp_path)
    _emit(tmp_path, typ="plan.updated", source="A01@orch_plan", task_id=None)
    assert len(fake.at("/mcp", "graph_bind")) == 1 and [e["graph_id"] for e in fake.events] == [GID_B, GID_B]


def test_server_refusal_drops_the_event_and_a_republish_retries(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    fake.events_status = 403
    rec = _emit(tmp_path)
    assert len(fake.events) == 1
    fake.events_status = 200
    assert tee_mod.tee(rec, tmp_path) is True and len(fake.events) == 2


# --- dedupe -------------------------------------------------------------------------------------------------
def test_double_publish_of_one_record_is_one_request(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    rec = _emit(tmp_path)
    assert tee_mod.tee(rec, tmp_path) is False and tee_mod.tee(dict(rec), tmp_path) is False
    assert len(fake.events) == 1
    rec2 = _emit(tmp_path)  # a different message is a different key
    assert rec2["msg_id"] != rec["msg_id"] and len(fake.events) == 2


def test_dedupe_key_is_surface_plus_msg_id(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    base = {"type": "task.claimed", "correlation_id": CORR, "task_id": "T", "payload": {}, "msg_id": "same"}
    assert tee_mod.tee({**base, "source": "A05@x"}, tmp_path) and tee_mod.tee({**base, "source": "A06@x"}, tmp_path)
    assert [e["surface"] for e in fake.events] == ["swarm-a05-be", "swarm-a06-fe"]


def test_dedupe_window_is_24h_and_pruned_on_write(tmp_path, on, fake):
    fake.bindings[CORR] = GID_A
    _emit(tmp_path)
    con = sqlite3.connect(_db(tmp_path))
    con.execute("INSERT INTO seen VALUES ('swarm-a05-be', 'ancient', ?)", (time.time() - 25 * 3600,))
    con.execute("INSERT INTO seen VALUES ('swarm-a05-be', 'recent', ?)", (time.time() - 23 * 3600,))
    con.commit()
    con.close()
    _emit(tmp_path)
    con = sqlite3.connect(_db(tmp_path))
    ids = {r[0] for r in con.execute("SELECT msg_id FROM seen")}
    con.close()
    assert "ancient" not in ids and "recent" in ids and len(ids) == 3


# --- fail-open ----------------------------------------------------------------------------------------------
def test_tee_never_raises(tmp_path, on, fake, monkeypatch):
    fake.bindings[CORR] = GID_A
    monkeypatch.setattr(tee_mod, "_claim", lambda *a, **k: 1 / 0)
    assert tee_mod.tee({"type": "task.claimed", "source": "A05@x", "correlation_id": CORR, "msg_id": "m", "payload": {}}, tmp_path) is False
    assert tee_mod.tee(None, tmp_path) is False  # type: ignore[arg-type]
    monkeypatch.setattr(tee_mod, "tee", lambda *a, **k: 1 / 0)  # runlog.emit swallows even a broken tee
    assert _emit(tmp_path)["type"] == "task.claimed"


def test_runlog_survives_a_dead_substrate(tmp_path, on, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError(TOKEN)))
    rec = _emit(tmp_path)
    assert rec["type"] == "task.claimed" and len((tmp_path / ".swarm" / "events.jsonl").read_text().splitlines()) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# --- orch_plan end to end -----------------------------------------------------------------------------------
def test_orch_plan_unchanged_when_substrate_unreachable_in_process(tmp_path, on, capsys, monkeypatch):
    attempts = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: attempts.append(a) or (_ for _ in ()).throw(urllib.error.URLError(f"refused {TOKEN}")))
    rc, out, raw = _orch_plan(capsys, tmp_path, "--brief-text", "billing", "--prefix", "R", "--correlation-id", CORR)
    assert rc == 0 and out["status"] == "ok" and len(out["tasks"]) == 13 and out["correlation_id"] == CORR
    assert attempts, "the unreachable substrate was never attempted"
    assert Path(out["plan_file"]).exists() and (tmp_path / ".swarm" / "tasks.db").exists()
    assert TOKEN not in raw.out + raw.err
    # the second run of the same plan still reuses
    rc, again, _ = _orch_plan(capsys, tmp_path, "--brief-text", "billing", "--prefix", "R", "--correlation-id", CORR)
    assert rc == 0 and again["reused"] is True


def test_orch_plan_unchanged_when_substrate_unreachable_subprocess(tmp_path):
    for name in ("plain", "down"):
        (tmp_path / name).mkdir()
    base = ["--json", "--brief-text", "billing", "--prefix", "R", "--correlation-id", CORR]
    plain = run_script("orch_plan.py", *base, "--root", str(tmp_path / "plain"), env={"SWARM_DIR": str(tmp_path / "plain" / ".swarm")})
    down = run_script("orch_plan.py", *base, "--root", str(tmp_path / "down"),
                      env={"SWARM_DIR": str(tmp_path / "down" / ".swarm"), "SUBSTRATE_URL": "http://127.0.0.1:1", "SUBSTRATE_TOKEN": TOKEN})
    assert plain.returncode == 0 and down.returncode == 0, plain.stdout + plain.stderr + down.stdout + down.stderr
    a, b = json.loads(plain.stdout), json.loads(down.stdout)
    assert set(a) == set(b) and [t["task_id"] for t in a["tasks"]] == [t["task_id"] for t in b["tasks"]]
    assert a["status"] == b["status"] == "ok" and TOKEN not in down.stdout + down.stderr
    assert not (tmp_path / "plain" / ".swarm" / "substrate-tee.db").exists()


def test_orch_plan_without_substrate_creates_no_tee_state(tmp_path, capsys, monkeypatch):
    calls = _no_urlopen(monkeypatch)
    rc, out, _ = _orch_plan(capsys, tmp_path, "--brief-text", "billing", "--prefix", "R")
    assert rc == 0 and calls == [] and not _db(tmp_path).exists()


def test_orch_plan_binds_registers_and_tees(tmp_path, on, fake, capsys):
    rc, out, _ = _orch_plan(capsys, tmp_path, "--brief-text", f"billing\nGraph ID: {GID_B}", "--prefix", "R", "--correlation-id", CORR)
    assert rc == 0
    (bind,) = fake.at("/mcp", "graph_bind")
    assert bind["body"]["params"]["arguments"] == {"correlation_id": CORR, "graph_id": GID_B}  # brief text > minted
    (reg,) = fake.at("/mcp", "graph_register")
    args = reg["body"]["params"]["arguments"]
    assert args["graph_id"] == GID_B and args["repo"] == "myrepo" and args["surface"] == "swarm-a01-orch" and args["status"] == "planned"
    assert args["nodes"] == [{"node_id": t["task_id"]} for t in out["tasks"]]
    # bind happens before the first tee'd event, which are the plan snapshot and the script exit
    paths = [c["path"] + (c["body"]["params"]["name"] if c["path"] == "/mcp" else "") for c in fake.calls]
    assert paths[:2] == ["/mcpgraph_bind", "/mcpgraph_register"] and paths[2:] == ["/events", "/events"]
    assert [(e["kind"], e["payload"]["swarm_type"]) for e in fake.events] == [("note", "plan.updated"), ("tool.call", "script.orch_plan")]
    assert {e["surface"] for e in fake.events} == {"swarm-a01-orch"} and {e["session_id"] for e in fake.events} == {f"A01@r0:{GID_B}"}
    assert tee_mod.cached_graph_id(CORR, tmp_path / "myrepo") == GID_B


def test_orch_plan_graph_id_flag_and_conflict_adoption(tmp_path, on, monkeypatch, capsys):
    f = FakeSubstrate(bindings={CORR: GID_A})
    monkeypatch.setattr(urllib.request, "urlopen", f)
    rc, out, _ = _orch_plan(capsys, tmp_path, "--brief-text", "billing", "--prefix", "R", "--correlation-id", CORR, "--graph-id", GID_B)
    assert rc == 0
    assert f.at("/mcp", "graph_bind")[0]["body"]["params"]["arguments"]["graph_id"] == GID_B
    assert f.at("/mcp", "graph_register")[0]["body"]["params"]["arguments"]["graph_id"] == GID_A  # the stored winner
    assert {e["graph_id"] for e in f.events} == {GID_A}


def test_orch_plan_rejects_a_malformed_graph_id(tmp_path, capsys):
    rc, out, _ = _orch_plan(capsys, tmp_path, "--brief-text", "billing", "--graph-id", "not-a-graph")
    assert rc == 2 and out["error"]["code"] == "E-INPUT"


def test_replan_reuses_and_does_not_rebind(tmp_path, on, fake, capsys):
    base = ("--brief-text", "billing", "--prefix", "R", "--correlation-id", CORR)
    assert _orch_plan(capsys, tmp_path, *base)[0] == 0
    binds = len(fake.at("/mcp", "graph_bind"))
    rc, again, _ = _orch_plan(capsys, tmp_path, *base)
    assert rc == 0 and again["reused"] is True
    assert len(fake.at("/mcp", "graph_bind")) == binds and len(fake.at("/mcp", "graph_register")) == 1


def test_dry_run_plan_never_touches_substrate(tmp_path, on, fake, capsys):
    rc, out, _ = _orch_plan(capsys, tmp_path, "--dry-run")
    assert rc == 0 and out["dry_run"] is True
    assert fake.calls == []  # a dry run has no correlation on the context: no bind, no register, no tee
