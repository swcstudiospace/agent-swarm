"""Behaviour tests for scripts/dapp_wallet_check.py (A29) and its Bun twin.

Real-mode tests build fixture trees in tmp_path and assert findings (id, severity, location), exit codes and the JSON
shape; the dry-run contract is asserted separately. No fixture value that looks like a secret is ever expected in the
output: findings carry locations and lengths only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT, run_script

SCRIPT = "dapp_wallet_check.py"
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
HAS_BUN = Path(BUN).exists()
INFURA_KEY = "9aa3d95b3bc440fa88ea12eaa4456161"
FAKE_PRIVATE_KEY = "0x" + "ab" * 32
MNEMONIC = "test test test test test test test test test test test junk"


def module():
    spec = importlib.util.spec_from_file_location("dapp_wallet_check_under_test", ROOT / "scripts" / SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def scan(root: Path, *args: str) -> tuple[int, dict, str]:
    result = run_script(SCRIPT, "--root", str(root), "--json", *args)
    return result.returncode, json.loads(result.stdout), result.stdout


def found(report: dict) -> set[tuple[str, str, str]]:
    return {(f["id"], f["severity"], f["location"]) for f in report["findings"]}


# --- dry run ---------------------------------------------------------------------------------------------------


def test_dry_run_is_canned_deterministic_and_reads_nothing(tmp_path):
    missing = tmp_path / "does-not-exist"
    first = run_script(SCRIPT, "--dry-run", "--json", "--root", str(missing))
    second = run_script(SCRIPT, "--dry-run", "--json", "--root", str(missing))
    assert first.returncode == 0 and first.stdout == second.stdout and first.stderr == ""
    report = json.loads(first.stdout)
    assert report["status"] == "ok" and report["dry_run"] is True and report["agent"] == "A29" and report["script"] == "dapp_wallet_check"
    assert report["findings"] == [] and report["counts"] == {"info": 0, "minor": 0, "major": 0, "blocker": 0}
    assert (report["files_scanned"], report["files_skipped"], report["truncated"], report["skipped"]) == (0, 0, False, [])
    assert not missing.exists()
    assert run_script(SCRIPT, "--dry-run", "--json").returncode == 0  # no other argument is needed


# --- real mode: shape, status and exit codes -------------------------------------------------------------------


def test_clean_tree_is_ok_with_exit_zero_and_stable_shape(tmp_path):
    write(tmp_path, "src/app.ts", 'const chainId = await provider.getChainId();\nawait simulateContract(client, request);\nawait writeContract(client, request);\n')
    code, report, _ = scan(tmp_path)
    assert code == 0 and report["status"] == "ok" and report["findings"] == []
    assert set(report) == {"findings", "counts", "files_scanned", "files_skipped", "truncated", "skipped", "summary", "status", "agent", "script"}
    assert report["files_scanned"] == 1 and report["counts"] == {"info": 0, "minor": 0, "major": 0, "blocker": 0}
    assert report["summary"] == "1 file(s) scanned, 0 finding(s) (0 major or worse)"


def test_status_fails_on_major_or_worse_but_not_on_minor_or_info(tmp_path):
    write(tmp_path, "a.ts", 'const p = window.ethereum;\nawait signer.sendTransaction(tx);\nconst chainId = 1;\n')
    code, report, _ = scan(tmp_path)
    assert code == 0 and report["status"] == "ok"
    assert found(report) == {("DAPP-WINDOW-ETHEREUM", "info", "a.ts:1"), ("DAPP-NO-SIMULATION", "minor", "a.ts:2")}
    write(tmp_path, "b.ts", 'await ethereum.request({ method: "eth_sign" });\n')
    code, report, _ = scan(tmp_path)
    assert code == 1 and report["status"] == "fail" and report["counts"] == {"info": 1, "minor": 1, "major": 0, "blocker": 1}
    assert report["findings"][0]["id"] == "DAPP-ETH-SIGN"  # most severe first


def test_findings_are_sorted_and_output_is_deterministic(tmp_path):
    write(tmp_path, "z.ts", 'x.request({ method: "eth_sign" });\n')
    write(tmp_path, "a.ts", 'x.request({ method: "eth_sign" });\ny.approve(s, MaxUint256);\n')
    _, first, text = scan(tmp_path)
    assert [f["location"] for f in first["findings"]] == ["a.ts:1", "z.ts:1", "a.ts:2"]
    assert scan(tmp_path)[2] == text


# --- real mode: one test per rule family -----------------------------------------------------------------------


def test_eth_sign_and_legacy_typed_data(tmp_path):
    write(tmp_path, "src/sign.ts", 'const a = 1;\nawait provider.request({ method: "eth_sign", params: [account, hash] });\n'
                                   "await provider.request({ method: 'eth_signTypedData', params: [] });\n"
                                   'await provider.request({ method: "eth_signTypedData_v4", params: [] });\n'
                                   'await provider.request({ method: "personal_sign", params: [] });\n')
    _, report, _ = scan(tmp_path)
    assert found(report) >= {("DAPP-ETH-SIGN", "blocker", "src/sign.ts:2"), ("DAPP-TYPED-DATA-LEGACY", "minor", "src/sign.ts:3")}
    assert sum(1 for f in report["findings"] if f["id"] == "DAPP-TYPED-DATA-LEGACY") == 1  # v4 is not the legacy format
    assert sum(1 for f in report["findings"] if f["id"] == "DAPP-ETH-SIGN") == 1  # personal_sign and v4 are fine


def test_unlimited_approval_variants(tmp_path):
    write(tmp_path, "src/approve.ts",
          "await token.approve(spender, ethers.constants.MaxUint256);\n"                       # line 1: ethers v5
          "await token.approve(spender, 1000n);\n"                                              # line 2: exact amount
          "await token.approve(\n  spender,\n  MaxUint256\n);\n"                                # lines 3-6: multi-line
          "await token.increaseAllowance(spender, 2n ** 256n - 1n);\n"                          # line 7
          "await writeContract({ functionName: 'approve', args: [spender, maxUint256] });\n"    # line 8: object style
          "await token.approve(spender, '0x" + "f" * 64 + "');\n"                              # line 9: max as hex
          "const MaxUint256 = 5;\n"                                                             # line 10: no approve call nearby
          "await token.approve(spender, amount);\n"                                             # line 11: the max above is another call
          "const unrelated = MaxUint256;\n")
    _, report, _ = scan(tmp_path)
    flagged = {f["location"] for f in report["findings"] if f["id"] == "DAPP-UNLIMITED-APPROVAL"}
    assert flagged == {"src/approve.ts:1", "src/approve.ts:3", "src/approve.ts:7", "src/approve.ts:8", "src/approve.ts:9"}
    assert {f["severity"] for f in report["findings"] if f["id"] == "DAPP-UNLIMITED-APPROVAL"} == {"major"}


def test_set_approval_for_all_is_a_minor_prompt_to_review(tmp_path):
    write(tmp_path, "nft.tsx", "await nft.setApprovalForAll(operator, true);\n")
    _, report, _ = scan(tmp_path)
    assert found(report) == {("DAPP-APPROVAL-FOR-ALL", "minor", "nft.tsx:1")}


def test_rpc_keys_credentials_and_cleartext_transport(tmp_path):
    write(tmp_path, "src/rpc.ts",
          f'const infura = "https://mainnet.infura.io/v3/{INFURA_KEY}";\n'                                  # 1
          'const alchemy = createPublicClient({ transport: http("https://eth-mainnet.g.alchemy.com/v2/abcDEF1234567890abcdEF1234567890") });\n'  # 2
          'const basic = new JsonRpcProvider("https://user:hunter2@rpc.example.com");\n'                       # 3
          'const clear = new JsonRpcProvider("http://rpc.example.com");\n'                                    # 4
          'const local = new JsonRpcProvider("http://localhost:8545");\n'                                     # 5
          'const query = new JsonRpcProvider("https://rpc.example.com/?apikey=abcdef123456");\n'               # 6
          'const image = "https://cdn.example.com/assets/9aa3d95b3bc440fa88ea12eaa4456161/logo.png";\n'      # 7: not an RPC line
          'const ok = new JsonRpcProvider("https://rpc.ankr.com/eth");\n')                                    # 8
    code, report, text = scan(tmp_path)
    assert found(report) == {
        ("DAPP-RPC-KEY", "major", "src/rpc.ts:1"), ("DAPP-RPC-KEY", "major", "src/rpc.ts:2"), ("DAPP-RPC-CREDENTIALS", "blocker", "src/rpc.ts:3"),
        ("DAPP-RPC-INSECURE", "major", "src/rpc.ts:4"), ("DAPP-RPC-KEY", "major", "src/rpc.ts:6"),
    }
    assert code == 1
    for secret in (INFURA_KEY, "abcDEF1234567890abcdEF1234567890", "hunter2", "abcdef123456"):
        assert secret not in text  # locations and lengths only, never the value
    key_finding = next(f for f in report["findings"] if f["location"] == "src/rpc.ts:1")
    assert "32 chars" in key_finding["summary"]


def test_key_material_storage_and_logging_never_echo_the_value(tmp_path):
    write(tmp_path, "src/keys.ts",
          f'const privateKey = "{FAKE_PRIVATE_KEY}";\n'                       # 1
          f'const cfg = {{ mnemonic: "{MNEMONIC}" }};\n'                       # 2
          "const fromEnv = process.env.PRIVATE_KEY;\n"                         # 3: a reference, not a literal
          "const privateKey2 = loadKey();\n"                                   # 4
          'localStorage.setItem("mnemonic", phrase);\n'                        # 5
          'console.log("debug", privateKey);\n'                                # 6
          'console.log("connected", account);\n'                               # 7
          "const w = new ethers.Wallet(key, provider);\n"                      # 8
          "const acct = privateKeyToAccount(k);\n")                            # 9
    _, report, text = scan(tmp_path)
    assert found(report) == {
        ("DAPP-KEY-MATERIAL", "blocker", "src/keys.ts:1"), ("DAPP-KEY-MATERIAL", "blocker", "src/keys.ts:2"),
        ("DAPP-KEY-STORAGE", "blocker", "src/keys.ts:5"), ("DAPP-KEY-LOG", "blocker", "src/keys.ts:6"),
        ("DAPP-CLIENT-SIGNER", "major", "src/keys.ts:8"), ("DAPP-CLIENT-SIGNER", "major", "src/keys.ts:9"),
    }
    assert FAKE_PRIVATE_KEY not in text and MNEMONIC not in text and "ab" * 32 not in text
    assert "66 chars" in next(f for f in report["findings"] if f["location"] == "src/keys.ts:1")["summary"]


def test_blind_signing_prompts(tmp_path):
    write(tmp_path, "src/login.ts",
          "await signer.signMessage(ethers.utils.arrayify(hash));\n"            # 1
          "await signer.signMessage(getBytes(digest));\n"                       # 2
          "await walletClient.signMessage({ account, message: { raw: hash } });\n"  # 3
          'await signer.signMessage("Sign in to Example");\n'                  # 4: readable text
          "await signer.signMessage(message);\n")                              # 5
    _, report, _ = scan(tmp_path)
    assert found(report) == {("DAPP-BLIND-SIGN", "major", "src/login.ts:1"), ("DAPP-BLIND-SIGN", "major", "src/login.ts:2"),
                             ("DAPP-BLIND-SIGN", "major", "src/login.ts:3")}


def test_missing_chain_check_and_missing_simulation(tmp_path):
    write(tmp_path, "src/send.ts", "const tx = build();\nawait signer.sendTransaction(tx);\nawait signer.sendTransaction(tx2);\n")
    write(tmp_path, "src/typed.ts", "await signer._signTypedData(domain, types, value);\n")
    write(tmp_path, "src/guarded.ts", "const { chainId } = await provider.getNetwork();\nawait signer.sendTransaction(tx);\nawait estimateGas(tx);\n")
    write(tmp_path, "src/viem.ts", "await publicClient.simulateContract(req);\nawait walletClient.writeContract(req);\n")
    _, report, _ = scan(tmp_path)
    assert found(report) == {
        ("DAPP-NO-CHAIN-CHECK", "major", "src/send.ts:2"), ("DAPP-NO-SIMULATION", "minor", "src/send.ts:2"),
        ("DAPP-NO-CHAIN-CHECK", "major", "src/typed.ts:1"), ("DAPP-NO-SIMULATION", "minor", "src/typed.ts:1"),
        ("DAPP-NO-CHAIN-CHECK", "major", "src/viem.ts:2"),
    }


def test_comment_lines_and_declaration_files_are_ignored(tmp_path):
    write(tmp_path, "src/notes.ts", '// await provider.request({ method: "eth_sign" });\n/* token.approve(s, MaxUint256) */\n * eth_sign "eth_sign"\n')
    write(tmp_path, "src/types.d.ts", 'declare const x: "eth_sign";\nawait y.request({ method: "eth_sign" });\n')
    _, report, _ = scan(tmp_path)
    assert report["findings"] == [] and report["files_scanned"] == 1


# --- real mode: .env, JSON and YAML ----------------------------------------------------------------------------


def test_env_files_flag_only_client_exposed_variables(tmp_path):
    write(tmp_path, ".env.local",
          "NEXT_PUBLIC_PRIVATE_KEY=0xabc123abc123abc123\n"                                           # 1
          f"NEXT_PUBLIC_RPC_URL=https://mainnet.infura.io/v3/{INFURA_KEY}\n"                         # 2
          f"RPC_URL=https://mainnet.infura.io/v3/{INFURA_KEY}\n"                                     # 3: server side, fine
          "VITE_MNEMONIC=\n"                                                                          # 4: empty
          "NEXT_PUBLIC_CHAIN_ID=1\n"                                                                  # 5
          "# NEXT_PUBLIC_SECRET=x\n"                                                                  # 6
          'export VITE_API_SECRET="topsecretvalue"\n')                                                # 7
    _, report, text = scan(tmp_path)
    assert found(report) == {("DAPP-PUBLIC-SECRET-ENV", "blocker", ".env.local:1"), ("DAPP-RPC-KEY", "major", ".env.local:2"),
                             ("DAPP-PUBLIC-SECRET-ENV", "blocker", ".env.local:7")}
    assert "abc123abc123" not in text and "topsecretvalue" not in text and INFURA_KEY not in text
    write(tmp_path, ".env.example", "NEXT_PUBLIC_SEED=word\n")
    assert (".env.example:1" in {f["location"] for f in scan(tmp_path)[1]["findings"]})


def test_json_configuration_is_parsed_by_key_and_value(tmp_path):
    write(tmp_path, "config/chains.json", json.dumps({
        "chains": [{"id": 1, "rpcUrls": [f"https://rpc.example.com/v3/{INFURA_KEY}", "https://u:pw@rpc.example.com", "https://rpc.ankr.com/eth"]}],
        "wallet": {"privateKey": "abcdef0123456789", "mnemonic": "${MNEMONIC}", "seedPhrase": ""},
        "docs": "https://cdn.example.com/9aa3d95b3bc440fa88ea12eaa4456161",
    }))
    write(tmp_path, "config/broken.json", "{ not json")
    _, report, text = scan(tmp_path)
    assert found(report) == {
        ("DAPP-RPC-KEY", "major", "config/chains.json#chains[0].rpcUrls[0]"), ("DAPP-RPC-CREDENTIALS", "blocker", "config/chains.json#chains[0].rpcUrls[1]"),
        ("DAPP-KEY-MATERIAL", "blocker", "config/chains.json"), ("DAPP-CONFIG-PARSE", "info", "config/broken.json"), ("scan/incomplete", "major", "."),
    }
    assert "abcdef0123456789" not in text and INFURA_KEY not in text and "pw@" not in text
    assert "16 chars" in next(f for f in report["findings"] if f["id"] == "DAPP-KEY-MATERIAL")["summary"]


def test_yaml_configuration_when_pyyaml_is_available(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "config.yaml", "rpc_url: https://rpc.example.com/?apikey=abcdef123456\nsigner:\n  private_key: deadbeefdeadbeef\nnote: plain\n")
    write(tmp_path, "bad.yml", "key: [unclosed\n")
    code, report, text = scan(tmp_path)
    assert code == 1 and len(report["skipped"]) == 1 and report["skipped"][0].startswith("bad.yml: skipped:unparsable yml")  # old: skipped == []; the unparsed file is now a reason
    assert found(report) == {("DAPP-RPC-KEY", "major", "config.yaml#rpc_url"), ("DAPP-KEY-MATERIAL", "blocker", "config.yaml"),
                             ("DAPP-CONFIG-PARSE", "info", "bad.yml"), ("scan/incomplete", "major", ".")}
    assert "abcdef123456" not in text and "deadbeefdeadbeef" not in text


def test_yaml_checks_report_tool_missing_when_pyyaml_is_absent(tmp_path, monkeypatch):
    loaded = module()
    monkeypatch.setattr(loaded, "_load_yaml", lambda: None)
    write(tmp_path, "config.yaml", "rpc_url: https://rpc.example.com/?apikey=abcdef123456\n")
    write(tmp_path, "src/a.ts", 'x.request({ method: "eth_sign" });\n')
    report = loaded.scan_tree(tmp_path, tmp_path)
    assert report["skipped"] == ["yaml-config-structure: skipped:tool-missing (PyYAML is not installed)"]
    assert (report["files_scanned"], report["files_skipped"]) == (1, 1)
    assert [f["id"] for f in report["findings"]] == ["DAPP-ETH-SIGN", "scan/incomplete"] and report["status"] == "fail"  # JSON and code checks stay native
    only_yaml = tmp_path / "only"
    write(only_yaml, "c.yml", "a: b\n")
    clean = loaded.scan_tree(only_yaml, only_yaml)
    assert clean["status"] == "fail" and clean["skipped"] != []  # old: ok; the only in-scope file was not analysed, so the scan is incomplete


# --- real mode: bounds and traversal ----------------------------------------------------------------------------


def test_large_binary_and_ignored_directories_are_skipped(tmp_path):
    hazard = 'x.request({ method: "eth_sign" });\n'
    write(tmp_path, "big.ts", hazard + "// " + "x" * (1 << 20) + "\n")
    (tmp_path / "bin.js").write_bytes(b"\x00\x01" + hazard.encode())
    write(tmp_path, "node_modules/pkg/index.js", hazard)
    write(tmp_path, "dist/bundle.js", hazard)
    write(tmp_path, ".git/hooks/x.js", hazard)
    write(tmp_path, "src/real.ts", hazard)
    write(tmp_path, "notes.txt", hazard)
    _, report, _ = scan(tmp_path)
    assert found(report) == {("DAPP-ETH-SIGN", "blocker", "src/real.ts:1"), ("scan/incomplete", "major", ".")}  # the skipped files make the scan incomplete
    assert (report["files_scanned"], report["files_skipped"]) == (1, 2)  # big.ts and bin.js


def test_file_at_exactly_one_mebibyte_is_scanned(tmp_path):
    line = 'x.request({ method: "eth_sign" });\n'
    body = line + "/" * ((1 << 20) - len(line))
    write(tmp_path, "edge.ts", body)
    assert len(body.encode()) == 1 << 20
    _, report, _ = scan(tmp_path)
    assert report["files_scanned"] == 1 and found(report) == {("DAPP-ETH-SIGN", "blocker", "edge.ts:1")}


def test_scan_stops_at_the_file_limit(tmp_path, monkeypatch):
    loaded = module()
    monkeypatch.setattr(loaded, "MAX_FILES", 3)
    for index in range(5):
        write(tmp_path, f"f{index}.ts", "const a = 1;\n")
    report = loaded.scan_tree(tmp_path, tmp_path)
    assert report["files_scanned"] == 3 and report["truncated"] is True and "stopped at the 3-file limit" in report["summary"]
    monkeypatch.setattr(loaded, "MAX_FILES", 5)
    assert loaded.scan_tree(tmp_path, tmp_path)["truncated"] is False  # exactly at the limit is not truncated


def test_symlinks_leaving_the_root_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "root"
    write(outside, "secret.ts", 'x.request({ method: "eth_sign" });\n')
    write(outside, "dir/inner.ts", 'x.request({ method: "eth_sign" });\n')
    write(root, "src/ok.ts", "const a = 1;\n")
    os.symlink(outside / "secret.ts", root / "src" / "link.ts")
    os.symlink(outside / "dir", root / "linked-dir")
    os.symlink(root / "src" / "ok.ts", root / "src" / "inside.ts")  # a link that stays inside the root is read
    code, report, _ = scan(root)
    assert code == 1 and [f["id"] for f in report["findings"]] == ["scan/incomplete"] and report["status"] == "fail"  # old: code 0; a skipped link is not ok
    assert report["files_scanned"] == 2 and report["files_skipped"] == 1


def test_path_option_scopes_the_scan_and_rejects_escapes(tmp_path):
    write(tmp_path, "a/x.ts", 'x.request({ method: "eth_sign" });\n')
    write(tmp_path, "b/y.ts", "const ok = 1;\n")
    code, report, _ = scan(tmp_path, "--path", "b")
    assert code == 0 and report["findings"] == [] and report["files_scanned"] == 1
    code, report, _ = scan(tmp_path, "--path", "a/x.ts")
    assert code == 1 and found(report) == {("DAPP-ETH-SIGN", "blocker", "a/x.ts:1")}
    for bad in ("../elsewhere", "missing"):
        code, report, _ = scan(tmp_path, "--path", bad)
        assert code == 2 and report["status"] == "error" and report["error"]["code"] == "E-INPUT"


# --- the shared URL rule ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("url,codes", [
    ("https://rpc.ankr.com/eth", []),
    ("https://eth.llamarpc.com", []),
    (f"https://mainnet.infura.io/v3/{INFURA_KEY}", ["path-key"]),
    ("https://eth-mainnet.g.alchemy.com/v2/abcDEF1234567890abcdEF1234567890", ["path-key"]),
    ("https://nd-123-456-789.p2pify.com/350670db-19fa-4704-a166-e52e178b59d2", ["path-key"]),
    ("https://rpc.example.com/ethereum-mainnet-archive-node", []),
    ("https://rpc.example.com/?apikey=abcdef123456", ["query-key"]),
    ("https://rpc.example.com/?APIKEY=abcdef123456", ["query-key"]),
    ("https://rpc.example.com/?key=short", []),
    ("https://rpc.example.com/?chain=mainnet", []),
    ("https://user:pw@rpc.example.com", ["credentials"]),
    ("https://token@rpc.example.com", ["credentials"]),
    ("http://rpc.example.com", ["insecure-scheme"]),
    ("ws://rpc.example.com", ["insecure-scheme"]),
    ("wss://rpc.example.com", []),
    ("http://localhost:8545", ["local-endpoint"]),
    ("http://127.0.0.1:8545", ["local-endpoint"]),
    ("http://[::1]:8545", ["local-endpoint"]),
    ("ftp://rpc.example.com", ["scheme"]),
])
def test_rpc_url_inspect_rows(url, codes):
    assert [issue["code"] for issue in module().rpc_url_inspect(url)["issues"]] == codes


def test_rpc_url_inspect_malformed_and_details_never_contain_the_secret():
    inspect = module().rpc_url_inspect
    for bad in ("not a url", "https://", "rpc.example.com", "https://rpc.example.com:notaport", "https://exa\nmple.com", ""):
        result = inspect(bad)
        assert result["valid"] is False and [i["code"] for i in result["issues"]] == ["malformed"], bad
    result = inspect(f"https://user:hunter2@mainnet.infura.io/v3/{INFURA_KEY}?apikey=abcdef123456")
    assert [i["code"] for i in result["issues"]] == ["credentials", "path-key", "query-key"]
    assert result["host"] == "mainnet.infura.io" and result["scheme"] == "https"
    blob = json.dumps(result)
    assert "hunter2" not in blob and INFURA_KEY not in blob and "abcdef123456" not in blob
    assert "7 chars" in result["issues"][0]["detail"]  # the password length
    assert inspect("HTTPS://RPC.Example.COM")["host"] == "rpc.example.com"


# --- Bun twin -------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_bun_twin_matches_python_in_dry_run_and_real_mode(tmp_path):
    write(tmp_path, "src/a.ts", 'x.request({ method: "eth_sign" });\n')
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    for args in (["--dry-run", "--json"], ["--root", str(tmp_path), "--json"]):
        py = subprocess.run(["python3", str(ROOT / "scripts" / SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT)
        ts = subprocess.run([BUN, str(ROOT / "scripts" / "ts" / "dapp_wallet_check.ts"), *args], capture_output=True, text=True, env=env, cwd=ROOT)
        assert ts.returncode == py.returncode and json.loads(ts.stdout) == json.loads(py.stdout)
    assert json.loads(ts.stdout)["findings"][0]["id"] == "DAPP-ETH-SIGN" and ts.returncode == 1


# --- independent review (A29 reviewer-fixer): regressions with exact values ------------------------------------------

ALCHEMY_STYLE_KEY = "Zu8mQ-aB3x9_QzPLkd0e1f2g3h4i5j6k"


@pytest.mark.parametrize("url,codes", [
    ("http://127.0.0.2:8545", ["local-endpoint"]),
    ("http://127.255.255.254", ["local-endpoint"]),
    ("http://192.168.1.5:8545", ["local-endpoint"]),
    ("http://10.0.0.1", ["local-endpoint"]),
    ("http://172.16.0.9", ["local-endpoint"]),
    ("http://169.254.169.254/latest", ["local-endpoint"]),
    ("http://0.0.0.0:8545", ["local-endpoint"]),
    ("http://[fe80::1]:8545", ["local-endpoint"]),
    ("http://rpc.localhost:8545", ["local-endpoint"]),
    ("http://node:8545", ["local-endpoint"]),
    ("http://printer.local", ["local-endpoint"]),
    ("http://1.1.1.1", ["insecure-scheme"]),
    ("https://8.8.8.8", []),
    ("https://rpc.example.com./eth", []),
    (f"https://eth.example.net/v2/{ALCHEMY_STYLE_KEY}", ["path-key"]),
    ("https://eth.example.net/v2/Zu8mQ_aB3x9-QzPLkd0e1f2g3h4i5j6k7l8m9n0", ["path-key"]),
    ("https://rpc.example.com/ethereum-mainnet-archive", []),
    ("https://rpc.example.com/polygon-mainnet-fullnode-2", []),
    ("https://rpc.example.com/eth_mainnet_archive_node", []),
    ("https://rpc.example.com/v3/eth-sepolia", []),
])
def test_rpc_url_inspect_local_ranges_and_hyphenated_keys(url, codes):
    assert [issue["code"] for issue in module().rpc_url_inspect(url)["issues"]] == codes, url


def test_rpc_url_inspect_trailing_dot_host_is_normalized():
    assert module().rpc_url_inspect("https://RPC.Example.com./eth")["host"] == "rpc.example.com"
    assert module().rpc_url_inspect("https://./x")["valid"] is False


def test_decimal_max_uint256_literal_is_an_unlimited_approval(tmp_path):
    write(tmp_path, "src/a.ts", 'await token.approve(spender, "115792089237316195423570985008687907853269984665640564039457584007913129639935");\n'
                               'await token.approve(spender, 115792089237316195423570985008687907853269984665640564039457584007913129639935n);\n'
                               'await token.approve(spender, "115792089237316195423570985008687907853269984665640564039457584007913129639934");\n'
                               'await token.approve(spender, parseUnits("100", 6));\n')
    _, report, _ = scan(tmp_path)
    assert {(f["id"], f["location"]) for f in report["findings"] if f["id"] == "DAPP-UNLIMITED-APPROVAL"} == {
        ("DAPP-UNLIMITED-APPROVAL", "src/a.ts:1"), ("DAPP-UNLIMITED-APPROVAL", "src/a.ts:2")}


def test_env_inline_comments_do_not_hide_an_rpc_key_or_a_secret_variable(tmp_path):
    write(tmp_path, ".env.production",
          f"VITE_RPC_URL=https://mainnet.infura.io/v3/{INFURA_KEY} # prod\n"                       # 1
          f'NEXT_PUBLIC_RPC="https://eth.example.net/v2/{ALCHEMY_STYLE_KEY}"   # quoted\n'       # 2
          f"VITE_WS_URL=wss://eth.example.net/ws/{ALCHEMY_STYLE_KEY}\n"                            # 3
          f"VITE_SECRET_KEY=abcdef0123456789 # {INFURA_KEY}\n"                                     # 4
          "VITE_BASE=https://rpc.example.com/path#anchor\n"                                       # 5: a # without a space is part of the value
          "VITE_NOTE=plain # comment\n")                                                          # 6
    _, report, text = scan(tmp_path)
    assert found(report) == {("DAPP-RPC-KEY", "major", ".env.production:1"), ("DAPP-RPC-KEY", "major", ".env.production:2"),
                             ("DAPP-RPC-KEY", "major", ".env.production:3"), ("DAPP-PUBLIC-SECRET-ENV", "blocker", ".env.production:4")}
    for secret in (INFURA_KEY, ALCHEMY_STYLE_KEY, "abcdef0123456789"):
        assert secret not in text
    assert "16 chars" in next(f for f in report["findings"] if f["location"].endswith(":4"))["summary"]  # the value without its comment


def test_findings_output_is_capped_but_counts_stay_complete(tmp_path):
    for index in range(600):
        write(tmp_path, f"src/f{index:04d}.ts", 'await provider.request({ method: "eth_sign" });\n')
    code, report, text = scan(tmp_path)
    assert code == 1 and report["counts"]["blocker"] == 600
    assert len(report["findings"]) == 500 and report["truncated"] is True
    assert report["skipped"] == ["findings: output capped at 500 of 600 (counts are complete)"]
    assert len(text) < 256 * 1024
    assert report["summary"] == "600 file(s) scanned, 600 finding(s) (600 major or worse); only the 500 most severe are listed"


def test_script_never_echoes_secrets_from_any_source_kind(tmp_path):
    canary_url = f"https://user:hunter2Zq8@node.example.org/v2/{ALCHEMY_STYLE_KEY}?apikey=AKIAZ7Q2X5LMN3B8T6YR"
    canary_key = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
    write(tmp_path, "src/a.ts", f'const rpc = new JsonRpcProvider("{canary_url}");\nconst privateKey = "0x{canary_key}";\nconst mnemonic = "{MNEMONIC}";\n'
                                f'localStorage.setItem("privateKey", "{canary_key}");\n')
    write(tmp_path, ".env", f"NEXT_PUBLIC_PRIVATE_KEY={canary_key}\nVITE_RPC={canary_url}\n")
    write(tmp_path, "c.json", json.dumps({"rpcUrl": canary_url, "wallet": {"privateKey": canary_key, "mnemonic": MNEMONIC}}))
    _, report, text = scan(tmp_path)
    assert report["counts"]["blocker"] >= 6
    for secret in ("hunter2Zq8", ALCHEMY_STYLE_KEY, "AKIAZ7Q2X5LMN3B8T6YR", canary_key, MNEMONIC):
        assert secret not in text, secret


def test_long_minified_lines_scan_in_linear_time(tmp_path):
    # unbounded `[^)]*` rules were quadratic: one 1 MiB line of repeated calls took minutes
    line = ("localStorage.setItem(x," * 45000)[:300_000] + "\nconsole.log(x," * 1 + ("console.log(x," * 60000)[:100_000] + "\n"
    write(tmp_path, "public/vendor.min.js", line + ("signMessage({" * 60000)[:100_000] + "\n")
    import time
    start = time.monotonic()
    code, report, _ = scan(tmp_path)
    assert time.monotonic() - start < 20 and code == 0 and report["files_scanned"] == 1
    write(tmp_path, "public/hit.js", "console.log('privateKey', k); localStorage.setItem('mnemonic', m); signMessage({ raw: h });\n")
    ids = {f["id"] for f in scan(tmp_path)[1]["findings"]}
    assert {"DAPP-KEY-LOG", "DAPP-KEY-STORAGE", "DAPP-BLIND-SIGN"} <= ids


def test_secret_looking_mapping_keys_are_never_echoed_in_finding_locations(tmp_path):
    token_key, hex_key = "AKIAZ7Q2X5LMN3B8T6YR", "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
    write(tmp_path, "c.json", json.dumps({"keys": {token_key: {"privateKey": "abcdef0123456789"}, hex_key: {"rpcUrl": f"https://rpc.example.com/v3/{INFURA_KEY}"}}}))
    _, report, text = scan(tmp_path)
    assert {f["id"] for f in report["findings"]} == {"DAPP-KEY-MATERIAL", "DAPP-RPC-KEY"}
    for secret in (token_key, hex_key, INFURA_KEY, "abcdef0123456789"):
        assert secret not in text
    assert "keys.<key 20 chars>.privateKey" in text


def test_yaml_alias_cycle_does_not_stop_the_scan(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "config.yaml", "loop: &x {again: *x}\nrpc_url: https://rpc.example.com/?apikey=abcdef123456\n")
    code, report, text = scan(tmp_path)
    assert code == 1 and report["status"] == "fail"
    assert found(report) == {("DAPP-RPC-KEY", "major", "config.yaml#rpc_url")}
    assert "abcdef123456" not in text


def test_alias_reached_under_two_keys_is_checked_under_each_key(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "config.yaml", "shared: &cfg {primary: 'https://rpc.example.com/?apikey=abcdef123456'}\nrpc: {ethereum: *cfg}\n")
    _, report, text = scan(tmp_path)
    assert ("DAPP-RPC-KEY", "major", "config.yaml#rpc.ethereum.primary") in found(report)
    assert "abcdef123456" not in text


def test_values_shared_through_many_aliases_stop_at_the_budget_and_findings_stay_capped(tmp_path):
    pytest.importorskip("yaml")
    shared = "".join(f'  privateKey{i}: "abcdef0123456789"\n' for i in range(10_000))
    fanout = "".join("- *shared\n" for _ in range(2_000))
    write(tmp_path, "config.yaml", f"shared: &shared\n{shared}items:\n{fanout}")
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "swarm-state")}
    # Unbounded work would stall here; the timeout turns that regression into a failure instead of a hung suite.
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / SCRIPT), "--root", str(tmp_path), "--json"], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=60)
    report = json.loads(proc.stdout)
    assert proc.returncode == 1 and report["status"] == "fail" and report["truncated"] is True
    assert len(report["findings"]) == 500 and report["counts"]["blocker"] > 500
    assert report["counts"]["info"] == 1  # the DAPP-CONFIG-PARSE that stopped the walk
    assert "abcdef0123456789" not in proc.stdout


# --- one-number IPv4 hosts ---------------------------------------------------------------------------------------------


def test_decimal_and_hex_ipv4_hosts_are_classed_as_addresses_not_single_label_names():
    inspect = module().rpc_url_inspect
    assert [i["code"] for i in inspect("http://134744072/rpc")["issues"]] == ["insecure-scheme"]  # decimal 8.8.8.8 is public
    assert [i["code"] for i in inspect("https://134744072/rpc")["issues"]] == []
    assert [i["code"] for i in inspect("http://0x08080808/rpc")["issues"]] == ["insecure-scheme"]  # hex 8.8.8.8
    assert [i["code"] for i in inspect("http://127.0.0.1:8545")["issues"]] == ["local-endpoint"]
    assert [i["code"] for i in inspect("http://2130706433:8545")["issues"]] == ["local-endpoint"]  # decimal 127.0.0.1
    assert [i["code"] for i in inspect("http://4294967296/rpc")["issues"]] == ["insecure-scheme"]  # beyond 32 bits: not a local address


# --- QA-03 remediation: regressions for the blocker and major fixes ----------------------------------------------------


def test_hash_prefixed_and_camel_case_key_literals_are_reported(tmp_path):
    key = "0x" + "ab" * 32
    write(tmp_path, "src/signer.ts", f'class Signer {{\n  #privateKey = "{key}";\n}}\n')
    write(tmp_path, "src/a.ts", f'const deployerPrivateKey = "{key}";\nconst MY_PRIVATE_KEY = "{key}";\nconst signerSecretKey = "{key}";\n')
    write(tmp_path, "src/m.ts", f'const deployerMnemonic = "{MNEMONIC}";\n')
    _, report, text = scan(tmp_path)
    assert found(report) == {
        ("DAPP-KEY-MATERIAL", "blocker", "src/signer.ts:2"), ("DAPP-KEY-MATERIAL", "blocker", "src/a.ts:1"),
        ("DAPP-KEY-MATERIAL", "blocker", "src/a.ts:2"), ("DAPP-KEY-MATERIAL", "blocker", "src/a.ts:3"),
        ("DAPP-KEY-MATERIAL", "blocker", "src/m.ts:1"),
    }
    assert key not in text and MNEMONIC not in text


def test_every_key_literal_on_one_line_is_counted(tmp_path):
    key = "0x" + "ab" * 32
    write(tmp_path, "src/cfg.ts", f'const cfg = {{ privateKey: "{key}", mnemonic: "{MNEMONIC}" }};\n')
    _, report, text = scan(tmp_path)
    assert [f["id"] for f in report["findings"]] == ["DAPP-KEY-MATERIAL", "DAPP-KEY-MATERIAL"] and report["counts"]["blocker"] == 2
    assert key not in text and MNEMONIC not in text


def test_url_named_public_variables_are_not_secret_findings(tmp_path):
    write(tmp_path, ".env.local", "REACT_APP_SEED_PHRASE_URL=https://ok.example.org\nREACT_APP_SEED=abcdef0123456789\n")
    _, report, text = scan(tmp_path)
    assert found(report) == {("DAPP-PUBLIC-SECRET-ENV", "blocker", ".env.local:2")} and "abcdef0123456789" not in text


def test_url_scheme_is_read_when_urlsplit_refuses_the_url():
    loaded = module()
    assert loaded.url_scheme_userinfo("javascript://[%0aalert(document.cookie)") == ("javascript", "")
    assert loaded.url_scheme_userinfo("\x01 VBS\tcript://[x") == ("vbscript", "")
    assert loaded.url_scheme_userinfo("https://user@rpc.example.com") == ("https", "plain")


def test_rpc_scheme_detail_never_echoes_the_scheme_text():
    token = "sk1a2b3c4d5e6f7g8h9i0jKLMNOP"
    issues = module().rpc_url_inspect(f"{token}://polygon-rpc.com")["issues"]
    assert [i["code"] for i in issues] == ["scheme"] and token.lower() not in json.dumps(issues)


def test_unparsable_config_makes_the_scan_incomplete(tmp_path):
    write(tmp_path, "config/broken.json", "{ not json")
    code, report, _ = scan(tmp_path)
    assert code == 1 and report["status"] == "fail"
    assert found(report) == {("DAPP-CONFIG-PARSE", "info", "config/broken.json"), ("scan/incomplete", "major", ".")}
    assert report["skipped"] == ["config/broken.json: skipped:unparsable json (JSONDecodeError)"]


def test_scan_with_no_analysed_input_is_not_ok(tmp_path):
    write(tmp_path, "README.md", "# nothing in scope\n")
    code, report, _ = scan(tmp_path)
    assert code == 1 and report["status"] == "fail" and report["files_scanned"] == 0
    assert found(report) == {("scan/incomplete", "major", ".")}


def test_joined_string_literals_are_one_key_literal(tmp_path):
    write(tmp_path, "src/join.ts", 'const privateKey = "0x" + "ab12ab12ab12ab12";\nconst short = "0x" + "ab12";\n')
    _, report, text = scan(tmp_path)
    assert found(report) == {("DAPP-KEY-MATERIAL", "blocker", "src/join.ts:1")}  # "0x" + "ab12" is 6 chars, below the key length
    assert "18 chars" in report["findings"][0]["summary"] and "ab12ab12" not in text


def test_key_named_value_that_is_not_a_plain_literal_makes_the_scan_incomplete(tmp_path):
    write(tmp_path, "src/deploy.ts", 'const deployerPrivateKey = "0x" + "ab12"*16;\n')  # the report's exact shape
    code, report, text = scan(tmp_path)
    assert code == 1 and report["status"] == "fail"
    incomplete = [f for f in report["findings"] if f["id"] == "scan/incomplete"]
    assert len(incomplete) == 1 and incomplete[0]["severity"] == "major"
    assert "src/deploy.ts:1" in incomplete[0]["summary"]  # file and line named, never the value
    assert "ab12" not in text


def test_env_references_type_names_and_nullish_values_are_not_incomplete(tmp_path):
    write(tmp_path, "src/ok.ts", "const privateKey = process.env.PRIVATE_KEY;\nconst mnemonic = null;\ninterface W { privateKey: string; mnemonic?: string }\n")
    code, report, _ = scan(tmp_path)
    assert code == 0 and report["status"] == "ok" and report["findings"] == []
