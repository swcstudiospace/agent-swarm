"""Grok Bot seat map and installer.

The schema, the 15-role coverage, android/ios/desktop routing, deterministic
output, --check drift, and the installer's dry-run, check, stamp, hash,
symlink refusal and rollback are pinned here. The hand-written
swarm-cloud-dispatch skill is not a generated lane.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_agents  # noqa: E402
from swarm.manifest import load_manifest  # noqa: E402

import _install_grokbot as grok_install  # noqa: E402

SEAT_MAP = ROOT / "grokbot" / "swarm" / "seat-map.json"
DISPATCH = ROOT / "grokbot" / "skills" / "swarm-cloud-dispatch" / "SKILL.md"
HOMES = {
    "desk-lead",
    "bot-01-systems-backend",
    "bot-02-web-edge",
    "bot-03-android",
    "bot-04-ios",
    "bot-05-infrastructure",
    "bot-06-quality-security",
    "executor",
    "routine",
    "cloud",
}
ROLE_KEYS = {
    "autonomy_ceiling",
    "code",
    "home",
    "id",
    "lane",
    "path_globs",
    "seat",
    "slug",
    "verification_tools",
}


def _agents() -> list[dict]:
    return list(load_manifest())


def _rendered() -> dict[str, str]:
    assert hasattr(build_agents, "render_grokbot"), "render_grokbot is missing"
    return build_agents.render_grokbot(_agents())


def _map() -> dict:
    files = _rendered()
    assert "grokbot/swarm/seat-map.json" in files
    return json.loads(files["grokbot/swarm/seat-map.json"])


def _role(doc: dict, agent_id: str) -> dict:
    found = [r for r in doc["roles"] if r["id"] == agent_id]
    assert len(found) == 1, agent_id
    return found[0]


def _route(doc: dict, kind: str) -> dict:
    found = [r for r in doc["routing"] if r["kind"] == kind]
    assert len(found) == 1, kind
    return found[0]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def _run_build(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    return subprocess.run(
        [sys.executable, str(cwd / "scripts" / "build_agents.py"), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


def test_seat_map_schema_covers_exactly_the_fifteen_roles():
    agents = _agents()
    doc = _map()
    assert doc["schema"] == "grokbot.seat-map.v1"
    assert doc["precedence"] == build_agents.SEAT_MAP_PRECEDENCE
    assert "equal length: lowest role id" in doc["precedence"]
    assert {a["id"] for a in agents} == {f"A{n:02d}" for n in range(1, 16)}
    assert [r["slug"] for r in doc["roles"]] == sorted(a["slug"] for a in agents)
    assert len(doc["roles"]) == 15
    assert all(not r["id"].startswith("A16") and r["slug"] != "a16" for r in doc["roles"])
    by_id = {a["id"]: a for a in agents}
    for role in doc["roles"]:
        assert set(role) == ROLE_KEYS
        agent = by_id[role["id"]]
        assert role["slug"] == agent["slug"]
        assert role["code"] == agent["code"]
        assert role["lane"] == agent["lane"]
        assert role["home"] in HOMES
        assert role["home"] not in {"bot-03-android", "bot-04-ios"}
        assert role["seat"].startswith("bot-0")
        assert role["autonomy_ceiling"] == agent["autonomy_ceiling"]
        assert role["path_globs"] and role["path_globs"] == sorted(set(role["path_globs"]))
        assert role["verification_tools"] and role["verification_tools"] == sorted(role["verification_tools"])
        assert "android/**" not in role["path_globs"]
        assert "ios/**" not in role["path_globs"]
        assert "desktop/**" not in role["path_globs"]
    homes = {r["home"] for r in doc["roles"]}
    assert {"desk-lead", "executor", "routine", "cloud"} <= homes
    assert any(h.startswith("bot-0") for h in homes)


def test_role_homes_globs_and_verification_follow_the_desk_seats():
    doc = _map()
    orch = _role(doc, "A01")
    assert orch["home"] == "desk-lead" and orch["seat"] == "bot-00-programming-lead"
    assert "grokbot/**" in orch["path_globs"]
    assert "scripts/swarm_run.py" in orch["path_globs"]
    assert "scripts/ts/swarm_run.ts" in orch["path_globs"]
    assert orch["verification_tools"] == ["desk_receipt_check"]

    req = _role(doc, "A02")
    assert req["home"] == "executor" and req["seat"] == "bot-00-programming-lead"

    arch = _role(doc, "A04")
    assert arch["home"] == "bot-01-systems-backend"
    assert "design/**" in arch["path_globs"] and "docs/design/**" in arch["path_globs"]

    backend = _role(doc, "A05")
    assert backend["home"] == "bot-01-systems-backend"
    assert "services/**" in backend["path_globs"] and "**/*.py" in backend["path_globs"]
    assert backend["verification_tools"] == ["cargo", "pytest", "ruff"]

    front = _role(doc, "A06")
    assert front["home"] == "bot-02-web-edge" and "web/**" in front["path_globs"]
    assert front["verification_tools"] == ["bun", "deno"]

    data = _role(doc, "A07")
    assert "migrations/**" in data["path_globs"]

    quality = _role(doc, "A08")
    assert quality["seat"] == "bot-06-quality-security" and "ci/gates/**" in quality["path_globs"]
    assert quality["verification_tools"] == ["greptile", "pytest", "ruff"]

    release = _role(doc, "A12")
    assert release["home"] == "cloud" and release["seat"] == "bot-05-infrastructure"
    assert "deploy/**" in release["path_globs"]
    assert release["verification_tools"] == ["helm", "terraform"]

    maint = _role(doc, "A14")
    assert maint["home"] == "routine" and maint["seat"] == "bot-01-systems-backend"

    docs = _role(doc, "A15")
    assert docs["home"] == "executor" and docs["seat"] == "bot-06-quality-security"
    assert "docs/**" in docs["path_globs"] and "README.md" in docs["path_globs"]


def test_android_ios_and_desktop_route_to_their_seats():
    doc = _map()
    assert [r["kind"] for r in doc["routing"]] == ["android", "desktop", "ios"]
    android = _route(doc, "android")
    assert android["seat"] == "bot-03-android"
    assert "android/**" in android["path_globs"] and "**/*.kt" in android["path_globs"]
    assert android["verification_tools"] == ["gradle"]
    ios = _route(doc, "ios")
    assert ios["seat"] == "bot-04-ios"
    assert "ios/**" in ios["path_globs"] and "**/*.swift" in ios["path_globs"]
    assert ios["verification_tools"] == ["xcodebuild"]
    desktop = _route(doc, "desktop")
    assert desktop["seat"] == "bot-02-web-edge"
    assert {"desktop/**", "electron/**", "tauri/**"} <= set(desktop["path_globs"])
    assert desktop["verification_tools"] == ["bun", "deno"]
    for route in doc["routing"]:
        assert set(route) == {"kind", "path_globs", "seat", "verification_tools"}
        assert route["path_globs"] == sorted(set(route["path_globs"]))


def test_render_is_deterministic_sorted_and_leaves_dispatch_and_grok_alone():
    first = _rendered()
    second = _rendered()
    assert first == second
    raw = first["grokbot/swarm/seat-map.json"]
    assert raw == json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n"
    assert SEAT_MAP.read_text(encoding="utf-8") == raw
    lanes = {a["lane"] for a in _agents()}
    skill_paths = {p for p in first if p.startswith("grokbot/skills/")}
    assert skill_paths == {f"grokbot/skills/swarm-{lane}/SKILL.md" for lane in lanes}
    assert "grokbot/skills/swarm-cloud-dispatch/SKILL.md" not in first
    assert all(not p.startswith(".grok/") for p in first)
    for lane in sorted(lanes):
        text = first[f"grokbot/skills/swarm-{lane}/SKILL.md"]
        assert f"name: swarm-{lane}\n" in text
        assert "disable-model-invocation: false" in text
        assert "## When to Use" in text and "## Procedure" in text
        assert 'python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json' in text
        assert 'bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json' in text
        assert "python3 scripts/<script>.py" not in text
        for agent in _agents():
            if agent["lane"] == lane:
                assert agent["slug"] in text
                assert f"$SWARM_ROOT/{agent['prompt']}" in text
    control = first["grokbot/skills/swarm-control/SKILL.md"]
    assert "swarm_run.py" not in control
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--", "grokbot/skills/swarm-cloud-dispatch/SKILL.md"],
        cwd=ROOT, capture_output=True,
    )
    assert diff.returncode == 0
    assert DISPATCH.is_file()


def test_build_agents_check_covers_the_seat_map(tree):
    """--check is clean on a copy that includes grokbot/, and a mutated seat map is stale."""
    clean = _run_build("--check", cwd=tree)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert clean.stdout.startswith("up-to-date")
    target = tree / "grokbot" / "swarm" / "seat-map.json"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n", encoding="utf-8")
    drifted = _run_build("--check", cwd=tree)
    assert drifted.returncode == 1
    assert "stale:" in drifted.stdout and "grokbot/swarm/seat-map.json" in drifted.stdout
    assert target.read_text(encoding="utf-8") == original + "\n"


def test_unknown_lane_skill_is_pruned_and_dispatch_is_not(tree):
    orphan = tree / "grokbot" / "skills" / "swarm-extra" / "SKILL.md"
    orphan.parent.mkdir()
    orphan.write_text("orphan\n", encoding="utf-8")
    dispatch = (tree / "grokbot" / "skills" / "swarm-cloud-dispatch" / "SKILL.md").read_bytes()
    grok_before = _tree_bytes(tree / ".grok")
    flagged = _run_build("--check", cwd=tree)
    assert flagged.returncode == 1
    assert "grokbot/skills/swarm-extra/SKILL.md" in flagged.stdout
    assert orphan.is_file()
    wrote = _run_build(cwd=tree)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr
    assert not orphan.exists()
    assert (tree / "grokbot" / "skills" / "swarm-cloud-dispatch" / "SKILL.md").read_bytes() == dispatch
    assert _tree_bytes(tree / ".grok") == grok_before
    assert _run_build("--check", cwd=tree).returncode == 0


def _snapshot(root: Path) -> dict[str, bytes]:
    base = root / "grokbot"
    if not base.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in base.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def test_installer_dry_run_check_stamp_and_hash(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "keep.txt").write_text("foreign\n", encoding="utf-8")
    before = _snapshot(target)
    out, err = StringIO(), StringIO()
    assert grok_install.install_grokbot(target, dry_run=True, out=out, err=err) == 0
    listed = [ln for ln in out.getvalue().splitlines() if ln and not ln.startswith("remove:")]
    assert "grokbot/swarm/seat-map.json" in listed
    assert "grokbot/skills/swarm-cloud-dispatch/SKILL.md" in listed
    assert "grokbot/skills/swarm-code/SKILL.md" in listed
    assert "grokbot/.agent-swarm-grokbot.json" in listed
    assert _snapshot(target) == before
    assert (target / "keep.txt").read_text(encoding="utf-8") == "foreign\n"

    assert grok_install.install_grokbot(target) == 0
    stamp_path = target / grok_install.STAMP_REL
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp["installer"] == grok_install.STAMP_INSTALLER
    assert set(stamp["files"]) == set(grok_install.export_sources())
    for rel, digest in stamp["files"].items():
        assert digest == _sha((target / rel).read_bytes())
        assert (target / rel).read_bytes() == (ROOT / rel).read_bytes()
    out, err = StringIO(), StringIO()
    assert grok_install.install_grokbot(target, check=True, out=out, err=err) == 0
    assert out.getvalue().startswith("up-to-date")
    assert _snapshot(target)  # the install wrote the tree
    # a second install writes nothing new
    again = _snapshot(target)
    assert grok_install.install_grokbot(target) == 0
    assert _snapshot(target) == again


def test_installer_refuses_a_file_it_did_not_write_and_keeps_foreign_files(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    stray = target / "grokbot" / "notes.txt"
    stray.parent.mkdir()
    stray.write_text("leave me\n", encoding="utf-8")
    local = target / "grokbot" / "swarm" / "seat-map.json"
    local.parent.mkdir()
    local.write_text("{}\n", encoding="utf-8")
    err = StringIO()
    assert grok_install.install_grokbot(target, err=err) == 2
    assert "Nothing was written" in err.getvalue()
    assert local.read_text(encoding="utf-8") == "{}\n"
    assert stray.read_text(encoding="utf-8") == "leave me\n"
    assert not (target / grok_install.STAMP_REL).exists()

    assert grok_install.install_grokbot(target) == 2  # still refused; the local file is untouched
    local.unlink()
    assert grok_install.install_grokbot(target) == 0
    assert stray.read_text(encoding="utf-8") == "leave me\n"
    recorded = json.loads((target / grok_install.STAMP_REL).read_text(encoding="utf-8"))["files"]
    assert "grokbot/notes.txt" not in recorded


def test_installer_refuses_symlinked_parents_and_destinations(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    err = StringIO()
    assert grok_install.install_grokbot(link, err=err) == 2
    assert "symlink" in err.getvalue()
    assert list(real.iterdir()) == []

    child_parent = tmp_path / "via"
    child_parent.symlink_to(real, target_is_directory=True)
    child = child_parent / "repo"
    child.mkdir()
    err = StringIO()
    assert grok_install.install_grokbot(child, err=err) == 2
    assert "symlink" in err.getvalue()

    target = tmp_path / "repo"
    target.mkdir()
    assert grok_install.install_grokbot(target) == 0
    dest = target / "grokbot" / "swarm" / "seat-map.json"
    dest.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    dest.symlink_to(outside)
    err = StringIO()
    assert grok_install.install_grokbot(target, err=err) == 2
    assert "symlink" in err.getvalue()
    assert outside.read_text(encoding="utf-8") == "{}\n"


def test_installer_refuses_the_checkout_and_combined_flags(tmp_path):
    err = StringIO()
    assert grok_install.install_grokbot(ROOT, err=err) == 2
    assert "agent-swarm checkout" in err.getvalue()
    err = StringIO()
    assert grok_install.install_grokbot(tmp_path, dry_run=True, check=True, err=err) == 2


def test_failed_install_restores_via_the_staged_rollback(tmp_path, monkeypatch):
    target = tmp_path / "repo"
    target.mkdir()
    assert grok_install.install_grokbot(target) == 0
    before = _snapshot(target)
    source = tmp_path / "src"
    source.mkdir()
    for rel, text in grok_install.export_sources().items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            doc = json.loads(text)
            doc["note"] = "regenerated"
            path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            path.write_text(text + "\n# regenerated\n", encoding="utf-8")

    state = {"n": 0, "armed": True}
    real = Path.write_text

    def wrapped(self, data, *args, **kwargs):
        if state["armed"]:
            state["n"] += 1
            if state["n"] == 2:
                raise OSError("injected write failure")
        return real(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", wrapped)
    err = StringIO()
    rc = grok_install.install_grokbot(target, source=source, err=err)
    assert rc == 2
    assert "Traceback" not in err.getvalue()
    assert _snapshot(target) == before

    state["armed"] = False
    assert grok_install.install_grokbot(target, source=source) == 0
    out = StringIO()
    assert grok_install.install_grokbot(target, source=source, check=True, out=out) == 0
    assert out.getvalue().startswith("up-to-date")
    sample = target / "grokbot" / "skills" / "swarm-code" / "SKILL.md"
    assert sample.read_text(encoding="utf-8").endswith("# regenerated\n")
    seat = json.loads((target / "grokbot" / "swarm" / "seat-map.json").read_text(encoding="utf-8"))
    assert seat["note"] == "regenerated"


def test_non_utf8_edit_is_not_treated_as_ours(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    assert grok_install.install_grokbot(target) == 0
    dest = target / "grokbot" / "skills" / "swarm-code" / "SKILL.md"
    dest.write_bytes(b"\xff\xfe local")
    err = StringIO()
    assert grok_install.install_grokbot(target, err=err) == 2
    assert dest.read_bytes() == b"\xff\xfe local"
    assert "Nothing was written" in err.getvalue()


def test_render_grokbot_rejects_a_sixteenth_role():
    agents = _agents()
    extra = dict(agents[0])
    extra["id"] = "A16"
    extra["slug"] = "a16-extra"
    extra["code"] = "EXTRA"
    with pytest.raises(ValueError, match="15"):
        build_agents.render_grokbot(agents + [extra])


def test_equal_length_shared_scripts_resolve_to_one_seat():
    doc = _map()
    for agent_id in ("A05", "A06"):
        globs = set(_role(doc, agent_id)["path_globs"])
        assert "scripts/code_checks.py" in globs
        assert "scripts/ts/code_checks.ts" in globs
    for path in ("scripts/code_checks.py", "scripts/ts/code_checks.ts"):
        got = build_agents.resolve_owner(doc, path)
        assert got["via"] == "role"
        assert got["role_id"] == "A05"
        assert got["seat"] == "bot-01-systems-backend"
        assert got["glob"] == path
    runner = build_agents.resolve_owner(doc, "scripts/swarm_run.py")
    assert runner["role_id"] == "A01" and runner["seat"] == "bot-00-programming-lead"
    twin = build_agents.resolve_owner(doc, "scripts/ts/swarm_run.ts")
    assert twin["role_id"] == "A01" and twin["glob"] == "scripts/ts/swarm_run.ts"
    lead_doc = build_agents.resolve_owner(doc, "docs/desk-operating-model.md")
    assert lead_doc["role_id"] == "A01"
    nested = build_agents.resolve_owner(doc, "docs/other/page.md")
    assert nested["role_id"] == "A15"


def test_routing_wins_over_a_longer_role_glob():
    doc = {
        "roles": [{
            "id": "A06",
            "seat": "bot-02-web-edge",
            "path_globs": ["android/app/special/file.kt"],
        }],
        "routing": [{
            "kind": "android",
            "seat": "bot-03-android",
            "path_globs": ["android/**"],
        }],
    }
    got = build_agents.resolve_owner(doc, "android/app/special/file.kt")
    assert len("android/app/special/file.kt") > len("android/**")
    assert got["via"] == "routing" and got["seat"] == "bot-03-android" and got["kind"] == "android"
    live = _map()
    desktop = build_agents.resolve_owner(live, "desktop/shell/main.ts")
    assert desktop["via"] == "routing" and desktop["seat"] == "bot-02-web-edge"
    ios = build_agents.resolve_owner(live, "ios/App/App.swift")
    assert ios["seat"] == "bot-04-ios"


def test_only_leaves_unselected_lanes_untouched(tree):
    code = tree / "grokbot" / "skills" / "swarm-code" / "SKILL.md"
    control = tree / "grokbot" / "skills" / "swarm-control" / "SKILL.md"
    original_code = code.read_bytes()
    original_control = control.read_bytes()
    code.write_bytes(original_code + b"\n# drifted\n")
    checked = _run_build("--check", "--only", "a01-orchestrator", cwd=tree)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "swarm-code" not in checked.stdout
    assert code.read_bytes() == original_code + b"\n# drifted\n"
    wrote = _run_build("--only", "a01-orchestrator", cwd=tree)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr
    assert code.read_bytes() == original_code + b"\n# drifted\n"
    assert control.read_bytes() == original_control
    control.write_bytes(original_control + b"\n")
    drifted = _run_build("--check", "--only", "a01-orchestrator", cwd=tree)
    assert drifted.returncode == 1
    assert "grokbot/skills/swarm-control/SKILL.md" in drifted.stdout
    assert code.read_bytes() == original_code + b"\n# drifted\n"
    restored = _run_build("--only", "a05-backend", cwd=tree)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert code.read_bytes() == original_code
    assert control.read_bytes() == original_control + b"\n"


def test_rejected_roster_writes_nothing(tree):
    roots = (".claude", ".grok", ".cursor", "omp", "grokbot")
    before = {name: _tree_bytes(tree / name) for name in roots}
    manifest = json.loads((tree / "agents.json").read_text(encoding="utf-8"))
    extra = dict(manifest["agents"][0])
    extra["id"] = "A16"
    extra["slug"] = "a16-extra"
    extra["code"] = "EXTRA"
    manifest["agents"].append(extra)
    agents_file = tree / "agents-extra.json"
    agents_file.write_text(json.dumps(manifest), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "SWARM_AGENTS_FILE"}
    env["SWARM_AGENTS_FILE"] = str(agents_file)
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "build_agents.py")],
        cwd=tree, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 1
    assert "15" in proc.stderr
    assert "Traceback" not in proc.stderr
    after = {name: _tree_bytes(tree / name) for name in roots}
    assert after == before


def test_installer_refuses_a_source_missing_a_declared_lane(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    assert grok_install.install_grokbot(target) == 0
    before = _snapshot(target)
    source = tmp_path / "src"
    for rel, text in grok_install.export_sources().items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    missing = source / "grokbot" / "skills" / "swarm-code" / "SKILL.md"
    missing.unlink()
    err = StringIO()
    assert grok_install.install_grokbot(target, source=source, err=err) == 2
    assert "swarm-code/SKILL.md" in err.getvalue()
    assert "Nothing was written" in err.getvalue()
    assert _snapshot(target) == before
