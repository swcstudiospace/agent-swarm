"""Behaviour tests for scripts/chain_config_check.py (A28): fixture trees in tmp_path, findings, exit codes, JSON shape, bounds."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "chain_config_check.py"
TWIN = ROOT / "scripts" / "ts" / "chain_config_check.ts"
KEYS = {"status", "files_scanned", "skipped", "checks", "truncated", "counts", "findings", "summary", "agent", "script"}


def module():
    spec = importlib.util.spec_from_file_location("chain_config_check_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(mod)
    return mod


def cli(tmp_path: Path, *args: str, root: Path | None = None) -> tuple[int, dict]:
    env = {**os.environ, "SWARM_DIR": str(tmp_path.parent / f"{tmp_path.name}-state")}  # event log stays outside the scanned tree
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(root or tmp_path), *args], capture_output=True, text=True, env=env, cwd=ROOT)
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def write(base: Path, rel: str, content) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def rules(tmp_path: Path, *args: str) -> dict[str, str]:
    """{rule: severity} for the whole tree (the highest severity per rule)."""
    code, out = cli(tmp_path, *args)
    order = ["info", "minor", "major", "blocker"]
    found: dict[str, str] = {}
    for f in out["findings"]:
        if f["rule"] not in found or order.index(f["severity"]) > order.index(found[f["rule"]]):
            found[f["rule"]] = f["severity"]
    assert code == (1 if out["status"] == "fail" else 0)
    return found


def net(chain_id=11155111, url="https://rpc.example.org/", name=None, **extra) -> dict:
    body = {"chainId": chain_id, "rpcUrl": url, **extra}
    if name:
        body["name"] = name
    return body


# --- contract -----------------------------------------------------------------------------------------------


def test_dry_run_is_canned_json_and_reads_nothing(tmp_path):
    write(tmp_path, "chains.json", net(1, "http://user:pw@x.example/"))  # would fail in real mode
    code, out = cli(tmp_path, "--dry-run")
    assert code == 0 and out["status"] == "ok" and out["dry_run"] is True and out["findings"] == [] and out["files_scanned"] == 0
    assert out["agent"] == "A28" and out["script"] == "chain_config_check" and KEYS <= set(out)
    assert cli(tmp_path, "--dry-run") == (code, out)


def test_ts_twin_passes_through_to_the_python_script():
    text = TWIN.read_text(encoding="utf-8")
    assert 'passthrough("chain_config_check")' in text


def test_real_mode_output_shape_on_an_empty_tree(tmp_path):
    code, out = cli(tmp_path)
    assert code == 0 and KEYS <= set(out) and out["status"] == "ok" and out["findings"] == [] and out["files_scanned"] == 0
    assert out["counts"] == {"info": 0, "minor": 0, "major": 0, "blocker": 0} and out["truncated"] is False
    assert out["checks"] == {"json": "no-files", "toml": "no-files", "yaml": "no-files", "env": "no-files"}
    assert out["summary"] == "0 finding(s): 0 blocker, 0 major, 0 minor, 0 info"


def test_clean_testnet_and_opted_in_mainnet_configs_pass(tmp_path):
    write(tmp_path, "networks.json", {
        "sepolia": net(11155111, "https://sepolia.example.org/${RPC_KEY}", "sepolia"),
        "mainnet": net(1, "wss://mainnet.example.org/ws?token=${RPC_TOKEN}", "Ethereum Mainnet", allowMainnet=True),
    })
    code, out = cli(tmp_path)
    assert (code, out["status"], out["findings"], out["files_scanned"]) == (0, "ok", [], 1)


def test_path_must_stay_inside_the_root_and_exist(tmp_path):
    code, out = cli(tmp_path, "--path", "../outside")
    assert code == 2 and out["error"]["code"] == "E-INPUT"
    code, out = cli(tmp_path, "--path", "missing.json")
    assert code == 2 and out["error"]["code"] == "E-INPUT"


def test_path_can_name_a_single_file(tmp_path):
    write(tmp_path, "a/bad.json", net(1))
    write(tmp_path, "b/other.json", net(1))
    code, out = cli(tmp_path, "--path", "a/bad.json")
    assert code == 1 and out["files_scanned"] == 1 and {f["file"] for f in out["findings"]} == {"a/bad.json"}


def test_output_is_deterministic_and_sorted_by_severity_then_file(tmp_path):
    write(tmp_path, "b.json", net(1, "https://u:p@x.example/"))
    write(tmp_path, "a.json", net(1))
    first = cli(tmp_path)
    assert first == cli(tmp_path)
    order = ["info", "minor", "major", "blocker"]
    keys = [(-order.index(f["severity"]), f["file"], f["rule"], f["location"]) for f in first[1]["findings"]]
    assert keys == sorted(keys) and first[1]["findings"][0]["severity"] == "blocker"


# --- URL rules ------------------------------------------------------------------------------------------------


def test_credentials_in_a_url_are_a_blocker_and_never_printed(tmp_path):
    write(tmp_path, "chains.json", net(11155111, "https://alice:hunter2@rpc.example.org/"))
    code, out = cli(tmp_path)
    assert code == 1 and out["status"] == "fail"
    finding = next(f for f in out["findings"] if f["rule"] == "rpc-url-credentials")
    assert (finding["severity"], finding["file"], finding["location"]) == ("blocker", "chains.json", "chains.json:rpcUrl")
    assert "hunter2" not in json.dumps(out) and "alice" not in json.dumps(out)


def test_username_only_userinfo_is_also_a_credential(tmp_path):
    write(tmp_path, "c.json", net(11155111, "https://token123@rpc.example.org/"))
    assert rules(tmp_path)["rpc-url-credentials"] == "blocker"


def test_api_keys_in_query_or_path_are_blockers_but_placeholders_are_not(tmp_path):
    write(tmp_path, "q.json", {"rpcUrls": ["https://x.example/rpc?apikey=abc123def", "https://x.example/rpc?KEY=abc", "https://x.example/rpc?apikey=${KEY}", "https://x.example/rpc?apikey=", "https://x.example/rpc?chain=eth"]})
    code, out = cli(tmp_path)
    flagged = sorted(f["location"] for f in out["findings"] if f["rule"] == "rpc-url-embedded-key")
    assert flagged == ["q.json:rpcUrls[0]", "q.json:rpcUrls[1]"] and code == 1


def test_long_token_like_path_segments_are_blockers(tmp_path):
    write(tmp_path, "p.json", {"rpcUrls": [
        "https://mainnet.example.io/v3/0123456789abcdef0123456789abcdef",  # infura-style
        "https://eth.example.com/v2/Ab3_dE-fGh1JkLmN0pQrStUvWxYz",  # alchemy-style, one hyphen
        "https://rpc.example.com/ethereum-mainnet-archive-node-v2-endpoint",  # words, many hyphens
        "https://rpc.example.com/v1/mainnet",
        "https://rpc.example.com/abcdefghijklmnopqrstuvwxyz",  # long but no digit
        "https://rpc.example.com/v3/${INFURA_API_KEY_FROM_ENV_00}",
    ]})
    _, out = cli(tmp_path)
    assert sorted(f["location"] for f in out["findings"] if f["rule"] == "rpc-url-embedded-key") == ["p.json:rpcUrls[0]", "p.json:rpcUrls[1]"]


def test_plaintext_schemes_are_major_unless_loopback(tmp_path):
    write(tmp_path, "s.json", {"rpcUrls": ["http://rpc.example.org", "ws://rpc.example.org/ws"]})
    code, out = cli(tmp_path)
    assert code == 1 and [(f["rule"], f["severity"]) for f in out["findings"]] == [("rpc-url-insecure-scheme", "major")] * 2
    assert [f["summary"] for f in out["findings"]] == ["the endpoint uses plaintext http: use https", "the endpoint uses plaintext ws: use wss"]


@pytest.mark.parametrize("url", ["http://localhost:8545", "http://127.0.0.1:8545", "ws://[::1]:8546"])
def test_loopback_plaintext_is_info_only(tmp_path, url):
    write(tmp_path, "l.json", net(31337, url, "hardhat"))
    code, out = cli(tmp_path)
    assert code == 0 and out["status"] == "ok" and [(f["rule"], f["severity"]) for f in out["findings"]] == [("rpc-url-local-plaintext", "info")]


def test_other_schemes_are_minor_and_malformed_urls_are_reported(tmp_path):
    write(tmp_path, "o.json", {"rpcUrls": ["ipc:///var/run/geth.ipc", "https://host:notaport/"]})
    found = rules(tmp_path)
    assert found == {"rpc-url-scheme": "minor", "rpc-url-malformed": "minor"}


def test_only_rpc_like_keys_are_treated_as_endpoints(tmp_path):
    write(tmp_path, "w.json", {"chainId": 11155111, "rpcUrl": "https://ok.example.org", "blockExplorerUrls": ["http://explorer.example.org"],
                               "faucetUrl": "http://faucet.example.org", "homepage": "http://example.org", "docs": {"url": "http://docs.example.org"}})
    code, out = cli(tmp_path)
    assert (code, out["findings"]) == (0, [])


def test_plain_url_key_counts_only_next_to_a_chain_id(tmp_path):
    write(tmp_path, "u.json", {"service": {"url": "http://example.org/"}, "net": {"chainId": 11155111, "url": "http://node.example.org/"}})
    _, out = cli(tmp_path)
    assert [f["location"] for f in out["findings"]] == ["u.json:net.url"]


def test_eip3085_and_viem_shaped_chain_objects(tmp_path):
    write(tmp_path, "add.json", {"chainId": "0xaa36a7", "chainName": "Sepolia", "rpcUrls": ["http://rpc.example.org"], "blockExplorerUrls": ["http://e.example.org"],
                                 "nativeCurrency": {"name": "Sepolia Ether", "symbol": "ETH", "decimals": 18}})
    write(tmp_path, "viem.json", {"id": 1, "name": "Ethereum", "nativeCurrency": {"symbol": "ETH"}, "rpcUrls": {"default": {"http": ["https://u:p@rpc.example.org"]}}})
    _, out = cli(tmp_path)
    assert sorted((f["file"], f["rule"]) for f in out["findings"]) == [("add.json", "rpc-url-insecure-scheme"), ("viem.json", "mainnet-without-opt-in"), ("viem.json", "rpc-url-credentials")]


# --- chain-id rules ---------------------------------------------------------------------------------------------


def test_mainnet_grade_chain_ids_need_an_opt_in_in_the_same_or_an_enclosing_object(tmp_path):
    write(tmp_path, "none.json", net(1))
    write(tmp_path, "same.json", net(1, allowMainnet=True))
    write(tmp_path, "text.json", net(137, mainnetOptIn="yes"))
    write(tmp_path, "outer.json", {"mainnet_approved": True, "networks": {"eth": net(1), "base": net(8453)}})
    write(tmp_path, "false.json", net(1, allowMainnet=False))
    _, out = cli(tmp_path)
    flagged = sorted(f["file"] for f in out["findings"] if f["rule"] == "mainnet-without-opt-in")
    assert flagged == ["false.json", "none.json"]
    assert all(f["severity"] == "major" for f in out["findings"] if f["rule"] == "mainnet-without-opt-in")


def test_unknown_chain_id_is_minor_and_treated_as_mainnet(tmp_path):
    write(tmp_path, "x.json", net(424242))
    assert rules(tmp_path) == {"mainnet-without-opt-in": "major", "chain-id-unknown": "minor"}
    write(tmp_path, "x.json", net(424242, allowMainnet=True))
    assert rules(tmp_path) == {"chain-id-unknown": "minor"}


@pytest.mark.parametrize("value,expected", [(1, "mainnet-without-opt-in"), ("1", "mainnet-without-opt-in"), ("0x1", "mainnet-without-opt-in"), ("0X1", "mainnet-without-opt-in"),
                                            (11155111, None), ("0xaa36a7", None)])
def test_chain_ids_parse_as_integers_decimal_text_and_hex_text(tmp_path, value, expected):
    write(tmp_path, "c.json", {"chain_id": value, "rpc_url": "https://x.example.org/"})
    assert set(rules(tmp_path)) == ({expected} if expected else set())


@pytest.mark.parametrize("value", [0, -5, True, "abc", "0xzz", 2**53, 1.5, None, ""])
def test_unparseable_chain_ids_are_minor(tmp_path, value):
    write(tmp_path, "c.json", {"chainId": value})
    assert rules(tmp_path) == {"chain-id-unparseable": "minor"}


@pytest.mark.parametrize("name,chain_id,rule,severity", [
    ("sepolia", 1, "chain-id-name-mismatch", "blocker"),
    ("Ethereum Mainnet", 11155111, "chain-id-name-mismatch", "blocker"),
    ("Base", 8453, None, None),
    ("base-sepolia", 8453, "chain-id-name-mismatch", "blocker"),
    ("my-testnet", 1, "chain-id-name-mismatch", "blocker"),
    ("staging-devnet", 137, "chain-id-name-mismatch", "blocker"),
    ("custom-mainnet", 11155111, "chain-id-name-mismatch", "major"),
    ("my-private-chain", 11155111, None, None),
    ("anvil", 31337, None, None),
])
def test_network_name_must_agree_with_the_chain_id(tmp_path, name, chain_id, rule, severity):
    write(tmp_path, "n.json", net(chain_id, name=name, allowMainnet=True))
    found = rules(tmp_path)
    assert found.get("chain-id-name-mismatch") == severity and (rule is None) == ("chain-id-name-mismatch" not in found)


def test_endpoint_host_must_agree_with_the_chain_id_class(tmp_path):
    write(tmp_path, "h1.json", net(1, "https://sepolia.example.org/", allowMainnet=True))
    write(tmp_path, "h2.json", net(11155111, "https://mainnet.example.org/"))
    write(tmp_path, "h3.json", net(1, "https://mainnet.example.org/", allowMainnet=True))
    _, out = cli(tmp_path)
    assert sorted(f["file"] for f in out["findings"] if f["rule"] == "rpc-host-chain-mismatch") == ["h1.json", "h2.json"]


def test_one_network_name_with_two_chain_ids_conflicts_across_files(tmp_path):
    write(tmp_path, "a.json", net(11155111, name="staging"))
    write(tmp_path, "b.json", net(80002, name="Staging"))
    write(tmp_path, "c.json", net(80002, name="other"))
    _, out = cli(tmp_path)
    conflicts = [f for f in out["findings"] if f["rule"] == "chain-id-conflict"]
    assert len(conflicts) == 1 and conflicts[0]["file"] == "b.json" and conflicts[0]["severity"] == "major" and "a.json" in conflicts[0]["summary"]


# --- file kinds -------------------------------------------------------------------------------------------------


def test_env_files_are_read_as_flat_key_values(tmp_path):
    write(tmp_path, ".env", "# comment\nexport CHAIN_ID=1\nRPC_URL=\"https://user:pw@rpc.example.org\"\nNETWORK=mainnet\nALLOW_MAINNET=true\n")
    write(tmp_path, ".env.staging", "CHAIN_ID=11155111\nRPC_URL=http://rpc.example.org\n")
    _, out = cli(tmp_path)
    assert sorted((f["file"], f["rule"]) for f in out["findings"]) == [(".env", "rpc-url-credentials"), (".env.staging", "rpc-url-insecure-scheme")]
    assert out["checks"]["env"] == "ran" and out["files_scanned"] == 2


def test_toml_files_are_parsed_with_the_standard_library(tmp_path):
    write(tmp_path, "foundry.toml", '[rpc_endpoints]\nmain = "https://u:p@rpc.example.org"\n\n[profile.default]\nchain_id = 1\n')
    code, out = cli(tmp_path)
    assert out["checks"]["toml"] == "ran" and code == 1
    assert {(f["rule"], f["location"]) for f in out["findings"]} == {("rpc-url-credentials", "foundry.toml:rpc_endpoints.main"), ("mainnet-without-opt-in", "foundry.toml:profile.default.chain_id")}


def test_yaml_files_are_parsed_when_pyyaml_is_available(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "c.yaml", "networks:\n  main:\n    chain_id: 1\n    rpc_url: http://rpc.example.org\n---\nchainId: 11155111\nrpcUrl: https://u:p@x.example.org\n")
    code, out = cli(tmp_path)
    assert code == 1 and out["checks"]["yaml"] == "ran"
    assert sorted(f["rule"] for f in out["findings"]) == ["mainnet-without-opt-in", "rpc-url-credentials", "rpc-url-insecure-scheme"]


def test_missing_pyyaml_is_reported_as_skipped_and_json_still_runs(tmp_path, monkeypatch):
    write(tmp_path, "c.yaml", "chainId: 1\n")
    write(tmp_path, "c.json", net(1))
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now raises ImportError
    out = module().scan(tmp_path, tmp_path)
    assert out["checks"] == {"json": "ran", "toml": "no-files", "yaml": "skipped:tool-missing", "env": "no-files"}
    assert out["files_scanned"] == 1 and {f["file"] for f in out["findings"]} == {"c.json"} and out["status"] == "fail"


def test_unparseable_and_unrelated_files_never_crash_the_scan(tmp_path):
    write(tmp_path, "broken.json", "{not json")
    write(tmp_path, "list.json", [1, 2, {"chainId": 11155111}])
    write(tmp_path, "scalar.json", "42")
    write(tmp_path, "settings.toml", "= broken")
    code, out = cli(tmp_path)
    assert code == 0 and out["findings"] == [] and out["skipped"]["unparsed"] >= 2


def test_deeply_nested_json_does_not_crash(tmp_path):
    write(tmp_path, "deep.json", "[" * 5000 + "]" * 5000)
    write(tmp_path, "deep2.json", '{"a":' * 100 + "1" + "}" * 100)
    code, out = cli(tmp_path)
    assert code == 0 and out["findings"] == []


# --- bounds -----------------------------------------------------------------------------------------------------


def test_files_over_1_mib_binaries_symlinks_and_vendored_dirs_are_skipped(tmp_path):
    big = tmp_path / "big.json"
    big.write_text(json.dumps(net(1)) + " " * (1024 * 1024), encoding="utf-8")
    (tmp_path / "bin.json").write_bytes(b'{"chainId": 1}\0\0')
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    write(outside, "secret.json", net(1))
    (tmp_path / "link.json").symlink_to(outside / "secret.json")
    (tmp_path / "linkdir").symlink_to(outside, target_is_directory=True)
    write(tmp_path, "node_modules/pkg/c.json", net(1))
    write(tmp_path, ".git/c.json", net(1))
    write(tmp_path, "package-lock.json", net(1))
    write(tmp_path, "ok/c.json", net(11155111))
    code, out = cli(tmp_path)
    assert code == 0 and out["findings"] == [] and out["files_scanned"] == 1
    assert out["skipped"] == {"too_large": 1, "binary": 1, "symlink": 1, "unparsed": 0}


def test_exactly_one_mib_is_still_scanned(tmp_path):
    body = json.dumps(net(1))
    (tmp_path / "edge.json").write_text(body + " " * (1024 * 1024 - len(body)), encoding="utf-8")
    code, out = cli(tmp_path)
    assert code == 1 and out["skipped"]["too_large"] == 0 and out["files_scanned"] == 1


def test_scan_stops_after_2000_candidate_files(tmp_path):
    for index in range(2005):
        write(tmp_path, f"d{index % 7}/f{index:04d}.json", {"x": index})
    code, out = cli(tmp_path)
    assert code == 0 and out["truncated"] is True and out["files_scanned"] == 2000


def test_finding_count_is_capped(tmp_path):
    write(tmp_path, "many.json", {"rpcUrls": [f"http://h{i}.example.org" for i in range(600)]})
    code, out = cli(tmp_path)
    assert code == 1 and len(out["findings"]) == 500 and out["truncated"] is True


def test_script_does_not_write_into_the_scanned_tree(tmp_path):
    write(tmp_path, "c.json", net(1))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    cli(tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before


def test_tool_and_script_share_one_chain_table():
    mod = module()
    tools = sys.modules["swarm.tools.a28_tools"]
    assert mod.CHAINS is tools.CHAINS
    assert mod.NAME_TO_ID["sepolia"] == 11155111 and mod.NAME_TO_ID["ethereum-mainnet"] == 1 and mod.NAME_TO_ID["op-sepolia"] == 11155420


# --- independent review: secrets never echoed, severity past the cap, bombs, templates --------------------------

CANARIES = ["ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "AKIAIOSFODNN7EXAMPLE", "https://user:hunter2pass@rpc.example.org/v2/abc", "password=hunter2"]


@pytest.mark.parametrize("canary", CANARIES)
def test_secret_looking_network_names_and_keys_are_never_echoed(tmp_path, canary):
    write(tmp_path, "a.json", {canary: {"chainId": 1, "name": canary, "rpcUrl": f"https://{canary.split('//')[-1]}"}})
    write(tmp_path, "b.json", {"chainId": 11155111, "name": canary})
    write(tmp_path, "c.json", {"chainId": 80002, "name": canary})
    code, out = cli(tmp_path)
    assert code == 1 and out["findings"]
    blob = json.dumps(out)
    assert canary not in blob and "hunter2" not in blob and "a1B2c3D4" not in blob and "AKIAIOSFODNN7" not in blob


def test_a_plain_network_name_is_still_shown_in_the_message(tmp_path):
    write(tmp_path, "c.json", net(1, name="Sepolia"))
    _, out = cli(tmp_path)
    assert any("'Sepolia'" in f["summary"] for f in out["findings"] if f["rule"] == "chain-id-name-mismatch")


def test_a_blocker_past_the_finding_cap_still_fails_the_scan_and_is_listed_first(tmp_path):
    write(tmp_path, "a.json", {"rpcUrls": [f"http://localhost:{8000 + i}" for i in range(600)]})  # 600 loopback notes (info)
    write(tmp_path, "z.json", net(11155111, "https://alice:pw@rpc.example.org/"))  # sorts after every a.json finding
    code, out = cli(tmp_path)
    assert code == 1 and out["status"] == "fail" and out["truncated"] is True
    assert len(out["findings"]) == 500 and out["findings"][0]["rule"] == "rpc-url-credentials"
    assert out["counts"] == {"info": 600, "minor": 0, "major": 0, "blocker": 1}
    assert "601 finding(s)" in out["summary"] and "(101 not listed)" in out["summary"]


def test_userinfo_made_only_of_templates_is_not_a_credential_but_literal_text_is(tmp_path):
    write(tmp_path, "t.json", {"rpcUrls": ["https://${RPC_USER}:${RPC_PASS}@rpc.example.org/", "https://<user>:<pass>@rpc.example.org/", "https://%RPC_USER%:%RPC_PASS%@rpc.example.org/"]})
    code, out = cli(tmp_path)
    assert code == 0 and out["findings"] == []
    write(tmp_path, "t.json", {"rpcUrls": ["https://${RPC_USER}:realpassword@rpc.example.org/", "https://user:p%40ss@rpc.example.org/", "https://u:@rpc.example.org/"]})
    _, out = cli(tmp_path)
    assert [f["rule"] for f in out["findings"]] == ["rpc-url-credentials"] * 3


def test_literal_secrets_that_merely_contain_a_dollar_or_percent_are_not_templates(tmp_path):
    write(tmp_path, "q.json", {"rpcUrls": ["https://x.example/rpc?apikey=abc$def", "https://x.example/rpc?token=ab%25cd", "https://x.example/rpc?apikey=$KEY", "https://x.example/rpc?token=<TOKEN>"]})
    _, out = cli(tmp_path)
    assert sorted(f["location"] for f in out["findings"] if f["rule"] == "rpc-url-embedded-key") == ["q.json:rpcUrls[0]", "q.json:rpcUrls[1]"]


@pytest.mark.parametrize("param", ["password", "api-key", "jwt", "client_secret", "PRIVATE_KEY"])
def test_more_secret_query_parameters_are_blockers(tmp_path, param):
    write(tmp_path, "q.json", {"rpcUrls": [f"https://x.example/rpc?{param}=s3cret"]})
    code, out = cli(tmp_path)
    assert code == 1 and [f["rule"] for f in out["findings"]] == ["rpc-url-embedded-key"] and "s3cret" not in json.dumps(out)


def test_any_true_opt_in_key_in_the_object_counts_even_after_a_false_one(tmp_path):
    write(tmp_path, "c.json", {"chainId": 1, "allowMainnet": False, "mainnetApproved": True})
    code, out = cli(tmp_path)
    assert code == 0 and out["findings"] == []
    write(tmp_path, "d.json", {"chainId": 10, "allowMainnet": False, "enableMainnet": "no"})
    _, out = cli(tmp_path)
    assert [f["rule"] for f in out["findings"]] == ["mainnet-without-opt-in"]


def test_a_yaml_alias_bomb_is_cut_off_quickly_and_reported(tmp_path):
    pytest.importorskip("yaml")
    lines = ["l0: &l0 [x, x, x, x, x, x, x, x, x]"]
    for level in range(1, 10):
        lines.append(f"l{level}: &l{level} [" + ", ".join([f"*l{level - 1}"] * 9) + "]")  # 9^9 nodes when expanded
    write(tmp_path, "bomb.yaml", "\n".join(lines) + "\n")
    write(tmp_path, "ok.json", net(1))
    started = time.monotonic()
    code, out = cli(tmp_path)
    assert time.monotonic() - started < 20
    assert {f["rule"] for f in out["findings"]} == {"scan-truncated", "mainnet-without-opt-in"} and code == 1
    assert [f for f in out["findings"] if f["rule"] == "scan-truncated"][0]["file"] == "bomb.yaml"


def test_exceeding_the_file_cap_adds_a_visible_note_instead_of_a_silent_ok(tmp_path):
    for index in range(2001):
        write(tmp_path, f"d{index % 7}/f{index:04d}.json", {"x": index})
    code, out = cli(tmp_path)
    assert code == 0 and out["truncated"] is True
    assert [(f["rule"], f["severity"], f["file"]) for f in out["findings"]] == [("scan-truncated", "minor", ".")]


def test_dotenv_inline_comments_and_quoted_values_do_not_hide_a_mainnet_chain_id(tmp_path):
    write(tmp_path, ".env", "CHAIN_ID=1 # production chain\nRPC_URL='https://alice:pw@rpc.example.org/' # primary\nNETWORK=\"Ethereum Mainnet\"  # label\n")
    _, out = cli(tmp_path)
    assert sorted(f["rule"] for f in out["findings"]) == ["mainnet-without-opt-in", "rpc-url-credentials"]
    assert not any(f["rule"] == "chain-id-unparseable" for f in out["findings"])
    assert "alice" not in json.dumps(out) and "primary" not in json.dumps(out)
