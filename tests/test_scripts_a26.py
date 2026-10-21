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


def test_empty_tree_scans_nothing_and_is_incomplete(tmp_path):
    result, data = lint(tmp_path)
    assert result.returncode == 1 and data["files_scanned"] == 0 and [f["id"] for f in data["findings"]] == ["scan/incomplete"]


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
    assert result.returncode == 1 and data["status"] == "fail" and data["files_scanned"] == 1
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"]  # skipped in-scope inputs are never reported as ok
    assert data["skipped"]["too_large"] == 1 and data["skipped"]["binary"] == 1 and data["skipped"]["symlinks"] == 2  # file + directory link


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
    assert result.returncode == 1 and data["status"] == "fail" and data["files_scanned"] == 2000 and data["skipped"]["limit_reached"] is True
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"]  # 1 file over the limit was not analysed: incomplete, not ok
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
    assert module.guards(mint, module.visible_index(contracts[1], contracts)) == []  # unguarded: the access matrix must flag it


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


# --- QA-03 remediation: lint rules shared with the tools (one regression per fixed defect) ----------------------------------


@pytest.mark.parametrize("pragma, expected", [
    ("pragma solidity ^0.7.6;", [("SOL-PRAGMA-BELOW-MIN", "major"), ("SOL-PRAGMA-NOT-PINNED", "minor")]),
    ("pragma solidity >=0.9.0 <0.8.0;", [("SOL-PRAGMA-EMPTY-RANGE", "major")]),
    ("pragma solidity " + ">=0.8.0 " * 30 + ";", [("SOL-PRAGMA-INVALID", "minor")]),  # over-long: invalid, not missing
])
def test_lint_reports_every_pragma_policy_finding_and_an_overlong_pragma_as_invalid(pragma, expected):
    assert sorted((r, s) for r, s, _ in findings(pragma + "\ncontract C { }\n")) == sorted(expected)


@pytest.mark.parametrize("snippet, severity", [
    ("    function f(address owner) external { emit E(); address o = tx.origin; require(o == owner); }", "major"),
    ("    function f() external view returns (bool) { return tx.origin != address(0); }", "minor"),  # zero is not a principal
])
def test_tx_origin_is_major_only_when_compared_with_a_principal(snippet, severity):
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-TX-ORIGIN"] == [("SOL-TX-ORIGIN", severity)]


@pytest.mark.parametrize("snippet, flagged", [
    ('    function f(address t) external { emit E(); (bool ok, ) = t.call(""); }', True),  # bound to ok, never read
    ('    function f(address t) external { emit E(); bool ok = payable(t).send(1); if (ok) emit E(); }', True),  # D-B: no revert on the false path
])
def test_a_bound_low_level_result_must_be_read(snippet, flagged):
    assert bool([r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"]) is flagged


@pytest.mark.parametrize("snippet, severity", [
    # reached from an unguarded public function: the callee runs for any caller
    ('    function execute(address t, bytes memory d) public { emit E(); _run(t, d); }\n'
     '    function _run(address t, bytes memory d) internal { (bool ok, ) = t.delegatecall(d); require(ok); }', "major"),
    # inline assembly is a delegatecall too
    ('    function f(address t, bytes memory d) public { emit E(); assembly { let ok := delegatecall(gas(), t, add(d, 32), mload(d), 0, 0) } }', "major"),
    ('    function f(address t, bytes memory d) external onlyOwner { emit E(); assembly { let ok := delegatecall(gas(), t, add(d, 32), mload(d), 0, 0) } }', "minor"),
])
def test_delegatecall_exposure_follows_reachability_and_assembly(snippet, severity):
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-DELEGATECALL"] == [("SOL-DELEGATECALL", severity)]


def test_an_unnamed_fallback_that_writes_state_is_examined_for_its_event():
    assert ("SOL-MISSING-EVENT", "minor", 6) in findings(body("    function () external payable { x = 1; }"))


def test_a_dependency_directory_is_excluded_by_design_listed_by_name_and_never_fails(tmp_path):
    write(tmp_path, "contracts/lib/Vault.sol", VAULT)
    write(tmp_path, "contracts/Ok.sol", CLEAN)
    result, data = lint(tmp_path)
    assert result.returncode == 0 and data["status"] == "ok" and data["files_scanned"] == 1
    assert data["skipped"]["excluded_dirs"] == ["contracts/lib"] and data["findings"] == []  # by design: not unanalysed input


def guards_of(condition: str) -> list[str]:
    """The access labels the lint derives for `f()` whose body starts with `condition` (a sender check on state variables)."""
    module = load_module()
    source = ("pragma solidity 0.8.28;\ncontract C { address adminA; address adminB; address adminC; address owner; bool open; uint256 v;\n"
              f"    function f() external {{ {condition} v = 1; }} }}")
    contracts = module.parse_source(source)
    fn = next(f for f in contracts[0]["functions"] if f["name"] == "f")
    return module.guards(fn, module.visible_index(contracts[0], contracts))


def test_an_or_chain_of_sender_checks_against_principals_is_a_guard_for_each_principal():
    assert guards_of("require(msg.sender == adminA || msg.sender == adminB);") == ["msg.sender==adminA", "msg.sender==adminB"]
    assert guards_of("require(msg.sender == adminA || msg.sender == adminB || msg.sender == adminC);") == [
        "msg.sender==adminA", "msg.sender==adminB", "msg.sender==adminC"]


def test_an_or_with_a_non_sender_term_is_not_a_guard():
    assert guards_of("require(msg.sender == owner || open);") == []  # D1: the `open` branch reaches the write unchecked
    assert guards_of("require(msg.sender == adminA || msg.sender == adminB || open);") == []
    assert guards_of("require(open || msg.sender == adminA || msg.sender == adminB);") == []


# --- QA-03 re-review (N1, N2, N5, N7): path-aware guards, OR conditions, linear scans, unparseable input -----------------------


@pytest.mark.parametrize("guard, severity", [
    ("require(msg.sender == owner && open || a);", "major"),  # the `a` branch reaches the delegatecall with no sender check
    ("require(msg.sender == owner && open);", "minor"),  # both conjuncts must hold: a real guard, the call is only minor
])
def test_an_or_around_a_sender_check_leaves_its_branch_open(guard, severity):
    snippet = f"    function f(address t, bytes memory d, bool a) external {{ emit E(); {guard} (bool ok, ) = t.delegatecall(d); require(ok); }}"
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-DELEGATECALL"] == [("SOL-DELEGATECALL", severity)]


@pytest.mark.parametrize("snippet", [
    # a guard inside an if without an else does not guard the call after the if
    "    function f(address t, bytes memory d, bool c) external { emit E(); if (c) { require(msg.sender == owner); } (bool ok, ) = t.delegatecall(d); require(ok); }",
    # nothing after an unconditional return runs, so a guard placed after it is dead
    "    function f(address t, bytes memory d) external { emit E(); return; require(msg.sender == owner); (bool ok, ) = t.delegatecall(d); require(ok); }",
])
def test_a_guard_on_a_conditional_or_dead_path_does_not_guard_the_delegatecall(snippet):
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-DELEGATECALL"] == [("SOL-DELEGATECALL", "major")]


@pytest.mark.parametrize("make", [
    lambda: "pragma solidity 0.8.20;\ncontract C { address owner; function f() external { " + "address o = tx.origin; " * 2800 + "} }",  # 64 KiB
    lambda: "pragma solidity 0.8.28;\ncontract C { function f(address t) external { " + 't.call("") ' * 6000 + "} }",  # 64 KiB of call sites
    lambda: "pragma solidity 0.8.28;\ncontract C { function f(address t) external { " + "".join(
        f'(bool k{i}, ) = t.call(""); ' for i in range(1400)) + "".join(f"require(k{i}); " for i in range(1400))
        + "} }",  # ~63 KiB: calls whose checks come later
])
def test_a_64_kib_source_of_repeated_origin_or_call_sites_is_scanned_in_seconds(make):
    import time
    module = load_module()
    source = make()
    assert len(source) >= 60000
    started = time.perf_counter()
    module.lint_source("a.sol", source)
    assert time.perf_counter() - started < 5, len(source)


def test_a_truncated_source_fails_closed_as_an_incomplete_scan(tmp_path):
    write(tmp_path, "src/Cut.sol", "pragma solidity 0.8.28;\ncontract S { uint256 x; function f(uint256 a")
    result, data = lint(tmp_path)
    assert result.returncode == 1 and data["status"] == "fail" and data["files_scanned"] == 1
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("scan/incomplete", "major")]


def test_a_function_after_the_closing_brace_fails_closed_as_an_incomplete_scan(tmp_path):
    write(tmp_path, "src/Late.sol", "pragma solidity 0.8.28;\ncontract S { uint256 x; }\nfunction f() external { x = 1; }\n")
    result, data = lint(tmp_path)
    assert result.returncode == 1 and data["status"] == "fail"
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("scan/incomplete", "major")]


# --- statement boundary (residual 2): a call is judged by its statement, not by a fixed window before it -----------------------


def test_a_result_check_is_found_for_a_call_behind_a_statement_head_longer_than_512_characters():
    long_args = ", ".join(["x"] * 150)  # 600 characters of arguments
    long_head = " + ".join(["x"] * 150)  # 600 characters before the call, in the same require
    checked_by_binding = f"    function f(address t) external {{ emit E(); (bool ok, ) = t.call({long_args}); require(ok); }}"
    checked_in_require = f"    function f(address t) external {{ emit E(); require({long_head} > 0 && t.call(\"\")); }}"
    for snippet in (checked_by_binding, checked_in_require):
        assert [r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"] == [], snippet[:60]


def test_a_long_statement_whose_call_result_is_never_tested_is_still_flagged():
    long_args = ", ".join(["x"] * 150)
    snippet = f"    function f(address t) external {{ emit E(); t.call({long_args}); }}"
    assert [r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"] == ["SOL-UNCHECKED-LOW-LEVEL-CALL"]


# --- re-verification round two (NEW-6, NEW-7, NEW-8): for headers, callee guards at their call site, stored success flags ---------


@pytest.mark.parametrize("statement", [
    "for (uint i; i < 1; require(true)) (bool ok, ) = a.call(\"\");",
    "for (uint i; i < 1; assert(true)) (bool ok, ) = a.call(\"\");",
])
def test_a_semicolon_in_a_for_header_is_not_a_statement_boundary_so_the_call_is_still_judged(statement):  # NEW-6
    snippet = f"    function f(address a) external {{ {statement} }}"
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"] == [("SOL-UNCHECKED-LOW-LEVEL-CALL", "major")]


@pytest.mark.parametrize("snippet, flagged", [
    ('    function f(address t) external { (bool ok, ) = t.call(""); emit R(ok); }', True),  # stored in an event: not a test
    ('    bool lastOk; function f(address t) external { (bool ok, ) = t.call(""); lastOk = ok; }', True),  # assigned on: not a test
    ('    function f(address t) external { (bool ok, ) = t.call(""); require(ok); }', False),
    ('    function f(address t) external { (bool ok, ) = t.call(""); if (!ok) revert(); }', False),
])
def test_a_success_flag_counts_as_checked_only_when_a_condition_reads_it(snippet, flagged):  # NEW-8
    rules = [r for r, _, _ in findings(body(snippet)) if r == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


@pytest.mark.parametrize("call, severity", [
    ("if (f > 0) { _auth(); }", "major"),  # the guard runs on one path only: the delegatecall is exposed
    ("_auth();", "minor"),  # the guard runs on every path: the delegatecall is guarded
])
def test_a_callee_guard_counts_for_a_delegatecall_only_when_its_call_is_unconditional(call, severity):  # NEW-7
    snippet = ("    function _auth() internal view { require(msg.sender == owner); }\n"
               f"    function pay(address t, bytes memory d, uint f) external {{ {call} (bool ok, ) = t.delegatecall(d); require(ok); }}")
    assert [(r, s) for r, s, _ in findings(body(snippet)) if r == "SOL-DELEGATECALL"] == [("SOL-DELEGATECALL", severity)]


def test_a_call_chain_past_the_recursion_limit_is_a_scan_incomplete_major_in_the_lint(tmp_path):  # recursion backstop
    depth = 1200  # past the interpreter's limit of 1000 frames and the guard walk's measured limit of about 494 levels
    lines = ["    function f0(address t, bytes memory d, uint v) external { f1(v); (bool ok, ) = t.delegatecall(d); require(ok); }"]
    lines += [f"    function f{i}(uint v) internal {{ f{i + 1}(v); }}" for i in range(1, depth)]
    lines.append(f"    function f{depth}(uint v) internal {{ x = v; }}")
    write(tmp_path, "src/Deep.sol", "pragma solidity 0.8.28;\ncontract S { uint256 x;\n" + "\n".join(lines) + "\n}\n")
    result, data = lint(tmp_path)
    assert result.returncode == 1 and data["status"] == "fail" and data["files_scanned"] == 1
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("scan/incomplete", "major")]
    assert "recursion limit" in data["findings"][0]["summary"]


# --- round three (D-A, D-B): a success flag is checked only by its next read in scope, and an if checks only when its failure path
# reverts or returns --------------------------------------------------------------------------------------------------------------


def test_a_flag_overwritten_by_a_later_call_before_any_read_checks_nothing_for_the_earlier_call():  # D-A
    snippet = ("    function f(address a, address b) external {\n"
               "        (bool ok, ) = a.call(\"\");\n"
               "        (bool ok2, ) = b.call(\"\");\n"
               "        require(ok);\n"
               "    }")
    lines = [line for rule, _, line in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert lines == [7, 8]  # the first call (line 7) is unchecked; the second is never read either


def test_a_read_of_a_flag_in_another_scope_checks_nothing_for_the_call_in_its_own_block():  # D-A
    snippet = ("    function f(address a, address b) external {\n"
               "        { (bool ok, ) = a.call(\"\"); }\n"
               "        { (bool ok, ) = b.call(\"\"); require(ok); }\n"
               "    }")
    lines = [line for rule, _, line in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert lines == [7]  # the first block's flag is out of scope before the second block reads its own flag


@pytest.mark.parametrize("snippet, flagged", [
    ('    function f(address a) external { (bool ok, ) = a.call(""); if (ok) { lastOk = true; } }', True),  # no revert on the false path
    ('    function f(address a) external { (bool ok, ) = a.call(""); if (!ok) revert(); }', False),
    ('    function f(address a) external { (bool ok, ) = a.call(""); if (ok) { lastOk = true; } else { revert(); } }', False),
])
def test_an_if_checks_the_call_only_when_its_failure_path_reverts_or_returns(snippet, flagged):  # D-B
    rules = [rule for rule, _, _ in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


# --- round four (N-2): a failure branch that reverts as one of its top-level statements checks the call -------------------------


@pytest.mark.parametrize("branch, flagged", [
    ("if (!ok) { emit E(); revert(); }", False),  # the revert is reached on every path of the failure branch
    ("if (!ok) { emit E(); }", True),  # nothing in the failure branch stops the call
    ("if (!ok) { if (c) revert(); }", True),  # the revert is conditional, so it does not stop the call on every path
])
def test_a_failure_branch_checks_the_call_when_a_top_level_statement_of_it_reverts(branch, flagged):  # N-2
    snippet = f"    function f(address t, bool c) external {{ (bool ok, ) = t.call(\"\"); {branch} }}"
    rules = [rule for rule, _, _ in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


# --- round five (N-5): a brace inside call arguments is part of an expression, so it neither opens a block nor ends a statement ---


@pytest.mark.parametrize("branch, flagged", [
    ("if (!ok) { return S({a: 1}); }", False),  # the failure branch exits through the return; the struct-literal brace is no block
    ("if (!ok) { revert E({a: 1}); }", False),  # the failure branch exits through the revert, whose argument is a struct literal
    ("if (!ok) { emit E({a: 1}); }", True),  # an emit is not an exit, however its arguments are written
])
def test_a_brace_inside_call_arguments_is_an_expression_not_a_block(branch, flagged):  # N-5
    snippet = f"    function f(address t) external {{ (bool ok, ) = t.call(\"\"); {branch} }}"
    rules = [rule for rule, _, _ in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


# --- review follow-ups: valid code the lint must not fail on, and checks it must not credit ---


@pytest.mark.parametrize("snippet, flagged", [
    # a success flag declared earlier and bound by a tuple assignment is checked; an empty first slot names no flag
    ("    function f(address t) external { bool ok; bytes memory d; (ok, d) = t.call(\"\"); require(ok); }", False),
    ("    function f(address t) external { bytes memory d; (, d) = t.call(\"\"); require(d.length > 0); }", True),
    # a read that only logs the flag is passed over; the next read that tests it is the check
    ("    function f(address t) external { (bool ok, ) = t.call(\"\"); emit E(); require(ok); }", False),
    ("    function f(address t) external { (bool ok, ) = t.call(\"\"); emit E(ok); require(ok); }", False),
    ("    function f(address t) external { (bool ok, ) = t.call(\"\"); emit E(ok); }", True),
    ("    function f(address a, address b) external { (bool ok1, ) = a.call(\"\"); (bool ok2, ) = b.call(\"\"); "
     "require(ok2); require(ok1); }", False),
    # a flag overwritten by a later call, as a plain assignment or as a tuple slot, is never checked
    ("    function f(address a, address b) external { (bool ok, ) = a.call(\"\"); (ok, ) = b.call(\"\"); require(ok); }", True),
])
def test_a_success_flag_is_checked_by_a_later_test_and_never_after_an_overwrite(snippet, flagged):
    rules = [rule for rule, _, _ in findings(body(snippet)) if rule == "SOL-UNCHECKED-LOW-LEVEL-CALL"]
    assert bool(rules) is flagged


BASE_WITH_EMITTING_OVERLOAD = ("pragma solidity 0.8.28;\ncontract Base {\n    event Logged(address who);\n"
                               "    function _log(address who) internal { emit Logged(who); }\n}\n")


@pytest.mark.parametrize("override, emits", [
    ("function _log(uint256 v) internal { x = v; }", True),  # another overload: the base's emitting _log(address) stays callable
    ("function _log(address who) internal { x = 2; }", False),  # the same signature overrides the base, which is hidden
])
def test_an_inherited_overload_stays_callable_and_only_an_override_hides_its_base(override, emits):
    text = BASE_WITH_EMITTING_OVERLOAD + ("contract Child is Base {\n    uint256 public x;\n    " + override + "\n"
                                          "    function run(address a) external { x = 1; _log(a); }\n}\n")
    missing = [rule for rule, _, _ in findings(text) if rule == "SOL-MISSING-EVENT"]
    assert bool(missing) is (not emits)


def test_a_function_type_parameter_is_not_a_visibility_of_a_free_function():
    assert load_module().source_problems("pragma solidity 0.8.28;\nfunction invoke(function() external callback) { callback(); }\n") == []
    assert load_module().source_problems("pragma solidity 0.8.28;\nfunction bad() external { }\n") != []


def test_a_tx_origin_alias_is_judged_only_within_the_scope_that_declares_it():
    other = body("    function peek() external view returns (address) { address who = tx.origin; return who; }\n"
                 "    function set(address who) external { require(who == owner); }")
    assert [sev for rule, sev, _ in findings(other) if rule == "SOL-TX-ORIGIN"] == ["minor"]
    same = body("    function auth() external view { address o = tx.origin; require(o == owner); }")
    assert [sev for rule, sev, _ in findings(same) if rule == "SOL-TX-ORIGIN"] == ["major"]


def test_a_call_credits_only_the_overload_its_argument_types_select():  # round 2: an unresolved overload gives no credit
    text = ("pragma solidity 0.8.28;\n"
            "contract Base {\n    event Logged(address who);\n    function _log(address who) internal { emit Logged(who); }\n}\n"
            "contract Child is Base {\n    uint256 public total;\n    function _log(uint256 v) internal { total = v; }\n"
            "    function mint(uint256 v) external {\n        _log(v);\n        total += v;\n    }\n}\n")
    # `_log(v)` with a uint256 argument resolves to the child's empty overload, not the base's emitting one: mint is missing its event
    assert [rule for rule, _, _ in findings(text) if rule == "SOL-MISSING-EVENT"] == ["SOL-MISSING-EVENT"]
    literal = text.replace("_log(v);", "_log(1);")
    # a literal argument has no declared type: every overload is a possible target, and the base's emitting overload does not hold
    assert [rule for rule, _, _ in findings(literal) if rule == "SOL-MISSING-EVENT"] == ["SOL-MISSING-EVENT"]


def test_a_check_inside_a_conditional_for_condition_does_not_guard_the_write_after_it():  # round 3: _Ends keeps its control heads
    text = ("pragma solidity 0.8.28;\ncontract C {\n    address owner;\n    uint256 public x;\n"
            "    function _checkOwner() internal view returns (bool) {\n"
            "        require(msg.sender == owner);\n        return false;\n    }\n"
            "    function mint(bool audit) external {\n        if (audit) for (; _checkOwner(); ) { break; }\n        x++;\n    }\n}\n")
    # mint(false) skips the owner check and writes x: the access matrix must flag mint
    access_matrix = importlib.import_module("swarm.tools.a26_tools").access_matrix
    result = access_matrix({"source": text, "contract": "C"})
    assert result["verdict"] == "fail" and result["unprotected"] == 1 and result["guarded"] == 0
    assert [f["id"] for f in result["findings"]] == ["ACCESS-UNPROTECTED-PRIVILEGED"]
