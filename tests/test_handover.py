"""swarm.handover: versioned handover documents (DESIGN 4.7). Real files in a tmp SWARM_DIR, FakeClock for time.

Pins: A->B continuation from the document alone, validation by field on write AND read, digest tamper detection, the
size cap with mandatory sections intact, the five statuses, race-safe seq across threads and processes, the
`swarm.run.handover` event, idempotent receipts."""
import importlib
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh imports per test (other suites purge sys.modules) plus an isolated SWARM_DIR."""
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    for name in ("SWARM_HANDOVER_MAX_BYTES", "SWARM_HANDOVER_STALE_S"):
        monkeypatch.delenv(name, raising=False)

    class E:
        ho = importlib.import_module("swarm.handover")
        clock_mod = importlib.import_module("swarm.clock")
        schema = importlib.import_module("swarm.schema")
        runlog = importlib.import_module("swarm.runlog")
        taskstore = importlib.import_module("swarm.taskstore")
        telemetry = importlib.import_module("swarm.telemetry")
        root = tmp_path
        sdir = tmp_path / ".swarm"
        clock = importlib.import_module("swarm.clock").FakeClock()

        @classmethod
        def write(cls, doc=None, **kw):
            kw.setdefault("emit", False)
            return cls.ho.write_handover(doc if doc is not None else base(), root=tmp_path, clock=cls.clock, **kw)

        @classmethod
        def status(cls, task_id="T1-be", **kw):
            return cls.ho.handover_status(task_id, root=tmp_path, clock=cls.clock, **kw)

        @classmethod
        def file(cls, task="T1-be", seq=1, corr="corr-1"):
            return cls.sdir / "handovers" / corr / f"{task}.{seq}.json"

    return E


def art(i=0, producer="A05"):
    return {"kind": "file", "uri": f"src/mod{i}.py", "digest": "sha256:" + f"{i:x}".rjust(64, "a"), "producer": producer}


def base(**over):
    doc = {
        "correlation_id": "corr-1", "task_id": "T1-be", "from_agent": "A05", "to_agent": "A08",
        "goal": "Implement the CSV export endpoint", "completed": ["Added the /export route"],
        "remaining": ["Write the unit tests", "Document the endpoint"],
        "decisions": [{"decision": "Stream rows", "rationale": "Exports can be large"}],
        "artifacts": [art(1)], "open_questions": ["Is 50k rows the maximum?"], "blockers": [],
        "constraints": ["Standard library only", "Do not change the public API"],
        "verification": {"status": "partial", "confidence": 0.6, "checks": ["unit: 3 passed"]},
    }
    doc.update(over)
    return doc


# ---- A -> B round trip -----------------------------------------------------------------------------------------
def test_receiver_continues_from_the_document_alone(env):
    written = env.write()
    assert written["handover_id"] == "T1-be.1" and written["seq"] == 1 and written["duplicate"] is False
    path = env.file()
    assert path.read_bytes() == env.ho.canonical(written["document"])  # canonical, one file
    assert len(path.read_bytes()) == written["bytes"]  # `bytes` is the real size of the stored file

    seen_by_b = env.ho.latest_handover("T1-be", root=env.root, to_agent="A08")
    assert env.ho.latest_handover("T1-be", root=env.root, to_agent="A02") is None  # not addressed to A02
    # everything B needs to continue is in the document
    assert seen_by_b["goal"] == "Implement the CSV export endpoint"
    assert seen_by_b["remaining"] == ["Write the unit tests", "Document the endpoint"]
    assert seen_by_b["constraints"] == ["Standard library only", "Do not change the public API"]
    assert seen_by_b["artifacts"] == [art(1)]
    assert seen_by_b["from_agent"] == "A05" and seen_by_b["to_agent"] == "A08"
    assert seen_by_b["digest"] == env.ho.compute_digest(seen_by_b) == written["digest"]
    assert env.status()["status"] == "ok"
    assert env.ho.read_handover("T1-be.1", root=env.root) == seen_by_b


def test_digest_is_over_canonical_json_without_the_digest_field(env):
    doc = env.write()["document"]
    reordered = dict(reversed(list(doc.items())))
    assert env.ho.compute_digest(reordered) == doc["digest"]
    assert env.ho.compute_digest({**doc, "digest": "sha256:" + "0" * 64}) == doc["digest"]
    assert env.ho.compute_digest({**doc, "goal": "changed"}) != doc["digest"]


@pytest.mark.parametrize("n", [1, 2, 7, 64, 333, 4000, 9000])
def test_bytes_is_the_exact_stored_size_at_every_size(env, n):
    """`bytes` counts its own digits: the fixed point must hold across digit boundaries."""
    written = env.write(base(completed=["x" * n]))
    assert written["bytes"] == len(env.file().read_bytes())


# ---- validation on write AND read -----------------------------------------------------------------------------
def _bad_goal(d): d["goal"] = "   "
def _no_goal(d): del d["goal"]
def _blank_remaining(d): d["remaining"] = ["ok", ""]
def _blockers_not_list(d): d["blockers"] = "waiting on A02"
def _constraint_not_str(d): d["constraints"] = [5]
def _open_question_blank(d): d["open_questions"] = ["  "]
def _digest_format(d): d["artifacts"][0]["digest"] = "md5:abc"
def _artifact_no_kind(d): del d["artifacts"][0]["kind"]
def _artifact_blank_uri(d): d["artifacts"][0]["uri"] = " "
def _artifact_producer_slug(d): d["artifacts"][0]["producer"] = "a05-backend"
def _confidence(d): d["verification"]["confidence"] = 1.5
def _confidence_nan(d): d["verification"]["confidence"] = float("nan")
def _verification_status(d): d["verification"]["status"] = "done"
def _slug_agent(d): d["to_agent"] = "a08-qa"
def _unknown_agent(d): d["from_agent"] = "A99"
def _task_id(d): d["task_id"] = "../x"
def _schema_version(d): d["schema_version"] = "swarm.handover.v2"
def _created(d): d["created_wall"] = "now"
def _extra(d): d["foo"] = 1
def _decision_rationale(d): del d["decisions"][0]["rationale"]
def _decision_blank(d): d["decisions"][0]["decision"] = ""


DEFECTS = [
    (_bad_goal, "/goal"), (_no_goal, "/goal"), (_blank_remaining, "/remaining/1"), (_blockers_not_list, "/blockers"),
    (_constraint_not_str, "/constraints/0"), (_open_question_blank, "/open_questions/0"), (_digest_format, "/artifacts/0/digest"),
    (_artifact_no_kind, "/artifacts/0/kind"), (_artifact_blank_uri, "/artifacts/0/uri"),
    (_artifact_producer_slug, "/artifacts/0/producer"), (_confidence, "/verification/confidence"),
    (_confidence_nan, "/verification/confidence"), (_verification_status, "/verification/status"),
    (_slug_agent, "/to_agent"), (_unknown_agent, "/from_agent"), (_task_id, "/task_id"),
    (_schema_version, "/schema_version"), (_created, "/created_wall"), (_extra, "/foo"),
    (_decision_rationale, "/decisions/0/rationale"), (_decision_blank, "/decisions/0/decision"),
]
IDS = [fn.__name__.lstrip("_") for fn, _ in DEFECTS]


@pytest.mark.parametrize(("mutate", "pointer"), DEFECTS, ids=IDS)
def test_write_rejects_each_malformed_field_naming_it(env, mutate, pointer):
    doc = base()
    mutate(doc)
    with pytest.raises(env.ho.HandoverInvalid) as exc:
        env.write(doc)
    assert any(p.startswith(pointer) for p in exc.value.problems), exc.value.problems
    assert pointer.split("/")[1] in str(exc.value) or pointer in str(exc.value)
    assert not (env.sdir / "handovers").exists()  # nothing was stored


@pytest.mark.parametrize(("mutate", "pointer"), DEFECTS, ids=IDS)
def test_read_rejects_each_malformed_field_naming_it(env, mutate, pointer):
    """The same defect in a stored file (with a digest that matches it) is refused on read, not just on write."""
    stored = env.write()["document"]
    doc = {k: v for k, v in stored.items() if k != "digest"}
    mutate(doc)
    try:
        doc["digest"] = env.ho.compute_digest(doc)
    except ValueError:  # NaN cannot be canonicalised: write it as plain JSON text instead
        doc["digest"] = stored["digest"]
    env.file().write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(env.ho.HandoverInvalid) as exc:
        env.ho.read_handover("T1-be.1", root=env.root)
    assert any(p.startswith(pointer) for p in exc.value.problems), exc.value.problems
    assert env.status()["status"] == "malformed"


def test_validate_reports_every_problem_at_once(env):
    doc = base(goal=" ", remaining=[""], to_agent="a08-qa")
    problems = env.ho.validate_handover({**doc, "schema_version": env.ho.SCHEMA_VERSION, "handover_id": "x.1",
                                         "created_wall": 1.0, "summarized": False, "bytes": 0})
    assert {p.split(":")[0] for p in problems} >= {"/goal", "/remaining/0", "/to_agent"}
    assert env.ho.validate_handover([]) == ["/: handover must be an object"]
    assert "use the agent id A08" in " ".join(problems)  # a slug is refused with the id to use


# ---- tamper detection -----------------------------------------------------------------------------------------
def test_read_rejects_a_modified_document(env):
    env.write()
    path = env.file()
    path.write_text(path.read_text().replace("CSV export", "CSV import"), encoding="utf-8")
    with pytest.raises(env.ho.HandoverInvalid, match="modified after it was written") as exc:
        env.ho.read_handover("T1-be.1", root=env.root)
    assert exc.value.problems[0].startswith("/digest")
    result = env.status()
    assert result["status"] == "malformed" and "/digest" in result["reasons"][0]


def test_read_rejects_a_swapped_digest_and_a_misplaced_file(env):
    first = env.write()["document"]
    other = env.write(base(goal="A different goal"))["document"]
    doc = {**first, "digest": other["digest"]}  # content of one, digest of the other
    env.file().write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(env.ho.HandoverInvalid, match="digest"):
        env.ho.read_handover("T1-be.1", root=env.root)
    # a valid document copied under another task's file name is not that task's handover
    target = env.sdir / "handovers" / "corr-1" / "T9-other.1.json"
    target.write_bytes(env.file(seq=2).read_bytes())
    with pytest.raises(env.ho.HandoverInvalid, match="handover_id"):
        env.ho.read_handover("corr-1/T9-other.1", root=env.root)


def test_ref_forms_and_path_escape(env):
    doc = env.write()["document"]
    for ref in ("T1-be.1", "corr-1/T1-be.1", str(env.file()), "corr-1/T1-be.1.json"):
        assert env.ho.read_handover(ref, root=env.root) == doc
    for ref in ("../../etc/passwd", str(env.root / "elsewhere.json"), "../corr-1/T1-be.1.json"):
        with pytest.raises(env.ho.HandoverInvalid):
            env.ho.read_handover(ref, root=env.root)
    with pytest.raises(env.ho.HandoverInvalid, match="no handover"):
        env.ho.read_handover("T1-be.7", root=env.root)


# ---- size cap and summarization -------------------------------------------------------------------------------
def _big():
    return base(completed=[f"step {i}: " + "x" * 200 for i in range(60)],
                decisions=[{"decision": f"decision {i} " * 8, "rationale": "r" * 200} for i in range(30)],
                artifacts=[art(i) for i in range(40)],
                verification={"status": "partial", "confidence": 0.5, "checks": [f"check {i} " * 10 for i in range(20)]})


def test_oversize_is_summarized_and_mandatory_sections_survive(env):
    doc = _big()
    written = env.write(doc, max_bytes=4000)
    stored = env.ho.read_handover(written["ref"], root=env.root)
    assert written["summarized"] is True and stored["summarized"] is True
    assert written["bytes"] <= 4000 and len(env.file().read_bytes()) == written["bytes"]
    for key in ("goal", "remaining", "blockers", "open_questions", "constraints"):
        assert stored[key] == doc[key], key
    assert stored["completed"][0].startswith("[summarized: omitted")  # says what was left out
    assert len(stored["completed"]) < len(doc["completed"])
    kept = stored["artifacts"]
    assert kept and kept == doc["artifacts"][len(doc["artifacts"]) - len(kept):]  # newest artifacts are the ones kept
    assert stored["from_agent"] == "A05" and stored["to_agent"] == "A08"


def test_summarize_is_deterministic_and_only_acts_over_the_cap(env):
    sized = env.ho._sized({**base(), "schema_version": env.ho.SCHEMA_VERSION, "handover_id": "T1-be.1",
                           "created_wall": 1.0, "summarized": False})
    assert env.ho.summarize_handover(sized, 100_000)["summarized"] is False  # fits: not marked
    big = {**_big(), "schema_version": env.ho.SCHEMA_VERSION, "handover_id": "T1-be.1", "created_wall": 1.0,
           "summarized": False, "bytes": 0}
    first = env.ho.summarize_handover(big, 3000)
    assert first == env.ho.summarize_handover(big, 3000)
    assert first["summarized"] is True and first["bytes"] <= 3000
    assert env.ho.summarize_handover(first, 3000) == first  # already small: a second pass changes nothing


def test_cap_comes_from_the_environment(env, monkeypatch):
    monkeypatch.setenv("SWARM_HANDOVER_MAX_BYTES", "2500")
    assert env.ho.max_bytes_default() == 2500
    written = env.ho.write_handover(_big(), root=env.root, clock=env.clock, emit=False)
    assert written["summarized"] and written["bytes"] <= 2500
    monkeypatch.setenv("SWARM_HANDOVER_MAX_BYTES", "garbage")
    assert env.ho.max_bytes_default() == env.ho.DEFAULT_MAX_BYTES


def test_mandatory_sections_that_alone_exceed_the_cap_are_refused_not_cut(env):
    with pytest.raises(env.ho.HandoverInvalid, match="never cut") as exc:
        env.write(base(goal="g" * 5000), max_bytes=1000)
    assert exc.value.field == "goal"
    assert not (env.sdir / "handovers" / "corr-1").exists() or not list((env.sdir / "handovers" / "corr-1").iterdir())


# ---- status ---------------------------------------------------------------------------------------------------
def test_status_missing_ok_stale_partial_malformed(env, monkeypatch):
    assert env.status() == {"status": "missing", "reasons": ["no handover for task T1-be recorded"]}
    env.write()
    ok = env.status()
    assert ok["status"] == "ok" and ok["reasons"] == [] and ok["age_s"] == 0.0
    assert ok["handover_id"] == "T1-be.1" and ok["to_agent"] == "A08"

    env.clock.advance(21_600)
    assert env.status()["status"] == "ok"  # exactly at the limit is not yet stale
    env.clock.advance(1)
    stale = env.status()
    assert stale["status"] == "stale" and "21600" in stale["reasons"][0]
    monkeypatch.setenv("SWARM_HANDOVER_STALE_S", "100000")
    assert env.status()["status"] == "ok"
    monkeypatch.delenv("SWARM_HANDOVER_STALE_S")

    env.clock.jump_wall(-50_000)  # a wall-clock step backwards never makes a handover stale or negative-aged
    assert env.status()["age_s"] == 0.0 and env.status()["status"] == "ok"

    env.write(base(remaining=[], artifacts=[], verification={"status": "unverified", "confidence": 0.0, "checks": []},
                   goal="Empty handover"))
    partial = env.status()
    assert partial["status"] == "partial" and "remaining" in partial["reasons"][0] and partial["seq"] == 2
    env.write(base(remaining=[], artifacts=[art(2)], goal="Done, with an artifact"))
    assert env.status()["status"] == "ok"  # no remaining work but an artifact: the work is described

    env.file(seq=3).write_text("{not json", encoding="utf-8")
    bad = env.status()
    assert bad["status"] == "malformed" and "not valid JSON" in bad["reasons"][0]


def test_status_is_stale_when_the_correlation_differs_from_the_tasks_plan(env):
    env.write()  # handover written under corr-1
    store = env.taskstore.TaskStore(root=env.root)
    store.create(task_id="T1-be", correlation_id="corr-2", capability="be.code", agent_id="A05")
    stale = env.status()
    assert stale["status"] == "stale"
    assert "correlation mismatch" in stale["reasons"][0] and "corr-2" in stale["reasons"][0]
    env.write(base(correlation_id="corr-2", goal="Re-planned"))
    assert env.status()["status"] == "ok"  # a handover of the current plan wins
    assert env.status(correlation_id="corr-1")["status"] == "ok"  # asking for the old plan explicitly judges that one


def test_status_by_sequence_number(env):
    env.write()
    env.write(base(goal="Second"))
    assert env.status(seq=1)["handover_id"] == "T1-be.1"
    assert env.status(seq=2)["handover_id"] == "T1-be.2"
    assert env.status(seq=3)["status"] == "missing"


def test_a_malformed_newest_file_is_not_skipped_for_an_older_valid_one(env):
    env.write()
    env.write(base(goal="Second"))
    env.file(seq=2).write_text("[]", encoding="utf-8")
    assert env.status()["status"] == "malformed"
    with pytest.raises(env.ho.HandoverInvalid):
        env.ho.latest_handover("T1-be", root=env.root)


# ---- clarification --------------------------------------------------------------------------------------------
def test_clarification_request_is_a_blocked_task_result(env):
    schema = env.schema.load_schema("task.result.v1")
    request = env.ho.clarification_request("Handover has no remaining work", ["remaining steps", "artifact digests"],
                                           task_id="T1-be")
    assert env.schema.validate(request, schema) == []
    assert request["state"] == "BLOCKED" and request["task_id"] == "T1-be"
    assert request["needs"]["missing"] == ["remaining steps", "artifact digests"]
    assert "remaining steps" in request["summary_md"]
    assert "task_id" not in env.ho.clarification_request("r", ["m"])  # task id only when known
    for reason, missing in (("", ["x"]), ("r", []), ("r", [""]), ("r", "x")):
        with pytest.raises(env.ho.HandoverInvalid):
            env.ho.clarification_request(reason, missing)


# ---- concurrency ----------------------------------------------------------------------------------------------
def test_concurrent_writers_get_distinct_sequence_numbers(env):
    errors, seqs = [], []

    def writer(i):
        try:
            seqs.append(env.write(base(goal=f"writer {i}"))["seq"])
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(seqs) == list(range(1, 11))
    docs = [env.ho.read_handover(f"T1-be.{n}", root=env.root) for n in range(1, 11)]
    assert len({d["goal"] for d in docs}) == 10 and {d["handover_id"] for d in docs} == {f"T1-be.{n}" for n in range(1, 11)}
    leftovers = [p.name for p in (env.sdir / "handovers" / "corr-1").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []  # every temp file was replaced into place


def test_writers_in_separate_processes_never_share_a_sequence_number(env):
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from swarm import handover as ho
        who = sys.argv[1]
        for n in range(4):
            doc = {{"correlation_id": "corr-1", "task_id": "T1-be", "from_agent": "A05", "to_agent": "A08",
                    "goal": f"{{who}}-{{n}}", "completed": [], "remaining": ["x"], "decisions": [], "artifacts": [],
                    "open_questions": [], "blockers": [], "constraints": [],
                    "verification": {{"status": "unverified", "confidence": 0.0, "checks": []}}}}
            ho.write_handover(doc, emit=False)
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script, f"p{i}"], env={**os.environ, "SWARM_DIR": str(env.sdir)})
             for i in range(3)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0, 0]
    names = sorted(p.name for p in (env.sdir / "handovers" / "corr-1").glob("T1-be.*.json"))
    assert names == sorted(f"T1-be.{n}.json" for n in range(1, 13))
    goals = {env.ho.read_handover(f"T1-be.{n}", root=env.root)["goal"] for n in range(1, 13)}
    assert goals == {f"p{i}-{n}" for i in range(3) for n in range(4)}


def test_a_reserved_but_unfinished_sequence_is_skipped_by_readers(env):
    env.write()
    (env.sdir / "handovers" / "corr-1" / "T1-be.2.json").touch()  # a writer reserved seq 2 and has not finished
    assert env.status()["handover_id"] == "T1-be.1"
    assert env.write(base(goal="Third"))["seq"] == 3  # the reserved number is never reused


# ---- redaction, dedupe, events --------------------------------------------------------------------------------
def test_secrets_are_redacted_before_the_digest_and_the_file(env):
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    written = env.write(base(goal=f"Deploy with {token}", constraints=["API_KEY=zzzz-super-secret-1234"]))
    stored = env.file().read_text(encoding="utf-8")
    assert token not in stored and "zzzz-super-secret" not in stored and "[REDACTED]" in stored
    assert written["redacted"] is True
    assert env.ho.read_handover("T1-be.1", root=env.root)["goal"] == "Deploy with [REDACTED]"  # digest matches the redacted text
    assert env.write(base(goal="plain"))["redacted"] is False


def test_identical_content_is_not_written_twice(env):
    first = env.write()
    again = env.write()
    assert again["duplicate"] is True and again["seq"] == 1 and again["digest"] == first["digest"]
    assert len(list((env.sdir / "handovers" / "corr-1").glob("*.json"))) == 1
    env.clock.advance(5)
    assert env.write()["duplicate"] is True  # a later retry of the same content is still the same handover
    assert env.write(base(remaining=["Something else"]))["seq"] == 2


def test_write_emits_the_handover_event_with_correlation_and_task(env):
    written = env.ho.write_handover(base(goal="Ship with token ghp_" + "Z" * 36), root=env.root, clock=env.clock)
    assert written["emitted"] is True
    events = env.runlog.read_events(event_type="swarm.run.handover", root=env.root)
    assert len(events) == 1
    record = events[0]
    assert record["correlation_id"] == "corr-1" and record["task_id"] == "T1-be" and record["source"] == "telemetry"
    assert record["payload"] == {"run_id": "T1-be", "from_agent": "A05", "to_agent": "A08", "handover_id": "T1-be.1",
                                 "digest": written["digest"], "bytes": written["bytes"], "summarized": False}
    assert env.schema.validate(record, env.schema.load_schema("swarm.event.v1")) == []
    assert "ghp_" not in (env.sdir / "events.jsonl").read_text(encoding="utf-8")
    env.ho.write_handover(base(goal="Ship with token ghp_" + "Z" * 36), root=env.root, clock=env.clock)
    assert len(env.runlog.read_events(event_type="swarm.run.handover", root=env.root)) == 1  # duplicate: no new event


def test_event_run_id_follows_the_task_attempt_and_the_live_snapshot_shows_the_handover(env):
    store = env.taskstore.TaskStore(root=env.root)
    store.create(task_id="T1-be", correlation_id="corr-1", capability="be.code", agent_id="A05")
    env.ho.write_handover(base(), root=env.root, clock=env.clock)
    record = env.runlog.read_events(event_type="swarm.run.handover", root=env.root)[0]
    assert record["payload"]["run_id"] == "T1-be.a1"
    live = env.telemetry.read_live(env.root)
    row = next(r for r in live["runs"] if r["task_id"] == "T1-be")
    assert row["handover_out"] == "T1-be.1"


# ---- receipts -------------------------------------------------------------------------------------------------
def test_receipt_is_idempotent_by_task_receiver_and_digest(env):
    written = env.write()
    digest = written["digest"]
    first, dup1 = env.ho.write_receipt("T1-be", by="A08", digest=digest, understood=["Goal is the CSV export"],
                                       questions=["Is 50k the maximum?"], root=env.root, clock=env.clock)
    assert dup1 is False and first["handover_id"] == "T1-be.1" and first["to_agent"] == "A08"
    env.clock.advance(60)
    second, dup2 = env.ho.write_receipt("T1-be", by="A08", digest=digest, understood=["something else"],
                                        root=env.root, clock=env.clock)
    assert dup2 is True and second == first  # the first receipt wins
    assert len(list((env.sdir / "handovers" / "corr-1").glob("*.receipt.*"))) == 1
    with pytest.raises(env.ho.HandoverInvalid, match="addressed to A08, not to A05"):
        env.ho.write_receipt("T1-be", by="A05", digest=digest, root=env.root, clock=env.clock)
    with pytest.raises(env.ho.HandoverInvalid, match="no valid handover"):
        env.ho.write_receipt("T1-be", by="A08", digest="sha256:" + "1" * 64, root=env.root, clock=env.clock)
    with pytest.raises(env.ho.HandoverInvalid, match="/understood"):
        env.ho.write_receipt("T1-be", by="A08", digest=digest, understood=[""], root=env.root, clock=env.clock)
    # a new handover (new digest) is a new receipt key
    new = env.write(base(goal="Second round"))["digest"]
    _, dup3 = env.ho.write_receipt("T1-be", by="A08", digest=new, root=env.root, clock=env.clock)
    assert dup3 is False


# ---- RV-B fixes ------------------------------------------------------------------------------------------------
def test_newest_handover_is_the_highest_seq_even_when_file_mtimes_are_inverted(env):
    """RV-B #1: write T9 seq 1 ("first") and seq 2 ("second"), then os.utime(T9.1.json, now+5): `handover_read{task_id}`
    returned "first" and handover_verify reported T9.1, because the newest was picked by mtime when no correlation_id
    was passed. Order is by seq within a correlation."""
    env.write(base(task_id="T9", goal="first"))
    env.write(base(task_id="T9", goal="second"))
    future = env.file("T9", 1).stat().st_mtime + 5
    os.utime(env.file("T9", 1), (future, future))
    assert [c.seq for c in env.ho.list_handovers("T9", root=env.root)] == [1, 2]
    assert env.ho.latest_handover("T9", root=env.root)["goal"] == "second"
    assert env.ho.latest_handover("T9", root=env.root, correlation_id="corr-1")["goal"] == "second"
    status = env.status("T9")
    assert status["handover_id"] == "T9.2" and status["seq"] == 2


def test_the_tasks_plan_decides_between_correlations_not_the_mtime(env):
    """Across correlations the Task Store row picks the correlation; within it the highest seq wins."""
    store = env.taskstore.TaskStore(root=env.root)
    store.create(task_id="T9", correlation_id="corr-plan", capability="be.code", agent_id="A05", title="t")
    env.write(base(task_id="T9", correlation_id="corr-plan", goal="planned"))
    env.write(base(task_id="T9", correlation_id="corr-old", goal="older run, written later"))
    other = env.sdir / "handovers" / "corr-old" / "T9.1.json"
    later = env.sdir / "handovers" / "corr-plan" / "T9.1.json"
    os.utime(later, (1000, 1000))  # the planned run's file looks ancient
    assert other.stat().st_mtime > later.stat().st_mtime
    assert env.ho.list_handovers("T9", root=env.root)[-1].correlation_id == "corr-plan"
    assert env.ho.latest_handover("T9", root=env.root)["goal"] == "planned"


def test_token_shaped_ids_are_not_redacted_into_invalid_ones(env):
    """RV-B #4: task_id "sk-onboarding-flow-v2" matches the `sk-` token pattern; redaction ran after validation and turned
    it into "[REDACTED]", so the write was refused with "/task_id: '[REDACTED]' is not a valid id" and an empty folder
    was left behind. Ids, agent ids and digests are identifiers, not free text."""
    doc = base(task_id="sk-onboarding-flow-v2", correlation_id="sk-onboarding-run-v2")
    doc["artifacts"] = [{**art(1), "producer": "A05"}]
    written = env.write(doc)
    assert written["document"]["task_id"] == "sk-onboarding-flow-v2"
    assert written["document"]["correlation_id"] == "sk-onboarding-run-v2"
    assert written["ref"].startswith("sk-onboarding-run-v2/sk-onboarding-flow-v2.1")
    assert (env.sdir / "handovers" / "sk-onboarding-run-v2" / "sk-onboarding-flow-v2.1.json").is_file()
    assert not (env.sdir / "handovers" / "[REDACTED]").exists()
    assert env.ho.read_handover(written["ref"], root=env.root)["task_id"] == "sk-onboarding-flow-v2"


def test_free_text_is_still_redacted_next_to_token_shaped_ids(env):
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    written = env.write(base(task_id="sk-flow", goal=f"use {secret} to push"))
    assert secret not in env.file("sk-flow").read_text() and written["redacted"] is True
    assert written["document"]["task_id"] == "sk-flow"


def test_a_crashed_receipt_writer_does_not_block_the_receipt_key_for_good(env):
    """RV-B #5: a writer that died between the O_EXCL create and the replace left an empty file; every later call
    returned duplicate:true with the caller's own receipt, which was never saved (the file stayed 0 bytes)."""
    written = env.write()
    digest = written["digest"]
    husk = env.sdir / "handovers" / "corr-1" / f"T1-be.receipt.A08.{digest.split(':', 1)[1][:16]}.json"
    husk.touch()
    old = husk.stat().st_mtime - 60
    os.utime(husk, (old, old))
    receipt, duplicate = env.ho.write_receipt("T1-be", by="A08", digest=digest, understood=["goal"], root=env.root, clock=env.clock)
    assert duplicate is False
    assert json.loads(husk.read_text()) == receipt and husk.stat().st_size > 0
    again, duplicate = env.ho.write_receipt("T1-be", by="A08", digest=digest, understood=["other"], root=env.root, clock=env.clock)
    assert duplicate is True and again == receipt


def test_a_receipt_file_is_never_visible_empty_and_leaves_no_temp_files(env):
    written = env.write()
    env.ho.write_receipt("T1-be", by="A08", digest=written["digest"], root=env.root, clock=env.clock)
    folder = env.sdir / "handovers" / "corr-1"
    assert all(p.stat().st_size > 0 for p in folder.iterdir())
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_a_fresh_empty_receipt_file_is_a_writer_still_at_work(env):
    written = env.write()
    husk = env.sdir / "handovers" / "corr-1" / f"T1-be.receipt.A08.{written['digest'].split(':', 1)[1][:16]}.json"
    husk.touch()
    _receipt, duplicate = env.ho.write_receipt("T1-be", by="A08", digest=written["digest"], root=env.root, clock=env.clock)
    assert duplicate is True and husk.stat().st_size == 0  # not reclaimed while young


def test_best_effort_summary_returns_the_mandatory_sections_instead_of_refusing(env):
    doc = env.write(base(remaining=["x" * 3000, "y" * 3000]))["document"]
    with pytest.raises(env.ho.HandoverInvalid):
        env.ho.summarize_handover(doc, 4096)
    short = env.ho.summarize_handover(doc, 4096, best_effort=True)
    assert short["summarized"] is True and short["remaining"] == doc["remaining"] and short["goal"] == doc["goal"]
    assert short["bytes"] == len(env.ho.canonical({**short, "digest": "sha256:" + "0" * 64}))


def test_verdicts_of_the_reader_keep_pre_rework_rows_flagged_stale(env):
    """The verdicts that CAUSED a rework are older than notes.verdicts_since; they used to be filtered out, leaving
    rework intake with nothing. Now the latest row per gate is returned with `stale` set; a newer verdict replaces it."""
    import time

    store = env.taskstore.TaskStore(root=env.root)
    store.create(task_id="T-v", correlation_id="corr-1", capability="be.code", agent_id="A05", title="t")
    for state in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        store.transition("T-v", state)
    store.conn.execute(
        "INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, ts) VALUES (?,?,?,?,?,?)",
        ("T-v", "quality", "fail", "A08", json.dumps([{"id": "F1", "severity": "major"}]), time.time()))
    store.conn.commit()
    store.transition("T-v", "CHANGES_REQUESTED")
    with env.ho.StoreReader(env.root) as reader:
        rows = reader.verdicts("T-v")
    assert [(r["gate"], r["verdict"], r["stale"]) for r in rows] == [("quality", "fail", True)]
    assert rows[0]["findings"][0]["id"] == "F1"
    time.sleep(0.01)
    store.conn.execute(
        "INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, ts) VALUES (?,?,?,?,?,?)",
        ("T-v", "quality", "pass", "A08", "[]", time.time()))
    store.conn.commit()
    with env.ho.StoreReader(env.root) as reader:
        rows = reader.verdicts("T-v")
    assert [(r["verdict"], r["stale"]) for r in rows] == [("pass", False)]
