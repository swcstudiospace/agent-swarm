"""Pure signal detector (n2/n3). Read-only. Parity with omp/src/hooks.ts classify + user_prompt_submit.
Fail-open. Detects a01-orchestrator completion (IN_REVIEW or UPLIFT boundary) or SDLC.
"""

from __future__ import annotations
import json
def classify(text: str) -> str:
    """Parity classify for SDLC (build, implement, ...). Mirrors hooks.ts + user_prompt_submit.py."""
    if not text or text.strip().startswith(("/", "?", "explain")):
        return "non-sdlc"
    sdlc = ("build", "implement", "add", "create", "fix", "refactor", "migrate", "deploy", "release", "ship", "test", "setup", "integrate", "scaffold", "upgrade")
    t = text.lower()
    if any(k in t for k in sdlc):
        return "sdlc"
    return "non-sdlc"


def detect_completion(payload: str | dict | None) -> dict | None:
    """Detect a01-orchestrator complete or uplift end.
    Returns event dict or None. Uses n1 facts: task.result with state IN_REVIEW, or UPLIFT <ORIGINAL>.
    """
    if not payload:
        return None
    text = payload if isinstance(payload, str) else json.dumps(payload)
    # a01 task.result marker
    if '"state": "IN_REVIEW"' in text or "IN_REVIEW" in text:
        return {"type": "a01_complete", "reason": "in_review", "raw": text[:512]}
    # uplift boundary
    if "UPLIFT" in text.upper() or "<ORIGINAL>" in text:
        return {"type": "uplift_complete", "reason": "uplift", "raw": text[:512]}
    # swarm.status DONE counts or final
    if '"state": "DONE"' in text or "COMPLETE" in text.upper():
        return {"type": "swarm_complete", "reason": "done", "raw": text[:512]}
    return None


def is_a01_session(transcript: str | list) -> bool:
    """Check if transcript/session is a01-orchestrator or swarm orchestrate."""
    t = str(transcript).lower()
    return "a01-orchestrator" in t or "orchestrate" in t or "swarm" in t and "plan" in t
