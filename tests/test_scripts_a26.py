"""Behaviour tests for scripts/sol_contract_lint.py (A26): the real-mode scan over fixture trees, the dry-run contract,
the bounds (size, file count, symlinks, binaries), the optional-tool report and the per-rule heuristics."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_script

ROOT = Path(__file__).resolve().parent.parent
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
HAS_BUN = Path(BUN).exists()

VAULT = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Vault {
    address public owner;
    uint256 public total;

    function setTotal(uint256 v) external {
        total = v;
    }

    function kill() external {
        selfdestruct(payable(msg.sender));
    }

    function pay(address to) external {
        to.call{value: 1}("");
    }

    function guard() external view returns (bool) {
        return tx.origin == owner;
    }
}
"""

CLEAN = """pragma solidity 0.8.28;

contract Clean {
    uint256 public total;
    event TotalSet(uint256 value);

    function setTotal(uint256 v) external {
        total = v;
        emit TotalSet(v);
    }
}
"""


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def lint(tmp_path: Path, *args: str, env: dict | None = None):
    result = run_script("sol_contract_lint.py", "--root", str(tmp_path), "--json", *args, env={"SWARM_DIR": str(tmp_path / ".swarm-state"), **(env or {})})
    return result, json.loads(result.stdout)


def by_rule(data: dict, rule: str) -> list[dict]:
    return [f for f in data["findings"] if f["id"] == rule]


def load_module():
    spec = importlib.util.spec_from_file_location("_sol_lint_under_test", ROOT / "scripts" / "sol_contract_lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- dry run -----------------------------------------------------------------------------------------------------


def test_dry_run_is_canned_json_and_reads_nothing(tmp_path):
    write(tmp_path, "src/Vault.sol", VAULT)
    result = run_script("sol_contract_lint.py", "--dry-run", "--json", "--root", str(tmp_path / "does-not-exist"), env={"SWARM_DIR": str(tmp_path / ".s")})
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["status"] == "ok" and data["dry_run"] is True and data["agent"] == "A26" and data["script"] == "sol_contract_lint"
    assert data["files_scanned"] == 0 and data["findings"] == [] and data["findings_total"] == 0
    assert data["tools"] == {"slither": "skipped:dry-run", "forge": "skipped:dry-run"}
    again = run_script("sol_contract_lint.py", "--dry-run", "--json", env={"SWARM_DIR": str(tmp_path / ".s")})
    assert json.loads(again.stdout) == data  # deterministic, independent of --root
    assert not (tmp_path / ".swarm-state").exists()


# --- real mode ---------------------------------------------------------------------------------------------------


def test_real_scan_reports_rules_with_locations_and_fails_on_major(tmp_path):
    write(tmp_path, "src/Vault.sol", VAULT)
    write(tmp_path, "src/Clean.sol", CLEAN)
    result, data = lint(tmp_path)
    assert result.returncode == 1 and data["status"] == "fail" and data["files_scanned"] == 2
    got = sorted((f["id"], f["severity"], f["location"]) for f in data["findings"])
    assert got == sorted([
        ("SOL-PRAGMA-NOT-PINNED", "minor", "src/Vault.sol:2"),
        ("SOL-SELFDESTRUCT", "major", "src/Vault.sol:13"),
        ("SOL-UNCHECKED-LOW-LEVEL-CALL", "major", "src/Vault.sol:17"),
        ("SOL-TX-ORIGIN", "major", "src/Vault.sol:21"),
        ("SOL-MISSING-EVENT", "minor", "src/Vault.sol:8"),
        ("SOL-MISSING-EVENT", "minor", "src/Vault.sol:12"),
        ("SOL-MISSING-EVENT", "minor", "src/Vault.sol:16"),
    ])
    assert data["counts"] == {"info": 0, "minor": 4, "major": 3, "blocker": 0} and data["findings_total"] == 7
    assert data["summary"] == "2 file(s) scanned, 7 finding(s), 3 major or worse"
    assert [f["location"] for f in data["findings"]] == sorted((f["location"] for f in data["findings"]), key=lambda loc: (loc.rsplit(":", 1)[0], int(loc.rsplit(":", 1)[1])))
    assert data["agent"] == "A26" and data["script"] == "sol_contract_lint"


def test_clean_tree_exits_zero_and_minor_findings_alone_do_not_fail(tmp_path):
    write(tmp_path, "src/Clean.sol", CLEAN)
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["status"] == "ok" and data["findings"] == [] and data["files_scanned"] == 1
    write(tmp_path, "src/Float.sol", CLEAN.replace("0.8.28", "^0.8.20"))
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["status"] == "ok" and [f["id"] for f in data["findings"]] == ["SOL-PRAGMA-NOT-PINNED"]


def test_empty_tree_scans_nothing_and_passes(tmp_path):
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["files_scanned"] == 0 and data["findings"] == []


def test_dependency_build_and_test_directories_and_foundry_files_are_skipped(tmp_path):
    write(tmp_path, "src/Clean.sol", CLEAN)
    for rel in ("lib/forge-std/Bad.sol", "node_modules/pkg/Bad.sol", "out/Bad.sol", "cache/Bad.sol", "artifacts/Bad.sol", ".git/Bad.sol"):
        write(tmp_path, rel, VAULT)
    write(tmp_path, "test/Vault.t.sol", VAULT)
    write(tmp_path, "script/Deploy.s.sol", VAULT)
    write(tmp_path, "src/readme.txt", VAULT)
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["files_scanned"] == 1 and data["findings"] == []
    assert data["skipped"]["test_files"] == 2


def test_path_selects_a_file_or_a_subdirectory(tmp_path):
    write(tmp_path, "src/Vault.sol", VAULT)
    write(tmp_path, "other/Clean.sol", CLEAN)
    result, data = lint(tmp_path, "--path", "other")
    assert result.returncode == 0 and data["files_scanned"] == 1
    result, data = lint(tmp_path, "--path", "src/Vault.sol")
    assert result.returncode == 1 and data["files_scanned"] == 1 and by_rule(data, "SOL-SELFDESTRUCT")[0]["location"] == "src/Vault.sol:13"


def test_path_outside_root_or_missing_is_an_input_error(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    write(tmp_path, "outside/Bad.sol", VAULT)
    for bad in ("../outside", "nope"):
        result = run_script("sol_contract_lint.py", "--root", str(root), "--json", "--path", bad, env={"SWARM_DIR": str(tmp_path / ".s")})
        data = json.loads(result.stdout)
        assert result.returncode == 2 and data["status"] == "error" and data["error"]["code"] == "E-INPUT", bad


def test_output_is_deterministic_and_sorted(tmp_path):
    for name in ("b", "a", "c"):
        write(tmp_path, f"src/{name}/Vault.sol", VAULT)
    _, first = lint(tmp_path)
    _, second = lint(tmp_path)
    assert first == second
    files = [f["location"].rsplit(":", 1)[0] for f in first["findings"]]
    assert files == sorted(files)


# --- bounds ------------------------------------------------------------------------------------------------------


def test_large_binary_and_symlinked_files_are_skipped(tmp_path):
    write(tmp_path, "src/Clean.sol", CLEAN)
    write(tmp_path, "src/Big.sol", VAULT + "// " + "x" * (1 << 20))
    write(tmp_path, "src/Bin.sol", b"contract X { selfdestruct(a); }\x00\x01")
    outside = write(tmp_path.parent / f"{tmp_path.name}-outside", "Evil.sol", VAULT)
    (tmp_path / "src" / "Link.sol").symlink_to(outside)
    (tmp_path / "linkdir").symlink_to(outside.parent, target_is_directory=True)
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["files_scanned"] == 1 and data["findings"] == []
    assert data["skipped"]["too_large"] == 1 and data["skipped"]["binary"] == 1 and data["skipped"]["symlinks"] == 1


def test_file_exactly_at_the_size_limit_is_scanned(tmp_path):
    body = VAULT
    padding = (1 << 20) - len(body.encode()) - 3
    write(tmp_path, "src/Edge.sol", body + "//" + "x" * padding + "\n")
    assert (tmp_path / "src" / "Edge.sol").stat().st_size == 1 << 20
    _, data = lint(tmp_path)
    assert data["files_scanned"] == 1 and data["skipped"]["too_large"] == 0 and by_rule(data, "SOL-SELFDESTRUCT")


def test_scan_stops_at_two_thousand_files(tmp_path):
    for i in range(2001):
        write(tmp_path, f"src/C{i:04d}.sol", "pragma solidity 0.8.28;\n")
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["files_scanned"] == 2000 and data["skipped"]["limit_reached"] is True
    exact = tmp_path / "exact"
    for i in range(1999):
        write(exact, f"C{i:04d}.sol", "pragma solidity 0.8.28;\n")
    _, data = lint(tmp_path, "--path", "exact")
    assert data["files_scanned"] == 1999 and data["skipped"]["limit_reached"] is False


# --- optional tools ----------------------------------------------------------------------------------------------


def fake_binary(directory: Path, name: str, script: str = "true") -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\nexit 99\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_missing_optional_tools_are_reported_as_skipped(tmp_path):
    empty = tmp_path / "bin"
    empty.mkdir()
    write(tmp_path, "src/Clean.sol", CLEAN)
    result, data = lint(tmp_path, env={"PATH": str(empty)})
    assert result.returncode == 0
    assert data["tools"] == {"slither": "skipped:tool-missing", "forge": "skipped:tool-missing"}


def test_present_optional_tools_are_detected_but_never_invoked(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "forge-was-run"
    fake_binary(bin_dir, "forge", f"touch {marker}")
    write(tmp_path, "src/Clean.sol", CLEAN)
    result, data = lint(tmp_path, env={"PATH": str(bin_dir)})
    assert result.returncode == 0
    assert data["tools"] == {"slither": "skipped:tool-missing", "forge": "available:not-invoked"}
    assert not marker.exists()


# --- Bun twin ----------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_bun_twin_matches_the_python_script(tmp_path):
    write(tmp_path, "src/Vault.sol", VAULT)
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".s")}
    twin = subprocess.run([BUN, str(ROOT / "scripts" / "ts" / "sol_contract_lint.ts"), "--root", str(tmp_path), "--json"], cwd=ROOT,
                          capture_output=True, text=True, env=env)
    _, python = lint(tmp_path)
    assert twin.returncode == 1 and json.loads(twin.stdout) == python
    dry = subprocess.run([BUN, str(ROOT / "scripts" / "ts" / "sol_contract_lint.ts"), "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True, env=env)
    assert dry.returncode == 0 and json.loads(dry.stdout)["dry_run"] is True


# --- rules -------------------------------------------------------------------------------------------------------


def findings(text: str, rel: str = "X.sol") -> list[tuple[str, str, int]]:
    return sorted((f["id"], f["severity"], int(f["location"].rsplit(":", 1)[1])) for f in load_module().lint_source(rel, text))


def body(snippet: str) -> str:
    return "pragma solidity 0.8.28;\ncontract C {\n    uint256 x;\n    event E();\n    address owner;\n" + snippet + "\n}\n"


@pytest.mark.parametrize("pragma, expected", [
    ("pragma solidity 0.8.28;", []),
    ("pragma solidity =0.8.28;", []),
    ("pragma solidity ^0.8.20;", [("SOL-PRAGMA-NOT-PINNED", "minor")]),
    ("pragma solidity >=0.8.0 <0.9.0;", [("SOL-PRAGMA-KNOWN-BUG", "minor"), ("SOL-PRAGMA-NOT-PINNED", "minor")]),
    ("pragma solidity 0.8.13;", [("SOL-PRAGMA-KNOWN-BUG", "major")]),
    ("pragma solidity 0.8.2;", [("SOL-PRAGMA-KNOWN-BUG", "major")]),  # KeccakCaching, optimizer assumed on
    ("pragma solidity foo;", [("SOL-PRAGMA-INVALID", "minor")]),
])
def test_pragma_rules(pragma, expected):
    got = [(rule, severity) for rule, severity, _ in findings(pragma + "\ncontract C { }\n")]
    assert sorted(got) == sorted(expected)


def test_missing_pragma_only_matters_when_a_contract_is_declared():
    assert findings("contract C { }\n") == [("SOL-PRAGMA-MISSING", "minor", 1)]
    assert findings("// nothing here\nlibrary L { }\n") == [("SOL-PRAGMA-MISSING", "minor", 1)]
    assert findings("// only a comment\n") == []
    assert findings("pragma abicoder v2;\n") == []


@pytest.mark.parametrize("snippet, severity", [
    ("    function f() external view { require(tx.origin == owner); }", "major"),
    ("    function f() external view { require(owner == tx.origin); }", "major"),
    ("    function f() external view { if (tx.origin != owner) revert(); }", "major"),
    ("    function f() external { emit E(); address o = tx.origin; o; }", "minor"),
])
def test_tx_origin(snippet, severity):
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-TX-ORIGIN"] == [("SOL-TX-ORIGIN", severity)]


def test_selfdestruct_and_suicide_are_major_and_comments_and_strings_are_ignored():
    assert [(r, s, line) for r, s, line in findings(body("    function f() external { emit E(); selfdestruct(payable(owner)); }")) if r == "SOL-SELFDESTRUCT"] == [("SOL-SELFDESTRUCT", "major", 6)]
    assert [r for r, _, _ in findings(body("    function f() external { emit E(); suicide(owner); }"))].count("SOL-SELFDESTRUCT") == 1
    quiet = body('    // selfdestruct(payable(owner));\n    /* tx.origin */\n    string constant S = "selfdestruct( tx.origin t.call(x)";')
    assert findings(quiet) == []


@pytest.mark.parametrize("snippet, flagged", [
    ('    function f(address t) external { emit E(); t.call(""); }', True),
    ('    function f(address t) external { emit E(); t.call{value: 1}(""); }', True),
    ('    function f(address t) external { emit E(); payable(t).send(1); }', True),
    ('    function f(address t, bytes memory d) external { emit E(); t.staticcall(d); }', True),
    ('    function f(address t, bytes memory d) external { emit E(); t.delegatecall(d); }', True),
    ('    function f(address t) external { emit E(); (bool ok, ) = t.call(""); require(ok); }', False),
    ('    function f(address t) external { emit E(); bool ok = payable(t).send(1); require(ok); }', False),
    ('    function f(address t) external { emit E(); require(payable(t).send(1)); }', False),
    ('    function f(address t) external { emit E(); if (!payable(t).send(1)) revert(); }', False),
    ('    function f(address t) external { emit E(); (bool ok, bytes memory r) = t.call{value: 1}(""); r; require(ok); }', False),
    ('    function f(address t, bytes memory d) external onlyOwner { emit E(); (bool ok, ) = t.delegatecall(d); require(ok); }', False),
])
def test_unchecked_low_level_calls(snippet, flagged):
    rules = [r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


@pytest.mark.parametrize("snippet, calls", [
    ('    function f(address a, bool c) external { emit E(); if (c) a.call{value: 1}(""); }', 1),
    ('    function f(address a, bool c) external { emit E(); if (c) { a.call{value: 1}(""); } }', 1),
    ('    function f(address a, bool c) external { emit E(); if (c) if (c) a.call{value: 1}(""); }', 1),
    ('    function f(address a, bool c) external { emit E(); while (c) a.call{value: 1}(""); }', 1),
    ('    function f(address a, bool c) external { emit E(); if (c) a.call{value: 1}(""); else a.call{value: 1}(""); }', 2),
    ('    function f(address a, bool c) external { emit E(); if (c) (bool ok, ) = a.call{value: 1}(""); require(ok); }', 0),
    ('    function f(address a, bool c) external { emit E(); if (c) require(a.call{value: 1}("")); }', 0),
    ('    function f(address a, bool c) external { emit E(); if (a.call{value: 1}("")) emit E(); }', 0),
])
def test_a_bare_if_or_loop_body_call_is_unchecked_but_a_call_in_a_condition_is_not(snippet, calls):
    rules = [r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert len(rules) == calls


@pytest.mark.parametrize("snippet, severity", [
    ('    function f(address t, bytes memory d) external { emit E(); (bool ok, ) = t.delegatecall(d); require(ok); }', "major"),
    ('    function f(address t, bytes memory d) public { emit E(); (bool ok, ) = t.delegatecall(d); require(ok); }', "major"),
    ('    function f(address t, bytes memory d) external onlyOwner { emit E(); (bool ok, ) = t.delegatecall(d); require(ok); }', "minor"),
    ('    function f(address t, bytes memory d) external { require(msg.sender == owner); (bool ok, ) = t.delegatecall(d); require(ok); }', "minor"),
    ('    function f(address t, bytes memory d) internal { (bool ok, ) = t.delegatecall(d); require(ok); }', "minor"),
    ('    function f(address t, bytes memory d) private { (bool ok, ) = t.delegatecall(d); require(ok); }', "minor"),
])
def test_delegatecall_severity_depends_on_exposure_and_guard(snippet, severity):
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-DELEGATECALL"] == [("SOL-DELEGATECALL", severity)]


@pytest.mark.parametrize("snippet, missing", [
    ("    function f() external { x = 1; }", True),
    ("    function f() external { x = 1; emit E(); }", False),
    ("    function f() external { _g(); }\n    function _g() internal { emit E(); }", False),
    ("    function f() external { _g(); }\n    function _g() internal { x = 1; }", True),
    ("    function f() external { _inheritedHook(); }", False),
    ("    function f() external view returns (uint256) { return x; }", False),
    ("    function f() external pure returns (uint256) { return 1; }", False),
    ("    function f() internal { x = 1; }", False),
    ("    function f() external { }", False),
    ("    constructor() { x = 1; }", False),
])
def test_missing_event_rule(snippet, missing):
    rules = [(r, line) for r, _, line in findings(body(snippet)) if r == "SOL-MISSING-EVENT"]
    assert bool(rules) is missing
    if missing:
        assert rules[0][1] == 6


def test_interfaces_and_libraries_are_not_checked_for_events():
    assert findings("pragma solidity 0.8.28;\ninterface I { function f() external; }\nlibrary L { function g(uint256 a) public { a; } }\n") == []


def test_findings_use_the_four_severities():
    module = load_module()
    assert module.SEVERITIES == ("info", "minor", "major", "blocker")
    for f in module.lint_source("V.sol", VAULT):
        assert f["severity"] in module.SEVERITIES and set(f) == {"id", "severity", "summary", "location"}


def test_range_parser_follows_solidity_semver():
    module = load_module()
    parse = module.parse_range
    assert parse("^0.8.20") == [((0, 8, 20), (0, 9, 0))]
    assert parse("~0.8.5") == [((0, 8, 5), (0, 9, 0))]
    assert parse(">=0.8.0 <0.9.0") == [((0, 8, 0), (0, 9, 0))]
    assert parse("0.8.28") == [((0, 8, 28), (0, 8, 29))]
    assert parse("0.8") == [((0, 8, 0), (0, 9, 0))]
    assert parse("0.8.x") == [((0, 8, 0), (0, 9, 0))]
    assert parse(">0.8.19") == [((0, 8, 20), module.INF)]
    assert parse("<=0.8.5") == [((0, 0, 0), (0, 8, 6))]
    assert parse("0.7.6 || ^0.8.20") == [((0, 7, 6), (0, 7, 7)), ((0, 8, 20), (0, 9, 0))]
    assert parse("0.8.20 - 0.8.25") == [((0, 8, 20), (0, 8, 26))]
    assert parse("^1.2.3") == [((1, 2, 3), (2, 0, 0))]
    assert parse(">=0.9.0 <0.8.0") == []
    for bad in ("", "garbage", ">=", "^^0.8.20", "0.8.20 foo", "x" * 80):
        with pytest.raises(ValueError):
            parse(bad)


# --- independent review regressions ------------------------------------------------------------------------------------------

CANARY_KEY = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
CANARY_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def test_lint_findings_never_echo_secret_shaped_identifiers_or_constants(tmp_path):
    write(tmp_path, "src/K.sol", (
        "pragma solidity 0.8.28;\n"
        f"contract K {{\n    bytes32 constant KEY = 0x{CANARY_KEY};\n    string constant TOKEN = \"{CANARY_TOKEN}\";\n    uint256 total;\n"
        f"    function sweep_{CANARY_KEY}() external {{ total = 1; }}\n    function {CANARY_TOKEN}() external {{ total = 2; }}\n}}\n"))
    result, data = lint(tmp_path)
    assert result.returncode == 0 and by_rule(data, "SOL-MISSING-EVENT")
    for canary in (CANARY_KEY, CANARY_TOKEN, CANARY_KEY[:40]):
        assert canary not in result.stdout


def test_lint_and_tool_redaction_rules_are_the_same_rule():
    import importlib
    tools = importlib.import_module("swarm.tools.a26_tools")
    lint_module = load_module()
    samples = ["plain text with transfer(address,uint256)", CANARY_TOKEN, "0x" + CANARY_KEY, CANARY_KEY, "AKIAIOSFODNN7EXAMPLE",
               "https://alice:hunter2hunter2@rpc.example.com/x", "'" + " ".join(["test"] * 11 + ["junk"]) + "'", "A" * 500, "x " * 300,
               "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "sk-" + "a1" * 16]
    for sample in samples:
        assert lint_module.redact(sample) == tools._text(sample), sample
    assert lint_module.redact(CANARY_TOKEN) == "<redacted 40 chars>" and lint_module.redact("0x" + CANARY_KEY) == "<redacted 66 chars>"


def test_pragma_error_messages_do_not_echo_free_text():
    module = load_module()
    for bad in (CANARY_TOKEN, "secret phrase " * 4):
        with pytest.raises(ValueError) as info:
            module.parse_range(bad)
        assert CANARY_TOKEN not in str(info.value) and "secret phrase" not in str(info.value) and "characters" in str(info.value)
    with pytest.raises(ValueError) as info:
        module.parse_range("^^0.8.20")
    assert "'^^0.8.20'" in str(info.value)  # short version-like text is still quoted: it is the useful part


@pytest.mark.parametrize("name, source", [
    ("call-braces", "contract C { function f() external { " + "x.call{" * 60000 + " } }"),
    ("pragma-unterminated", "pragma solidity ^0.8.20 " * 40000),
    ("require-run", "contract C { function f() external { require(" + "a" * 900000 + " } }"),
    ("sender-compare-run", "contract C { function f() external { " + "require(msg.sender == a || " * 30000 + " } }"),
    ("many-findings", "pragma solidity 0.8.28;\ncontract C { function f() external {\n" + "tx.origin;\n" * 80000 + "} }"),  # a finding per line
    ("many-pragmas", "pragma solidity 0.8.28;\n" * 40000),
])
def test_lint_is_linear_on_adversarial_files(name, source):
    import time
    module = load_module()
    started = time.perf_counter()
    module.lint_source("a.sol", source)
    assert time.perf_counter() - started < 15, name


# --- adversarial inputs: a FIFO, quadratic regex work, a bare if body ----------------------------------------------------


def lint_bounded(tmp_path: Path, *args: str):
    """The real-mode scan in a child process with a deadline: a hang fails the test instead of stalling the run."""
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "sol_contract_lint.py"), "--root", str(tmp_path), "--json", *args],
                            capture_output=True, text=True, timeout=60, cwd=ROOT, env={**os.environ, "SWARM_DIR": str(tmp_path / ".swarm-state")})
    return result, json.loads(result.stdout)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_fifo_named_like_a_contract_is_unreadable_and_the_scan_finishes(tmp_path):
    write(tmp_path, "fifodir/Ok.sol", VAULT)
    os.mkfifo(tmp_path / "fifodir" / "Hang.sol")
    result, data = lint_bounded(tmp_path, "--path", "fifodir")
    assert result.returncode == 1 and data["files_scanned"] == 1 and data["skipped"]["unreadable"] == 1
    assert [f["location"] for f in by_rule(data, "SOL-SELFDESTRUCT")] == ["fifodir/Ok.sol:13"]


def test_repeated_contract_keywords_without_a_body_finish_quickly(tmp_path):
    write(tmp_path, "mc/Q.sol", "contract a " * 16000)
    result, data = lint_bounded(tmp_path, "--path", "mc")
    assert result.returncode == 0 and data["files_scanned"] == 1
    assert [f["location"] for f in by_rule(data, "SOL-PRAGMA-MISSING")] == ["mc/Q.sol:1"]


def test_contract_headers_still_match_under_the_length_bound():
    module = load_module()
    bases = ", ".join(f"B{i}" for i in range(80))  # a header of about 390 characters
    long_header = module.parse_source(f"pragma solidity 0.8.28;\ncontract Long is {bases} {{ }}\n")
    assert [(c["name"], c["kind"], len(c["bases"])) for c in long_header] == [("Long", "contract", 80)]
    small = module.parse_source("pragma solidity 0.8.28;\ncontract Vault is Ownable, ReentrancyGuard {\n}\n")
    assert [(c["name"], c["bases"]) for c in small] == [("Vault", ["Ownable", "ReentrancyGuard"])]

def test_contract_after_a_long_comment_block_is_still_checked():
    module = load_module()
    header_comment = "/* " + "n" * 600 + " */"  # blanks to 600+ spaces: the old 512-char header bound skipped this contract
    src = ("pragma solidity 0.8.28;\n"
           "contract Short { uint256 a; function poke() external { a += 1; } }\n"
           f"contract Vulnerable {header_comment} is Ownable {{\n"
           " function mint(address to) external { _mint(to, 1); }\n"
           "}\n")
    contracts = module.parse_source(src)
    assert [c["name"] for c in contracts] == ["Short", "Vulnerable"]
    assert contracts[1]["bases"] == ["Ownable"]
    mint = next(f for f in contracts[1]["functions"] if f["name"] == "mint")
    assert mint["visibility"] == "external" and mint["has_body"] and mint["body"].strip()
    assert module.guards(mint, module._callable_index(contracts)) == []  # unguarded: the access matrix must flag it


def test_contract_with_a_header_past_the_old_length_bound_is_still_checked():
    module = load_module()
    bases = ", ".join(f"B{i}" for i in range(160))  # a header of about 1100 characters, no comment involved
    contracts = module.parse_source(f"pragma solidity 0.8.28;\ncontract Long is {bases} {{ }}\n")
    assert [(c["name"], len(c["bases"])) for c in contracts] == [("Long", 160)]


def test_lint_redaction_of_a_long_unbroken_run_is_linear_and_normal_tokens_are_unchanged():
    import time
    module = load_module()
    started = time.perf_counter()
    clipped = module.redact("g" * 40000)
    assert time.perf_counter() - started < 2
    assert clipped == "g" * 72 + "..." and module.redact("g" * 40000 + "1") == "<redacted 40001 chars>"
    assert module.redact(CANARY_TOKEN) == "<redacted 40 chars>"
