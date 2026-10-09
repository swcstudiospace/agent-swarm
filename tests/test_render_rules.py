"""One shared rule block in every render, and the A01-complete Stop hook is opt-in.

Every generated agent (Claude, Grok, omp, Cursor) carries the identical SHARED_RULES block (merge, branch, keyless
advisory, no unattended runner or bypass mode), and the grants the shared prompts still make (auto-merge, `auto-fix/*`,
the old branch names) are rewritten in every render, not only in Cursor's. The workspace installer no longer registers
hooks/on_a01_complete.py as a Stop hook unless --with-a01-complete-hook is given.

The strings below are written out literally (they are the pre-change grants), not read back from the code under test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from install_helpers import _ENV_KEYS
from swarm.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_agents  # noqa: E402

RENDERS = {
    "claude": ROOT / ".claude" / "agents",
    "grok": ROOT / ".grok" / "agents",
    "omp": ROOT / "omp" / "agents",
    "cursor": ROOT / ".cursor" / "agents",
}
AGENTS = load_manifest()
CASES = [(render, a["slug"]) for render in RENDERS for a in AGENTS]
IDS = [f"{render}-{slug}" for render, slug in CASES]
SHARED_AGENTS = {"A05", "A06", "A09", "A14"}

# Pre-change text of the shared prompts' grants; none may survive in any render.
GRANTS = (
    "auto-mergeable",
    "merge semver-compatible bumps behind green gates",
    "branches that the producer still merges",
    "PR on branch `swarm/<task_id>`",
    '"branch": "swarm/T-884"',
    '"branch": "swarm/T-902"',
    "auto-fix/",
)
# Words that only the shared block may mention (as prohibitions).
PROHIBITED_WORDS = ("bypassPermissions", "--dangerously-skip-permissions", "yolo", "auto-merge")


def _text(render: str, slug: str) -> str:
    return (RENDERS[render] / f"{slug}.md").read_text(encoding="utf-8")


def test_there_are_fifteen_agents_in_each_of_the_four_renders():
    assert len(AGENTS) == 15
    for render, directory in RENDERS.items():
        assert {p.name for p in directory.glob("*.md")} == {f"{a['slug']}.md" for a in AGENTS}, render


def test_the_shared_block_states_each_rule():
    block = build_agents.SHARED_RULES
    for needle in ("Never merge a pull request", "open a draft PR and report instead", "<seat-prefix>/<task_id>",
                   "bot-0N-<seat>/<task_id>", "ownership.yaml", "^bot-0[0-6]-[a-z0-9-]+$", "nothing counts as APPROVED",
                   "advisory, never as a pass", "scripts/swarm_run.py", "hooks/autonomous_run.py", "bypassPermissions",
                   "--dangerously-skip-permissions", "--yolo"):
        assert needle in block, needle
    assert "swarm/<task_id>" not in block  # tests/test_cursor_agents.py forbids the literal in Cursor output


@pytest.mark.parametrize("render,slug", CASES, ids=IDS)
def test_every_render_embeds_the_shared_block_once(render, slug):
    assert _text(render, slug).count(build_agents.SHARED_RULES) == 1


@pytest.mark.parametrize("render,slug", CASES, ids=IDS)
def test_no_pre_change_grant_survives(render, slug):
    text = _text(render, slug)
    for grant in GRANTS:
        assert grant not in text, (render, slug, grant)


@pytest.mark.parametrize("render,slug", CASES, ids=IDS)
def test_prohibited_words_appear_only_inside_the_shared_block(render, slug):
    rest = _text(render, slug).replace(build_agents.SHARED_RULES, "")
    for word in PROHIBITED_WORDS:
        assert word not in rest, (render, slug, word)


@pytest.mark.parametrize("render", ["claude", "grok", "omp"])
def test_body_gets_the_shared_substitutions_and_nothing_else(render):
    for agent in AGENTS:
        prompt = (ROOT / agent["prompt"]).read_text(encoding="utf-8").strip()
        body = build_agents.apply_shared_substitutions(agent["id"], prompt)
        assert _text(render, agent["slug"]).rstrip().endswith(body), (render, agent["id"])
        assert (body != prompt) == (agent["id"] in SHARED_AGENTS), agent["id"]


@pytest.mark.parametrize("render", ["claude", "grok", "omp"])
def test_a01_keeps_the_operators_unattended_mode(render):
    # Only the shared rule limits who may start the runner; A01's own description of it is not rewritten.
    text = _text(render, "a01-orchestrator")
    assert "python3 scripts/swarm_run.py" in text.replace(build_agents.SHARED_RULES, "")


def test_cursor_keeps_its_cursor_only_rewrites_and_the_shared_ones():
    assert set(build_agents.CURSOR_BODY_SUBSTITUTIONS) == {"A01", "A05", "A06", "A09", "A10", "A14"}
    assert set(build_agents.SHARED_BODY_SUBSTITUTIONS) == SHARED_AGENTS
    for agent_id, pairs in build_agents.CURSOR_BODY_SUBSTITUTIONS.items():
        shared = build_agents.SHARED_BODY_SUBSTITUTIONS.get(agent_id, ())
        assert pairs[: len(shared)] == shared, agent_id  # shared rewrites first, then the Cursor-only ones
    for pairs in build_agents.SHARED_BODY_SUBSTITUTIONS.values():
        assert all("Cursor" not in new and "Desk" not in new for _, new in pairs)  # runtime- and desk-neutral wording


def test_a_missing_or_duplicated_shared_pattern_fails_loudly():
    pairs = build_agents.SHARED_BODY_SUBSTITUTIONS["A14"]
    whole = "\n".join(old for old, _ in pairs)  # A14 has two grants; each must occur exactly once
    out = build_agents.apply_shared_substitutions("A14", whole)
    assert all(old not in out for old, _ in pairs) and all(new in out for _, new in pairs)
    with pytest.raises(ValueError, match="found 0 times"):
        build_agents.apply_shared_substitutions("A14", "a body that no longer holds the grant")
    with pytest.raises(ValueError, match="found 2 times"):
        build_agents.apply_shared_substitutions("A14", f"{whole}\n{pairs[0][0]}")
    assert build_agents.apply_shared_substitutions("A02", "untouched body") == "untouched body"


# ---------------------------------------------------------------------------------------------------------------------
# the A01-complete Stop hook is opt-in


def test_committed_grok_hook_file_has_no_stop_hook():
    hooks = json.loads((ROOT / ".grok" / "hooks" / "agent-swarm.json").read_text(encoding="utf-8"))["hooks"]
    assert "UserPromptSubmit" in hooks and "Stop" not in hooks


def _install(tree: Path, ws: Path, home: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env["HOME"] = str(home)
    cmd = [sys.executable, str(tree / "scripts" / "build_agents.py"), "--install-workspace", str(ws), "--no-substrate", *args]
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, env=env)


def _claude(ws: Path) -> dict:
    return json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _grok(ws: Path) -> dict:
    return json.loads((ws / ".grok" / "hooks" / "agent-swarm.json").read_text(encoding="utf-8"))


def _stop(doc: dict) -> list[str]:
    return [h["command"] for entry in doc["hooks"].get("Stop", []) for h in entry["hooks"]]


def _seed(ws: Path, stop: list) -> None:
    for path in (ws / ".claude" / "settings.json", ws / ".grok" / "hooks" / "agent-swarm.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hooks": {"Stop": stop}}), encoding="utf-8")


def test_default_install_registers_only_user_prompt_submit(tree, ws, home):
    r = _install(tree, ws, home)
    assert r.returncode == 0, r.stdout + r.stderr
    for doc in (_claude(ws), _grok(ws)):
        assert "Stop" not in doc["hooks"]
        assert doc["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == f"python3 {tree / 'hooks' / 'user_prompt_submit.py'}"


def test_flag_registers_the_stop_hook_once_even_when_run_twice(tree, ws, home):
    for _ in range(2):
        r = _install(tree, ws, home, "--with-a01-complete-hook")
        assert r.returncode == 0, r.stdout + r.stderr
    expected = [f"python3 {tree / 'hooks' / 'on_a01_complete.py'}"]
    assert _stop(_claude(ws)) == expected and _stop(_grok(ws)) == expected


def test_reinstall_without_the_flag_removes_only_our_hook(tree, ws, home):
    keep = {"hooks": [{"type": "command", "command": "echo keep"}]}
    mixed = {"hooks": [{"type": "command", "command": f"python3 {tree / 'hooks' / 'on_a01_complete.py'}"},
                       {"type": "command", "command": "echo also-keep"}]}
    _seed(ws, [keep, mixed])
    assert _install(tree, ws, home, "--with-a01-complete-hook").returncode == 0
    assert _stop(_claude(ws)).count(f"python3 {tree / 'hooks' / 'on_a01_complete.py'}") == 1
    r = _install(tree, ws, home)
    assert r.returncode == 0, r.stdout + r.stderr
    for doc in (_claude(ws), _grok(ws)):
        assert _stop(doc) == ["echo keep", "echo also-keep"]


def test_reinstall_without_the_flag_drops_an_empty_stop_key(tree, ws, home):
    assert _install(tree, ws, home, "--with-a01-complete-hook").returncode == 0
    assert _install(tree, ws, home).returncode == 0
    assert "Stop" not in _claude(ws)["hooks"] and "Stop" not in _grok(ws)["hooks"]


@pytest.mark.parametrize("command, match", [
    ("python3 /old/checkout/hooks/on_a01_complete.py", True),
    ('python3 "/old checkout/hooks/on_a01_complete.py"', True),
    ("python3 '/old checkout/hooks/on_a01_complete.py'", True),
    (r"python3 /old\ checkout/hooks/on_a01_complete.py", True),
    ("python3 hooks/on_a01_complete.py --json", True),
    ("python3 check_on_a01_complete.py", False),
    ("python3 /tmp/hooks/check_on_a01_complete.py", False),
    ("python3 /tmp/on_a01_complete.py", False),
    ("python3 /tmp/myhooks/on_a01_complete.py", False),
    ('python3 "/old checkout/hooks/on_a01_complete.py', False),  # unmatched quote: do not guess
])
def test_a01_complete_match_parses_quoting_and_requires_the_hooks_path(command, match):
    assert build_agents._is_a01_complete({"type": "command", "command": command}) is match


def test_a_quoted_registration_is_replaced_and_a_lookalike_stays(tree, ws, home):
    quoted = 'python3 "/old checkout/hooks/on_a01_complete.py"'
    lookalike = "python3 /tmp/hooks/check_on_a01_complete.py"
    _seed(ws, [{"hooks": [
        {"type": "command", "command": quoted},
        {"type": "command", "command": lookalike},
    ]}])
    assert _install(tree, ws, home, "--with-a01-complete-hook").returncode == 0
    expected = [lookalike, f"python3 {tree / 'hooks' / 'on_a01_complete.py'}"]
    assert _stop(_claude(ws)) == expected and _stop(_grok(ws)) == expected
    assert _install(tree, ws, home).returncode == 0
    assert _stop(_claude(ws)) == [lookalike] and _stop(_grok(ws)) == [lookalike]


def test_the_flag_moves_the_registration_to_this_checkouts_path(tree, ws, home):
    _seed(ws, [{"hooks": [{"type": "command", "command": "python3 /old/checkout/hooks/on_a01_complete.py"}]}])
    assert _install(tree, ws, home, "--with-a01-complete-hook").returncode == 0
    assert _stop(_claude(ws)) == [f"python3 {tree / 'hooks' / 'on_a01_complete.py'}"]


def test_the_flag_needs_install_workspace(tmp_path):
    for args in (["--with-a01-complete-hook"], ["--install-cursor", str(tmp_path), "--with-a01-complete-hook"]):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_agents.py"), *args], capture_output=True, text=True,
                           cwd=ROOT)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "--with-a01-complete-hook" in r.stderr


@pytest.mark.parametrize("flag", [False, True])
def test_dry_run_states_the_hook_choice_and_writes_nothing(tree, ws, home, flag):
    r = _install(tree, ws, home, "--dry-run", *(["--with-a01-complete-hook"] if flag else []))
    assert r.returncode == 0, r.stdout + r.stderr
    assert ("would set the Stop (a01-complete) hook" in r.stdout) is flag
    assert ("--with-a01-complete-hook opts in" in r.stdout) is not flag
    assert list(ws.iterdir()) == []


@pytest.mark.parametrize("value", [None, "text", {"not": "a list"}])
def test_a_malformed_stop_value_is_left_alone_unless_the_flag_replaces_it(value):
    hooks = {"Stop": value} if value is not None else {}
    build_agents._set_a01_complete_hook(hooks, "python3 /x/hooks/on_a01_complete.py", False)
    assert hooks == ({"Stop": value} if value is not None else {})
    build_agents._set_a01_complete_hook(hooks, "python3 /x/hooks/on_a01_complete.py", True)
    assert hooks["Stop"] == [{"hooks": [{"type": "command", "command": "python3 /x/hooks/on_a01_complete.py"}]}]


def test_entries_that_are_not_ours_or_not_understood_are_preserved_verbatim():
    odd = ["a string", {"matcher": "x"}, {"hooks": "nope"}, {"hooks": [{"type": "command", "command": "echo other"}]}]
    hooks = {"Stop": list(odd)}
    build_agents._set_a01_complete_hook(hooks, "python3 /x/hooks/on_a01_complete.py", False)
    assert hooks["Stop"] == odd
