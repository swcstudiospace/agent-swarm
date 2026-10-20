"""Runner -> bar contract for `<swarm_dir>/live.json` (v2.2 section 4.5).

tests/fixtures/live_runner_sample.json is a mid-run snapshot that the REAL `scripts/swarm_run.py` wrote while it ran the
32-task DAG of tests/test_e2e_swarm.py (regenerate: `SWARM_WRITE_LIVE_FIXTURE=1 python3 -m pytest tests/test_e2e_swarm.py
-k fixture`). This file pins what the producer promises about it; omp/test/live_contract.test.ts feeds the same bytes to the
bar's ingestion and renderer.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from swarm.lifecycle import RunState, is_terminal
from swarm.schema import load_schema, validate

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "live_runner_sample.json"
# display order of the design: failed, timed out, stalled, running, waiting, queued, then settled
RANK = {"failed": 0, "timed_out": 1, "running": 3, "starting": 3, "handing_off": 3, "waiting": 4, "queued": 5,
        "completed": 6, "cancelled": 6}
COUNTED_AS_RUNNING = ("running", "starting", "handing_off")


@pytest.fixture(scope="module")
def live() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def slugs() -> dict[str, str]:
    agents = json.loads((ROOT / "agents.json").read_text())["agents"]
    return {a["slug"]: a["id"] for a in agents}


def test_the_sample_is_valid_swarm_live_v1(live):
    assert validate(live, load_schema("swarm.live.v1")) == []
    assert live["schema"] == "swarm.live.v1" and live["correlation_id"] and live["seq"] > 0


def test_it_was_written_by_a_running_scheduler_and_not_derived(live):
    assert live["producer"] == "scheduler" and isinstance(live["pid"], int) and live["pid"] > 0
    assert live["producer_updated"] == live["updated_wall"]
    assert live["swarm"]["state"] == "running"
    assert 0 < live["swarm"]["elapsed_s"] < 600


def test_it_is_a_mid_run_snapshot_with_every_row_kind(live):
    states = {r["state"] for r in live["runs"]}
    assert {"running", "queued", "waiting", "completed", "failed"} <= states
    assert [r for r in live["runs"] if is_terminal(RunState(r["state"]))] and [r for r in live["runs"] if not is_terminal(RunState(r["state"]))]


def test_every_manifest_agent_has_a_row_and_run_ids_follow_the_task_and_attempt(live, slugs):
    assert {r["agent"] for r in live["runs"]} == set(slugs)
    assert len(live["runs"]) <= 64
    ids = [r["run_id"] for r in live["runs"]]
    assert len(set(ids)) == len(ids)
    for r in live["runs"]:
        assert r["run_id"] == f"{r['task_id']}.a{r['attempt']}"


def test_the_swarm_counters_agree_with_the_rows(live):
    swarm, rows = live["swarm"], live["runs"]
    count = lambda *states: sum(1 for r in rows if r["state"] in states)  # noqa: E731
    assert swarm["running"] == count(*COUNTED_AS_RUNNING)
    assert swarm["queued"] == count("queued") and swarm["waiting"] == count("waiting")
    assert swarm["completed"] == count("completed") and swarm["failed"] == count("failed")
    assert swarm["timed_out"] == count("timed_out") and swarm["cancelled"] == count("cancelled")
    assert swarm["stalled"] == sum(1 for r in rows if r["stalled"])
    assert 1 <= swarm["effective_parallel"] <= swarm["max_parallel"]


def test_timers_are_monotonic_differences_that_never_go_negative_or_exceed_the_swarm(live):
    total = live["swarm"]["elapsed_s"]
    for r in live["runs"]:
        assert all(isinstance(r[k], (int, float)) and math.isfinite(r[k]) for k in ("elapsed_s", "state_elapsed_s"))
        assert 0 <= r["state_elapsed_s"] <= r["elapsed_s"] + 0.05, r["run_id"]
        assert r["elapsed_s"] <= total + 0.05, r["run_id"]  # a run can not be older than the swarm that dispatched it


def test_rows_are_ordered_worst_first(live):
    ranks = [RANK[r["state"]] - (2 if r["stalled"] else 0) for r in live["runs"]]
    assert ranks == sorted(ranks)
    assert live["runs"][0]["state"] == "failed" and live["runs"][0]["error"]


def test_strings_are_short_and_a_failure_names_its_code(live):
    for r in live["runs"]:
        for key in ("activity", "error", "last_tool"):
            assert r[key] is None or (isinstance(r[key], str) and len(r[key]) <= 120)
    failed = [r for r in live["runs"] if r["state"] == "failed"]
    assert failed and failed[0]["error"].startswith("E-")


def test_nothing_secret_shaped_is_in_the_sample():
    from swarm.redact import redact_text

    text = FIXTURE.read_text()
    assert redact_text(text) == text
