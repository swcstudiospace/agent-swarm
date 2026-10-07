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
    """Stands in for substrate_client._open: REST /memory (scripted replies) and /brief, stateless MCP /mcp.

    `memory` is a list of `(http_status, body)`; the last reply repeats once the list is used up (an exhausted script must not
    raise: substrate_client swallows every exception, which would turn a test bug into a silent "unreachable").

    `memory_search` answers `entries` verbatim whatever the query (for tests where search semantics do not matter), or, when
    `corpus` is given, searches it the way the real server does (see `_search`).
    """

    def __init__(self, memory=(), entries=None, corpus=None, brief=("# Brief\n- decision d1\n", 200)):
        self.memory = list(memory)
        self.entries = entries          # what memory_search answers; None = the MCP call fails (HTTP 500)
        self.corpus = corpus            # entry dicts, oldest first; when set, memory_search really searches them
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
            out = self._search(body["params"]["arguments"]) if self.corpus is not None else self.entries
            if out is None:
                return self._answer(req, 500, "boom")
        else:  # graph_bind forward lookup (assignment_prompt)
            out = {"status": "unbound", "correlation_id": body["params"]["arguments"].get("correlation_id"), "graph_id": None}
        rpc = {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(out)}]}}
        return _Resp(json.dumps(rpc))

    def _search(self, args):
        """The substrate's `memory_search`: superseded entries excluded, the query matched against the entry BODY only (`text`
        on the wire; every query word must appear, case-insensitive; an empty query matches everything), newest first, capped
        at the call's limit. Never against the subject: that is what makes an older decision hard to find."""
        words = set(re.findall(r"\w+", args.get("query", "").lower()))
        hits = [e for e in self.corpus
                if not e.get("superseded_by") and e.get("scope") == args.get("scope", e.get("scope"))
                and words <= set(re.findall(r"\w+", str(e.get("text", "")).lower()))]
        return hits[::-1][:args["limit"]]

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


OLDER_SERVER = object()  # `version_conflict(current_version=...)` default: the reply carries no current_version at all


def version_conflict(reason="version.stale", conflict_with="mem_old", current_version=OLDER_SERVER):
    """A version conflict. A current server reports `current_version` (the version in force, or None when none is); by default
    the key is absent, as from an older server, so the client falls back to re-reading it."""
    extra = {"conflict_with": conflict_with} if conflict_with else {}
    if current_version is not OLDER_SERVER:
        extra["current_version"] = current_version
    return (409, result("conflict", reason, verified=False, **extra))


def decision(version, subject="db choice", entry_id="mem_old", text="use postgres 16", **kw):
    """A decision entry as memory_search returns it; its body (`text`) does not name its subject unless a test says so."""
    return {"id": entry_id, "kind": "decision", "subject": subject, "version": version, "scope": f"repo:{REPO}", "text": text, **kw}


def _newer(n):
    """`n` facts in the decision's scope written after it (a corpus is oldest first), none naming "db" or "choice"."""
    return [{"id": f"mem_new_{i}", "kind": "fact", "scope": f"repo:{REPO}", "text": f"retro note {i}: cache warmed"} for i in range(n)]


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
        monkeypatch.setattr(substrate_client, "_open", fake)
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


def _no_open(monkeypatch):
    calls = []
    monkeypatch.setattr(substrate_client, "_open", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("socket")))
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
def _key_of(*fields):
    return hashlib.sha256(json.dumps(list(fields), separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def test_idempotency_key_is_sha256_of_the_json_encoded_fields():
    key = mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text", subject="  DB Choice ")
    assert key == _key_of("c1", "T1", 2, "decision", "repo:acme/widgets", "db choice", "text") and re.fullmatch(r"[0-9a-f]{64}", key)
    assert key == mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text", subject="db choice")  # normalised
    assert mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "tëxt") == _key_of("c1", "T1", 2, "decision",
                                                                                                 "repo:acme/widgets", "", "tëxt")


@pytest.mark.parametrize("other", [("c2", "T1", 2, "decision", "repo:acme/widgets", "text"), ("c1", "T2", 2, "decision", "repo:acme/widgets", "text"),
                                   ("c1", "T1", 3, "decision", "repo:acme/widgets", "text"), ("c1", "T1", 2, "fact", "repo:acme/widgets", "text"),
                                   ("c1", "T1", 2, "decision", "repo:acme/other", "text"), ("c1", "T1", 2, "decision", "repo:acme/widgets", "text2")])
def test_idempotency_key_differs_per_field(other):
    assert mem.idempotency_key(*other) != mem.idempotency_key("c1", "T1", 2, "decision", "repo:acme/widgets", "text")


def test_idempotency_key_differs_per_subject():
    base = ("c1", "T1", 1, "decision", "repo:acme/widgets", "use postgres")
    keys = {mem.idempotency_key(*base), mem.idempotency_key(*base, subject="db choice"), mem.idempotency_key(*base, subject="cache")}
    assert len(keys) == 3
    assert mem.idempotency_key(*base, subject="  ") == mem.idempotency_key(*base)  # a blank subject is no subject, as on the wire


@pytest.mark.parametrize("left,right", [(("a|b", "c"), ("a", "b|c")), (("c1|T1", "1"), ("c1", "T1|1"))])
def test_idempotency_key_has_no_separator_ambiguity(left, right):
    assert mem.idempotency_key(*left, 1, "fact", "global", "t") != mem.idempotency_key(*right, 1, "fact", "global", "t")
    assert mem.idempotency_key("c", "T", 1, "fact", "global", "x|y", subject="s") != mem.idempotency_key("c", "T", 1, "fact", "global",
                                                                                                        "y", subject="s|x")


def test_same_write_same_key_different_attempt_content_scope_or_kind_new_key(serve, write):
    fake = serve(memory=[accepted()])
    a, b = write(text="x"), write(text="x")
    assert a.idempotency_key == b.idempotency_key == mem.idempotency_key(CORR, "T1-arch", 1, "decision", f"repo:{REPO}", "x")
    assert write(text="x", attempt=2).idempotency_key != a.idempotency_key
    assert write(text="y").idempotency_key != a.idempotency_key
    assert write(text="x", scope="graph", graph_id=GID).idempotency_key != a.idempotency_key
    assert write(kind="retro", text="x").idempotency_key != a.idempotency_key
    assert len(fake.writes) == 6


def test_two_decisions_in_one_attempt_with_different_subjects_get_different_keys(serve, write):
    fake = serve(memory=[accepted()])
    a, b = write(text="same text", subject="db choice"), write(text="same text", subject="cache choice")
    assert a.idempotency_key != b.idempotency_key and fake.writes[0]["idempotency_key"] != fake.writes[1]["idempotency_key"]
    retry = write(text="same text", subject="db choice", expected_version=3)  # a resubmit that adds expected_version: same write
    assert retry.idempotency_key == a.idempotency_key and fake.writes[2]["expected_version"] == 3


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


# The substrate's versioned conflict reasons, listed here rather than read from mem.VERSION_REASONS, so dropping one from the
# client fails a test instead of silently dropping its test cases.
RETRIED_REASONS = ("standing.raced", "version.raced", "version.required", "version.stale")


def test_every_versioned_conflict_reason_is_retried():
    assert mem.VERSION_REASONS == frozenset(RETRIED_REASONS)


@pytest.mark.parametrize("reason", RETRIED_REASONS)
def test_reported_current_version_is_resubmitted_without_a_reread(serve, write, reason):
    fake = serve(memory=[version_conflict(reason, current_version=5), accepted(version=6)])
    out = write(subject="db choice")
    assert out.ok and out.version == 6 and out.attempts == 2
    first, second = fake.writes
    assert "expected_version" not in first and second["expected_version"] == 5
    assert first["idempotency_key"] == second["idempotency_key"]  # one write, one key, however many re-submits
    assert fake.at("/mcp", "memory_search") == []


def test_reported_null_current_version_resubmits_without_expected_version(serve, write):
    fake = serve(memory=[version_conflict(current_version=None), accepted()])
    assert write(subject="db choice", expected_version=2).ok
    assert "expected_version" not in fake.writes[1] and [w.get("expected_version") for w in fake.writes] == [2, None]
    assert fake.at("/mcp", "memory_search") == []


def test_reported_current_version_still_gives_up_after_two_resubmits(serve, write):
    fake = serve(memory=[version_conflict(current_version=5)])
    out = write(subject="db choice")
    assert out.status == mem.CONFLICT and not out.ok and out.attempts == 1 + mem.MAX_VERSION_RESUBMITS
    assert [w.get("expected_version") for w in fake.writes] == [None, 5, 5] and len({w["idempotency_key"] for w in fake.writes}) == 1
    assert fake.at("/mcp", "memory_search") == []


@pytest.mark.parametrize("reported", ["5", True, 5.0, {"version": 5}])
def test_a_non_int_current_version_falls_back_to_the_reread(serve, write, reported):
    fake = serve(memory=[version_conflict(current_version=reported), accepted(version=4)],
                 corpus=[decision(3, text="db choice: use postgres 16")])
    assert write(subject="db choice").ok
    assert [w.get("expected_version") for w in fake.writes] == [None, 3]
    assert len(fake.at("/mcp", "memory_search")) == 1


# Older servers do not report current_version: the client re-reads it with memory_search (body-text match, newest 100 per scope).
@pytest.mark.parametrize("reason", RETRIED_REASONS)
def test_older_server_version_conflict_rereads_and_resubmits_with_expected_version(serve, write, reason):
    other = decision(9, subject="cache choice", entry_id="mem_other", text="cache choice: redis, not the db choice")
    fake = serve(memory=[version_conflict(reason), accepted(version=4)],
                 corpus=[decision(3, text="db choice: use postgres 16"), other])
    out = write(subject="db choice")
    assert out.ok and out.version == 4 and out.attempts == 2
    first, second = fake.writes
    assert "expected_version" not in first and second["expected_version"] == 3  # matched by id / subject, not the other hit
    assert first["idempotency_key"] == second["idempotency_key"]
    (search,) = fake.at("/mcp", "memory_search")  # the targeted read (subject as query) found it: no scope-wide read
    assert search["body"]["params"]["arguments"] == {"query": "db choice", "limit": 100, "scope": f"repo:{REPO}"}


def test_older_server_finds_a_decision_whose_body_names_its_subject_beyond_the_newest_100(serve, write):
    fake = serve(memory=[version_conflict(), accepted(version=6)],
                 corpus=[decision(5, text="db choice: use postgres 16")] + _newer(120))
    out = write(subject="db choice")
    assert out.ok and out.version == 6
    assert [w.get("expected_version") for w in fake.writes] == [None, 5]
    assert [c["body"]["params"]["arguments"]["query"] for c in fake.at("/mcp", "memory_search")] == ["db choice"]


def test_older_server_targeted_read_miss_falls_back_to_the_scope_wide_read(serve, write):
    fake = serve(memory=[version_conflict(), accepted(version=4)], corpus=[decision(3)] + _newer(5))  # body lacks "db choice"
    assert write(subject="db choice").ok
    assert [w.get("expected_version") for w in fake.writes] == [None, 3]
    assert [c["body"]["params"]["arguments"]["query"] for c in fake.at("/mcp", "memory_search")] == ["db choice", ""]


def test_older_server_cannot_find_a_decision_whose_body_lacks_its_subject_beyond_the_newest_100(serve, write):
    """The live repro: subject "db choice", body "use postgres 16", 120 newer entries in its scope. Neither read can find it,
    so the write stops as a conflict rather than re-submitting, least of all without expected_version."""
    fake = serve(memory=[version_conflict("version.required"), accepted()], corpus=[decision(1)] + _newer(120))
    out = write(subject="db choice", text="use postgres 17")
    assert out.status == mem.CONFLICT and not out.ok and out.reason == "version.required" and out.attempts == 1
    assert len(fake.writes) == 1
    assert [c["body"]["params"]["arguments"]["query"] for c in fake.at("/mcp", "memory_search")] == ["db choice", ""]


def test_stale_expected_version_is_replaced_by_the_current_one(serve, write):
    fake = serve(memory=[version_conflict(), accepted(version=8)], entries=[decision(7)])
    assert write(subject="db choice", expected_version=2).ok
    assert [w.get("expected_version") for w in fake.writes] == [2, 7]


def test_stale_with_nothing_in_force_resubmits_without_expected_version(serve, write):
    fake = serve(memory=[version_conflict(conflict_with=None), accepted()], entries=[])  # the server named no standing entry
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
    calls = _no_open(monkeypatch)
    out = write()
    assert out.status == mem.UNAVAILABLE and out.reason == "substrate.disabled" and not out.ok and calls == []


def test_lost_reply_is_unknown_and_the_same_write_retried_reuses_the_key(serve, write, monkeypatch):
    fake = serve(memory=[accepted(version=2)])

    def reply_lost(req, timeout=None):
        fake(req, timeout)  # the server got the POST and stored the entry ...
        raise TimeoutError("reply lost")  # ... but the answer never came back

    monkeypatch.setattr(substrate_client, "_open", reply_lost)
    first = write(subject="db choice")
    assert first.status == mem.UNAVAILABLE and first.reason == "substrate.unreachable" and not first.ok  # unknown, not "not written"
    substrate_client.reset()  # the outage back-off has passed
    monkeypatch.setattr(substrate_client, "_open", fake)
    second = write(subject="db choice")  # the SAME logical write: same correlation, task, attempt, kind, scope, subject, text
    assert second.ok
    assert len(fake.writes) == 2 and fake.writes[0]["idempotency_key"] == fake.writes[1]["idempotency_key"]
    assert first.idempotency_key == second.idempotency_key == fake.writes[0]["idempotency_key"]


def test_unreachable_substrate_is_unavailable_not_ok_and_not_retried(on, monkeypatch, write, sleeps):
    calls = _no_open(monkeypatch)
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
    calls = _no_open(monkeypatch)
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
    calls = _no_open(monkeypatch)
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
    calls = _no_open(monkeypatch)
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
    calls = _no_open(monkeypatch)
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


SCANNED_DIRS = ("swarm", "scripts", "hooks")


def _py_sources(root=ROOT):
    """repo-relative path -> source of every Python file under swarm/, scripts/ and hooks/, nested packages included."""
    out = {}
    for d in SCANNED_DIRS:
        for p in sorted((root / d).rglob("*.py")):
            if "__pycache__" not in p.relative_to(root).parts:
                out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    return out


# `import ... from "x"`, `export ... from "x"`, side-effect `import "x"`, dynamic `import("x")`, `require("x")`
TS_IMPORT = re.compile(r"""\b(?:from|import|require)\s*\(?\s*["'`]([^"'`]+)["'`]""")


def ts_store_imports(src: str) -> set[str]:
    return {m.split("/")[0] for m in TS_IMPORT.findall(src)} & FORBIDDEN_STORES


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
        assert not ts_store_imports(ts.read_text(encoding="utf-8")), ts.name


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


def test_the_scan_reaches_nested_python_modules(tmp_path):
    nested = tmp_path / "swarm" / "stores" / "vector"
    nested.mkdir(parents=True)
    (nested / "kv.py").write_text("import redis\n\ndef memory_query(q):\n    return []\n", encoding="utf-8")
    (tmp_path / "scripts" / "tools").mkdir(parents=True)
    (tmp_path / "scripts" / "tools" / "ok.py").write_text("import json\n", encoding="utf-8")
    (nested / "__pycache__").mkdir()
    (nested / "__pycache__" / "kv.cpython-312.py").write_text("import chromadb\n", encoding="utf-8")
    sources = _py_sources(tmp_path)
    assert set(sources) == {"swarm/stores/vector/kv.py", "scripts/tools/ok.py"}  # full repo-relative paths, __pycache__ skipped
    assert forbidden_store_imports(sources) == {"swarm/stores/vector/kv.py": {"redis"}}
    assert memory_door_definers(sources) == {"swarm/stores/vector/kv.py": {"memory_query"}}


@pytest.mark.parametrize("src,store", [
    ('import "redis";\n', "redis"), ("import 'shelve'\n", "shelve"), ('const db = await import("chromadb");\n', "chromadb"),
    ("const m = import ( 'faiss' )\n", "faiss"), ('export * from "lancedb";\n', "lancedb"), ('export { Q } from "qdrant_client"\n', "qdrant_client"),
    ('import { createClient } from "redis";\n', "redis"), ("import type {\n  Db,\n} from 'lancedb/arrow'\n", "lancedb"),
    ('const r = require("redis")\n', "redis"), ('import pg = require("pgvector")\n', "pgvector"),
])
def test_the_ts_scan_catches_every_import_form(src, store):
    assert ts_store_imports(src) == {store}, src


def test_the_ts_scan_does_not_flag_allowed_imports():
    allowed = ('import { createHash } from "node:crypto";\nimport "./substrate";\nexport * from "./memory";\n'
               'const m = await import("zod");\nconst important = "redis is mentioned in a string";\n')
    assert ts_store_imports(allowed) == set()
