"""scripts/custody_policy_check.py (A30): policy validation and key-material scan over fixture trees.

Real mode runs as a subprocess over trees built in tmp_path (exit codes, stdout JSON); the YAML-absent and registry-absent
paths run in-process because they need a patched module. Key-shaped values are built at runtime, never written in this file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "custody_policy_check.py"
TWIN = ROOT / "scripts" / "ts" / "custody_policy_check.ts"
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
HEX_KEY = bytes(range(32)).hex()

SIGNERS = [
    {"signer_id": "s0", "entity": "treasury", "region": "DE", "device": "hardware"},
    {"signer_id": "s1", "entity": "security", "region": "US", "device": "hardware"},
    {"signer_id": "s2", "entity": "cfo", "region": "SG", "device": "hsm"},
]
MULTISIG = {"threshold": 2, "signers": SIGNERS}
TIMELOCK = {
    "delay_seconds": 172800, "value_at_risk_usd": 5000000,
    "delay_bands": [{"min_value_usd": 0, "min_delay_seconds": 3600}, {"min_value_usd": 1000000, "min_delay_seconds": 172800}],
    "response_seconds": 7200, "canceller_count": 2, "guardian_can_cancel": True, "admin_renounced": True, "execution_window_seconds": 1209600,
}
WALLET_TIERS = {
    "tiers": [{"tier": "hot", "balance_usd": 50000, "max_pct_of_total": 5}, {"tier": "cold", "balance_usd": 9950000, "max_pct_of_total": 100}],
    "min_cold_pct": 70,
}
SIGNER_POLICY = {
    "per_tx_limit_usd": 250000, "daily_limit_usd": 1000000, "balance_usd": 20000000, "velocity_max_tx_per_hour": 10,
    "allowlist_enforced": True, "allowlist_cooloff_seconds": 172800, "allowlist_reattest_days": 90, "signing_threshold": 3,
    "policy_change_approvals": 4, "approval_expiry_seconds": 3600,
}


def b58check(payload: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    raw = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    number, out = int.from_bytes(raw, "big"), ""
    while number:
        number, digit = divmod(number, 58)
        out = alphabet[digit] + out
    return out


def wif_key() -> str:
    return b58check(b"\x80" + bytes(range(1, 33)) + b"\x01")


def put(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def put_json(root: Path, rel: str, document) -> Path:
    return put(root, rel, json.dumps(document))


def listing(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() or p.is_symlink())


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def run_script(root: Path, *extra: str, tmp_path: Path | None = None) -> tuple[int, dict, str]:
    state = (tmp_path or root.parent) / "swarm-state"
    env = {**os.environ, "SWARM_DIR": str(state)}
    env.pop("SWARM_TASK_ID", None)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--json", *extra], capture_output=True, text=True, env=env, cwd=root.parent)
    return proc.returncode, json.loads(proc.stdout), proc.stdout


def by_id(report: dict) -> dict:
    return {f["id"]: f for f in report["findings"]}


# --- dry run ----------------------------------------------------------------------------------------------------------


def test_dry_run_prints_one_canned_json_document_and_reads_nothing(tmp_path):
    missing = tmp_path / "does-not-exist"
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--json", "--root", str(missing)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0 and proc.stderr == ""
    report = json.loads(proc.stdout)
    assert report == {
        "status": "ok", "dry_run": True, "findings": [], "files_scanned": 0, "policy_files": 0, "secret_findings": 0,
        "counts": {"info": 0, "minor": 0, "major": 0, "blocker": 0},
        "files_skipped": {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 0}, "skipped": [], "truncated": False,
        "summary": "dry-run: would validate custody policy sections and scan --path under --root for key material",
        "agent": "A30", "script": "custody_policy_check",
    }
    assert not missing.exists()


@pytest.mark.skipif(not Path(BUN).exists(), reason="bun not installed")
def test_bun_twin_dry_run_matches_the_python_contract(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "state")}
    proc = subprocess.run([BUN, str(TWIN), "--dry-run", "--json"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "ok" and report["agent"] == "A30" and report["script"] == "custody_policy_check" and report["dry_run"] is True


# --- clean tree -------------------------------------------------------------------------------------------------------


def test_clean_tree_with_valid_policy_passes(tree, tmp_path):
    put(tree, "README.md", "# custody\n")
    put_json(tree, "policy/custody.json", {"multisig": MULTISIG, "timelock": TIMELOCK, "signer_policy": SIGNER_POLICY, "wallet_tiers": WALLET_TIERS})
    before = listing(tree)
    code, report, _ = run_script(tree)
    assert code == 0 and report["status"] == "ok" and report["agent"] == "A30" and report["script"] == "custody_policy_check"
    assert report["findings"] == [] and report["files_scanned"] == 2 and report["policy_files"] == 1 and report["secret_findings"] == 0
    assert report["counts"] == {"info": 0, "minor": 0, "major": 0, "blocker": 0} and report["truncated"] is False and report["skipped"] == []
    assert listing(tree) == before


def test_output_is_deterministic(tree):
    put(tree, "b.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "a.env", f"PRIVATE_KEY={HEX_KEY}\n")
    first = run_script(tree)[2]
    assert run_script(tree)[2] == first
    assert [f["location"] for f in json.loads(first)["findings"]] == ["a.env:1", "b.env:1"]


# --- key-material scan ------------------------------------------------------------------------------------------------


def test_key_material_is_reported_by_location_and_length_only(tree):
    token = wif_key()
    put(tree, "src/app.env", f"RPC=x\nETH_PRIVATE_KEY=0x{HEX_KEY}\n")
    put(tree, "notes/wallet.txt", f"backup\n{token}\n")
    code, report, raw = run_script(tree)
    assert code == 1 and report["status"] == "fail"
    found = by_id(report)
    hex_finding = found["secret:hex-private-key:src/app.env:2"]
    assert (hex_finding["severity"], hex_finding["location"], hex_finding["length"], hex_finding["kind"]) == ("blocker", "src/app.env:2", 64, "hex-private-key")
    wif_finding = found["secret:wif-private-key:notes/wallet.txt:2"]
    assert wif_finding["length"] == len(token) == 52 and wif_finding["location"] == "notes/wallet.txt:2"
    assert report["secret_findings"] == 2 and report["counts"]["blocker"] == 2
    assert HEX_KEY not in raw and token not in raw


def test_encrypted_keystore_is_major_and_fails_the_run(tree):
    put(tree, "keystore/UTC--1.json", '{"crypto": {"ciphertext": "00", "kdf": "scrypt", "cipher": "aes-128-ctr"}}')
    code, report, _ = run_script(tree)
    assert code == 1 and [f["severity"] for f in report["findings"]] == ["major"] and report["findings"][0]["kind"] == "encrypted-keystore"


def test_path_option_limits_the_scan_to_a_subtree(tree):
    put(tree, "a/ok.txt", "nothing")
    put(tree, "b/leak.env", f"PRIVATE_KEY={HEX_KEY}")
    code, report, _ = run_script(tree, "--path", "a")
    assert code == 0 and report["files_scanned"] == 1
    code, report, _ = run_script(tree, "--path", "b/leak.env")
    assert code == 1 and report["files_scanned"] == 1 and report["findings"][0]["location"] == "b/leak.env:1"


@pytest.mark.parametrize("path", ["../outside", "missing-dir"])
def test_path_outside_root_or_missing_is_an_input_error(tree, path):
    code, report, _ = run_script(tree, "--path", path)
    assert code == 2 and report["status"] == "error" and report["error"]["code"] == "E-INPUT"


# --- bounds -----------------------------------------------------------------------------------------------------------


def test_large_binary_symlinked_and_vendored_files_are_skipped(tree, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    put(outside, "leak.env", f"PRIVATE_KEY={HEX_KEY}")
    put(tree, "big.env", f"PRIVATE_KEY={HEX_KEY}\n" + "x" * (1 << 20))
    put(tree, "blob.bin", b"\x00\x01" + f"PRIVATE_KEY={HEX_KEY}".encode())
    put(tree, ".git/config.env", f"PRIVATE_KEY={HEX_KEY}")
    put(tree, "node_modules/pkg/k.env", f"PRIVATE_KEY={HEX_KEY}")
    (tree / "link.env").symlink_to(outside / "leak.env")
    (tree / "linkdir").symlink_to(outside, target_is_directory=True)
    put(tree, "ok.txt", "fine")
    code, report, _ = run_script(tree)
    assert code == 0 and report["findings"] == [] and report["files_scanned"] == 1
    assert report["files_skipped"] == {"binary": 1, "symlink": 1, "too_large": 1, "unreadable": 0}


def test_file_exactly_one_mebibyte_is_still_scanned(tree):
    line = f"PRIVATE_KEY={HEX_KEY}\n"
    put(tree, "edge.env", line + "x" * ((1 << 20) - len(line)))
    code, report, _ = run_script(tree)
    assert code == 1 and report["files_scanned"] == 1 and report["files_skipped"]["too_large"] == 0


def test_scan_stops_at_two_thousand_files(tree):
    for index in range(2005):
        put(tree, f"f/{index:04d}.txt", "x")
    code, report, _ = run_script(tree)
    assert code == 0 and report["truncated"] is True and report["files_scanned"] == 2000
    assert by_id(report)["scan-limit-reached"]["severity"] == "minor"


# --- policy validation ------------------------------------------------------------------------------------------------


def test_weak_policy_sections_map_tool_findings_to_the_file(tree):
    put_json(tree, "custody/policy.json", {
        "multisig": {"threshold": 1, "signers": SIGNERS},
        "timelock": {**TIMELOCK, "delay_seconds": 0, "admin_renounced": False},
        "signer_policy": {**SIGNER_POLICY, "allowlist_enforced": False},
        "wallet_tiers": {**WALLET_TIERS, "min_cold_pct": 99.6},
    })
    code, report, _ = run_script(tree)
    found = by_id(report)
    assert code == 1 and report["status"] == "fail" and report["policy_files"] == 1
    assert found["multisig:threshold-one"]["severity"] == "blocker" and found["multisig:threshold-one"]["location"] == "custody/policy.json"
    assert found["multisig:threshold-one"]["section"] == "multisig"
    assert found["timelock:no-delay"]["severity"] == "blocker" and found["timelock:admin-not-renounced"]["severity"] == "major"
    assert found["signer_policy:allowlist-not-enforced"]["severity"] == "major"
    assert found["wallet_tiers:cold-below-minimum"]["severity"] == "major"
    assert report["counts"]["blocker"] >= 3


def test_minor_findings_alone_keep_the_status_ok(tree):
    put_json(tree, "multisig.json", {"multisig": {"threshold": 2, "signers": [{**s, "region": "DE"} for s in SIGNERS]}})
    code, report, _ = run_script(tree)
    assert code == 0 and report["status"] == "ok" and [f["id"] for f in report["findings"]] == ["multisig:single-region"]
    assert report["counts"] == {"info": 0, "minor": 1, "major": 0, "blocker": 0}


def test_unparseable_and_malformed_policies_are_findings_not_crashes(tree):
    put(tree, "custody-a.json", "{ not json")
    put(tree, "custody-b.json", "[1, 2]")
    put_json(tree, "custody-c.json", {"multisig": [1], "timelock": {**TIMELOCK, "probe": "sleep"}})
    code, report, _ = run_script(tree)
    found = by_id(report)
    assert code == 1
    assert found["policy-unparseable"]["location"] == "custody-a.json" and found["policy-unparseable"]["severity"] == "major"
    assert found["policy-not-an-object"]["location"] == "custody-b.json"
    assert found["multisig:policy-section-malformed"]["location"] == "custody-c.json"
    assert found["timelock:policy-section-malformed"]["severity"] == "major"


def test_invalid_section_values_are_reported_with_the_offending_field(tree):
    put_json(tree, "a/custody.json", {"multisig": {"threshold": 4, "signers": SIGNERS}})
    put_json(tree, "b/timelock.json", {"timelock": {**TIMELOCK, "unknown": 1}})
    put_json(tree, "c/custody.json", {"wallet_tiers": {"tiers": [{"tier": "hot", "balance_usd": 1, "max_pct_of_total": 5}] * 2, "min_cold_pct": 1}})
    code, report, _ = run_script(tree)
    found = by_id(report)
    assert code == 1
    messages = {f["location"]: f["summary"] for f in report["findings"] if f["id"].endswith("policy-invalid")}
    assert messages["a/custody.json"].startswith("threshold:") and messages["b/timelock.json"].startswith("unknown:")
    assert messages["c/custody.json"].startswith("tiers:")
    assert found["multisig:policy-invalid"]["severity"] == "major"


def test_policy_named_file_without_known_sections_is_informational(tree):
    put_json(tree, "custody.schema.json", {"type": "object"})
    code, report, _ = run_script(tree)
    assert code == 0 and [(f["id"], f["severity"]) for f in report["findings"]] == [("no-policy-sections", "info")]


def test_files_not_named_like_policies_are_not_validated(tree):
    put_json(tree, "config.json", {"multisig": {"threshold": 1, "signers": SIGNERS}})
    code, report, _ = run_script(tree)
    assert code == 0 and report["policy_files"] == 0 and report["findings"] == []


def test_a_secret_inside_a_policy_label_is_refused_without_echoing_it(tree):
    token = wif_key()
    leaked = [{**SIGNERS[0], "signer_id": token}, *SIGNERS[1:]]
    put_json(tree, "custody.json", {"multisig": {"threshold": 2, "signers": leaked}})
    code, report, raw = run_script(tree)
    kinds = {f["id"] for f in report["findings"]}
    assert code == 1 and "multisig:policy-invalid" in kinds and "secret:wif-private-key:custody.json:1" in kinds
    assert raw.count(token) == 0


# --- YAML and the optional-tool paths ---------------------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("custody_policy_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_in_process(module, root: Path, capsys, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / "state"))
    code = module.AgentScript("A30", "custody_policy_check", module.run, description="t", add_args=module.add_args).main(["--root", str(root), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_yaml_policies_report_skipped_tool_missing_when_pyyaml_is_absent(tree, tmp_path, capsys, monkeypatch):
    put(tree, "custody.yaml", "multisig:\n  threshold: 1\n")
    put(tree, "timelock.yml", "timelock: {}\n")
    put_json(tree, "custody.json", {"multisig": MULTISIG})
    module = load_script()
    monkeypatch.setattr(module, "yaml_module", lambda: None)
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    assert code == 0 and report["policy_files"] == 1 and report["findings"] == []
    assert report["skipped"] == [{"check": "yaml-policy-files", "status": "skipped:tool-missing", "tool": "PyYAML", "files": 2}]


def test_yaml_policies_are_checked_when_pyyaml_imports(tree, tmp_path, capsys, monkeypatch):
    pytest.importorskip("yaml")
    put(tree, "custody.yaml", "multisig:\n  threshold: 1\n  signers:\n" + "".join(
        f"    - {{signer_id: {s['signer_id']}, entity: {s['entity']}, region: {s['region']}, device: {s['device']}}}\n" for s in SIGNERS))
    put(tree, "timelock.yml", "timelock: [unclosed\n")
    module = load_script()
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    found = by_id(report)
    assert code == 1 and report["policy_files"] == 2 and report["skipped"] == []
    assert found["multisig:threshold-one"]["location"] == "custody.yaml" and found["policy-unparseable"]["location"] == "timelock.yml"


def test_missing_tool_registration_is_a_dependency_error(tree, tmp_path, capsys, monkeypatch):
    module = load_script()
    monkeypatch.setattr(module, "registry_data", lambda: None)
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    assert code == 2 and report["status"] == "error" and report["error"]["code"] == "E-DEP"


# --- independent-review regressions -----------------------------------------------------------------------------------


def test_a_secret_shaped_path_is_shown_by_position_and_never_echoed(tree):
    canary = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    put(tree, f"keys/{canary}.env", f"ETH_PRIVATE_KEY=0x{HEX_KEY}\n")
    put_json(tree, f"custody/{canary}.json", ["not", "an", "object"])
    put(tree, "plain/ok.env", f"ETH_PRIVATE_KEY=0x{HEX_KEY}\n")
    code, report, raw = run_script(tree)
    assert code == 1 and canary not in raw and HEX_KEY not in raw
    found = by_id(report)
    assert {"secret:hex-private-key:plain/ok.env:1", "policy-not-an-object"} <= set(found)
    # walk order is custody/<canary>.json (1), keys/<canary>.env (2), plain/ok.env (3)
    assert sorted(f["location"] for f in report["findings"] if f["id"].startswith("secret:") and "redacted" in f["location"]) == ["<redacted-path-2>:1"]
    assert found["policy-not-an-object"]["location"] == "<redacted-path-1>"


def test_unquoted_prefixed_mnemonic_in_an_env_file_is_found_by_the_script(tree):
    phrase = " ".join(["abandon"] * 11 + ["about"])
    put(tree, "deploy/.env", f"DEPLOYER_MNEMONIC={phrase}\nPK={HEX_KEY}\n")
    code, report, raw = run_script(tree)
    assert code == 1 and phrase not in raw and HEX_KEY not in raw
    assert {(f["kind"], f["location"], f["length"]) for f in report["findings"]} == {
        ("mnemonic-phrase", "deploy/.env:1", len(phrase)), ("hex-private-key", "deploy/.env:2", 64)}
    assert report["secret_findings"] == 2


def test_many_key_hits_in_one_file_do_not_time_out_the_scan(tree):
    put(tree, "dump/keys.env", ("PRIVATE_KEY=0x" + HEX_KEY + "\n") * 12000)
    code, report, _ = run_script(tree)
    assert code == 1 and report["secret_findings"] == 12000 and len(report["findings"]) == 100
