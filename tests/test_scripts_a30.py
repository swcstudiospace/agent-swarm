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
    assert code == 1 and report["status"] == "fail" and report["files_scanned"] == 1
    assert report["files_skipped"] == {"binary": 1, "symlink": 2, "too_large": 1, "unreadable": 0}
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"] and report["findings"][0]["severity"] == "major"
    summary = report["findings"][0]["summary"]
    assert "big.env skipped: too_large" in summary and "linkdir skipped: symlink" in summary and "node_modules" not in summary


def test_file_exactly_one_mebibyte_is_still_scanned(tree):
    line = f"PRIVATE_KEY={HEX_KEY}\n"
    put(tree, "edge.env", line + "x" * ((1 << 20) - len(line)))
    code, report, _ = run_script(tree)
    assert code == 1 and report["files_scanned"] == 1 and report["files_skipped"]["too_large"] == 0


def test_scan_stops_at_two_thousand_files(tree):
    for index in range(2005):
        put(tree, f"f/{index:04d}.txt", "x")
    code, report, _ = run_script(tree)
    assert code == 1 and report["truncated"] is True and report["files_scanned"] == 2000
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"] and report["findings"][0]["severity"] == "major"
    assert "scan stopped after 2000 files" in report["findings"][0]["summary"]


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
    assert messages["a/custody.json"].startswith("threshold:") and messages["c/custody.json"].startswith("tiers:")
    # an unexpected key is counted, never named: the key itself is not echoed (N2)
    assert messages["b/timelock.json"].startswith("timelock section has 1 unexpected top-level key(s)")
    assert "unknown" not in messages["b/timelock.json"]
    assert found["multisig:policy-invalid"]["severity"] == "major"


def test_unexpected_policy_keys_are_counted_and_never_echoed(tree):
    # a private key written as a field name must not reach any output: the 64 hex characters stay out of the report
    key = "0x" + HEX_KEY
    put_json(tree, "custody.json", {"multisig": {"threshold": 2, "signers": SIGNERS, f"private_key={key}": 1}})
    code, report, raw = run_script(tree)
    assert code == 1 and HEX_KEY not in raw and f"private_key={key}" not in raw
    found = [f for f in report["findings"] if f["id"] == "multisig:policy-invalid"]
    assert len(found) == 1 and found[0]["severity"] == "major"
    assert found[0]["summary"].startswith("multisig section has 1 unexpected top-level key(s)")


def test_policy_named_file_without_known_sections_fails_closed(tree):
    put_json(tree, "custody.schema.json", {"type": "object"})
    code, report, _ = run_script(tree)
    assert code == 1 and report["status"] == "fail"
    assert [(f["id"], f["severity"]) for f in report["findings"]] == [("scan/incomplete", "major")]
    assert "custody.schema.json policy not analysed" in report["findings"][0]["summary"]


def test_timelock_file_without_a_timelock_section_is_not_read_as_compliant(tree):
    put_json(tree, "custody/timelock.json", {"delay_seconds": 0, "value_at_risk_usd": 5000000,
                                             "delay_bands": [{"min_value_usd": 0, "min_delay_seconds": 172800}]})
    code, report, _ = run_script(tree)
    assert code == 1 and report["status"] == "fail" and report["policy_files"] == 1
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"]


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
    assert code == 1 and report["policy_files"] == 1
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"]
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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need os.mkfifo")
def test_named_pipe_in_the_tree_is_skipped_and_never_opened(tree):
    os.mkfifo(tree / "custody-policy.json")
    put(tree, "notes.md", "nothing sensitive here\n")
    env = {**os.environ, "SWARM_DIR": str(tree.parent / "swarm-state")}
    env.pop("SWARM_TASK_ID", None)
    # A read of the FIFO would block forever; the timeout turns that regression into a failure instead of a hang.
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tree), "--json"], capture_output=True, text=True, env=env,
                          cwd=tree.parent, timeout=60)
    report = json.loads(proc.stdout)
    assert proc.returncode == 1 and report["status"] == "fail"
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"]
    assert report["files_scanned"] == 1 and report["policy_files"] == 0
    assert report["files_skipped"] == {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 1}


def test_symlink_given_as_path_is_skipped_and_its_target_is_not_read(tree):
    put(tree, "policy/real.env", f"PRIVATE_KEY={HEX_KEY}\n")
    (tree / "alias.env").symlink_to(tree / "policy" / "real.env")
    code, report, raw = run_script(tree, "--path", "alias.env")
    assert code == 1 and report["status"] == "fail" and report["files_scanned"] == 0
    assert report["files_skipped"]["symlink"] == 1 and report["secret_findings"] == 0
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"] and HEX_KEY not in raw


def test_unreadable_subdirectory_is_a_reason_not_a_silent_skip(tree, tmp_path, capsys, monkeypatch):
    # os.walk reports an unlistable directory through onerror: its key must not vanish behind a status ok. In-process, so the
    # os.scandir patch reaches the walk (a subprocess would not see it).
    put(tree, "locked/key.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "ok.txt", "fine")
    real_scandir = os.scandir

    def denied(path="."):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    module = load_script()
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    assert code == 1 and report["status"] == "fail"
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"] and report["findings"][0]["severity"] == "major"
    assert "locked directory not listed" in report["findings"][0]["summary"] and HEX_KEY not in json.dumps(report)


def test_build_and_dist_are_scanned_like_any_directory(tree):
    # build and dist are in scope: their text files are read and a key in them is reported, with no reason recorded for the directory
    put(tree, "build/keys/wallet.pem", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    put(tree, "dist/leak.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "ok.txt", "fine")
    code, report, raw = run_script(tree)
    found = by_id(report)
    assert code == 1 and report["status"] == "fail" and HEX_KEY not in raw
    assert report["files_scanned"] == 3
    assert found["secret:pem-private-key:build/keys/wallet.pem:1"]["severity"] == "blocker"
    assert found["secret:hex-private-key:dist/leak.env:1"]["severity"] == "blocker"
    assert "scan/incomplete" not in found


def test_unlistable_directory_named_like_a_key_is_labelled_never_named(tree, tmp_path, capsys, monkeypatch):
    # shaped like an AWS access key id: the reason must show the redacted label, and the name must not reach any output
    name = "AKIAABCDEFGHIJKLMNOP"
    put(tree, f"{name}/k.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "ok.txt", "fine")
    real_scandir = os.scandir

    def denied(path="."):
        if str(path).endswith(name):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    module = load_script()
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    assert code == 1 and report["status"] == "fail"
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"] and report["findings"][0]["severity"] == "major"
    assert "<redacted-path-1> directory not listed: PermissionError" in report["findings"][0]["summary"]
    assert name not in json.dumps(report) and HEX_KEY not in json.dumps(report)


def test_nested_unexpected_signer_key_gets_no_count_and_is_never_named(tree):
    # the registry quotes a key inside one signer: the summary must print no count (not even zero) and must not name the key
    key = f"private_key=0x{HEX_KEY}"
    put_json(tree, "custody.json", {"multisig": {"threshold": 2, "signers": [{**SIGNERS[0], key: 1}, *SIGNERS[1:]]}})
    code, report, raw = run_script(tree)
    found = [f for f in report["findings"] if f["id"] == "multisig:policy-invalid"]
    assert code == 1 and len(found) == 1 and found[0]["severity"] == "major"
    summary = found[0]["summary"]
    assert summary == "multisig section has an unexpected key in a nested object; the key is not named and its value is not reported"
    assert not any(ch.isdigit() for ch in summary) and "private_key" not in summary and HEX_KEY not in raw


def test_by_design_directories_stay_silent(tree):
    for name in (".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".swarm", ".tox", ".mypy_cache", ".terraform"):
        put(tree, f"{name}/k.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "ok.txt", "fine")
    code, report, _ = run_script(tree)
    assert code == 0 and report["status"] == "ok" and report["findings"] == [] and report["files_scanned"] == 1


def test_unlistable_directory_named_like_a_secret_assignment_shows_no_value(tree, tmp_path, capsys, monkeypatch):
    # a directory name that is a key assignment with a 64-hex value: the reason shows the redacted label and never the value
    name = f"secret={HEX_KEY}"
    put(tree, f"{name}/k.env", f"PRIVATE_KEY={HEX_KEY}\n")
    put(tree, "ok.txt", "fine")
    real_scandir = os.scandir

    def denied(path="."):
        if str(path).endswith(name):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", denied)
    module = load_script()
    code, report = run_in_process(module, tree, capsys, tmp_path, monkeypatch)
    out = json.dumps(report)
    assert code == 1 and report["status"] == "fail"
    assert [f["id"] for f in report["findings"]] == ["scan/incomplete"]
    assert "<redacted-path-1> directory not listed: PermissionError" in report["findings"][0]["summary"]
    assert HEX_KEY not in out and "secret=" not in out


def test_pem_under_a_secret_assignment_directory_is_reported_by_label(tree):
    # the finding's id and location name the file by label: neither the directory's key assignment nor its value is echoed
    put(tree, f"secret={HEX_KEY}/wallet.pem", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    code, report, raw = run_script(tree)
    assert code == 1 and report["status"] == "fail" and HEX_KEY not in raw and "secret=" not in raw
    found = by_id(report)
    assert found["secret:pem-private-key:<redacted-path-1>:1"]["severity"] == "blocker"
    assert found["secret:pem-private-key:<redacted-path-1>:1"]["location"] == "<redacted-path-1>:1"


def test_input_error_names_a_secret_shaped_path_only_by_label(tree):
    # --path is echoed in the input error: a key-assignment path must come back as its label, never with its value
    code, report, raw = run_script(tree, "--path", f"secret={HEX_KEY}-missing")
    assert code == 2 and report["status"] == "error" and report["error"]["code"] == "E-INPUT"
    assert HEX_KEY not in raw and "secret=" not in raw and "<redacted-path-1>" in report["error"]["message"]


# Key-shaped assignments: each must be redacted in a path and refused as an identifier (N10). Hex: 64 non-uniform digits, and
# 32 digits. Base64: 40 characters with letters and digits.
KEY_SHAPED = (
    f"secret={HEX_KEY}",
    "seed=fedcba9876543210fedcba9876543210",
    "secret: KCkqKywtLi8wMTIzNDU2Nzg5Ojs8PT4/QEFCQ0RF",
)
# Ordinary slugs, dates and ticket ids: each must pass with no refusal and no redaction (N10, and the N11 paths).
ORDINARY = (
    "secret: ceremony-record-2026-10-20-signed",
    "seed: 20261021",
    "token: approval-ticket-CUST-4421",
    "token=ops-dashboard-review-2026",
    "reports/run_seed=20261021/summary.json",
    "backups/seed=20261021.tar",
)


@pytest.mark.parametrize("value", KEY_SHAPED)
def test_key_shaped_assignments_are_redacted_in_paths_and_refused_as_identifiers(value):
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert module.path_label(value, 1) == "<redacted-path-1>"
    assert a30._secret_kind(value, strict=False) == "key-assignment"


@pytest.mark.parametrize("value", ORDINARY)
def test_ordinary_slugs_dates_and_tickets_pass_unredacted(value):
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert module.path_label(value, 1) == value
    assert a30._secret_kind(value) == "" and a30._secret_kind(value, strict=False) == ""


@pytest.mark.parametrize("value", ORDINARY)
def test_ordinary_evidence_references_do_not_refuse_the_ceremony_call(value):
    # the whole custody_ceremony_checklist call must succeed: its evidence_ref goes through the identifier check (N10)
    module = load_script()
    result = module.call(module.registry_data(), "custody_ceremony_checklist", {
        "ceremony_type": "key_generation", "min_witnesses": 1,
        "items": [{"item": "participants-identified", "done": True, "evidence_ref": value, "witness_count": 1}],
    })
    assert result["state"] == "SUCCESS", result


@pytest.mark.parametrize("value", ["secret=" + "0" * 64, "seed=" + "a" * 32])
def test_placeholder_assignment_of_one_repeated_character_is_not_key_material(value):
    # the same exemption the hex key scanner applies: one distinct character is a placeholder, so it is neither redacted nor refused
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert module.path_label(value, 1) == value
    assert a30._secret_kind(value, strict=False) == ""


def test_key_name_after_a_uniform_run_is_still_redacted():
    # N14: seed= followed by 32 a's is a placeholder, and secret= follows a letter: the second name must still be found
    value = "seed=" + "a" * 32 + f"secret={HEX_KEY}"
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", ["secret=aaaa" + "seed=" + HEX_KEY, "seed=aaaa/secret=" + HEX_KEY])
def test_reviewer_decoys_with_a_key_name_and_a_key_value_are_flagged(value):
    # N14 fail-safe: a key name after a letter or a path separator, followed by a key-shaped value, is still refused
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "secret: aB3dE5fG7hJ9kL1mN3pQ-rS7tU9vW1xY3zA5bC7d",  # base64url with a hyphen; bare form 39 characters, both cases
    "secret: aB3dE5fG7hJ9kL1mN3pQ_rS7tU9vW1xY3zA5bC7d",  # base64url with an underscore
])
def test_base64url_key_with_a_hyphen_or_underscore_is_redacted(value):
    # N17 (rule since N21): a value with '-' or '_' is key material when its bare form is 32+ characters with upper and lower case
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "secret: CKJo_LW2uuNEL1t4YbIw-wd6tOykye5MqoEINzG_ezo",  # token_urlsafe(32) with '_' and '-'; bare form 40 characters, both cases
    "secret: aB3dE5fG7hJ9kL1mN3pQ-rS7tU9vW1xY3zA5bC7d",
])
def test_token_urlsafe_style_key_with_separators_is_redacted(value):
    # N18/N21: a base64url key with '-' or '_' is flagged by its whole value (bare form of 32+ characters, upper and lower case)
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "secret: " + "-".join(HEX_KEY[i:i + 8] for i in range(0, 64, 8)),  # the 64-digit hex key, grouped by 8
    "secret: " + "-".join(["12345678"] * 8),  # digits only: no group mixes letters and digits, so only the hex rule matches it
])
def test_hex_key_grouped_by_eight_is_redacted(value):
    # N19: the hex form is tested on its own, whatever the grouping: separators removed, 64 hex digits remain
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "S4Gjc-veLI6Sm_LfH6dt_5-SfTCKk-FJcQl_kEv6iiU",  # base64url, every segment shorter than 8; bare form 37 characters, both cases
    "PDWF87M-vFpquO-_YJpVqm_kdL9_DJqbLAQ-WpHr9vI",  # base64url; bare form 37 characters, both cases
])
def test_base64url_key_with_short_segments_is_redacted(value):
    # N21: a value with '-' or '_' is key material by its whole bare form (32+ characters, upper and lower case), not by segments
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind("secret: " + value, strict=False) == "key-assignment"
    assert module.path_label("secret: " + value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "Approval-Ticket-Review-Signed-Final",  # bare form 31 characters: below the floor, so not key material
    "approval-ticket-review2026-signed-final",  # no upper-case letter
])
def test_hyphenated_identifiers_are_not_refused_as_evidence(value):
    # N22: neither identifier is key-shaped, so the whole custody_ceremony_checklist call with it as evidence_ref succeeds
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind("secret: " + value, strict=False) == ""
    assert module.path_label("secret: " + value, 1) == "secret: " + value
    result = module.call(module.registry_data(), "custody_ceremony_checklist", {
        "ceremony_type": "key_generation", "min_witnesses": 1,
        "items": [{"item": "participants-identified", "done": True, "evidence_ref": value, "witness_count": 1}],
    })
    assert result["state"] == "SUCCESS", result


@pytest.mark.parametrize("value", [
    "secret=" + HEX_KEY + "-missing",
    "secret=" + HEX_KEY + "-v2",
    "secret=" + HEX_KEY + "_old",
    "seed=fedcba9876543210fedcba9876543210-backup",
])
def test_key_followed_by_a_lowercase_suffix_is_redacted_and_flagged(value):
    # N23: the first segment before the separator is key material, so a key with a suffix is still flagged and redacted
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


def test_key_hex_followed_by_a_letter_without_a_separator_stays_flagged():
    # N23 control: with no separator the whole value is key material (hex plus a letter still has a letter and a digit)
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    value = "secret=" + HEX_KEY + "x"
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


def test_uniform_first_segment_before_a_suffix_is_a_placeholder():
    # N23 with the placeholder check: a repeated-character first segment is not key material, even with a suffix
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    value = "secret=" + "0" * 64 + "-x"
    assert a30._secret_kind(value, strict=False) == ""
    assert module.path_label(value, 1) == value


def test_open_decision_separated_mixed_case_identifier_after_a_label_is_refused_as_documented():
    # N24 (open decision, no rule change): a separated value of 32+ characters with both cases after a credential label is refused
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    value = "token: approval-ticket-quarterly-OPS-review-2026-final"
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    'secret  "=' + HEX_KEY,  # N26: whitespace, then a quote, then the separator: the documented order
    'secret="' + HEX_KEY,  # quote directly after the label, separator before the quote
    'secret: "' + HEX_KEY,  # the quote after the separator
    "secret  :   " + HEX_KEY,  # whitespace on both sides of the separator
    "secret\t=\t" + HEX_KEY,  # N27: tabs are whitespace too
])
def test_separator_forms_in_the_documented_order_are_redacted(value):
    # N26: optional whitespace, optional quote, optional whitespace, then one of = : =>; every documented form is key-assignment
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


@pytest.mark.parametrize("value", [
    "secret\u017f=" + HEX_KEY,  # U+017F (long s) after the label: the label ends at a non-ASCII character
    "token\u212a:" + HEX_KEY,  # U+212A (Kelvin sign) after the label
    "seed\u0131=" + "fedcba9876543210fedcba9876543210",  # U+0131 (dotless i) after the label, a 32-digit hex key
    "token\u00e9:" + HEX_KEY,  # a Unicode letter as a suffix is a word character: still matched
])
def test_non_ascii_character_after_a_label_ends_the_label(value):
    # N29: the label-end lookahead is ASCII-only, so these forms are matched, and the key is redacted
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


def test_ascii_letter_after_a_label_still_blocks_the_match():
    # N29 control: secretx is not the label secret followed by a non-letter, so no key-assignment match
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    value = "secretx=" + HEX_KEY
    assert a30._secret_kind(value, strict=False) == ""
    assert module.path_label(value, 1) == value


@pytest.mark.parametrize("label", ["TOKEN", "Token", "SECRET"])
def test_label_matches_in_upper_and_mixed_case(label):
    # N29: the label name keeps its case-insensitive match
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    value = label + ":" + HEX_KEY
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"


def test_value_class_keeps_the_non_ascii_letters_it_matched_before():
    # N29 value class: U+017F inside a key still belongs to the value (case folding kept), so the key is not split and is flagged
    module = load_script()
    a30 = sys.modules["swarm.tools.a30_tools"]
    key = "kJ8mPq2RvT5wXy7ZbN4cFh9LgD3sA6eU"
    value = "secret=" + key[:10] + "\u017f" + key[10:] + "vB1nM6"
    assert a30._secret_kind(value, strict=False) == "key-assignment"
    assert module.path_label(value, 1) == "<redacted-path-1>"
