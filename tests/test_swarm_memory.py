"""ADR 0001 S3: swarm memory is the substrate's governed `memory_write`; a refusal is handled, not worked around. No real network.

Covers S3 criteria 1-5 and 7 on the swarm side.

Criterion 7 (no swarm-side memory store is read by any agent) is proven HERE, by the source scan at the bottom of this module.
The substrate repo's `store-lock.test.ts` cannot prove it: it scans that repository's TypeScript for memory-table SQL and
`insertEntry` and knows nothing about this repository or any vector store / local KV that could sit on an agent's context path.
"""
import ast
import hashlib
import io
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

import pytest

from conftest import ROOT

from swarm import memory as mem, substrate_client, substrate_tee as tee_mod
from swarm.errors import ErrorCode, SwarmError

CORR = "corr-s3"
TOKEN = "sekrit-token-value"
GID = "ut-mabc123-0123abcd"
REPO = "acme/widgets"


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
    """Stands in for urllib.request.urlopen: REST /memory (scripted replies) and /brief, stateless MCP /mcp.

    `memory` is a list of `(http_status, body)`; the last reply repeats once the list is used up (an exhausted script must not
    raise: substrate_client swallows every exception, which would turn a test bug into a silent "unreachable").
    """

    def __init__(self, memory=(), entries=None, brief=("# Brief\n- decision d1\n", 200)):
        self.memory = list(memory)
        self.entries = entries          # what memory_search answers; None = the MCP call fails (HTTP 500)
        self.brief = brief
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        path = urlparse(req.full_url).path
        body = json.loads(req.data)
        self.calls.append({"path": path, "body": body, "headers": {k.lower(): v for k, v in req.header_items()}, "timeout": timeout})
        if path == "/memory":
            status, payload = self.memory.pop(0) if len(self.memory) > 1 else self.memory[0]
            return self._answer(req, status, payload if isinstance(payload, str) else json.dumps(payload))
        if path == "/brief":
            return self._answer(req, self.brief[1], self.brief[0])
        assert path == "/mcp", path
        name = body["params"]["name"]
        if name == "memory_search":
            if self.entries is None:
                return self._answer(req, 500, "boom")
            out = self.entries
        else:  # graph_bind forward lookup (assignment_prompt)
            out = {"status": "unbound", "correlation_id": body["params"]["arguments"].get("correlation_id"), "graph_id": None}
        rpc = {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(out)}]}}
        return _Resp(json.dumps(rpc))

    @staticmethod
    def _answer(req, status, text):
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(text.encode("utf-8")))
        return _Resp(text, status)

    def at(self, path, tool=None):
        return [c for c in self.calls if c["path"] == path and (tool is None or c["body"]["params"]["name"] == tool)]

    @property
    def writes(self):
        return [c["body"] for c in self.at("/memory")]


def result(outcome="accepted", reason="ok", verified=True, **extra):
    """A MemoryWriteResult as memory-write.ts / types.ts define it."""
    return {"outcome": outcome, "reason": reason, "remedy": f"remedy for {reason}", "verified_persisted": verified,
            "idempotency_key": "server-echo", "writer": "swarm-a03-arch", **extra}


def accepted(version=1, entry_id="mem_1"):
    return (200, result(entry={"id": entry_id, "version": version, "kind": "decision"}))


def unverified():
    return (202, result(reason="ok", verified=False))


def version_conflict(reason="version.stale"):
    return (409, result("conflict", reason, verified=False, conflict_with="mem_old"))


def decision(version, subject="db choice", entry_id="mem_old", **kw):
    return {"id": entry_id, "kind": "decision", "subject": subject, "version": version, "scope": f"repo:{REPO}", **kw}


# Other test modules reload every `swarm*` module (see the swarm_dir fixture); pin the ones imported here so the module under
# test and the client the tests patch around are the very same objects.
_PINNED = {k: v for k, v in sys.modules.items() if k == "swarm" or k.startswith("swarm.")}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k, v in _PINNED.items():
        monkeypatch.setitem(sys.modules, k, v)
    for k in [k for k in os.environ if k.startswith(("SUBSTRATE_", "SWARM_"))]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
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
def sleeps():
    return []


@pytest.fixture()
def serve(monkeypatch, on):
    def _serve(**kw):
        fake = FakeSubstrate(**kw)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return fake
    return _serve


@pytest.fixture()
def write(sleeps):
    def _write(kind="decision", scope="repo", text="use postgres", **kw):
        kw.setdefault("correlation_id", CORR)
        kw.setdefault("task_id", "T1-arch")
        kw.setdefault("sleep", sleeps.append)
        if scope == "repo":
            kw.setdefault("repo", REPO)
        return mem.memory_write(kind, scope, text, **kw)
    return _write


def _no_urlopen(monkeypatch):
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("socket")))
    return calls


# --- criterion 1: kind map, scope map, input errors -----------------------------------------------------------
@pytest.mark.parametrize("swarm_kind,substrate_kind", [("decision", "decision"), ("retro", "fact"), ("pattern", "fact")])
def test_kind_map(serve, write, swarm_kind, substrate_kind):
    fake = serve(memory=[accepted()])
    assert write(swarm_kind).ok
    assert fake.writes[0]["kind"] == substrate_kind and fake.at("/memory")[0]["path"] == "/memory"


@pytest.mark.parametrize("scope,kw,expected", [
    ("graph", {"graph_id": GID}, f"graph:{GID}"),
    ("repo", {"repo": REPO}, f"repo:{REPO}"),
    ("agent", {"surface": "swarm-a03-arch"}, "agent:swarm-a03-arch"),
    ("global", {}, "global"),
])
def test_scope_map(serve, write, scope, kw, expected):
    fake = serve(memory=[accepted()])
    write("retro", scope, **kw)
    assert fake.writes[0]["scope"] == expected


@pytest.mark.parametrize("args,kw,field", [
    (("opinion", "repo", "t"), {"repo": REPO}, "kind"),
    (("decision", "universe", "t"), {}, "scope"),
    (("decision", "person", "t"), {}, "scope"),
    (("decision", "graph", "t"), {}, "graph_id"),
    (("decision", "graph", "t"), {"graph_id": "has space"}, "graph_id"),
    (("decision", "repo", "t"), {}, "repo"),
    (("decision", "repo", "t"), {"repo": "  "}, "repo"),
    (("decision", "agent", "t"), {}, "surface"),
    (("decision", "agent", "t"), {"surface": "hermes"}, "surface"),
    (("decision", "agent", "t"), {"surface": "A03"}, "surface"),
    (("decision", "global", ""), {}, "text"),
    (("decision", "global", "t"), {"attempt": 0}, "attempt"),
    (("decision", "global", "t"), {"correlation_id": ""}, "correlation_id"),
    (("decision", "global", "t"), {"task_id": ""}, "task_id"),
])
def test_bad_kind_scope_or_ids_are_e_input_and_nothing_is_sent(serve, args, kw, field):
    fake = serve(memory=[accepted()])
    kw = {"correlation_id": CORR, "task_id": "T1", **kw}
    with pytest.raises(SwarmError) as e:
        mem.memory_write(*args, **kw)
    assert e.value.code == ErrorCode.E_INPUT and e.value.details["field"] == field
    assert fake.calls == []


def test_request_body_and_credentials(serve, write):
    fake = serve(memory=[accepted()])
    out = write("decision", "repo", "use postgres", subject="db choice", graph_id=GID)
    (call,) = fake.calls
    assert call["headers"]["authorization"] == f"Bearer {TOKEN}" and call["timeout"] == 1.5
    assert call["body"] == {"scope": f"repo:{REPO}", "kind": "decision", "text": "use postgres", "subject": "db choice",
                            "graph_id": GID, "repo": REPO, "idempotency_key": out.idempotency_key}
    assert "writer" not in call["body"]  # the server decides the writer from the token
    assert TOKEN not in repr(out)


def test_swarm_surfaces_match_the_tee_table():
    assert mem.SWARM_SURFACES == tuple(tee_mod.AGENT_SURFACES.values()) and len(mem.SWARM_SURFACES) == 15


# --- criterion 5: idempotency key ---------------------------------------------------------------------------
def test_idempotency_key_is_sha256_of_the_six_fields():
    key = mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text")
    assert key == hashlib.sha256(b"c1|T1|2|decision|repo:acme/widgets|text").hexdigest() and re.fullmatch(r"[0-9a-f]{64}", key)
    assert key == mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text")  # stable for the same write


@pytest.mark.parametrize("other", [("c2", "T1", 2, "decision", "repo:acme/widgets", "text"), ("c1", "T2", 2, "decision", "repo:acme/widgets", "text"),
                                   ("c1", "T1", 3, "decision", "repo:acme/widgets", "text"), ("c1", "T1", 2, "fact", "repo:acme/widgets", "text"),
                                   ("c1", "T1", 2, "decision", "repo:acme/other", "text"), ("c1", "T1", 2, "decision", "repo:acme/widgets", "text2")])
def test_idempotency_key_differs_per_field(other):
    assert mem.idempotency_key(*other) != mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text")


def test_same_write_same_key_different_attempt_content_scope_or_kind_new_key(serve, write):
    fake = serve(memory=[accepted()])
    a, b = write(text="x"), write(text="x")
    assert a.idempotency_key == b.idempotency_key == mem.idempotency_key(CORR, "T1-arch", 1, "decision", f"repo:{REPO}", "x")
    assert write(text="x", attempt=2).idempotency_key != a.idempotency_key
    assert write(text="y").idempotency_key != a.idempotency_key
    assert write(text="x", scope="graph", graph_id=GID).idempotency_key != a.idempotency_key
    assert write(kind="retro", text="x").idempotency_key != a.idempotency_key
    assert len(fake.writes) == 6


def test_decision_at_agent_scope_denied_by_server_is_e_policy(serve, write):
    fake = serve(memory=[(403, result("denied", "rbac.no-grant", verified=False))])
    with pytest.raises(SwarmError) as e:
        write("decision", "agent", surface="swarm-a03-arch")
    assert e.value.code == ErrorCode.E_POLICY and "rbac.no-grant" in str(e.value)
    assert len(fake.writes) == 1


# --- criterion 4: nothing is done unless accepted AND verified ----------------------------------------------
def test_ok_only_when_accepted_and_verified():
    assert mem.WriteOutcome(mem.ACCEPTED, verified_persisted=True).ok
    assert not mem.WriteOutcome(mem.ACCEPTED, verified_persisted=False).ok
    assert not mem.WriteOutcome(mem.ACCEPTED_UNVERIFIED, verified_persisted=True).ok
    for status in (mem.PROPOSAL, mem.QUARANTINED, mem.CONFLICT, mem.UNAVAILABLE):
        assert not mem.WriteOutcome(status, verified_persisted=True).ok


def test_accepted_and_verified_is_ok_with_the_entry(serve, write):
    serve(memory=[accepted(version=4, entry_id="mem_9")])
    out = write()
    assert out.ok and out.status == mem.ACCEPTED and out.verified_persisted and out.attempts == 1
    assert (out.entry_id, out.version) == ("mem_9", 4)


def test_unverified_write_retries_with_the_same_key_then_succeeds(serve, write, sleeps):
    fake = serve(memory=[unverified(), unverified(), accepted()])
    out = write()
    assert out.ok and out.attempts == 3
    assert len(fake.writes) == 3 and len({w["idempotency_key"] for w in fake.writes}) == 1
    assert sleeps == [0.5, 1.0]  # injected sleep: nothing really slept


def test_unverified_forever_is_accepted_unverified_not_ok(serve, write, sleeps):
    fake = serve(memory=[unverified()])
    out = write()
    assert not out.ok and out.status == mem.ACCEPTED_UNVERIFIED and not out.verified_persisted
    assert len(fake.writes) == mem.MAX_UNVERIFIED_POSTS == 3 and len({w["idempotency_key"] for w in fake.writes}) == 1
    assert len(sleeps) == 2


def test_in_flight_conflict_is_retried_with_the_same_key(serve, write, sleeps):
    fake = serve(memory=[(409, result("conflict", "write.in-flight", verified=False)), accepted()])
    out = write()
    assert out.ok and len({w["idempotency_key"] for w in fake.writes}) == 1 and sleeps == [0.5]


# --- criterion 3: the four outcomes ---------------------------------------------------------------------------
def test_denied_raises_e_policy_and_is_never_retried(serve, write, sleeps):
    fake = serve(memory=[(403, result("denied", "rbac.no-grant", verified=False))])
    with pytest.raises(SwarmError) as e:
        write("retro", "global")
    assert e.value.code == ErrorCode.E_POLICY and "rbac.no-grant" in str(e.value)
    assert e.value.details["reason"] == "rbac.no-grant" and e.value.details["writer"] == "swarm-a03-arch"
    assert len(fake.writes) == 1 and sleeps == []


@pytest.mark.parametrize("http", [401, 403])
def test_an_auth_refusal_without_a_result_body_is_also_e_policy(serve, write, http):
    fake = serve(memory=[(http, {"error": "unauthorized"})])
    with pytest.raises(SwarmError) as e:
        write()
    assert e.value.code == ErrorCode.E_POLICY and len(fake.writes) == 1


def test_quarantined_is_returned_and_not_retried(serve, write, sleeps):
    fake = serve(memory=[(422, result("quarantined", "quarantine.secret", verified=False, quarantine_id="memq_1"))])
    out = write("retro", "graph", graph_id=GID, text="the token is sk-ant-secret")
    assert out.status == mem.QUARANTINED and not out.ok
    assert (out.reason, out.quarantine_id, out.remedy) == ("quarantine.secret", "memq_1", "remedy for quarantine.secret")
    assert len(fake.writes) == 1 and sleeps == []


def test_review_required_is_a_proposal_and_not_retried(serve, write, sleeps):
    fake = serve(memory=[(409, result("conflict", "review.required", verified=False, review_id="memr_1"))])
    out = write("pattern", "global")
    assert out.status == mem.PROPOSAL and not out.ok and out.review_id == "memr_1" and out.reason == "review.required"
    assert fake.writes[0]["scope"] == "global" and len(fake.writes) == 1 and sleeps == []
    assert fake.at("/mcp") == []  # no re-read either


@pytest.mark.parametrize("reason", ["version.stale", "version.required", "version.raced", "standing.raced"])
def test_version_conflict_rereads_and_resubmits_with_expected_version(serve, write, reason):
    fake = serve(memory=[version_conflict(reason), accepted(version=4)],
                 entries=[decision(1, subject="other", entry_id="mem_other"), decision(3)])
    out = write(subject="db choice")
    assert out.ok and out.version == 4 and out.attempts == 2
    first, second = fake.writes
    assert "expected_version" not in first and second["expected_version"] == 3
    assert first["idempotency_key"] == second["idempotency_key"]  # one write, one key, however many re-submits
    (search,) = fake.at("/mcp", "memory_search")
    assert search["body"]["params"]["arguments"] == {"query": "", "limit": 100, "scope": f"repo:{REPO}"}


def test_stale_expected_version_is_replaced_by_the_current_one(serve, write):
    fake = serve(memory=[version_conflict(), accepted(version=8)], entries=[decision(7)])
    assert write(subject="db choice", expected_version=2).ok
    assert [w.get("expected_version") for w in fake.writes] == [2, 7]


def test_stale_with_nothing_in_force_resubmits_without_expected_version(serve, write):
    fake = serve(memory=[version_conflict(), accepted()], entries=[])
    assert write(subject="db choice", expected_version=2).ok
    assert [w.get("expected_version") for w in fake.writes] == [2, None]


def test_version_conflict_gives_up_after_two_resubmits(serve, write):
    fake = serve(memory=[version_conflict()], entries=[decision(3)])
    out = write(subject="db choice")
    assert out.status == mem.CONFLICT and not out.ok and out.reason == "version.stale"
    assert len(fake.writes) == 1 + mem.MAX_VERSION_RESUBMITS == 3
    assert len({w["idempotency_key"] for w in fake.writes}) == 1


def test_version_conflict_with_an_unreadable_current_version_is_a_conflict(serve, write):
    fake = serve(memory=[version_conflict()], entries=None)
    out = write(subject="db choice")
    assert out.status == mem.CONFLICT and len(fake.writes) == 1


def test_version_conflict_without_a_subject_or_for_a_non_decision_is_not_resubmitted(serve, write):
    fake = serve(memory=[version_conflict()], entries=[decision(3)])
    assert write().status == mem.CONFLICT and write("retro", subject="db choice").status == mem.CONFLICT
    assert len(fake.writes) == 2 and fake.at("/mcp") == []


def test_other_conflicts_are_returned_not_retried(serve, write, sleeps):
    fake = serve(memory=[(409, result("conflict", "idempotency.reused", verified=False, conflict_with="mem_x"))])
    out = write()
    assert out.status == mem.CONFLICT and out.reason == "idempotency.reused" and not out.ok
    assert len(fake.writes) == 1 and sleeps == [] and fake.at("/mcp") == []


# --- fail modes: memory write never pretends ------------------------------------------------------------------
def test_substrate_disabled_sends_nothing_and_is_unavailable(monkeypatch, write):
    calls = _no_urlopen(monkeypatch)
    out = write()
    assert out.status == mem.UNAVAILABLE and out.reason == "substrate.disabled" and not out.ok and calls == []


def test_unreachable_substrate_is_unavailable_not_ok_and_not_retried(on, monkeypatch, write, sleeps):
    calls = _no_urlopen(monkeypatch)
    out = write()
    assert out.status == mem.UNAVAILABLE and out.reason == "substrate.unreachable" and not out.ok
    assert len(calls) == 1 and sleeps == []


def test_store_outage_503_is_unavailable_not_denied(serve, write, sleeps):
    fake = serve(memory=[(503, result("denied", "store.unavailable", verified=False))])
    out = write()
    assert out.status == mem.UNAVAILABLE and out.reason == "store.unavailable" and not out.ok
    assert len(fake.writes) == 1 and sleeps == []


@pytest.mark.parametrize("http,body", [(503, {"error": "audit trail unwritable: memory_write"}), (500, "<html>proxy</html>"), (404, "nope")])
def test_an_answer_that_is_not_a_write_result_is_unavailable(serve, write, http, body):
    serve(memory=[(http, body)])
    out = write()
    assert out.status == mem.UNAVAILABLE and out.reason == f"http.{http}" and not out.ok


# --- criterion 2: reads are fail-open -------------------------------------------------------------------------
def test_memory_query_uses_memory_search(serve):
    fake = serve(entries=[decision(2), "junk", {"id": "m2"}])
    got = mem.memory_query("postgres", scope=f"repo:{REPO}", limit=5)
    assert got == [decision(2), {"id": "m2"}]
    (call,) = fake.at("/mcp")
    assert call["body"]["params"] == {"name": "memory_search", "arguments": {"query": "postgres", "limit": 5, "scope": f"repo:{REPO}"}}
    assert call["headers"]["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("limit,sent", [(0, 1), (500, 100), (10, 10)])
def test_memory_query_limit_is_clamped_and_scope_optional(serve, limit, sent):
    fake = serve(entries=[])
    mem.memory_query("q", limit=limit)
    assert fake.at("/mcp")[0]["body"]["params"]["arguments"] == {"query": "q", "limit": sent}


def test_memory_query_is_empty_when_unavailable(serve, monkeypatch):
    serve(entries=None)
    assert mem.memory_query("q") == []
    substrate_client.reset()
    calls = _no_urlopen(monkeypatch)
    assert mem.memory_query("q") == [] and len(calls) == 1
    monkeypatch.delenv("SUBSTRATE_URL")
    calls.clear()
    assert mem.memory_query("q") == [] and calls == []


def test_memory_query_with_a_non_list_answer_is_empty(serve):
    serve(entries={"error": "nope"})
    assert mem.memory_query("q") == []


def test_mcp_call_json_returns_arrays_and_mcp_call_still_only_objects(serve):
    serve(entries=[{"id": "a"}])
    args = {"query": ""}
    assert substrate_client.mcp_call_json("memory_search", args) == [{"id": "a"}]
    assert substrate_client.mcp_call("memory_search", args) is None


def test_run_start_context_posts_the_brief_request(serve):
    fake = serve(brief=("  # Brief\n- decision d1\n\n", 200))
    assert mem.run_start_context(REPO, GID) == "# Brief\n- decision d1"
    (call,) = fake.at("/brief")
    assert call["body"] == {"repo": REPO, "graph_id": GID, "surface": "swarm-a01-orch"}


def test_run_start_context_is_empty_when_unavailable(serve, monkeypatch):
    serve(brief=("boom", 500))
    assert mem.run_start_context(REPO) == ""
    serve(brief=("", 200))
    assert mem.run_start_context(REPO) == ""
    substrate_client.reset()
    calls = _no_urlopen(monkeypatch)
    assert mem.run_start_context(REPO) == "" and len(calls) == 1
    monkeypatch.delenv("SUBSTRATE_URL")
    calls.clear()
    assert mem.run_start_context(REPO) == "" and calls == []


# --- criterion 2: A01's run-start context in assignment_prompt ------------------------------------------------
def _load_swarm_run():
    spec = importlib.util.spec_from_file_location("swarm_run_s3_under_test", ROOT / "scripts" / "swarm_run.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # the envelope carries a fresh msg id and timestamp every call; pin it so prompts can be compared byte for byte
    mod.build_envelope = lambda **kw: {"fixed": True, "msg_type": kw["msg_type"]}
    mod.sign_envelope = lambda env, **kw: env
    return mod


def _task():
    return {"task_id": "T1-arch", "correlation_id": CORR, "capability": "design.arch", "title": "ADR", "budget": {"max_wall_s": 60},
            "inputs": {}, "acceptance": [], "risk_class": "low", "priority": 1, "attempt": 1, "rework_loops": 0,
            "notes_json": {"brief_excerpt": "build it"}, "depends_on": []}


def _prompt(mod, tmp_path):
    repo = tmp_path / "work"
    repo.mkdir(exist_ok=True)
    return mod.assignment_prompt(None, _task(), {"id": "A03"}, repo)


def test_assignment_prompt_is_unchanged_when_the_substrate_is_off(tmp_path, monkeypatch):
    calls = _no_urlopen(monkeypatch)
    mod = _load_swarm_run()
    prompt = _prompt(mod, tmp_path)
    assert prompt.startswith("# task.assign (signed envelope)\n```json\n") and "Substrate memory" not in prompt
    assert prompt.endswith("single ```json result block.") and "## Brief\nbuild it\n" in prompt
    assert calls == [] and not (tmp_path / ".swarm" / "substrate-tee.db").exists()


def test_assignment_prompt_is_prefixed_with_the_substrate_brief(tmp_path, monkeypatch, serve):
    mod = _load_swarm_run()
    fake = serve(brief=("# Brief\n- standing decision d1 (v3)\n", 200))
    with_ctx = _prompt(mod, tmp_path)
    monkeypatch.delenv("SUBSTRATE_URL")
    baseline = _prompt(_load_swarm_run(), tmp_path)
    assert with_ctx == "## Substrate memory\n# Brief\n- standing decision d1 (v3)\n\n" + baseline
    (brief,) = fake.at("/brief")
    assert brief["body"] == {"repo": tee_mod.repo_slug(tmp_path / "work"), "graph_id": None, "surface": "swarm-a01-orch"}


def test_substrate_context_is_computed_once_per_run(tmp_path, serve):
    mod = _load_swarm_run()
    fake = serve()
    first, second = _prompt(mod, tmp_path), _prompt(mod, tmp_path)
    assert first == second and first.startswith("## Substrate memory\n")
    assert len(fake.at("/brief")) == 1


@pytest.mark.parametrize("brief", [("boom", 500), ("", 200)])
def test_assignment_prompt_is_fail_open(tmp_path, serve, brief):
    mod = _load_swarm_run()
    serve(brief=brief)
    prompt = _prompt(mod, tmp_path)
    assert prompt.startswith("# task.assign (signed envelope)") and "Substrate memory" not in prompt


def test_assignment_prompt_survives_a_dead_substrate(tmp_path, on, monkeypatch):
    calls = _no_urlopen(monkeypatch)
    mod = _load_swarm_run()
    assert _prompt(mod, tmp_path).startswith("# task.assign (signed envelope)") and calls


# --- criterion 7: no second memory store, proven on the swarm side ---------------------------------------------
FORBIDDEN_STORES = {"chromadb", "faiss", "lancedb", "qdrant_client", "pgvector", "sqlite_vss", "redis", "shelve", "dbm"}
MEMORY_DOORS = {"memory_write", "memory_query"}
MEMORY_MODULE = "swarm/memory.py"
# memory.py may import only these: pure-compute stdlib plus the two sibling modules. No sqlite3, pathlib, os, urllib or socket.
MEMORY_STDLIB = {"__future__", "hashlib", "json", "time", "dataclasses", "typing"}
MEMORY_SIBLINGS = {("substrate_client", None), (None, "errors")}
MEMORY_CLIENT_CALLS = {"rest_post", "mcp_call_json", "enabled"}


def _py_sources():
    out = {}
    for d in ("swarm", "scripts", "hooks"):
        for p in sorted((ROOT / d).glob("*.py")):
            out[f"{d}/{p.name}"] = p.read_text(encoding="utf-8")
    return out


def _roots_imported(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__"):
                roots.add(node.args[0].value.split(".")[0])
    return roots


def memory_door_definers(sources: dict[str, str]) -> dict[str, set[str]]:
    """file -> which of `memory_write` / `memory_query` it defines (def, class or assignment)."""
    out: dict[str, set[str]] = {}
    for path, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            names = set()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            if names & MEMORY_DOORS:
                out.setdefault(path, set()).update(names & MEMORY_DOORS)
    return out


def forbidden_store_imports(sources: dict[str, str]) -> dict[str, set[str]]:
    out = {}
    for path, src in sources.items():
        hit = _roots_imported(ast.parse(src)) & FORBIDDEN_STORES
        if hit:
            out[path] = hit
    return out


def memory_module_violations(src: str) -> list[str]:
    """Ways swarm/memory.py could reach storage other than through `swarm.substrate_client`."""
    tree, bad = ast.parse(src), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [f"import {a.name}" for a in node.names if a.name.split(".")[0] not in MEMORY_STDLIB]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if (node.module or "").split(".")[0] not in MEMORY_STDLIB:
                    bad.append(f"from {node.module} import ...")
            elif node.level != 1 or any((a.name if node.module is None else None, node.module) not in MEMORY_SIBLINGS for a in node.names):
                bad.append(f"from {'.' * node.level}{node.module or ''} import {', '.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in ("open", "exec", "eval", "__import__", "compile"):
                bad.append(f"call {fn.id}()")
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "substrate_client":
                if fn.attr not in MEMORY_CLIENT_CALLS:
                    bad.append(f"substrate_client.{fn.attr}")
    return bad


def test_only_swarm_memory_defines_the_memory_doors():
    assert memory_door_definers(_py_sources()) == {MEMORY_MODULE: {"memory_write", "memory_query"}}


def test_no_module_imports_a_vector_store_or_local_kv():
    assert forbidden_store_imports(_py_sources()) == {}
    for ts in sorted((ROOT / "scripts" / "ts").glob("*.ts")):  # the TypeScript twins are held to the same rule
        found = re.findall(r"""(?:from|require\()\s*["']([^"']+)["']""", ts.read_text(encoding="utf-8"))
        assert not {m.split("/")[0] for m in found} & FORBIDDEN_STORES, ts.name


def test_memory_module_reaches_storage_only_through_substrate_client():
    src = _py_sources()[MEMORY_MODULE]
    assert memory_module_violations(src) == []
    tree = ast.parse(src)
    assert any(isinstance(n, ast.ImportFrom) and n.level == 1 and [a.name for a in n.names] == ["substrate_client"] for n in ast.walk(tree))
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "substrate_client"}
    assert {"rest_post", "mcp_call_json"} <= used


def test_the_scan_would_fail_if_a_second_memory_store_appeared():
    real = _py_sources()
    second_door = {**real, "swarm/vector_memory.py": "def memory_query(q):\n    return []\n"}
    assert set(memory_door_definers(second_door)) == {MEMORY_MODULE, "swarm/vector_memory.py"}
    assert set(memory_door_definers({**real, "scripts/x.py": "memory_write = lambda *a: None\n"})) == {MEMORY_MODULE, "scripts/x.py"}
    for src in ("import chromadb\n", "from faiss import IndexFlatL2\n", "import redis.asyncio\n", "import shelve\n", "import dbm.gnu\n",
                "import importlib\nimportlib.import_module('qdrant_client')\n", "__import__('lancedb')\n"):
        assert forbidden_store_imports({"swarm/y.py": src}), src
    base = _py_sources()[MEMORY_MODULE]
    for sneaky in ("import sqlite3\n", "import shelve\n", "import urllib.request\n", "from pathlib import Path\n", "from . import taskstore\n",
                   "from .substrate_tee import repo_slug\n", "import os\n", "open('mem.db', 'w')\n", "substrate_client._request('u', {}, {}, 'a')\n"):
        assert memory_module_violations(base + "\n" + sneaky), sneaky
