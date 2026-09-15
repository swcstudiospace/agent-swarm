"""AgentSwarm runtime toolkit (stdlib-only core).

Modules
-------
errors     shared error taxonomy (02-message-protocol.md §6)
envelope   swarm.envelope.v1 build/validate/sign/verify (§1)
taskstore  SQLite Task Store implementing the task lifecycle state machine (§3)
gates      gate verdict construction, validation and conjunction (04-integration-plan.md §5)
manifest   agent manifest registry loaded from agents.json (07-scalability.md)
runlog     append-only JSONL event log used by every agent script
"""
from .errors import SwarmError, ErrorCode
from .envelope import build_envelope, validate_envelope, sign_envelope, verify_envelope
from .taskstore import TaskStore, TaskState
from .gates import make_verdict, validate_verdict, conjunction
from .manifest import load_manifest, get_agent, AGENTS_FILE
from .runlog import emit, SWARM_DIR

__version__ = "1.0.0"
__all__ = [
    "SwarmError", "ErrorCode",
    "build_envelope", "validate_envelope", "sign_envelope", "verify_envelope",
    "TaskStore", "TaskState",
    "make_verdict", "validate_verdict", "conjunction",
    "load_manifest", "get_agent", "AGENTS_FILE",
    "emit", "SWARM_DIR",
]
