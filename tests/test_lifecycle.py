"""RunState x RunEvent matrix, wait_kind rules, terminal exits and the TaskState projection (DESIGN 4.1)."""
from __future__ import annotations

import itertools

import pytest

from swarm.errors import ErrorCode, SwarmError
from swarm.lifecycle import (
    TERMINAL_STATES,
    RunEvent,
    RunState,
    allowed_events,
    can_transition,
    from_task_state,
    is_terminal,
    next_state,
)
from swarm.taskstore import TaskState

# The contract restated independently of swarm.lifecycle.TRANSITIONS. `waiting` rows carry the wait_kind they need.
EXPECTED = {
    ("waiting", "deps_satisfied", "dependency"): "queued",
    ("waiting", "cancel", None): "cancelled",
    ("waiting", "deadline_exceeded", None): "timed_out",
    ("waiting", "finish_error", None): "failed",
    ("waiting", "input_received", "input"): "running",
    ("queued", "slot_acquired", None): "starting",
    ("queued", "cancel", None): "cancelled",
    ("queued", "deadline_exceeded", None): "timed_out",
    ("starting", "process_started", None): "running",
    ("starting", "finish_error", None): "failed",
    ("starting", "cancel", None): "cancelled",
    ("starting", "deadline_exceeded", None): "timed_out",
    ("running", "await_input", None): "waiting",
    ("running", "begin_handoff", None): "handing_off",
    ("running", "finish_ok", None): "completed",
    ("running", "finish_error", None): "failed",
    ("running", "cancel", None): "cancelled",
    ("running", "deadline_exceeded", None): "timed_out",
    ("handing_off", "handoff_done", None): "completed",
    ("handing_off", "finish_error", None): "failed",
    ("handing_off", "cancel", None): "cancelled",
    ("handing_off", "deadline_exceeded", None): "timed_out",
}
ALL_STATES = [s.value for s in RunState]
ALL_EVENTS = [e.value for e in RunEvent]


def _legal(state: str, event: str, wait_kind: str | None) -> str | None:
    """Expected target for (state, event, wait_kind) per the contract, or None when illegal."""
    for (s, e, need), target in EXPECTED.items():
        if (s, e) == (state, event) and (need is None or need == wait_kind):
            return target
    return None


def test_enum_values_are_pinned():
    assert ALL_STATES == ["waiting", "queued", "starting", "running", "handing_off", "completed", "failed",
                          "cancelled", "timed_out"]
    assert ALL_EVENTS == ["deps_satisfied", "slot_acquired", "process_started", "await_input", "input_received",
                          "begin_handoff", "handoff_done", "finish_ok", "finish_error", "cancel", "deadline_exceeded"]


@pytest.mark.parametrize("wait_kind", [None, "dependency", "input"])
@pytest.mark.parametrize(("state", "event"), list(itertools.product(ALL_STATES, ALL_EVENTS)))
def test_full_matrix(state, event, wait_kind):
    target = _legal(state, event, wait_kind)
    assert can_transition(state, event, wait_kind=wait_kind) is (target is not None)
    if target is not None:
        assert next_state(state, event, wait_kind=wait_kind) is RunState(target)
        return
    with pytest.raises(SwarmError) as ei:
        next_state(state, event, wait_kind=wait_kind)
    err = ei.value
    assert err.code == ErrorCode.E_CONTRACT
    assert repr(state) in str(err) and repr(event) in str(err)
    allowed = [e.value for e in allowed_events(state, wait_kind=wait_kind)]
    for name in allowed:
        assert name in str(err)
    if not allowed:
        assert "terminal" in str(err)


def test_accepts_enum_and_string_forms():
    assert next_state(RunState.QUEUED, RunEvent.SLOT_ACQUIRED) is RunState.STARTING
    assert next_state("queued", "slot_acquired") is RunState.STARTING


@pytest.mark.parametrize(("state", "event"), [("bogus", "cancel"), ("running", "bogus"), (None, "cancel")])
def test_unknown_names_raise_contract_error(state, event):
    with pytest.raises(SwarmError) as ei:
        next_state(state, event)
    assert ei.value.code == ErrorCode.E_CONTRACT
    assert can_transition(state, event) is False


def test_unknown_wait_kind_is_rejected():
    with pytest.raises(SwarmError) as ei:
        next_state("waiting", "cancel", wait_kind="sleep")
    assert ei.value.code == ErrorCode.E_CONTRACT
    assert can_transition("waiting", "cancel", wait_kind="sleep") is False


class TestWaitKind:
    def test_deps_satisfied_requires_dependency(self):
        assert next_state("waiting", "deps_satisfied", wait_kind="dependency") is RunState.QUEUED
        for bad in (None, "input"):
            with pytest.raises(SwarmError) as ei:
                next_state("waiting", "deps_satisfied", wait_kind=bad)
            assert "wait_kind" in str(ei.value) and "'dependency'" in str(ei.value)

    def test_input_received_requires_input(self):
        assert next_state("waiting", "input_received", wait_kind="input") is RunState.RUNNING
        for bad in (None, "dependency"):
            with pytest.raises(SwarmError) as ei:
                next_state("waiting", "input_received", wait_kind=bad)
            assert "'input'" in str(ei.value)

    @pytest.mark.parametrize("wait_kind", [None, "dependency", "input"])
    @pytest.mark.parametrize("event", ["cancel", "deadline_exceeded", "finish_error"])
    def test_other_waiting_events_ignore_kind(self, event, wait_kind):
        assert can_transition("waiting", event, wait_kind=wait_kind)

    def test_wait_kind_never_widens_other_states(self):
        assert not can_transition("running", "deps_satisfied", wait_kind="dependency")
        assert not can_transition("queued", "input_received", wait_kind="input")

    def test_allowed_events_filter_by_kind(self):
        dep = {e.value for e in allowed_events("waiting", wait_kind="dependency")}
        inp = {e.value for e in allowed_events("waiting", wait_kind="input")}
        both = {e.value for e in allowed_events("waiting")}
        assert "deps_satisfied" in dep and "input_received" not in dep
        assert "input_received" in inp and "deps_satisfied" not in inp
        assert both == dep | inp

    def test_await_input_roundtrip(self):
        s = next_state("running", "await_input")
        assert s is RunState.WAITING
        assert next_state(s, "input_received", wait_kind="input") is RunState.RUNNING


class TestTerminal:
    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_no_exits(self, state):
        assert is_terminal(state)
        assert allowed_events(state) == []
        for event in RunEvent:
            for kind in (None, "dependency", "input"):
                assert not can_transition(state, event, wait_kind=kind)
                with pytest.raises(SwarmError):
                    next_state(state, event, wait_kind=kind)

    def test_terminal_set_is_exactly_the_four(self):
        assert {s.value for s in TERMINAL_STATES} == {"completed", "failed", "cancelled", "timed_out"}
        assert [s.value for s in RunState if not is_terminal(s)] == ["waiting", "queued", "starting", "running",
                                                                    "handing_off"]

    def test_every_live_state_can_be_cancelled(self):
        for s in RunState:
            if not is_terminal(s):
                assert next_state(s, "cancel") is RunState.CANCELLED

    def test_every_live_state_can_time_out(self):
        for s in RunState:
            if not is_terminal(s):
                assert next_state(s, "deadline_exceeded") is RunState.TIMED_OUT

    def test_every_live_state_reaches_a_terminal_state(self):
        for start in RunState:
            seen, frontier = {start}, [start]
            while frontier:
                cur = frontier.pop()
                for ev in allowed_events(cur):
                    for kind in ("dependency", "input"):
                        if can_transition(cur, ev, wait_kind=kind):
                            nxt = next_state(cur, ev, wait_kind=kind)
                            if nxt not in seen:
                                seen.add(nxt)
                                frontier.append(nxt)
            assert seen & TERMINAL_STATES, start


class TestFromTaskState:
    EXPECTED_MAP = {
        "CREATED": "waiting",
        "VALIDATED": "waiting",
        "PLANNED": "queued",
        "CLAIMED": "starting",
        "IN_PROGRESS": "running",
        "IN_REVIEW": "completed",
        "CHANGES_REQUESTED": "waiting",
        "APPROVED": "completed",
        "DONE": "completed",
        "BLOCKED": "waiting",
        "FAILED": "failed",
        "RETRY": "queued",
        "ESCALATED": "failed",
        "CANCELLED": "cancelled",
    }

    def test_covers_every_task_state(self):
        assert {t.value for t in TaskState} == set(self.EXPECTED_MAP), "TaskState changed: update from_task_state + this map"

    @pytest.mark.parametrize("task_state", [t.value for t in TaskState])
    def test_mapping(self, task_state):
        assert from_task_state(task_state) is RunState(self.EXPECTED_MAP[task_state])
        assert from_task_state(TaskState(task_state)) is RunState(self.EXPECTED_MAP[task_state])

    @pytest.mark.parametrize("task_state", ["PLANNED", "RETRY"])
    def test_unmet_deps_wait(self, task_state):
        assert from_task_state(task_state, deps_met=False) is RunState.WAITING

    @pytest.mark.parametrize("task_state", [t.value for t in TaskState if t.value not in ("PLANNED", "RETRY")])
    def test_deps_met_flag_only_affects_planned_and_retry(self, task_state):
        assert from_task_state(task_state, deps_met=False) is from_task_state(task_state, deps_met=True)

    def test_non_string_is_rejected(self):
        with pytest.raises(SwarmError) as ei:
            from_task_state(None)  # type: ignore[arg-type]
        assert ei.value.code == ErrorCode.E_CONTRACT
