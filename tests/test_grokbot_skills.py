"""Pin grokbot/skills/swarm-cloud-dispatch/SKILL.md as the keyless dispatch source.

These assertions fail on the pre-change skill (main e7acd5c, same text as f3a0529).
That copy has a name and description, and it already treats a cloud session as
advisory, but it does not name --graph-id, the Greptile loop, the PR-body
receipt, the desk and grokbot branch forms, or the never-receive rule for
signing keys and substrate tokens.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "grokbot" / "skills" / "swarm-cloud-dispatch" / "SKILL.md"

# Named only so the skill cannot list them as allowed actions. The skill must
# not contain these tokens at all: a prohibition that spells them would still
# put the runner and the permission-bypass modes in the procedure text.
FORBIDDEN_ACTIONS = (
    "swarm_run.py",
    "autonomous_run.py",
    "yolo",
    "acceptEdits",
    "bypassPermissions",
)

# Each phrase is absent from the pre-change skill, so this module fails there.
REQUIRED = (
    "--graph-id",
    "@greptileai",
    "20 minutes",
    "cap 5",
    "bot-0N-<seat>/<slug>",
    "grokbot/<slug>",
    "PR body",
    "receipt",
    "keyless",
    "advisory-complete",
    "IN_REVIEW",
    "never APPROVED",
    "ultrathink",
    "desk gateway",
    "Ming",
    "no-merge",
    "SWARM_SIGNING_KEY",
    "SWARM_ED25519_KEY",
    "SUBSTRATE_TOKEN",
)

# An instruction to place a key or token into the cloud session. Naming the
# variables in a never-receive rule is required and does not match.
SECRET_IMPERATIVE = re.compile(
    r"(?i)\b(set|export|configure|store|provide|add|put|commit)\s+[\"'`]?"
    r"(SWARM_SIGNING_KEY|SWARM_ED25519_KEY|SUBSTRATE_TOKEN(?:_\w+)?)"
)


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _front_matter(text: str) -> str:
    assert text.startswith("---\n"), "skill must open with front matter"
    parts = text.split("---", 2)
    assert len(parts) == 3 and parts[2].strip(), "front matter must wrap a body"
    return parts[1]


def test_front_matter_names_the_skill():
    fm = _front_matter(_text())
    assert re.search(r"(?m)^name:\s*swarm-cloud-dispatch\s*$", fm)
    quoted = re.search(r'(?m)^description:\s*"(.*)"\s*$', fm)
    assert quoted and quoted.group(1).strip(), "description must be a non-empty quoted string"
    assert "keyless" in quoted.group(1).lower()


def test_no_forbidden_runner_or_permission_bypass():
    text = _text()
    for token in FORBIDDEN_ACTIONS:
        assert token not in text, token


def test_no_instruction_to_set_cloud_secrets():
    text = _text()
    assert SECRET_IMPERATIVE.search(text) is None
    lowered = text.lower()
    assert "as a cloud secret" not in lowered
    assert "as cloud secrets" not in lowered
    # Stale box-copy instructions that must not re-enter the repo source.
    assert "#71" not in text
    assert "static template" not in lowered
    assert "static prompt" not in lowered


def test_names_keyless_dispatch_contract():
    text = _text()
    missing = [phrase for phrase in REQUIRED if phrase not in text]
    assert not missing, missing
    assert re.search(r"(?i)never receives?\b", text)
    assert re.search(r"(?i)graph id", text)
