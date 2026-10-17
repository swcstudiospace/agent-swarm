"""AgentSwarm runtime toolkit (stdlib-only core).

Modules
-------
errors     shared error taxonomy
envelope   envelope v1
taskstore  Task Store implementing the task lifecycle state machine
gates      gate verdicts
manifest   agent manifest
tool_registry  tool contracts
runlog     append-only JSONL event log (the logger unit): emits structured logs at every phase + end-of-run summary report
paths      .swarm resolver + logging_paths() spec
"""
from __future__ import annotations

from .errors import SwarmError, ErrorCode
from .envelope import build_envelope, validate_envelope, sign_envelope, verify_envelope
from .taskstore import TaskStore, TaskState
from .gates import make_verdict, validate_verdict, conjunction
from .manifest import load_manifest, get_agent, AGENTS_FILE
from .runlog import emit, log_phase, log_summary
from .paths import swarm_dir, logging_paths

__version__ = "1.0.0"
__all__ = [
    "SwarmError", "ErrorCode",
    "build_envelope", "validate_envelope", "sign_envelope", "verify_envelope",
    "TaskStore", "TaskState",
    "make_verdict", "validate_verdict", "conjunction",
    "load_manifest", "get_agent", "AGENTS_FILE",
    "emit", "swarm_dir", "logging_paths", "log_phase", "log_summary",
]
