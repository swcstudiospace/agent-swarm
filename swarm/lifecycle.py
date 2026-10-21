"""Runtime lifecycle state machine for one run attempt (a projection over the Task Store, never a replacement).

A run is one attempt (`run_id = "<task_id>.a<attempt>"`). A retry is a NEW run that starts at `queued`, so terminal
states have no exits. `stalled` is not a state: it is `running` with `since_activity_s > stall_after_s`.
"""
from __future__ import annotations

from enum import Enum

from .errors import ErrorCode, SwarmError


class RunState(str, Enum):
    WAITING = "waiting"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    HANDING_OFF = "handing_off"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RunEvent(str, Enum):
    DEPS_SATISFIED = "deps_satisfied"
    SLOT_ACQUIRED = "slot_acquired"
    PROCESS_STARTED = "process_started"
    AWAIT_INPUT = "await_input"
    INPUT_RECEIVED = "input_received"
    BEGIN_HANDOFF = "begin_handoff"
    HANDOFF_DONE = "handoff_done"
    FINISH_OK = "finish_ok"
    FINISH_ERROR = "finish_error"
    CANCEL = "cancel"
    DEADLINE_EXCEEDED = "deadline_exceeded"


WAIT_DEPENDENCY = "dependency"
WAIT_INPUT = "input"
WAIT_KINDS = (WAIT_DEPENDENCY, WAIT_INPUT)

TERMINAL_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.TIMED_OUT})

_S, _E = RunState, RunEvent

#: (state, event) -> next state. `waiting` is further constrained by `wait_kind` (see `_WAIT_KIND_REQUIRED`).
TRANSITIONS: dict[tuple[RunState, RunEvent], RunState] = {
    (_S.WAITING, _E.DEPS_SATISFIED): _S.QUEUED,
    (_S.WAITING, _E.INPUT_RECEIVED): _S.RUNNING,
    (_S.WAITING, _E.CANCEL): _S.CANCELLED,
    (_S.WAITING, _E.DEADLINE_EXCEEDED): _S.TIMED_OUT,
    (_S.WAITING, _E.FINISH_ERROR): _S.FAILED,  # a dependency failed
    (_S.QUEUED, _E.SLOT_ACQUIRED): _S.STARTING,
    (_S.QUEUED, _E.CANCEL): _S.CANCELLED,
    (_S.QUEUED, _E.DEADLINE_EXCEEDED): _S.TIMED_OUT,
    (_S.STARTING, _E.PROCESS_STARTED): _S.RUNNING,
    (_S.STARTING, _E.FINISH_ERROR): _S.FAILED,
    (_S.STARTING, _E.CANCEL): _S.CANCELLED,
    (_S.STARTING, _E.DEADLINE_EXCEEDED): _S.TIMED_OUT,
    (_S.RUNNING, _E.AWAIT_INPUT): _S.WAITING,
    (_S.RUNNING, _E.BEGIN_HANDOFF): _S.HANDING_OFF,
    (_S.RUNNING, _E.FINISH_OK): _S.COMPLETED,
    (_S.RUNNING, _E.FINISH_ERROR): _S.FAILED,
    (_S.RUNNING, _E.CANCEL): _S.CANCELLED,
    (_S.RUNNING, _E.DEADLINE_EXCEEDED): _S.TIMED_OUT,
    (_S.HANDING_OFF, _E.HANDOFF_DONE): _S.COMPLETED,
    (_S.HANDING_OFF, _E.FINISH_ERROR): _S.FAILED,
    (_S.HANDING_OFF, _E.CANCEL): _S.CANCELLED,
    (_S.HANDING_OFF, _E.DEADLINE_EXCEEDED): _S.TIMED_OUT,
}

#: Events from `waiting` that are only legal for one wait kind.
_WAIT_KIND_REQUIRED: dict[RunEvent, str] = {
    RunEvent.DEPS_SATISFIED: WAIT_DEPENDENCY,
    RunEvent.INPUT_RECEIVED: WAIT_INPUT,
}


def _coerce_state(state: RunState | str) -> RunState:
    try:
        return RunState(state)
    except ValueError:
        raise SwarmError(ErrorCode.E_CONTRACT, f"unknown run state {state!r}; known: {[s.value for s in RunState]}",
                         state=str(state)) from None


def _coerce_event(event: RunEvent | str) -> RunEvent:
    try:
        return RunEvent(event)
    except ValueError:
        raise SwarmError(ErrorCode.E_CONTRACT, f"unknown run event {event!r}; known: {[e.value for e in RunEvent]}",
                         event=str(event)) from None


def _check_wait_kind(wait_kind: str | None) -> None:
    if wait_kind is not None and wait_kind not in WAIT_KINDS:
        raise SwarmError(ErrorCode.E_CONTRACT, f"unknown wait_kind {wait_kind!r}; expected one of {list(WAIT_KINDS)} or None",
                         wait_kind=str(wait_kind))


def allowed_events(state: RunState | str, *, wait_kind: str | None = None) -> list[RunEvent]:
    """Events legal from `state`, in table order. For `waiting`, a known `wait_kind` filters kind-specific events; with
    `wait_kind=None` every `waiting` event is listed (the caller still has to say which kind it is waiting on)."""
    st = _coerce_state(state)
    _check_wait_kind(wait_kind)
    out: list[RunEvent] = []
    for (src, ev) in TRANSITIONS:
        if src is not st:
            continue
        need = _WAIT_KIND_REQUIRED.get(ev) if st is RunState.WAITING else None
        if need is not None and wait_kind is not None and wait_kind != need:
            continue
        out.append(ev)
    return out


def can_transition(state: RunState | str, event: RunEvent | str, *, wait_kind: str | None = None) -> bool:
    """True when `event` is legal from `state`. From `waiting`, `deps_satisfied` needs `wait_kind="dependency"` and
    `input_received` needs `wait_kind="input"`; any other `wait_kind` (including None) makes them illegal."""
    try:
        st, ev = _coerce_state(state), _coerce_event(event)
        _check_wait_kind(wait_kind)
    except SwarmError:
        return False
    if (st, ev) not in TRANSITIONS:
        return False
    if st is RunState.WAITING:
        need = _WAIT_KIND_REQUIRED.get(ev)
        if need is not None and wait_kind != need:
            return False
    return True


def next_state(state: RunState | str, event: RunEvent | str, *, wait_kind: str | None = None) -> RunState:
    """The state after `event`, or `SwarmError(E_CONTRACT)` naming the state, the event and the allowed events."""
    st, ev = _coerce_state(state), _coerce_event(event)
    _check_wait_kind(wait_kind)
    if not can_transition(st, ev, wait_kind=wait_kind):
        allowed = [e.value for e in allowed_events(st, wait_kind=wait_kind)]
        why = ""
        if st is RunState.WAITING and ev in _WAIT_KIND_REQUIRED and (st, ev) in TRANSITIONS:
            why = f" (event requires wait_kind={_WAIT_KIND_REQUIRED[ev]!r}, got {wait_kind!r})"
        raise SwarmError(
            ErrorCode.E_CONTRACT,
            f"illegal run transition: state {st.value!r} cannot handle event {ev.value!r}{why}; "
            f"allowed events: {allowed or 'none (terminal state)'}",
            state=st.value, event=ev.value, allowed=allowed, wait_kind=wait_kind,
        )
    return TRANSITIONS[(st, ev)]


def is_terminal(state: RunState | str) -> bool:
    return _coerce_state(state) in TERMINAL_STATES


_TASK_TO_RUN: dict[str, RunState] = {
    "CLAIMED": RunState.STARTING,
    "IN_PROGRESS": RunState.RUNNING,
    "IN_REVIEW": RunState.COMPLETED,
    "APPROVED": RunState.COMPLETED,
    "DONE": RunState.COMPLETED,
    "FAILED": RunState.FAILED,
    "ESCALATED": RunState.FAILED,
    "CANCELLED": RunState.CANCELLED,
    "BLOCKED": RunState.WAITING,
}


def from_task_state(task_state: str, *, deps_met: bool = True) -> RunState:
    """Project a Task Store state onto a `RunState`. PLANNED and RETRY are `queued` once dependencies are met, else
    `waiting`; every other state (CREATED, VALIDATED, CHANGES_REQUESTED, ...) is `waiting`."""
    name = task_state.value if isinstance(task_state, Enum) else task_state
    if not isinstance(name, str):
        raise SwarmError(ErrorCode.E_CONTRACT, f"task_state must be a string, got {type(task_state).__name__}")
    if name in ("PLANNED", "RETRY"):
        return RunState.QUEUED if deps_met else RunState.WAITING
    return _TASK_TO_RUN.get(name, RunState.WAITING)
