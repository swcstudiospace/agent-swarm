"""Pure signal detector (n2/n3). Read-only. Parity with omp/src/hooks.ts classify + user_prompt_submit.
Fail-open. Detects a01-orchestrator completion (IN_REVIEW or UPLIFT boundary) or SDLC.
"""

from __future__ import annotations
import json
import re
from typing import Any

# Full guarded classify (greptile: shared classifier must not change routing; preserve all checks)
NEGATIVE = ("/uplift", "/think", "explain only", "/all-in-one:")
DISPATCH_MARKER = "[agent-swarm:dispatch]"
QUESTION = re.compile(r"^(what|why|how|when|where|who|which|is|are|can|could|does|do|should|explain|describe)\b", re.I | re.A)
TRIVIAL = re.compile(r"\btypos?\b|^rename\b", re.I | re.A)
SDLC = re.compile(
    r"\b(build|implement|add|create|fix|refactor|migrate|deploy|release|ship|write tests?|set up|setup|integrate|scaffold|upgrade)\b",
    re.I | re.A,
)


def classify(text: str) -> str:
    """Guarded SDLC classify (exact parity with user_prompt_submit + omp)."""
    if not text:
        return "non-sdlc"
    trimmed = text.strip()
    if not trimmed or trimmed.startswith("/") or trimmed.startswith("<system-reminder"):
        return "non-sdlc"
    low = trimmed.lower()
    if DISPATCH_MARKER in low or any(n in low for n in NEGATIVE):
        return "non-sdlc"
    if trimmed.endswith("?") or QUESTION.search(trimmed) or TRIVIAL.search(trimmed):
        return "non-sdlc"
    if SDLC.search(trimmed):
        return "sdlc"
    return "non-sdlc"


def detect_completion(payload: str | dict | None) -> dict | None:
    """Detect a01-orchestrator complete or uplift end.
    Requires a01/ultrathink identifier + specific state (greptile: avoid unrelated text).
    """
    if not payload:
        return None
    text = payload if isinstance(payload, str) else json.dumps(payload)
    tlow = text.lower()
    # require a01 or ultrathink context
    if not ("a01-orchestrator" in tlow or "a01" in tlow or "ultrathink" in tlow or "orchestrate" in tlow):
        return None
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
    return "a01-orchestrator" in t or "orchestrate" in t or ("swarm" in t and "plan" in t)
