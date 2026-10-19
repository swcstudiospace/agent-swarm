"""Audit trail (v2.2 SEC-03, AC12): one local-only `swarm.tool.call` row per registry call, and a log audit over a real
parallel run: every run and tool-call row carries its correlation id, and no planted secret survives in the run log, the
live snapshot, the results, the status and handover files or what the substrate tee would send."""
from __future__ import annotations

import importlib
import json
import time

import pytest

import test_e2e_swarm as e2e
import test_scheduler_continuous as sc

runlog = substrate_tee = tool_registry = TaskStore = None  # bound by `_current_swarm_modules` before each module's tests


@pytest.fixture(scope="module", autouse=True)
def _current_swarm_modules():
    """Bind the swarm modules of the CURRENT module graph.

    conftest's `swarm_dir` fixture evicts every swarm* module between tests, so names imported at the top of this file
    would belong to a different graph than the handlers `call_tool` imports lazily (a different `current_caller`
    ContextVar, a different `runlog` than the one the audit row is emitted through).
    """
    global runlog, substrate_tee, tool_registry, TaskStore
    runlog = importlib.import_module("swarm.runlog")
    substrate_tee = importlib.import_module("swarm.substrate_tee")
    tool_registry = importlib.import_module("swarm.tool_registry")
    TaskStore = importlib.import_module("swarm.taskstore").TaskStore

# secret-shaped test values, assembled so no scanner sees a literal token in the repository
GHP = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
SK = "sk-" + "proj9Xy8Wv7Ut6Sr5Qp4On3Ml2Kj1Ih0Gf"
PEM_BODY = "MIIEowIBAAKCAQEAexampleexampleexample0123456789"
PEM = f"-----BEGIN RSA PRIVATE {'KEY'}-----\n{PEM_BODY}\n-----END RSA PRIVATE {'KEY'}-----"
BEARER_VALUE = "abcdefghijklmnop0123456789XYZ"
BEARER = f"Authorization: Bearer {BEARER_VALUE}"
NEEDLES = (GHP, SK, PEM_BODY, BEARER_VALUE)


def rows(swarm_dir, *types):
    path = swarm_dir / "events.jsonl"
    recs = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [r for r in recs if not types or r["type"] in types]


@pytest.fixture()
def state(tmp_path, monkeypatch):
    sdir = tmp_path / ".swarm"
    sdir.mkdir()  # inside a real run `.swarm` exists; the cases without it are tested explicitly below
    monkeypatch.setenv("SWARM_DIR", str(sdir))
    monkeypatch.chdir(tmp_path)
    for name in ("SWARM_CORRELATION_ID", "SWARM_TASK_ID", "SUBSTRATE_URL"):
        monkeypatch.delenv(name, raising=False)
    return sdir


# ----------------------------------------------------------------------------------------- the registry audit event
def test_every_call_leaves_exactly_one_local_only_row_without_payload_values(state, monkeypatch):
    teed, emits = [], []
    real = runlog.emit

    def spy(*args, **kw):
        emits.append(kw)
        return real(*args, **kw)

    monkeypatch.setattr(runlog, "emit", spy)
    monkeypatch.setattr(substrate_tee, "tee", lambda *a, **k: teed.append(a))
    ok = tool_registry.call_tool("orch_registry_ping", "A01", {"token": "MARKER-4711-ping"})
    assert ok["state"] == "SUCCESS"
    (row,) = rows(state)
    assert row["type"] == "swarm.tool.call" and row["source"] == "registry"
    body = row["payload"]
    assert set(body) == {"tool", "caller", "state", "duration_ms"} and body["state"] == "SUCCESS"
    assert body["tool"] == "orch_registry_ping" and body["caller"] == "A01"
    assert isinstance(body["duration_ms"], float) and 0 <= body["duration_ms"] < 5000
    assert "MARKER-4711-ping" not in (state / "events.jsonl").read_text()
    assert emits and all(kw["tee"] is False for kw in emits), "the audit row must never be teed"
    assert teed == [], "the audit row reached the substrate tee"
    assert substrate_tee.agent_of(row) is None, "a registry row names no agent surface, so nothing could forward it"
    assert substrate_tee.kind_for("swarm.tool.call") == "tool.call"


@pytest.mark.parametrize("name,caller,payload,expected,field", [
    ("orch_registry_ping", "A01", {"token": "x"}, "SUCCESS", None),
    ("orch_registry_ping", "A01", {}, "INVALID_INPUT", "token"),
    ("orch_registry_ping", "A02", {"token": "x"}, "UNAUTHORIZED", None),
    ("not_a_registered_tool", "A01", {}, "INVALID_INPUT", "name"),
    ("orch_registry_ping", "A01", ["not", "an", "object"], "INVALID_INPUT", "payload"),
])
def test_the_row_records_the_outcome_state_and_names_a_failure(state, name, caller, payload, expected, field):
    result = tool_registry.call_tool(name, caller, payload)
    assert result["state"] == expected
    (row,) = rows(state)
    body = row["payload"]
    assert body["state"] == expected and body["caller"] == caller
    if expected == "SUCCESS":
        assert "error_state" not in body
    else:
        assert body["error_state"] == expected


def test_a_secret_shaped_name_or_caller_is_never_written(state):
    result = tool_registry.call_tool(GHP, SK, {"token": PEM})
    assert result["state"] == "INVALID_INPUT"
    text = (state / "events.jsonl").read_text()
    assert not any(n in text for n in NEEDLES)
    (row,) = rows(state)
    assert row["payload"]["tool"] == "" and row["payload"]["caller"] == ""


def test_ids_come_from_the_env_first_then_the_call_then_the_plan_then_the_latest_plan(state, monkeypatch):
    TaskStore(state / "tasks.db").create(task_id="T-plan", correlation_id="corr-plan", capability="code.backend",
                                         agent_id="A05", title="x")
    ping = {"token": "x"}
    monkeypatch.setenv("SWARM_CORRELATION_ID", "corr-env")
    monkeypatch.setenv("SWARM_TASK_ID", "T-env")
    tool_registry.call_tool("orch_registry_ping", "A01", {**ping, "task_id": "T-plan", "correlation_id": "corr-call"})
    monkeypatch.delenv("SWARM_CORRELATION_ID")
    monkeypatch.delenv("SWARM_TASK_ID")
    tool_registry.call_tool("orch_registry_ping", "A01", {**ping, "task_id": "T-call", "correlation_id": "corr-call"})
    tool_registry.call_tool("shared_open_questions", "A05", {"task_id": "T-plan"})  # a task id, plan from the Task Store
    (state / "latest_correlation").write_text("corr-latest\n")
    tool_registry.call_tool("orch_registry_ping", "A01", ping)
    got = [(r["correlation_id"], r["task_id"]) for r in rows(state, "swarm.tool.call")]
    assert got == [("corr-env", "T-env"), ("corr-call", "T-call"), ("corr-plan", "T-plan"), ("corr-latest", None)]


def test_secret_shaped_ids_are_dropped_not_logged(state):
    (state / "latest_correlation").write_text(GHP)
    tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x", "task_id": SK, "correlation_id": BEARER_VALUE + "!!"})
    (row,) = rows(state)
    assert row["task_id"] is None and row["correlation_id"] is None
    assert not any(n in (state / "events.jsonl").read_text() for n in NEEDLES)


def test_a_failing_log_never_changes_the_result_and_never_raises(state, monkeypatch, tmp_path):
    expected = tool_registry.call_tool("orch_registry_ping", "A01", {"token": "same"})

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(runlog, "emit", broken)
    assert tool_registry.call_tool("orch_registry_ping", "A01", {"token": "same"}) == expected
    monkeypatch.undo()
    (state / "events.jsonl").unlink()
    (state / "events.jsonl").mkdir()  # the log file can not be opened for append: IsADirectoryError
    assert tool_registry.call_tool("orch_registry_ping", "A01", {"token": "same"}) == expected
    assert tool_registry.call_tool("orch_registry_ping", "A02", {"token": "same"})["state"] == "UNAUTHORIZED"


def test_a_read_call_without_a_state_dir_writes_nothing_and_changes_no_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    for name in ("SWARM_CORRELATION_ID", "SWARM_TASK_ID"):
        monkeypatch.delenv(name, raising=False)
    first = tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x"})
    assert first["state"] == "SUCCESS"
    assert tool_registry.call_tool("orch_registry_ping", "A02", {"token": "x"})["state"] == "UNAUTHORIZED"
    assert tool_registry.call_tool("no_such_tool", "A01", {})["state"] == "INVALID_INPUT"
    assert not (tmp_path / ".swarm").exists(), "a READ call created the swarm state dir for its audit row"
    (tmp_path / ".swarm").mkdir()
    assert tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x"}) == first
    assert [r["payload"]["state"] for r in rows(tmp_path / ".swarm")] == ["SUCCESS"]  # from here on it is audited


def test_a_write_call_without_a_state_dir_is_audited(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sdir = tmp_path / ".swarm"
    monkeypatch.setenv("SWARM_DIR", str(sdir))
    for name in ("SWARM_CORRELATION_ID", "SWARM_TASK_ID"):
        monkeypatch.delenv(name, raising=False)
    tool = tool_registry.find_tool(tool_registry.load_document(), "shared_scratchpad_put")
    assert tool["side_effect_tier"] == "WRITE"
    result = tool_registry.call_tool("shared_scratchpad_put", "A01", tool["examples"]["success"])
    assert result["state"] == "SUCCESS"
    (row,) = rows(sdir, "swarm.tool.call")
    assert row["payload"]["tool"] == "shared_scratchpad_put" and row["payload"]["state"] == "SUCCESS"
    assert row["correlation_id"] == "example-corr"  # the call's own correlation id


def test_a_timeout_and_a_dependency_failure_are_audited_with_their_state(state):
    tool = tool_registry.find_tool(tool_registry.load_document(), "orch_registry_ping")
    probes = tool["examples"]
    timed = tool_registry.call_tool("orch_registry_ping", "A01", probes["timeout"], timeout_s=0.05)
    dep = tool_registry.call_tool("orch_registry_ping", "A01", probes["dependency"])
    assert (timed["state"], dep["state"]) == ("TIMEOUT", "DEPENDENCY_UNAVAILABLE")
    assert [r["payload"]["state"] for r in rows(state)] == ["TIMEOUT", "DEPENDENCY_UNAVAILABLE"]


def test_an_oversized_failure_is_audited_with_its_error_state(state, monkeypatch):
    """RV-G P2-2: an oversized INVALID_INPUT keeps its state, so the audit row records it as the error_state."""
    monkeypatch.setenv("SWARM_TOOL_OUTPUT_MAX_BYTES", "4096")
    monkeypatch.setattr("swarm.tools.orch_registry_ping.call",
                        lambda _args: {"state": "INVALID_INPUT", "field": "token", "message": "m" * 10_000})
    result = tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x"})
    assert result["state"] == "INVALID_INPUT" and result["output_withheld"] is True
    (row,) = rows(state)
    assert row["payload"]["state"] == "INVALID_INPUT" and row["payload"]["error_state"] == "INVALID_INPUT"


def test_the_audit_row_costs_next_to_nothing(state):
    data = tool_registry.load_document()
    n = 200
    started = time.perf_counter()
    for _ in range(n):
        tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x"}, data=data)
    with_audit = (time.perf_counter() - started) / n
    real, tool_registry._audit_call = tool_registry._audit_call, lambda *a, **k: None
    try:
        started = time.perf_counter()
        for _ in range(n):
            tool_registry.call_tool("orch_registry_ping", "A01", {"token": "x"}, data=data)
        without = (time.perf_counter() - started) / n
    finally:
        tool_registry._audit_call = real
    assert with_audit - without < 0.005, (with_audit, without)  # measured about 0.15 ms: a loose, load-proof bound
    assert len(rows(state)) == n


# ----------------------------------------------------------------------------------------------------- the log audit
def planted_tasks():
    return [
        {**sc._task("p1", "A05", "code.backend"), "title": f"deploy with {GHP} and {SK}"},
        {**sc._task("p2", "A08", "test.unit", ["p1"]), "title": f"verify {BEARER}"},
        sc._task("p3", "A06", "code.frontend"),
        sc._task("p4", "A14", "maint.patch"),   # fails, its stderr carries every secret
        sc._task("p5", "A15", "docs.api"),
    ]


@pytest.fixture(scope="module")
def audited(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("audit-run")
    monkeypatch = pytest.MonkeyPatch()
    env = {"PLANTED_TOKEN": GHP, "PLANTED_API_KEY": SK, "PLANTED_PEM": PEM, "PLANTED_AUTH": BEARER,
           "SWARM_HEARTBEAT_S": "0.2", "echo_env": True}
    names = ["PLANTED_TOKEN", "PLANTED_API_KEY", "PLANTED_PEM", "PLANTED_AUTH"]
    everything = f"{GHP} {SK} {PEM} {BEARER}"
    cfg = {"*": {"sleep": 0.2, "echo_env": names, "summary": f"done; leaked {everything}",
                 "tools": [{"name": "bash", "sleep": 0.5,
                           "args": {"command": f"curl -H '{BEARER}' -u x:{SK} https://example.invalid"}}]},
           "T-p4": {"fail": True, "stderr": everything}}
    run = e2e.Run(tmp, planted_tasks(), cfg, monkeypatch, **env)
    rig = run.rig
    try:
        rig.store().update("T-p4", max_attempts=1)
        monkeypatch.setenv("SWARM_DIR", str(rig.swarm))
        for name in ("SWARM_CORRELATION_ID", "SWARM_TASK_ID"):
            monkeypatch.delenv(name, raising=False)
        # a handover goal and a status note that carry secrets, written through the registry as their real callers
        written = tool_registry.call_tool("shared_handover_write", "A05", {
            "task_id": "T-p1", "to_agent": "A08", "goal": f"ship it; token {GHP}; key {SK}; {PEM}; {BEARER}",
            "remaining": [f"rotate {SK}"], "constraints": [f"never log {BEARER}"], "open_questions": [],
            "verification": {"status": "unverified", "confidence": 0.1, "checks": []}})
        assert written["state"] == "SUCCESS", written
        status = tool_registry.call_tool("shared_status_report", "A05", {
            "task_id": "T-p1", "kind": "progress", "step": f"using {GHP}", "percent": 5,
            "note": f"{PEM} and {BEARER}"})
        assert status["state"] == "SUCCESS", status
        run.start("--max-parallel", "3", "--task-timeout", "30")
        run.poll_until_exit()
        for task_id, caller in (("T-p1", "A05"), ("T-p2", "A08")):  # tool calls after the run, in the same state dir
            tool_registry.call_tool("shared_attempt_history", caller, {"task_id": task_id})
            tool_registry.call_tool("shared_open_questions", caller, {"task_id": task_id})
        tool_registry.call_tool("shared_cancel_check", "A05", {"task_id": "T-p1"})
        yield run
    finally:
        monkeypatch.undo()
        if hasattr(run, "proc"):
            run.kill()


def test_the_run_finished_and_the_failing_task_did_not_stop_the_rest(audited):
    states = audited.rig.states()
    assert states["T-p4"] == "ESCALATED" and all(s == "DONE" for t, s in states.items() if t != "T-p4"), states


def test_every_run_event_carries_the_correlation_id_and_its_task_id(audited):
    corr = audited.rig.corr
    run_rows = [r for r in rows(audited.rig.swarm) if r["type"].startswith("swarm.run.")]
    assert len(run_rows) > 20
    assert {r["type"] for r in run_rows} >= {"swarm.run.queued", "swarm.run.started", "swarm.run.state",
                                             "swarm.run.heartbeat", "swarm.run.finished", "swarm.run.handover"}
    assert [r for r in run_rows if r["correlation_id"] != corr] == []
    assert [r for r in run_rows if not r["task_id"]] == []
    for r in run_rows:
        assert r["payload"]["run_id"].startswith(r["task_id"] + ".a"), r
    for r in rows(audited.rig.swarm, "swarm.scheduler.tick", "swarm.degraded"):
        assert r["correlation_id"] == corr


def test_every_tool_call_row_carries_the_correlation_id_and_its_task_id(audited):
    calls = rows(audited.rig.swarm, "swarm.tool.call")
    assert {c["payload"]["tool"] for c in calls} == {
        "shared_handover_write", "shared_status_report", "shared_attempt_history", "shared_open_questions",
        "shared_cancel_check"}
    assert [c for c in calls if c["correlation_id"] != audited.rig.corr] == []
    assert [c for c in calls if not c["task_id"]] == []
    assert all(c["source"] == "registry" and set(c["payload"]) >= {"tool", "caller", "state", "duration_ms"} for c in calls)


# the operator's own input (task titles) lives in the plan, the Task Store and the assignment prompt built from them
OPERATOR_INPUT = ("tasks.db", "plans/", "assignments/")


def leaking_files(swarm, *, only=None, skip=OPERATOR_INPUT):
    found: dict[str, list[str]] = {}
    for path in sorted(p for p in swarm.rglob("*") if p.is_file()):
        rel = path.relative_to(swarm).as_posix()
        if rel.startswith(skip) or (only is not None and not rel.startswith(only)):
            continue
        text = path.read_text(errors="replace")
        hit = [n[:8] + "..." for n in NEEDLES if n in text]
        if hit:
            found[rel] = hit
    return found


def test_no_planted_secret_survives_in_the_run_log_the_snapshot_status_or_handovers(audited):
    swarm = audited.rig.swarm
    assert leaking_files(swarm, skip=(*OPERATOR_INPUT, "results/")) == {}
    for needed in ("events.jsonl", "live.json"):
        assert (swarm / needed).stat().st_size > 0
    assert any((swarm / "handovers").rglob("*.json")) and any((swarm / "status").glob("*.json"))
    assert len(list((swarm / "results").glob("*.md"))) == 5  # the results are scanned by the next test


def test_no_planted_secret_survives_in_the_results_directory(audited):
    assert leaking_files(audited.rig.swarm, only="results/") == {}


def test_redacted_evidence_still_parses_as_a_task_result(audited):
    """The stored `.md` of a real session keeps its fenced JSON block parseable after redaction, and the secret-bearing
    summary is the only thing that changed."""
    from swarm.results import parse_result, validate_result

    for tid in ("T-p1", "T-p2", "T-p3", "T-p5"):
        text = (audited.rig.swarm / "results" / f"{tid}.a1.md").read_text()
        result = validate_result(parse_result(text), task_id=tid)
        assert result["state"] == "IN_REVIEW" and result["outputs"][0]["kind"] == "code.backend"
        assert result["summary_md"].startswith("done; leaked ") and "[REDACTED]" in result["summary_md"]


def test_write_evidence_redacts_markdown_but_leaves_the_findings_file_as_given(tmp_path):
    from swarm.results import parse_result

    mod = sc._runner()
    result = {"task_id": "T-x", "state": "IN_REVIEW", "outputs": [], "metrics": {}, "summary_md": f"key {SK}\n{PEM}"}
    md = mod.write_evidence(tmp_path, "T-x", 1, "done\n```json\n" + json.dumps(result) + "\n```")
    stored = md.read_text()
    assert not any(n in stored for n in NEEDLES)
    parsed = parse_result(stored)
    assert parsed["task_id"] == "T-x" and parsed["state"] == "IN_REVIEW" and "[REDACTED]" in parsed["summary_md"]
    findings = json.dumps({"T-x": [{"id": "F1", "severity": "major", "kind": "bug", "summary": f"leaks {GHP}"}]}, indent=2)
    machine = mod.write_evidence(tmp_path, "T-x", 1, findings, ".findings.json")
    assert machine.name.endswith(".findings.json") and machine.read_text() == findings  # a gate script's input, as given


@pytest.mark.parametrize("summary, expected", [
    ('Set DB_PASSWORD="hunter2-very-secret" in .env', "Set DB_PASSWORD=[REDACTED] in .env"),
    ('run export API_KEY="hunter2-very-secret" first', "run export API_KEY=[REDACTED] first"),
    ('pass --password "hunter2-very-secret" to it', "pass --password [REDACTED] to it"),
    ("Set DB_PASSWORD=hunter2-very-secret in .env", "Set DB_PASSWORD=[REDACTED] in .env"),
    ("pass --password hunter2-very-secret to it", "pass --password [REDACTED] to it"),
], ids=["env_assign_quoted", "export_quoted", "flag_quoted", "env_assign_plain", "flag_plain"])
def test_a_quoted_secret_in_a_saved_summary_is_redacted_and_the_block_still_parses(tmp_path, summary, expected):
    """A quoted value is JSON-escaped (\\"...\\") inside the saved block: the secret goes and the block stays parseable."""
    from swarm.results import parse_result

    mod = sc._runner()
    result = {"task_id": "T-q", "state": "IN_REVIEW", "outputs": [], "metrics": {}, "summary_md": summary}
    md = mod.write_evidence(tmp_path, "T-q", 1, "done\n```json\n" + json.dumps(result) + "\n```")
    stored = md.read_text()
    assert "hunter2" not in stored
    parsed = parse_result(stored)
    assert parsed is not None and parsed["summary_md"] == expected


def test_the_planted_secrets_did_reach_the_session_so_the_scan_is_meaningful(audited):
    """The fake session streamed them (echoed env, tool args, failure stderr, result summary): the runner saw them and
    redacted them. Evidence: the heartbeat and snapshot activity text of a session shows the redaction marker where the
    tool call carried a secret, the failure reason is recorded without it, and the stored handover is redacted."""
    swarm = audited.rig.swarm
    beats = [r["payload"]["activity"] for r in rows(swarm, "swarm.run.heartbeat") if r["payload"].get("activity")]
    seen = [a for a in beats if "bash" in a]
    assert seen and any("[REDACTED]" in a for a in seen), beats[:5]
    snap = [r["activity"] for s in audited.snapshots for r in s["runs"] if r["activity"] and "bash" in r["activity"]]
    assert snap and all(not any(n in a for n in NEEDLES) for a in snap)
    reasons = audited.rig.reasons("T-p4")
    assert reasons and not any(n in reasons[0] for n in NEEDLES)
    handover = next((swarm / "handovers").rglob("T-p1.1.json"))
    assert "[REDACTED]" in handover.read_text() and not any(n in handover.read_text() for n in NEEDLES)


def test_what_the_substrate_tee_would_send_names_no_secret_for_any_row(audited):
    swarm = audited.rig.swarm
    built = 0
    for record in rows(swarm):
        body = substrate_tee.build_event(record, "ut-aaaaaaaa-00000000", env={})
        if body is None:
            continue
        built += 1
        assert not any(n in json.dumps(body) for n in NEEDLES)
    assert built > 0, "no row was attributable: the tee path was not exercised"
    # registry rows and runtime telemetry are local only: they have no agent surface to be forwarded under
    for record in rows(swarm, "swarm.tool.call", "swarm.run.heartbeat", "swarm.run.activity"):
        assert substrate_tee.build_event(record, "ut-aaaaaaaa-00000000", env={}) is None
