"""Behaviour tests for the A29 dApp & Wallet Integration Engineer tools.

Tools: dapp_typed_data_check, dapp_permit_check, dapp_siwe_check, dapp_chain_config_check, dapp_approval_risk,
dapp_tx_state_machine, dapp_userop_check, dapp_provider_error_map, dapp_rpc_fallback_check, dapp_wallet_matrix,
dapp_ens_name_check, dapp_signing_request_scan, dapp_gas_buffer_check.

Every assertion is on output values for specific inputs, boundaries, INVALID_INPUT fields and callers. The registry
document is the live registry plus this lane's intake fragment while it is unmerged, so the file passes before and
after `scripts/_registry_intake.py --apply` merges and deletes the fragment.

tests/conftest.py evicts swarm* modules between tests: modules are resolved through importlib at call time.
"""
from __future__ import annotations

import copy
import functools
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A29", "a29-dapp-wallet", "dapp_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "dapp_typed_data_check", "dapp_permit_check", "dapp_siwe_check", "dapp_chain_config_check", "dapp_approval_risk",
    "dapp_tx_state_machine", "dapp_userop_check", "dapp_provider_error_map", "dapp_rpc_fallback_check", "dapp_wallet_matrix",
    "dapp_ens_name_check", "dapp_signing_request_scan", "dapp_gas_buffer_check",
]

NOW = "2026-10-20T12:00:00Z"
NOW_EPOCH = int(datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp())
# EIP-55 reference vectors from the EIP text and well-known deployments
ALICE = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
BOB = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
CAROL = "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
ZERO = "0x" + "0" * 40
BAD_CHECKSUM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"  # last letter flipped


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _fragment_entries() -> list[dict]:
    """Entries still waiting in the lane's intake fragment(s): <slug>.tools.json and <slug>.<shard>.tools.json."""
    entries: list[dict] = []
    for path in sorted(INTAKE.glob(f"{SLUG}.*tools.json")):
        entries.extend(json.loads(path.read_text(encoding="utf-8")))
    return entries


@functools.cache
def lane_document() -> dict:
    """The registry plus this lane's fragment entries that are not merged yet."""
    document = copy.deepcopy(_registry().load_document())
    agent = next(a for a in document["agents"] if a["id"] == AGENT)
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(e) for e in _fragment_entries() if e["name"] not in present)
    return document


def lane_tools() -> list[dict]:
    return next(a for a in lane_document()["agents"] if a["id"] == AGENT)["registered_tools"]


def call(name: str, payload, caller: str = AGENT) -> dict:
    return _registry().call_tool(name, caller, payload, data=lane_document())


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def invalid(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result
    return result


def ids(result: dict) -> dict[str, str]:
    """finding id -> severity (an id that repeats keeps its highest severity)."""
    rank = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
    found: dict[str, str] = {}
    for finding in result["findings"]:
        if finding["id"] not in found or rank[finding["severity"]] > rank[found[finding["id"]]]:
            found[finding["id"]] = finding["severity"]
    return found


def a29_tools():
    return importlib.import_module("swarm.tools.a29_tools")


# --- registration ---------------------------------------------------------------------------------------------


def test_lane_registers_exactly_its_tools_as_read_only_owner_calls():
    tools = lane_tools()
    assert [t["name"] for t in tools] == TOOL_NAMES
    assert 10 <= len(tools) <= 15
    for tool in tools:
        suffix = tool["name"][len(PREFIX):]
        assert tool["name"].startswith(PREFIX) and tool["owner"] == AGENT and tool["permitted_callers"] == [AGENT]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["entrypoint"] == f"swarm/tools/{AGENT.lower()}_tools.py"
        assert tool["handler"] == f"swarm.tools.{AGENT.lower()}_tools:{suffix}"
        assert "probe" in tool["input_schema"]["properties"] and "|" not in tool["purpose"]
        assert tool["output_schema"]["properties"]["state"] == {"type": "string", "const": "SUCCESS"}


def test_lane_entries_validate_in_a_copy_of_the_registry(tmp_path):
    copy_path = tmp_path / "agents.json"
    copy_path.write_text(json.dumps(lane_document()), encoding="utf-8")
    errors = _registry().validate_registry(copy_path)
    mine = [e for e in errors if AGENT in e or any(name in e for name in TOOL_NAMES)]
    assert mine == []


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_refuses_other_callers_and_malformed_payloads(name):
    sample = next(t for t in lane_tools() if t["name"] == name)["examples"]["success"]
    assert call(name, sample, caller="A01")["state"] == "UNAUTHORIZED"
    assert call(name, {})["state"] == "INVALID_INPUT"
    assert call(name, [sample])["field"] == "payload"
    assert call(name, {**sample, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call(name, {**sample, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


# --- reference tables: pinned rows ----------------------------------------------------------------------------


def test_keccak256_matches_known_vectors():
    keccak = a29_tools()._keccak256
    assert keccak(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    assert keccak(b"transfer(address,uint256)").hex().startswith("a9059cbb")
    assert len(keccak(b"x" * 136)) == 32  # exactly one rate block: padding goes into a second block
    assert keccak(b"x" * 136) != keccak(b"x" * 135)


@pytest.mark.parametrize("signature,selector", [
    ("transfer(address,uint256)", "a9059cbb"),
    ("approve(address,uint256)", "095ea7b3"),
    ("transferFrom(address,address,uint256)", "23b872dd"),
    ("increaseAllowance(address,uint256)", "39509351"),
    ("setApprovalForAll(address,bool)", "a22cb465"),
    ("safeTransferFrom(address,address,uint256)", "42842e0e"),
    ("safeTransferFrom(address,address,uint256,bytes)", "b88d4fde"),
    ("permit(address,address,uint256,uint256,uint8,bytes32,bytes32)", "d505accf"),
    ("safeBatchTransferFrom(address,address,uint256[],uint256[],bytes)", "2eb2c2d6"),
])
def test_function_selector_table_rows(signature, selector):
    module = a29_tools()
    assert module._selector(signature) == selector
    assert module.CALL_TABLE[selector][0] == signature


def test_eip55_checksum_vectors_and_namehash_vectors():
    module = a29_tools()
    for address in (ALICE, BOB, CAROL, USDC, PERMIT2):
        assert module._checksum_state(address) == "valid"
    assert module._checksum_state(BAD_CHECKSUM) == "invalid"
    assert module._checksum_state(ALICE.lower().replace("0x5a", "0x5a")) == "none"
    assert module._namehash("") == "0x" + "00" * 32
    assert module._namehash("eth") == "0x93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    assert module._namehash("foo.eth") == "0xde9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f"


@pytest.mark.parametrize("chain_id,klass,symbols", [
    (1, "mainnet", ("ETH",)), (10, "mainnet", ("ETH",)), (56, "mainnet", ("BNB",)), (137, "mainnet", ("POL", "MATIC")),
    (324, "mainnet", ("ETH",)), (8453, "mainnet", ("ETH",)), (42161, "mainnet", ("ETH",)), (43114, "mainnet", ("AVAX",)),
    (59144, "mainnet", ("ETH",)), (534352, "mainnet", ("ETH",)),
    (97, "testnet", ("tBNB",)), (43113, "testnet", ("AVAX",)), (80002, "testnet", ("POL", "MATIC")), (84532, "testnet", ("ETH",)),
    (421614, "testnet", ("ETH",)), (11155111, "testnet", ("ETH",)), (11155420, "testnet", ("ETH",)), (17000, "testnet", ("ETH",)),
    (1337, "devnet", ("ETH",)), (31337, "devnet", ("ETH",)),
])
def test_known_chain_table_rows(chain_id, klass, symbols):
    assert a29_tools().KNOWN_CHAINS[chain_id] == (klass, symbols)


def test_provider_error_table_rows():
    table = a29_tools().ERROR_CODES
    assert table[4001][:2] == ("user_rejected", "EIP-1193")
    assert table[4100][0] == "unauthorized" and table[4200][0] == "unsupported_method"
    assert table[4900][0] == "disconnected" and table[4901][0] == "chain_disconnected"
    assert table[4902][:2] == ("chain_not_added", "MetaMask")  # EIP-3326 defines no error code; 4902 is MetaMask's "Unrecognized chain ID"
    assert table[-32601][0] == "method_not_found" and table[-32603][0] == "internal_error"
    assert table[-32005][0] == "rate_limited" and table[-32002][0] == "resource_unavailable"
    assert table[3][0] == "execution_reverted"
    assert a29_tools().PANIC_CODES[0x11] == "arithmetic overflow or underflow" and a29_tools().PANIC_CODES[0x32] == "array index out of bounds"


# --- dapp_typed_data_check --------------------------------------------------------------------------------------


def typed_doc(**changes) -> dict:
    document = {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                             {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "Permit": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"},
                       {"name": "nonce", "type": "uint256"}, {"name": "deadline", "type": "uint256"}],
        },
        "primaryType": "Permit",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 1, "verifyingContract": USDC},
        "message": {"owner": ALICE, "spender": BOB, "value": "1000000", "nonce": "0", "deadline": str(NOW_EPOCH + 1800)},
    }
    document.update(changes)
    return document


def typed(document: dict, **extra) -> dict:
    return ok("dapp_typed_data_check", {"typed_data_json": json.dumps(document), "expected_chain_id": 1, "now": NOW, **extra})


def with_message(**message) -> dict:
    document = typed_doc()
    document["message"].update(message)
    return document


def test_typed_data_clean_permit_passes_with_exact_summary():
    result = typed(typed_doc())
    assert result == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "primary_type": "Permit", "type_count": 2, "domain_chain_id": 1, "deadline_checked": True, "deadline_remaining_seconds": 1800,
    }


def test_typed_data_chain_id_forms_and_mismatch():
    for form in (1, "1", "0x1"):
        document = typed_doc()
        document["domain"]["chainId"] = form
        assert typed(document)["domain_chain_id"] == 1
    document = typed_doc()
    document["domain"]["chainId"] = 137
    result = typed(document)
    assert ids(result)["TD-CHAIN-MISMATCH"] == "blocker" and result["verdict"] == "block" and result["domain_chain_id"] == 137
    document["domain"]["chainId"] = "one"
    assert ids(typed(document))["TD-CHAIN-MALFORMED"] == "blocker"
    document["domain"]["chainId"] = -1
    assert ids(typed(document))["TD-CHAIN-MALFORMED"] == "blocker"
    del document["domain"]["chainId"]
    del document["types"]["EIP712Domain"][2]
    result = typed(document)
    assert ids(result) == {"TD-CHAIN-MISSING": "major"} and result["domain_chain_id"] == -1 and result["verdict"] == "review"


def test_typed_data_verifying_contract_rules():
    document = typed_doc()
    document["domain"]["verifyingContract"] = ZERO
    assert ids(typed(document)) == {"TD-VERIFYING-ZERO": "major"}
    document["domain"]["verifyingContract"] = "0x1234"
    assert ids(typed(document)) == {"TD-VERIFYING-ADDRESS-MALFORMED": "blocker"}
    document["domain"]["verifyingContract"] = USDC[:-1] + "b"
    assert ids(typed(document)) == {"TD-VERIFYING-ADDRESS-CHECKSUM": "major"}
    document = typed_doc()
    del document["domain"]["verifyingContract"]
    del document["types"]["EIP712Domain"][3]
    assert ids(typed(document)) == {"TD-VERIFYING-MISSING": "major"}  # old minor, new major: an unbound signature replays on any contract with the same domain
    allowed = typed(typed_doc(), expected_verifying_contracts=[BOB])
    assert ids(allowed) == {"TD-VERIFYING-UNEXPECTED": "major"}
    assert typed(typed_doc(), expected_verifying_contracts=[USDC.lower()])["findings"] == []


def test_typed_data_domain_shape_findings():
    document = typed_doc()
    document["domain"]["salt"] = "0x" + "11" * 32
    result = typed(document)
    assert ids(result) == {"TD-DOMAIN-TYPE-MISMATCH": "major"}  # salt is used but not declared in EIP712Domain
    document = typed_doc()
    document["domain"]["extra"] = 1
    assert ids(typed(document))["TD-DOMAIN-UNKNOWN-MEMBER"] == "major"
    document = typed_doc()
    document["types"]["EIP712Domain"][2]["type"] = "uint8"
    assert ids(typed(document)) == {"TD-DOMAIN-TYPE-MISMATCH": "major"}
    document = typed_doc()
    del document["types"]["EIP712Domain"]
    assert ids(typed(document)) == {"TD-DOMAIN-TYPE-OMITTED": "info"}
    document = typed_doc()
    del document["domain"]["name"]
    del document["types"]["EIP712Domain"][0]
    assert ids(typed(document)) == {"TD-DOMAIN-NAME-VERSION": "minor"}


def test_typed_data_type_closure_findings():
    document = typed_doc()
    document["types"]["Permit"].append({"name": "extra", "type": "Missing"})
    document["message"]["extra"] = {}
    result = typed(document)
    assert ids(result)["TD-TYPE-UNDEFINED"] == "blocker" and result["verdict"] == "block"
    document = typed_doc(primaryType="Nope")
    assert ids(typed(document))["TD-PRIMARY-UNDEFINED"] == "blocker"
    document = typed_doc()
    document["types"]["Unused"] = [{"name": "x", "type": "uint256"}]
    result = typed(document)
    assert ids(result) == {"TD-TYPE-UNREACHABLE": "minor"} and "Unused" in result["findings"][0]["summary"]
    document = typed_doc()
    document["types"]["Permit"][2]["type"] = "uint"
    assert ids(typed(document)) == {"TD-TYPE-ALIAS": "minor"}
    for bad in ("uint7", "uint264", "bytes33", "bytes0", "int", "uint08", "string[0]"):
        document = typed_doc()
        document["types"]["Permit"][2]["type"] = bad
        found = ids(typed(document))
        assert "TD-TYPE-UNDEFINED" in found or "TD-TYPE-ALIAS" in found, bad
    document = typed_doc()
    document["types"]["Permit"].append({"name": "owner", "type": "address"})
    assert "TD-FIELD-DUPLICATE" in ids(typed(document))


def test_typed_data_nested_structs_and_arrays_are_closed_and_walked():
    document = {
        "types": {"EIP712Domain": [{"name": "chainId", "type": "uint256"}],
                  "Order": [{"name": "legs", "type": "Leg[]"}, {"name": "pair", "type": "Leg[2]"}, {"name": "nonce", "type": "uint256"}],
                  "Leg": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint96"}]},
        "primaryType": "Order", "domain": {"chainId": 1},
        "message": {"legs": [{"token": USDC, "amount": "5"}], "pair": [{"token": USDC, "amount": 1}], "nonce": 7},
    }
    result = typed(document)
    assert ids(result) == {"TD-ARRAY-LENGTH": "major", "TD-VERIFYING-MISSING": "major", "TD-DOMAIN-NAME-VERSION": "minor", "TD-DEADLINE-MISSING": "major"}
    assert any("message.pair" in f["summary"] for f in result["findings"] if f["id"] == "TD-ARRAY-LENGTH")


def test_typed_data_message_value_findings():
    assert ids(typed(with_message(value=2**256))) == {"TD-VALUE-RANGE": "blocker"}
    assert typed(with_message(value=2**256 - 1))["findings"] == []
    assert ids(typed(with_message(value=-1))) == {"TD-VALUE-RANGE": "blocker"}
    assert ids(typed(with_message(value="0xff")))== {}
    assert ids(typed(with_message(value="abc"))) == {"TD-VALUE-TYPE": "major"}
    assert ids(typed(with_message(value=True))) == {"TD-VALUE-TYPE": "major"}
    assert ids(typed(with_message(owner="0x12"))) == {"TD-ADDRESS-MALFORMED": "blocker"}
    assert ids(typed(with_message(spender=BAD_CHECKSUM))) == {"TD-ADDRESS-CHECKSUM": "major"}
    document = typed_doc()
    del document["message"]["spender"]
    assert ids(typed(document)) == {"TD-FIELD-MISSING": "major"}
    assert ids(typed(with_message(surprise="x"))) == {"TD-FIELD-EXTRA": "minor"}


def test_typed_data_bytes_bool_string_int_values():
    document = {
        "types": {"EIP712Domain": [{"name": "chainId", "type": "uint256"}],
                  "M": [{"name": "h", "type": "bytes32"}, {"name": "b", "type": "bytes"}, {"name": "f", "type": "bool"},
                        {"name": "s", "type": "string"}, {"name": "i", "type": "int8"}, {"name": "nonce", "type": "uint256"},
                        {"name": "deadline", "type": "uint256"}]},
        "primaryType": "M", "domain": {"chainId": 1},
        "message": {"h": "0x" + "ab" * 32, "b": "0x", "f": False, "s": "hi", "i": -128, "nonce": 1, "deadline": NOW_EPOCH + 600},
    }
    base = ids(typed(document))
    assert base == {"TD-VERIFYING-MISSING": "major", "TD-DOMAIN-NAME-VERSION": "minor"}
    document["message"].update(h="0x" + "ab" * 31, b="0xz", f="no", s=5, i=128)
    result = typed(document)
    assert ids(result) == {"TD-VALUE-TYPE": "major", "TD-VALUE-RANGE": "blocker", "TD-VERIFYING-MISSING": "major", "TD-DOMAIN-NAME-VERSION": "minor"}
    assert sum(1 for f in result["findings"] if f["id"] == "TD-VALUE-TYPE") == 4


def test_typed_data_deadline_boundaries_exact():
    def remaining(seconds, **extra):
        return typed(with_message(deadline=str(NOW_EPOCH + seconds)), **extra)
    assert ids(remaining(3600)) == {}  # default max lifetime 3600 s: equal is fine
    assert ids(remaining(3601)) == {"TD-DEADLINE-TOO-LONG": "major"}
    assert ids(remaining(1800, max_ttl_seconds=1800)) == {}
    assert ids(remaining(1801, max_ttl_seconds=1800)) == {"TD-DEADLINE-TOO-LONG": "major"}
    assert ids(remaining(60)) == {}  # 60 s of runway is enough
    assert ids(remaining(59)) == {"TD-DEADLINE-TOO-SHORT": "major"}
    assert ids(remaining(0)) == {"TD-DEADLINE-TOO-SHORT": "major"}
    expired = remaining(-1)
    assert ids(expired) == {"TD-DEADLINE-EXPIRED": "blocker"} and expired["deadline_remaining_seconds"] == -1


def test_typed_data_deadline_special_values_and_fields():
    never = typed(with_message(deadline=str(2**256 - 1)))
    assert ids(never) == {"TD-DEADLINE-NEVER": "major"} and never["deadline_checked"] is False and never["deadline_remaining_seconds"] == 0
    millis = typed(with_message(deadline=str((NOW_EPOCH + 600) * 1000)))
    assert ids(millis) == {"TD-DEADLINE-MILLISECONDS": "major"} and millis["deadline_checked"] is False
    assert ids(typed(with_message(deadline="soon"))) == {"TD-DEADLINE-MALFORMED": "major", "TD-VALUE-TYPE": "major"}
    # custom field name; the default candidates are ignored then
    document = typed_doc()
    document["types"]["Permit"][4]["name"] = "expiresAt"
    document["message"]["expiresAt"] = document["message"].pop("deadline")
    assert ids(typed(document)) == {"TD-DEADLINE-MISSING": "major"}
    custom = typed(document, deadline_field="expiresAt")
    assert custom["findings"] == [] and custom["deadline_remaining_seconds"] == 1800


def test_typed_data_replay_protection_findings_and_nested_members():
    document = typed_doc()
    del document["types"]["Permit"][3]
    del document["message"]["nonce"]
    assert ids(typed(document)) == {"TD-NONCE-MISSING": "major"}
    document = typed_doc()
    del document["types"]["Permit"][4]
    del document["message"]["deadline"]
    assert ids(typed(document)) == {"TD-DEADLINE-MISSING": "major"}
    both = typed_doc()
    both["types"]["Permit"] = both["types"]["Permit"][:3]
    both["message"] = {k: both["message"][k] for k in ("owner", "spender", "value")}
    result = typed(both)
    assert ids(result) == {"TD-REPLAY-UNBOUNDED": "blocker"} and result["verdict"] == "block"
    assert ids(typed(with_message(nonce="not-a-number"))) == {"TD-NONCE-MALFORMED": "major", "TD-VALUE-TYPE": "major"}
    # Permit2 style: nonce and expiration live in a nested struct, sigDeadline at the top
    permit2 = {
        "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                  "PermitSingle": [{"name": "details", "type": "PermitDetails"}, {"name": "spender", "type": "address"}, {"name": "sigDeadline", "type": "uint256"}],
                  "PermitDetails": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint160"},
                                    {"name": "expiration", "type": "uint48"}, {"name": "nonce", "type": "uint48"}]},
        "primaryType": "PermitSingle", "domain": {"name": "Permit2", "chainId": 1, "verifyingContract": PERMIT2},
        "message": {"details": {"token": USDC, "amount": "1", "expiration": NOW_EPOCH + 86400, "nonce": 0}, "spender": BOB, "sigDeadline": NOW_EPOCH + 900},
    }
    result = typed(permit2)
    assert ids(result) == {"TD-DOMAIN-NAME-VERSION": "minor"} and result["deadline_remaining_seconds"] == 900


def test_typed_data_invalid_inputs_name_their_field():
    base = {"expected_chain_id": 1, "now": NOW}
    invalid("dapp_typed_data_check", {**base, "typed_data_json": "{not json"}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": "[1]"}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": '{"types": NaN, "primaryType": "A", "message": {}}'}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": json.dumps({"primaryType": "A", "message": {}})}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": json.dumps(typed_doc(types={"A": "x"}))}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": json.dumps(typed_doc(types={"A": [{"name": 1, "type": "x"}]}))}, "typed_data_json")
    invalid("dapp_typed_data_check", {**base, "typed_data_json": json.dumps(typed_doc(domain=[]))}, "typed_data_json")
    invalid("dapp_typed_data_check", {"typed_data_json": json.dumps(typed_doc()), "expected_chain_id": 1, "now": "2026-10-20T12:00:00"}, "now")
    deep: dict = {}
    node = deep
    for _ in range(40):
        node["next"] = {}
        node = node["next"]
    nested = {"types": {"EIP712Domain": [], "Node": [{"name": "next", "type": "Node"}]}, "primaryType": "Node", "domain": {}, "message": deep}
    invalid("dapp_typed_data_check", {**base, "typed_data_json": json.dumps(nested)}, "typed_data_json")
    assert call("dapp_typed_data_check", {**base, "typed_data_json": json.dumps(typed_doc()), "expected_chain_id": 0})["field"] == "expected_chain_id"


# --- dapp_permit_check ------------------------------------------------------------------------------------------


def permit(**changes) -> dict:
    payload = {"kind": "erc2612", "owner": ALICE, "spender": BOB, "token": USDC, "connected_account": ALICE,
               "domain_verifying_contract": USDC, "value": "1000000", "nonce": "3", "current_nonce": "3", "deadline": str(NOW_EPOCH + 1800),
               "known_spenders": [BOB], "domain_chain_id": 1, "expected_chain_id": 1, "now": NOW}
    payload.update(changes)
    return payload


def permit2(**changes) -> dict:
    base = {"kind": "permit2", "domain_verifying_contract": PERMIT2, "value": "1000", "nonce": "0", "current_nonce": "0"}
    base.update(changes)
    return permit(**base)


def test_permit_clean_values():
    assert ok("dapp_permit_check", permit()) == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "unlimited": False, "nonce_matches": True, "deadline_remaining_seconds": 1800,
    }
    assert ok("dapp_permit_check", permit2())["findings"] == []
    # case-insensitive address comparison
    assert ok("dapp_permit_check", permit(connected_account=ALICE.lower(), known_spenders=[BOB.lower()], domain_verifying_contract=USDC.lower()))["findings"] == []


def test_permit_owner_spender_and_token_rules():
    assert ids(ok("dapp_permit_check", permit(connected_account=CAROL))) == {"PERMIT-OWNER-MISMATCH": "blocker"}
    assert ids(ok("dapp_permit_check", permit(known_spenders=[CAROL]))) == {"PERMIT-SPENDER-UNKNOWN": "blocker"}
    assert ids(ok("dapp_permit_check", permit(known_spenders=[]))) == {"PERMIT-SPENDER-UNKNOWN": "blocker"}
    assert ids(ok("dapp_permit_check", permit(spender=ZERO))) == {"PERMIT-SPENDER-ZERO": "blocker"}
    assert ids(ok("dapp_permit_check", permit(spender=USDC, known_spenders=[USDC]))) == {"PERMIT-SPENDER-IS-TOKEN": "major"}
    assert ids(ok("dapp_permit_check", permit(spender=BAD_CHECKSUM, known_spenders=[BAD_CHECKSUM]))) == {"PERMIT-ADDRESS-CHECKSUM": "major"}


def test_permit_unlimited_threshold_and_requested_amount():
    assert ok("dapp_permit_check", permit(value=str(2**255)))["unlimited"] is True
    assert ok("dapp_permit_check", permit(value=str(2**255 - 1)))["unlimited"] is False
    result = ok("dapp_permit_check", permit(value=str(2**256 - 1)))
    assert ids(result) == {"PERMIT-UNLIMITED": "major"} and result["verdict"] == "review"
    assert ok("dapp_permit_check", permit2(value=str(2**159)))["unlimited"] is True
    assert ok("dapp_permit_check", permit2(value=str(2**159 - 1)))["unlimited"] is False
    assert ok("dapp_permit_check", permit2(value=str(2**160 - 1)))["unlimited"] is True
    assert ids(ok("dapp_permit_check", permit(value="1000", requested_amount="999"))) == {"PERMIT-EXCESS": "minor"}
    assert ok("dapp_permit_check", permit(value="1000", requested_amount="1000"))["findings"] == []
    assert ids(ok("dapp_permit_check", permit(value="999", requested_amount="1000"))) == {"PERMIT-INSUFFICIENT": "blocker"}
    # unlimited is reported as unlimited, not as excess
    assert ids(ok("dapp_permit_check", permit(value=str(2**256 - 1), requested_amount="5"))) == {"PERMIT-UNLIMITED": "major"}


def test_permit_nonce_must_equal_the_on_chain_nonce():
    stale = ok("dapp_permit_check", permit(nonce="2"))
    assert ids(stale) == {"PERMIT-NONCE-STALE": "blocker"} and stale["nonce_matches"] is False
    ahead = ok("dapp_permit_check", permit(nonce="4"))
    assert ids(ahead) == {"PERMIT-NONCE-AHEAD": "blocker"} and ahead["nonce_matches"] is False
    assert ok("dapp_permit_check", permit(nonce="3"))["nonce_matches"] is True


def test_permit_deadline_boundaries():
    def with_deadline(seconds, **extra):
        return ok("dapp_permit_check", permit(deadline=str(NOW_EPOCH + seconds), **extra))
    assert ids(with_deadline(60)) == {} and ids(with_deadline(59)) == {"PERMIT-DEADLINE-TOO-SHORT": "major"}
    assert ids(with_deadline(3600)) == {} and ids(with_deadline(3601)) == {"PERMIT-DEADLINE-TOO-LONG": "major"}
    assert ids(with_deadline(7200, max_ttl_seconds=7200)) == {}
    assert ids(with_deadline(-1)) == {"PERMIT-DEADLINE-EXPIRED": "blocker"}
    assert with_deadline(-1)["deadline_remaining_seconds"] == -1
    assert ids(ok("dapp_permit_check", permit(deadline=str(2**256 - 1)))) == {"PERMIT-DEADLINE-NEVER": "major"}
    assert ids(ok("dapp_permit_check", permit(deadline=str(NOW_EPOCH * 1000)))) == {"PERMIT-DEADLINE-MILLISECONDS": "major"}


def test_permit_domain_binding_and_permit2_expiration():
    assert ids(ok("dapp_permit_check", permit(domain_chain_id=137))) == {"PERMIT-CHAIN-MISMATCH": "blocker"}
    assert ids(ok("dapp_permit_check", permit(domain_verifying_contract=BOB))) == {"PERMIT-DOMAIN-CONTRACT": "blocker"}
    assert ids(ok("dapp_permit_check", permit2(domain_verifying_contract=USDC))) == {"PERMIT-DOMAIN-CONTRACT": "blocker"}
    assert ok("dapp_permit_check", permit2(domain_verifying_contract=PERMIT2.lower()))["findings"] == []
    assert ok("dapp_permit_check", permit2(expiration=str(NOW_EPOCH + 2592000)))["findings"] == []
    assert ids(ok("dapp_permit_check", permit2(expiration=str(NOW_EPOCH + 2592001)))) == {"PERMIT-ALLOWANCE-LONG": "minor"}
    assert ok("dapp_permit_check", permit2(expiration="0"))["findings"] == []  # 0 means "the current block" in Permit2
    assert ids(ok("dapp_permit_check", permit2(expiration=str(NOW_EPOCH - 1)))) == {"PERMIT-ALLOWANCE-EXPIRED": "blocker"}


def test_permit_invalid_inputs_name_their_field():
    invalid("dapp_permit_check", permit(now="2026-10-20T12:00:00"), "now")
    invalid("dapp_permit_check", permit(value="12a"), "value")
    invalid("dapp_permit_check", permit(nonce="-1"), "nonce")
    invalid("dapp_permit_check", permit(current_nonce=str(2**256)), "current_nonce")
    invalid("dapp_permit_check", permit(deadline="soon"), "deadline")
    invalid("dapp_permit_check", permit(owner="0x12"), "owner")
    invalid("dapp_permit_check", permit(spender="0x" + "g" * 40), "spender")
    invalid("dapp_permit_check", permit(token="nope"), "token")
    invalid("dapp_permit_check", permit(connected_account="0x" + "1" * 39), "connected_account")
    invalid("dapp_permit_check", permit(domain_verifying_contract="x" * 42), "domain_verifying_contract")
    invalid("dapp_permit_check", permit(known_spenders=[BOB, "bad"]), "known_spenders")
    invalid("dapp_permit_check", permit(requested_amount="x"), "requested_amount")
    invalid("dapp_permit_check", permit(requested_amount=str(2**256)), "requested_amount")
    invalid("dapp_permit_check", permit(expiration="5"), "expiration")  # only Permit2 has an allowance expiration
    invalid("dapp_permit_check", permit2(expiration="x"), "expiration")
    invalid("dapp_permit_check", permit2(expiration=str(2**48)), "expiration")
    invalid("dapp_permit_check", permit2(value=str(2**160)), "value")


# --- dapp_siwe_check --------------------------------------------------------------------------------------------


def siwe_text(*, domain="app.example.com", address=ALICE, statement="Sign in to Example.", uri="https://app.example.com/login", version="1",
              chain="1", nonce="k3fP9sQ2xW", issued="2026-10-20T11:58:00Z", expiration="2026-10-20T12:08:00Z", not_before=None,
              request_id=None, resources=None) -> str:
    lines = [f"{domain} wants you to sign in with your Ethereum account:", address, ""]
    lines += [statement, ""] if statement else [""]
    lines += [f"URI: {uri}", f"Version: {version}", f"Chain ID: {chain}", f"Nonce: {nonce}", f"Issued At: {issued}"]
    if expiration:
        lines.append(f"Expiration Time: {expiration}")
    if not_before:
        lines.append(f"Not Before: {not_before}")
    if request_id:
        lines.append(f"Request ID: {request_id}")
    if resources is not None:
        lines.append("Resources:")
        lines += [f"- {r}" for r in resources]
    return "\n".join(lines)


def siwe(message=None, **changes) -> dict:
    payload = {"message": message if message is not None else siwe_text(), "expected_domain": "app.example.com", "expected_chain_id": 1, "now": NOW}
    payload.update(changes)
    return payload


def test_siwe_clean_message_summary():
    assert ok("dapp_siwe_check", siwe()) == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "domain": "app.example.com", "address_checksum": "valid", "resource_count": 0, "has_expiration": True,
        "issued_at_valid": True, "issued_age_seconds": 120, "expires_in_seconds": 480,
    }
    full = siwe_text(not_before="2026-10-20T11:59:00Z", request_id="req-1", resources=["https://app.example.com/a", "ipfs://bafyexample"])
    result = ok("dapp_siwe_check", siwe(full, expected_address=ALICE.lower(), expected_nonce="k3fP9sQ2xW"))
    assert result["findings"] == [] and result["resource_count"] == 2
    # no statement is a valid grammar variant
    assert ok("dapp_siwe_check", siwe(siwe_text(statement="")))["findings"] == []


def test_siwe_domain_scheme_and_uri_binding():
    assert ids(ok("dapp_siwe_check", siwe(expected_domain="evil.com"))) == {"SIWE-DOMAIN-MISMATCH": "blocker"}
    assert ok("dapp_siwe_check", siwe(expected_domain="APP.Example.COM"))["findings"] == []
    port = siwe_text(domain="localhost:3000", uri="http://localhost:3000/login")
    assert ok("dapp_siwe_check", siwe(port, expected_domain="localhost:3000"))["findings"] == []
    assert ids(ok("dapp_siwe_check", siwe(port, expected_domain="localhost"))) == {"SIWE-DOMAIN-MISMATCH": "blocker"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(domain="http://app.example.com")))) == {"SIWE-SCHEME-INSECURE": "major"}
    assert ok("dapp_siwe_check", siwe(siwe_text(domain="https://app.example.com")))["findings"] == []
    assert ok("dapp_siwe_check", siwe(siwe_text(domain="http://localhost:3000", uri="http://localhost:3000"), expected_domain="localhost:3000"))["findings"] == []
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(uri="https://evil.com/login")))) == {"SIWE-URI-HOST": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(uri="not a uri")))) == {"SIWE-URI-MALFORMED": "major"}


def test_siwe_address_rules():
    lower = ok("dapp_siwe_check", siwe(siwe_text(address=ALICE.lower())))
    assert ids(lower) == {"SIWE-ADDRESS-NO-CHECKSUM": "minor"} and lower["address_checksum"] == "none"
    bad = ok("dapp_siwe_check", siwe(siwe_text(address=BAD_CHECKSUM)))
    assert ids(bad) == {"SIWE-ADDRESS-CHECKSUM": "major"} and bad["address_checksum"] == "invalid"
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(address="0x1234")))) == {"SIWE-ADDRESS-MALFORMED": "blocker"}
    assert ids(ok("dapp_siwe_check", siwe(expected_address=BOB))) == {"SIWE-ADDRESS-MISMATCH": "blocker"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(statement="Approve unlimited USDC spending")))) == {"SIWE-STATEMENT-ASSETS": "major"}


def test_siwe_version_chain_and_nonce():
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(version="2")))) == {"SIWE-VERSION": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(chain="137")))) == {"SIWE-CHAIN-MISMATCH": "major"}
    for malformed in ("0", "00", "abc", "-1"):
        assert ids(ok("dapp_siwe_check", siwe(siwe_text(chain=malformed)))) == {"SIWE-CHAIN-MALFORMED": "blocker"}, malformed
    assert ok("dapp_siwe_check", siwe(siwe_text(nonce="abcd1234")))["findings"] == []  # exactly 8 alphanumerics
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(nonce="abcd123")))) == {"SIWE-NONCE-WEAK": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(nonce="abcd-1234")))) == {"SIWE-NONCE-WEAK": "major"}
    assert ids(ok("dapp_siwe_check", siwe(expected_nonce="OtherNonce1"))) == {"SIWE-NONCE-MISMATCH": "blocker"}


def test_siwe_issued_at_window_boundaries():
    def issued(offset_s, **extra):
        stamp = datetime.fromtimestamp(NOW_EPOCH + offset_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ok("dapp_siwe_check", siwe(siwe_text(issued=stamp, expiration="2026-10-20T13:00:00Z"), **extra))
    assert ids(issued(-600)) == {} and issued(-600)["issued_age_seconds"] == 600
    assert ids(issued(-601)) == {"SIWE-ISSUED-STALE": "major"}
    assert ids(issued(-601, max_age_seconds=700)) == {}
    assert ids(issued(60)) == {} and issued(60)["issued_age_seconds"] == -60
    assert ids(issued(61)) == {"SIWE-ISSUED-FUTURE": "major"}
    malformed = ok("dapp_siwe_check", siwe(siwe_text(issued="yesterday")))
    assert ids(malformed) == {"SIWE-ISSUED-MALFORMED": "major"} and malformed["issued_age_seconds"] == 0 and malformed["issued_at_valid"] is False
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(issued="2026-10-20T11:58:00")))) == {"SIWE-ISSUED-MALFORMED": "major"}  # no offset
    assert ok("dapp_siwe_check", siwe(siwe_text(issued="2026-10-20T13:58:00+02:00")))["issued_age_seconds"] == 120


def test_siwe_expiration_and_not_before():
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(expiration="2026-10-20T12:00:00Z")))) == {"SIWE-EXPIRED": "blocker"}  # valid while now < expiration
    one_second = ok("dapp_siwe_check", siwe(siwe_text(expiration="2026-10-20T12:00:01Z")))
    assert one_second["findings"] == [] and one_second["expires_in_seconds"] == 1
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(expiration="2026-10-19T12:00:00Z")))) == {"SIWE-EXPIRED": "blocker", "SIWE-EXPIRATION-ORDER": "major"}
    none = ok("dapp_siwe_check", siwe(siwe_text(expiration=None)))
    assert ids(none) == {"SIWE-NO-EXPIRATION": "minor"} and none["has_expiration"] is False and none["expires_in_seconds"] == 0
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(expiration="tomorrow")))) == {"SIWE-EXPIRATION-MALFORMED": "major"}
    order = siwe_text(issued="2026-10-20T12:00:30Z", expiration="2026-10-20T12:00:30Z")
    assert ids(ok("dapp_siwe_check", siwe(order))) == {"SIWE-EXPIRATION-ORDER": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(not_before="2026-10-20T12:00:01Z")))) == {"SIWE-NOT-YET-VALID": "blocker"}
    assert ok("dapp_siwe_check", siwe(siwe_text(not_before="2026-10-20T12:00:00Z")))["findings"] == []
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(not_before="later")))) == {"SIWE-NOT-BEFORE-MALFORMED": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(not_before="2026-10-20T11:59:00Z", expiration="2026-10-20T11:59:00Z")))) == {
        "SIWE-EXPIRED": "blocker", "SIWE-NOT-BEFORE-ORDER": "major"}


def test_siwe_resources_and_line_endings():
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(resources=["nocolon"])))) == {"SIWE-RESOURCE-MALFORMED": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(resources=["has space:x"])))) == {"SIWE-RESOURCE-MALFORMED": "major"}
    assert ok("dapp_siwe_check", siwe(siwe_text(resources=[])))["resource_count"] == 0
    crlf = ok("dapp_siwe_check", siwe(siwe_text().replace("\n", "\r\n")))
    assert ids(crlf) == {"SIWE-LINE-ENDINGS": "major"}


def test_siwe_grammar_errors_are_invalid_input_on_message():
    good = siwe_text()
    for broken in (
        "hello world",
        good.replace("\n\n", "\n", 1),  # no blank line after the address
        good.replace("URI: https://app.example.com/login\n", ""),
        good.replace("Version: 1\nChain ID: 1", "Chain ID: 1\nVersion: 1"),
        good + "\n",
        good + "\nextra",
        good.replace("Sign in to Example.\n", "Sign in to Example."),  # statement not followed by a blank line
    ):
        invalid("dapp_siwe_check", siwe(broken), "message")
    invalid("dapp_siwe_check", siwe(now="2026-10-20T12:00:00"), "now")
    invalid("dapp_siwe_check", siwe(expected_address="0x12"), "expected_address")


# --- dapp_chain_config_check ------------------------------------------------------------------------------------


def chain(**changes) -> dict:
    payload = {"chain_id": "0xaa36a7", "chain_name": "Sepolia", "native_name": "Sepolia Ether", "native_symbol": "ETH", "native_decimals": 18,
               "rpc_urls": ["https://rpc.sepolia.org"], "explorer_urls": ["https://sepolia.etherscan.io"]}
    payload.update(changes)
    return payload


def test_chain_config_clean_testnet_and_mainnet_flag():
    assert ok("dapp_chain_config_check", chain()) == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "chain_id": 11155111, "chain_id_hex": "0xaa36a7", "network_class": "testnet", "mainnet_enable_requires_approval": False, "rpc_count": 1,
    }
    mainnet = ok("dapp_chain_config_check", chain(chain_id="0x1", chain_name="Ethereum Mainnet", rpc_urls=["https://cloudflare-eth.com"]))
    assert mainnet["network_class"] == "mainnet" and mainnet["mainnet_enable_requires_approval"] is True
    assert ids(mainnet) == {"CHAIN-MAINNET": "info"} and mainnet["verdict"] == "pass"
    unknown = ok("dapp_chain_config_check", chain(chain_id="0x2710", chain_name="Some Chain"))
    assert unknown["network_class"] == "unknown" and ids(unknown) == {"CHAIN-UNKNOWN": "info"}
    assert unknown["mainnet_enable_requires_approval"] is True  # old False, new True: a chain outside the reference table fails closed to L3 approval


def test_chain_id_form_rules():
    assert ids(ok("dapp_chain_config_check", chain(chain_id="11155111"))) == {"CHAIN-ID-NOT-HEX": "blocker"}
    assert ok("dapp_chain_config_check", chain(chain_id="11155111"))["chain_id"] == 11155111
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x0aa36a7"))) == {"CHAIN-ID-LEADING-ZERO": "major"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0xAA36A7"))) == {"CHAIN-ID-CASE": "minor"}
    assert ok("dapp_chain_config_check", chain(chain_id=hex(4503599627370476), chain_name="Edge"))["findings"][0]["id"] == "CHAIN-UNKNOWN"
    assert ids(ok("dapp_chain_config_check", chain(chain_id=hex(4503599627370477), chain_name="Edge"))).get("CHAIN-ID-TOO-LARGE") == "major"
    assert ids(ok("dapp_chain_config_check", chain(expected_chain_id=1))) == {"CHAIN-ID-MISMATCH": "blocker"}
    assert ok("dapp_chain_config_check", chain(expected_chain_id=11155111))["findings"] == []
    for field_value in ("0x0", "0", "zzz", "0x", "0xzz", ""):
        invalid("dapp_chain_config_check", chain(chain_id=field_value), "chain_id")


def test_chain_name_symbol_and_decimals_rules():
    assert ids(ok("dapp_chain_config_check", chain(native_symbol="SEP"))) == {"CHAIN-SYMBOL-MISMATCH": "major"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x89", chain_name="Polygon", native_symbol="MATIC", rpc_urls=["https://polygon-rpc.com"])))== {"CHAIN-MAINNET": "info"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x1", chain_name="Ethereum Sepolia Testnet"))) == {"CHAIN-NAME-SPOOF": "blocker", "CHAIN-MAINNET": "info"}
    # a test network called plainly "Ethereum" is a mainnet impersonation as well as a name that does not say test
    assert ids(ok("dapp_chain_config_check", chain(chain_name="Ethereum"))) == {"CHAIN-NAME-NOT-TEST": "minor", "CHAIN-NAME-IMPERSONATION": "blocker"}
    # MetaMask (metamask-extension ethereum-chain-utils.js) accepts a 1-6 character ticker, so one character is not a length defect
    assert ids(ok("dapp_chain_config_check", chain(native_symbol="E"))) == {"CHAIN-SYMBOL-MISMATCH": "major"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x2710", chain_name="X", native_symbol="ABCDEFG"))) == {"CHAIN-UNKNOWN": "info", "CHAIN-SYMBOL-LENGTH": "major"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x2710", chain_name="X", native_symbol="AB"))) == {"CHAIN-UNKNOWN": "info"}
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0x2710", chain_name="X", native_symbol="ABCDEF"))) == {"CHAIN-UNKNOWN": "info"}
    assert ids(ok("dapp_chain_config_check", chain(native_decimals=6))) == {"CHAIN-DECIMALS": "major"}  # MetaMask: "Expected the number 18 for 'nativeCurrency.decimals'"


def test_chain_rpc_url_rules():
    def rpc(url, **extra):
        return ids(ok("dapp_chain_config_check", chain(rpc_urls=[url], **extra)))
    assert rpc("http://rpc.sepolia.org") == {"CHAIN-RPC-1-INSECURE": "major"}
    assert rpc("https://u:p@rpc.sepolia.org") == {"CHAIN-RPC-1-CREDENTIALS": "blocker"}
    assert rpc("https://sepolia.infura.io/v3/9aa3d95b3bc440fa88ea12eaa4456161") == {"CHAIN-RPC-1-KEY": "major"}
    assert rpc("https://rpc.sepolia.org/?apikey=abcdef123456") == {"CHAIN-RPC-1-KEY": "major"}
    assert rpc("https://rpc.sepolia.org/?apikey=short") == {}
    assert rpc("https://rpc.sepolia.org/v1/eth-sepolia-archive") == {}
    assert rpc("wss://rpc.sepolia.org") == {"CHAIN-RPC-1-WEBSOCKET": "minor"}
    assert rpc("ftp://rpc.sepolia.org") == {"CHAIN-RPC-1-SCHEME": "blocker"}
    assert rpc("not a url") == {"CHAIN-RPC-1-MALFORMED": "blocker"}
    assert rpc("http://localhost:8545") == {"CHAIN-RPC-1-LOCAL": "major"}
    dev = ok("dapp_chain_config_check", chain(chain_id="0x7a69", chain_name="Anvil devnet", rpc_urls=["http://127.0.0.1:8545"], explorer_urls=[]))
    assert dev["network_class"] == "devnet" and ids(dev) == {"CHAIN-EXPLORER-NONE": "minor"}
    assert ids(ok("dapp_chain_config_check", chain(rpc_urls=[]))) == {"CHAIN-RPC-NONE": "blocker"}
    assert ids(ok("dapp_chain_config_check", chain(rpc_urls=["https://a.example", "https://a.example"]))) == {"CHAIN-RPC-DUPLICATE": "minor"}


def test_chain_explorer_rules():
    assert ids(ok("dapp_chain_config_check", chain(explorer_urls=[]))) == {"CHAIN-EXPLORER-NONE": "minor"}
    assert ids(ok("dapp_chain_config_check", chain(explorer_urls=["http://sepolia.etherscan.io"]))) == {"CHAIN-EXPLORER-1": "major"}
    assert ids(ok("dapp_chain_config_check", chain(explorer_urls=["https://u:p@sepolia.etherscan.io"]))) == {"CHAIN-EXPLORER-1": "major"}


# --- dapp_approval_risk -----------------------------------------------------------------------------------------


def approval(**item) -> dict:
    entry = {"token": USDC, "spender": BOB, "kind": "erc20_approve", "amount": "1000000"}
    entry.update(item)
    return entry


def risk(*entries, known=(BOB,)) -> dict:
    return ok("dapp_approval_risk", {"approvals": list(entries), "known_spenders": list(known)})


def test_approval_known_exact_amount_is_clean():
    assert risk(approval(requested_amount="1000000")) == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "approval_count": 1, "unlimited_count": 0, "unknown_spender_count": 0, "nft_blanket_count": 0, "revoke_count": 0,
    }
    assert risk(approval(spender=BOB.lower()))["findings"] == []


def test_approval_unlimited_threshold_and_spender_trust():
    known = risk(approval(amount=str(2**256 - 1)))
    assert ids(known) == {"APPROVAL-UNLIMITED": "major"} and known["unlimited_count"] == 1 and known["verdict"] == "review"
    unknown = risk(approval(spender=CAROL, amount=str(2**256 - 1)))
    assert ids(unknown) == {"APPROVAL-UNLIMITED": "blocker"} and unknown["unknown_spender_count"] == 1 and unknown["verdict"] == "block"
    assert risk(approval(amount=str(2**255)))["unlimited_count"] == 1
    below = risk(approval(amount=str(2**255 - 1)))
    assert below["unlimited_count"] == 0 and below["findings"] == []
    limited_unknown = risk(approval(spender=CAROL))
    assert ids(limited_unknown) == {"APPROVAL-SPENDER-UNKNOWN": "blocker"} and limited_unknown["unknown_spender_count"] == 1
    assert ids(risk(approval(kind="erc20_permit", amount=str(2**256 - 1)))) == {"APPROVAL-UNLIMITED": "major"}
    assert ids(risk(approval(kind="erc20_increase_allowance", spender=CAROL))) == {"APPROVAL-SPENDER-UNKNOWN": "blocker"}


def test_approval_excess_over_requested_amount():
    assert ids(risk(approval(amount="1001", requested_amount="1000"))) == {"APPROVAL-EXCESS": "minor"}
    assert risk(approval(amount="1000", requested_amount="1000"))["findings"] == []
    assert risk(approval(amount="999", requested_amount="1000"))["findings"] == []


def test_approval_nft_rules():
    unknown = risk({"token": USDC, "spender": CAROL, "kind": "nft_set_approval_for_all"})
    assert ids(unknown) == {"APPROVAL-NFT-BLANKET": "blocker"} and unknown["nft_blanket_count"] == 1 and unknown["unknown_spender_count"] == 1
    known = risk({"token": USDC, "spender": BOB, "kind": "nft_set_approval_for_all", "approved": True})
    assert ids(known) == {"APPROVAL-NFT-BLANKET": "major"} and known["nft_blanket_count"] == 1
    single_known = risk({"token": USDC, "spender": BOB, "kind": "erc721_approve"})
    assert ids(single_known) == {"APPROVAL-NFT-SINGLE": "info"} and single_known["nft_blanket_count"] == 0 and single_known["verdict"] == "pass"
    single_unknown = risk({"token": USDC, "spender": CAROL, "kind": "erc721_approve"})
    assert ids(single_unknown) == {"APPROVAL-NFT-SINGLE": "blocker"} and single_unknown["nft_blanket_count"] == 0


def test_approval_revokes_are_not_risks_even_for_unknown_spenders():
    erc20 = risk(approval(spender=CAROL, amount="0"))
    assert ids(erc20) == {"APPROVAL-REVOKE": "info"} and erc20["revoke_count"] == 1 and erc20["unknown_spender_count"] == 0 and erc20["verdict"] == "pass"
    nft = risk({"token": USDC, "spender": CAROL, "kind": "nft_set_approval_for_all", "approved": False})
    assert ids(nft) == {"APPROVAL-REVOKE": "info"} and nft["revoke_count"] == 1 and nft["nft_blanket_count"] == 0
    erc721 = risk({"token": USDC, "spender": ZERO, "kind": "erc721_approve"})
    assert ids(erc721) == {"APPROVAL-REVOKE": "info"} and erc721["revoke_count"] == 1
    assert ids(risk(approval(spender=ZERO, amount="5"))) == {"APPROVAL-SPENDER-ZERO": "blocker"}


def test_approval_address_hygiene_and_counts():
    assert ids(risk(approval(token=ZERO))) == {"APPROVAL-TOKEN-ZERO": "major"}
    assert ids(risk(approval(spender=BAD_CHECKSUM), known=(BAD_CHECKSUM,))) == {"APPROVAL-ADDRESS-CHECKSUM": "major"}
    mixed = risk(approval(), approval(spender=CAROL, amount=str(2**256 - 1)), approval(spender=BOB, amount="0"))
    assert (mixed["approval_count"], mixed["unlimited_count"], mixed["unknown_spender_count"], mixed["revoke_count"]) == (3, 1, 1, 1)
    assert mixed["findings"][0]["severity"] == "blocker"  # sorted most severe first


def test_approval_invalid_inputs_name_their_field():
    invalid("dapp_approval_risk", {"approvals": [approval()], "known_spenders": ["bad"]}, "known_spenders")
    invalid("dapp_approval_risk", {"approvals": [approval(token="0x12")], "known_spenders": []}, "approvals")
    invalid("dapp_approval_risk", {"approvals": [approval(spender="0x12")], "known_spenders": []}, "approvals")
    missing = {"token": USDC, "spender": BOB, "kind": "erc20_approve"}
    invalid("dapp_approval_risk", {"approvals": [missing], "known_spenders": []}, "approvals")
    invalid("dapp_approval_risk", {"approvals": [approval(amount="1.5")], "known_spenders": []}, "approvals")
    invalid("dapp_approval_risk", {"approvals": [approval(amount=str(2**256))], "known_spenders": []}, "approvals")
    invalid("dapp_approval_risk", {"approvals": [approval(requested_amount="x")], "known_spenders": []}, "approvals")
    invalid("dapp_approval_risk", {"approvals": [], "known_spenders": []}, "approvals")


# --- dapp_tx_state_machine --------------------------------------------------------------------------------------

H1 = "0x" + "ab" * 32
H2 = "0x" + "cd" * 32
LIFECYCLE = ["draft", "simulating", "simulated", "awaiting_confirmation", "user_confirmed", "awaiting_signature"]
NEEDS_HASH = {"submitted", "confirmed", "reverted", "replaced", "dropped", "reorged", "finalized"}


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history(states, ago=5, **extras):
    """Events for `states`; the last one happened `ago` seconds before NOW, earlier ones 10 s apart. `extras` maps a
    state to extra event fields."""
    events = []
    for index, state in enumerate(states):
        event = {"state": state, "at": iso(NOW_EPOCH - ago - 10 * (len(states) - 1 - index))}
        if state in NEEDS_HASH:
            event["tx_hash"] = H1
        event.update(extras.get(state, {}))
        event = {k: v for k, v in event.items() if v is not None}
        events.append(event)
    return events


def machine(states, ago=5, now=NOW, extras=None, **options):
    return ok("dapp_tx_state_machine", {"events": history(states, ago, **(extras or {})), "now": now, **options})


def test_tx_happy_path_to_finalized():
    result = machine(LIFECYCLE + ["submitted", "confirmed", "finalized"], extras={"finalized": {"confirmations": 64}}, required_confirmations=32)
    assert result == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "final_state": "finalized", "terminal": True, "valid": True, "transition_count": 8, "invalid_transition_count": 0,
        "pending_seconds": -1, "allowed_next": [],
    }


def test_tx_in_flight_states_report_allowed_next():
    signing = machine(LIFECYCLE)
    assert (signing["final_state"], signing["terminal"], signing["allowed_next"], signing["pending_seconds"]) == ("awaiting_signature", False, ["rejected", "submitted"], -1)
    assert machine(LIFECYCLE + ["submitted"])["allowed_next"] == ["confirmed", "dropped", "replaced", "reverted"]
    assert machine(LIFECYCLE + ["submitted", "confirmed"])["allowed_next"] == ["finalized", "reorged"]
    assert machine(LIFECYCLE + ["submitted", "confirmed", "reorged"])["allowed_next"] == ["confirmed", "dropped", "replaced", "submitted"]


def test_tx_terminal_exits_are_valid_endings():
    done = machine(LIFECYCLE[:4] + ["rejected"])
    assert (done["final_state"], done["terminal"], done["valid"], done["verdict"]) == ("rejected", True, True, "pass")
    assert machine(["draft", "simulating", "simulation_failed"])["terminal"] is True
    assert machine(LIFECYCLE + ["rejected"])["terminal"] is True
    assert machine(LIFECYCLE + ["submitted", "reverted"])["final_state"] == "reverted"
    dropped = machine(LIFECYCLE + ["submitted", "dropped"])
    assert dropped["terminal"] is True and dropped["allowed_next"] == [] and dropped["findings"] == []
    edited = ["draft", "simulating", "simulated", "draft", "simulating", "simulated", "awaiting_confirmation", "user_confirmed", "awaiting_signature"]
    assert machine(edited)["findings"] == []


def test_tx_invalid_transitions_are_blockers():
    skipped = machine(["draft", "simulating", "simulated", "submitted"])
    assert ids(skipped) == {"TX-TRANSITION-INVALID": "blocker", "TX-POLICY-NO-SIMULATION-CONFIRMATION": "blocker"}
    assert skipped["invalid_transition_count"] == 1 and skipped["valid"] is False and skipped["verdict"] == "block"
    assert any("simulated -> submitted" in f["summary"] for f in skipped["findings"])
    after_terminal = machine(LIFECYCLE + ["submitted", "dropped", "confirmed"])
    assert ids(after_terminal) == {"TX-TRANSITION-INVALID": "blocker"}
    assert "after a terminal state" in after_terminal["findings"][0]["summary"]
    assert machine(["draft", "simulating", "simulated", "awaiting_confirmation", "awaiting_signature"])["invalid_transition_count"] == 1


def test_tx_never_signs_without_simulation_and_confirmation():
    mid_history = machine(["awaiting_signature", "submitted"])
    assert ids(mid_history) == {"TX-HISTORY-INCOMPLETE": "major", "TX-POLICY-NO-SIMULATION-CONFIRMATION": "blocker"}
    assert sum(1 for f in mid_history["findings"] if f["id"] == "TX-POLICY-NO-SIMULATION-CONFIRMATION") == 1  # reported once
    skipped_confirmation = machine(["draft", "simulating", "simulated", "awaiting_confirmation", "awaiting_signature"])
    assert "TX-POLICY-NO-SIMULATION-CONFIRMATION" in ids(skipped_confirmation)


def test_tx_hash_and_replacement_rules():
    assert ids(machine(LIFECYCLE + ["submitted"], extras={"submitted": {"tx_hash": None}})) == {"TX-HASH-MISSING": "major"}
    assert ids(machine(LIFECYCLE + ["submitted"], extras={"submitted": {"tx_hash": "0x12"}})) == {"TX-HASH-MALFORMED": "major"}
    changed = machine(LIFECYCLE + ["submitted", "confirmed"], extras={"confirmed": {"tx_hash": H2}})
    assert ids(changed) == {"TX-HASH-CHANGED": "major"}
    assert machine(LIFECYCLE + ["submitted", "confirmed"], extras={"confirmed": {"tx_hash": H1.upper().replace("0X", "0x")}})["findings"] == []
    replaced = machine(LIFECYCLE + ["submitted", "replaced"], extras={"replaced": {"replaced_by": H2}})
    assert replaced["findings"] == [] and replaced["terminal"] is True
    assert ids(machine(LIFECYCLE + ["submitted", "replaced"])) == {"TX-REPLACEMENT-MISSING": "major"}
    assert ids(machine(LIFECYCLE + ["submitted", "replaced"], extras={"replaced": {"replaced_by": "0x12"}})) == {"TX-REPLACEMENT-MISSING": "major"}
    assert ids(machine(LIFECYCLE + ["submitted", "replaced"], extras={"replaced": {"replaced_by": H1}})) == {"TX-REPLACEMENT-SAME": "major"}


def test_tx_finalization_confirmation_count():
    base = LIFECYCLE + ["submitted", "confirmed", "finalized"]
    assert ids(machine(base)) == {"TX-CONFIRMATIONS-MISSING": "minor"}
    assert ids(machine(base, extras={"finalized": {"confirmations": 0}})) == {"TX-FINALIZED-EARLY": "major"}  # default requirement is 1
    assert machine(base, extras={"finalized": {"confirmations": 1}})["findings"] == []
    assert ids(machine(base, extras={"finalized": {"confirmations": 31}}, required_confirmations=32)) == {"TX-FINALIZED-EARLY": "major"}
    assert machine(base, extras={"finalized": {"confirmations": 32}}, required_confirmations=32)["findings"] == []


def test_tx_stuck_detection_boundaries_and_pending_seconds():
    waiting = machine(LIFECYCLE + ["submitted"], ago=599)
    assert waiting["findings"] == [] and waiting["pending_seconds"] == 599
    stuck = machine(LIFECYCLE + ["submitted"], ago=600)
    assert ids(stuck) == {"TX-STUCK": "major"} and stuck["pending_seconds"] == 600
    assert "10%" in stuck["findings"][0]["summary"] and "same nonce" in stuck["findings"][0]["summary"]
    assert ids(machine(LIFECYCLE + ["submitted"], ago=60, stuck_after_seconds=60)) == {"TX-STUCK": "major"}
    assert machine(LIFECYCLE + ["submitted"], ago=59, stuck_after_seconds=60)["findings"] == []
    assert ids(machine(LIFECYCLE + ["submitted", "confirmed", "reorged"], ago=900)) == {"TX-STUCK": "major"}
    assert machine(LIFECYCLE + ["submitted", "confirmed"], ago=9000)["pending_seconds"] == -1  # mined is not pending


def test_tx_time_order_and_invalid_inputs():
    events = history(LIFECYCLE)
    events[3]["at"], events[4]["at"] = events[4]["at"], events[3]["at"]
    result = ok("dapp_tx_state_machine", {"events": events, "now": NOW})
    assert ids(result) == {"TX-TIME-ORDER": "major"}
    bad_stamp = history(LIFECYCLE)
    bad_stamp[2]["at"] = "2026-10-20T11:00:00"
    invalid("dapp_tx_state_machine", {"events": bad_stamp, "now": NOW}, "events")
    invalid("dapp_tx_state_machine", {"events": history(LIFECYCLE), "now": "later"}, "now")
    invalid("dapp_tx_state_machine", {"events": history(LIFECYCLE, ago=5), "now": iso(NOW_EPOCH - 6)}, "now")
    assert ok("dapp_tx_state_machine", {"events": history(LIFECYCLE, ago=5), "now": iso(NOW_EPOCH - 5)})["final_state"] == "awaiting_signature"


# --- dapp_userop_check ------------------------------------------------------------------------------------------


def uop(**changes) -> dict:
    payload = {"entry_point_version": "0.6", "sender": ALICE, "nonce": "0x0", "call_gas_limit": "0x30d40", "verification_gas_limit": "0x186a0",
               "pre_verification_gas": "0xc350", "max_fee_per_gas": "0x77359400", "max_priority_fee_per_gas": "0x3b9aca00",
               "init_code": "0x", "call_data": "0xb61d27f6", "paymaster_and_data": "0x", "signature": "0x" + "aa" * 65}
    payload.update(changes)
    return payload


def test_userop_clean_values():
    # (200000 call + 100000 verification + 50000 pre-verification) gas at 2 gwei
    assert ok("dapp_userop_check", uop()) == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "required_prefund_wei": "700000000000000", "calldata_gas_floor": 4 * 16 + 65 * 16, "paymaster_present": False, "deploys_account": False,
    }


def test_userop_calldata_floor_counts_zero_and_nonzero_bytes():
    result = ok("dapp_userop_check", uop(call_data="0x00000000", signature="0x" + "00" * 65 + "ff"))
    assert result["calldata_gas_floor"] == 4 * 4 + 65 * 4 + 16
    floor = ok("dapp_userop_check", uop())["calldata_gas_floor"]
    assert floor == 1104
    assert ok("dapp_userop_check", uop(pre_verification_gas=hex(floor)))["findings"] == []  # equal to the floor is enough
    short = ok("dapp_userop_check", uop(pre_verification_gas=hex(floor - 1)))
    assert ids(short) == {"UOP-PRE-VERIFICATION-GAS": "blocker"} and short["verdict"] == "block"


def test_userop_fee_and_gas_limit_rules():
    assert ok("dapp_userop_check", uop(max_priority_fee_per_gas="0x77359400"))["findings"] == []  # priority == max fee is allowed
    assert ids(ok("dapp_userop_check", uop(max_priority_fee_per_gas="0x77359401"))) == {"UOP-FEE-ORDER": "major"}
    assert ids(ok("dapp_userop_check", uop(max_fee_per_gas="0x0", max_priority_fee_per_gas="0x0"))) == {"UOP-FEE-ZERO": "major"}
    assert ids(ok("dapp_userop_check", uop(verification_gas_limit="0x0"))) == {"UOP-VERIFICATION-GAS-ZERO": "blocker"}
    assert ids(ok("dapp_userop_check", uop(call_gas_limit="0x0"))) == {"UOP-CALL-GAS-ZERO": "major"}
    assert ids(ok("dapp_userop_check", uop(call_gas_limit="0x0", call_data="0x"))) == {"UOP-EMPTY": "minor"}
    assert ids(ok("dapp_userop_check", uop(sender=ZERO))) == {"UOP-SENDER-ZERO": "blocker"}
    assert ids(ok("dapp_userop_check", uop(signature="0x"))) == {"UOP-SIGNATURE-EMPTY": "major"}


def test_userop_init_code_rules():
    factory_call = "0x" + "11" * 20 + "5fbfb9cf"
    deploy = ok("dapp_userop_check", uop(init_code=factory_call, pre_verification_gas="0x186a0"))
    assert deploy["deploys_account"] is True and deploy["findings"] == []
    assert ids(ok("dapp_userop_check", uop(init_code="0x1234", pre_verification_gas="0x186a0"))) == {"UOP-INIT-CODE-SHORT": "blocker"}
    assert ids(ok("dapp_userop_check", uop(init_code=factory_call, nonce="0x1", pre_verification_gas="0x186a0"))) == {"UOP-INIT-NONCE": "major"}
    key_only = hex(1 << 64)  # nonce key 1, sequence 0
    assert ok("dapp_userop_check", uop(init_code=factory_call, nonce=key_only, pre_verification_gas="0x186a0"))["findings"] == []


def test_userop_v06_paymaster_prefund_uses_triple_verification_gas():
    paymaster = "0x" + "11" * 20
    result = ok("dapp_userop_check", uop(paymaster_and_data=paymaster))
    assert result["paymaster_present"] is True and result["required_prefund_wei"] == "1100000000000000"  # (200000 + 3*100000 + 50000) * 2 gwei
    assert result["calldata_gas_floor"] == 4 * 16 + 65 * 16 + 20 * 16
    assert ids(ok("dapp_userop_check", uop(paymaster_and_data="0x" + "11" * 10))) == {"UOP-PAYMASTER-SHORT": "blocker"}
    assert ids(ok("dapp_userop_check", uop(paymaster_and_data="0x" + "00" * 20))) == {"UOP-PAYMASTER-ZERO": "blocker"}


def test_userop_prefund_boundary_and_payer():
    assert ok("dapp_userop_check", uop(prefund_available_wei="700000000000000"))["findings"] == []
    short = ok("dapp_userop_check", uop(prefund_available_wei="699999999999999"))
    assert ids(short) == {"UOP-PREFUND-SHORT": "blocker"} and "account deposit" in short["findings"][0]["summary"]
    sponsored = ok("dapp_userop_check", uop(paymaster_and_data="0x" + "11" * 20, prefund_available_wei="1099999999999999"))
    assert ids(sponsored) == {"UOP-PREFUND-SHORT": "blocker"} and "paymaster deposit" in sponsored["findings"][0]["summary"]


def test_userop_v07_packed_paymaster_and_gas_overflow_limits():
    packed = "0x" + "11" * 20 + (50000).to_bytes(16, "big").hex() + (30000).to_bytes(16, "big").hex()
    result = ok("dapp_userop_check", uop(entry_point_version="0.7", paymaster_and_data=packed))
    assert result["findings"] == [] and result["required_prefund_wei"] == "860000000000000"  # 100000+200000+50000+30000+50000 gas at 2 gwei
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.7", paymaster_and_data=packed[:-2]))) == {"UOP-PAYMASTER-SHORT": "blocker"}
    zero_verification = "0x" + "11" * 20 + (0).to_bytes(16, "big").hex() + (30000).to_bytes(16, "big").hex()
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.7", paymaster_and_data=zero_verification))) == {"UOP-PAYMASTER-VERIFICATION-GAS-ZERO": "major"}
    huge = hex(2**120)  # EntryPoint v0.6.0/v0.7.0 reject maxGasValues > type(uint120).max with AA94, not at 2**128
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.7", call_gas_limit=huge))) == {"UOP-GAS-OVERFLOW": "blocker"}
    assert ok("dapp_userop_check", uop(entry_point_version="0.7", call_gas_limit=hex(2**120 - 1), max_fee_per_gas="0x1", max_priority_fee_per_gas="0x1"))["findings"] == []
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.6", call_gas_limit=huge))) == {"UOP-GAS-OVERFLOW": "blocker"}


def test_userop_invalid_inputs_name_their_field():
    invalid("dapp_userop_check", uop(sender="0x12"), "sender")
    for field in ("nonce", "call_gas_limit", "verification_gas_limit", "pre_verification_gas", "max_fee_per_gas", "max_priority_fee_per_gas"):
        invalid("dapp_userop_check", uop(**{field: "12345"}), field)
        invalid("dapp_userop_check", uop(**{field: "0x" + "f" * 65}), field)
    for field in ("init_code", "call_data", "paymaster_and_data", "signature"):
        invalid("dapp_userop_check", uop(**{field: "0xabc"}), field)
        invalid("dapp_userop_check", uop(**{field: "0xzz"}), field)
    invalid("dapp_userop_check", uop(prefund_available_wei="1e18"), "prefund_available_wei")


# --- dapp_provider_error_map ------------------------------------------------------------------------------------


def error_string(text: str) -> str:
    data = text.encode()
    return "0x08c379a0" + (32).to_bytes(32, "big").hex() + len(data).to_bytes(32, "big").hex() + data.hex().ljust(-(-len(data) // 32) * 64, "0")


def test_user_rejection_is_info_not_an_error():
    assert ok("dapp_provider_error_map", {"code": 4001, "message": "User rejected the request."}) == {
        "state": "SUCCESS", "category": "user_rejected", "standard": "EIP-1193", "message_class": "info", "retryable": False,
        "suggested_action": "reset_to_confirm_step", "show_error_toast": False, "matched_by": "code", "revert_kind": "", "revert_reason": "",
    }


@pytest.mark.parametrize("code,category,standard,klass,retryable", [
    (4100, "unauthorized", "EIP-1193", "warning", True),
    (4200, "unsupported_method", "EIP-1193", "error", False),
    (4900, "disconnected", "EIP-1193", "error", True),
    (4901, "chain_disconnected", "EIP-1193", "error", True),
    (4902, "chain_not_added", "MetaMask", "warning", True),
    (-32700, "parse_error", "JSON-RPC 2.0", "error", False),
    (-32601, "method_not_found", "JSON-RPC 2.0", "error", False),
    (-32603, "internal_error", "JSON-RPC 2.0", "error", True),
    (-32002, "resource_unavailable", "EIP-1474", "info", False),
    (-32005, "rate_limited", "EIP-1474", "warning", True),
    (3, "execution_reverted", "geth", "error", False),
])
def test_provider_error_code_rows(code, category, standard, klass, retryable):
    result = ok("dapp_provider_error_map", {"code": code})
    assert (result["category"], result["standard"], result["message_class"], result["retryable"], result["matched_by"]) == (category, standard, klass, retryable, "code")
    assert result["show_error_toast"] is (klass != "info")


def test_generic_codes_are_refined_by_message_patterns():
    def refine(code, message):
        return ok("dapp_provider_error_map", {"code": code, "message": message})
    funds = refine(-32000, "insufficient funds for gas * price + value")
    assert (funds["category"], funds["message_class"], funds["retryable"], funds["matched_by"], funds["standard"]) == ("insufficient_funds", "error", False, "message", "EIP-1474")
    assert refine(-32603, "nonce too low: next nonce 5")["category"] == "nonce_too_low"
    assert refine(-32000, "nonce too high")["category"] == "nonce_gap"
    assert refine(-32000, "replacement transaction underpriced")["category"] == "replacement_underpriced"
    assert refine(-32000, "replacement transaction underpriced")["suggested_action"] == "raise_fees_by_10_percent"
    assert refine(-32000, "Transaction UNDERPRICED")["category"] == "fee_too_low"
    assert refine(-32000, "max fee per gas less than block base fee")["category"] == "fee_too_low"
    known = refine(-32000, "already known")
    assert (known["category"], known["message_class"], known["show_error_toast"]) == ("already_known", "info", False)
    assert refine(-32000, "intrinsic gas too low")["category"] == "gas_too_low"
    assert refine(-32000, "exceeds block gas limit")["category"] == "gas_limit_exceeded"
    assert refine(-32602, "execution reverted: nope")["category"] == "execution_reverted"
    assert refine(-32000, "429 Too Many Requests")["category"] == "rate_limited"
    assert refine(-32000, "something odd")["category"] == "server_error" and refine(-32000, "something odd")["matched_by"] == "code"
    # explicit EIP-1193 codes are never overridden by message text
    assert refine(4001, "insufficient funds")["category"] == "user_rejected"
    assert refine(4100, "nonce too low")["category"] == "unauthorized"


def test_unknown_and_server_range_codes():
    unknown = ok("dapp_provider_error_map", {"code": 9999})
    assert (unknown["category"], unknown["standard"], unknown["matched_by"], unknown["message_class"]) == ("unknown", "none", "none", "error")
    assert ok("dapp_provider_error_map", {"code": 9999, "message": "User denied transaction signature"})["category"] == "user_rejected"
    assert ok("dapp_provider_error_map", {"code": 9999, "message": "User denied transaction signature"})["matched_by"] == "message"
    server = ok("dapp_provider_error_map", {"code": -32050})
    assert (server["category"], server["standard"], server["matched_by"]) == ("server_error", "EIP-1474", "code")
    assert ok("dapp_provider_error_map", {"code": -32100})["category"] == "unknown"
    assert ok("dapp_provider_error_map", {"code": -32000})["category"] == "server_error"


def test_revert_data_decoding():
    def decode(data, code=3):
        result = ok("dapp_provider_error_map", {"code": code, "revert_data": data})
        return result["revert_kind"], result["revert_reason"]
    assert decode(error_string("Insufficient balance")) == ("error_string", "Insufficient balance")
    assert decode(error_string("x" * 40)) == ("error_string", "x" * 40)  # spans two ABI words
    assert decode("0x4e487b71" + (0x11).to_bytes(32, "big").hex()) == ("panic", "Panic(0x11): arithmetic overflow or underflow")
    assert decode("0x4e487b71" + (0x12).to_bytes(32, "big").hex())[1] == "Panic(0x12): division or modulo by zero"
    assert decode("0x4e487b71" + (0x99).to_bytes(32, "big").hex())[1] == "Panic(0x99): unknown panic code"
    assert decode("0xdeadbeef" + "00" * 32) == ("custom", "custom error 0xdeadbeef")
    assert decode("0x") == ("empty", "execution reverted with no data")
    assert decode("0x1234")[0] == "malformed"
    assert decode("0x4e487b71" + "00" * 31)[0] == "malformed"
    assert decode("0x08c379a0")[0] == "malformed"
    wrong_offset = "0x08c379a0" + (64).to_bytes(32, "big").hex() + (2).to_bytes(32, "big").hex() + "6869".ljust(64, "0")
    assert decode(wrong_offset)[0] == "malformed"
    too_long = "0x08c379a0" + (32).to_bytes(32, "big").hex() + (99).to_bytes(32, "big").hex() + "6869".ljust(64, "0")
    assert decode(too_long)[0] == "malformed"
    bad_utf8 = "0x08c379a0" + (32).to_bytes(32, "big").hex() + (2).to_bytes(32, "big").hex() + "fffe".ljust(64, "0")
    assert decode(bad_utf8)[0] == "malformed"
    # revert reasons come from the chain: control characters are neutralised and the length is bounded
    assert decode(error_string("a\x07b")) == ("error_string", "a?b")
    assert len(decode(error_string("y" * 600))[1]) == 256
    assert decode(error_string("hi"), code=4001) == ("error_string", "hi")


def test_provider_error_map_invalid_inputs():
    invalid("dapp_provider_error_map", {"code": 3, "revert_data": "0xabc"}, "revert_data")
    invalid("dapp_provider_error_map", {"code": 3, "revert_data": "zz"}, "revert_data")
    invalid("dapp_provider_error_map", {"code": 1.5}, "code")


# --- dapp_rpc_fallback_check ------------------------------------------------------------------------------------


def provider(url, timeout=3000, retries=1, **extra) -> dict:
    return {"url": url, "timeout_ms": timeout, "retries": retries, **extra}


def fallback(providers=None, quorum=2, backoff=200, budget=20000, **extra) -> dict:
    providers = providers or [provider("https://eth.llamarpc.com"), provider("https://rpc.ankr.com/eth")]
    return ok("dapp_rpc_fallback_check", {"providers": providers, "quorum": quorum, "backoff_ms": backoff, "ui_budget_ms": budget, **extra})


def test_rpc_fallback_clean_values():
    assert fallback() == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "provider_count": 2, "distinct_hosts": 2, "total_weight": 2, "worst_case_ms": 12400, "majority_quorum": True,
    }  # 2 x (3000 ms x 2 attempts + 200 ms backoff)


def test_rpc_fallback_worst_case_budget_boundary():
    assert fallback(budget=12400)["findings"] == []
    assert ids(fallback(budget=12399)) == {"RPC-WORST-CASE": "major"}


def test_rpc_fallback_failure_domains():
    single = fallback([provider("https://eth.llamarpc.com")], quorum=1)
    assert ids(single) == {"RPC-NO-FALLBACK": "major"} and single["distinct_hosts"] == 1
    same_host = fallback([provider("https://eth.llamarpc.com/a"), provider("https://ETH.llamarpc.com/b")])
    assert ids(same_host) == {"RPC-SAME-HOST": "major"} and same_host["distinct_hosts"] == 1
    three = fallback([provider("https://a.example/x"), provider("https://a.example/y"), provider("https://b.example")], quorum=2)
    assert ids(three) == {"RPC-DUPLICATE-HOST": "minor"} and three["distinct_hosts"] == 2


def test_rpc_fallback_quorum_arithmetic():
    weighted = [provider("https://a.example", weight=2), provider("https://b.example", weight=1)]
    assert fallback(weighted, quorum=3)["total_weight"] == 3
    assert ids(fallback(weighted, quorum=4)) == {"RPC-QUORUM-UNREACHABLE": "blocker"}
    assert fallback(weighted, quorum=3)["findings"] == []
    assert fallback(weighted, quorum=2)["majority_quorum"] is True  # 2 of 3
    half = fallback(quorum=1)
    assert ids(half) == {"RPC-QUORUM-NO-MAJORITY": "minor"} and half["majority_quorum"] is False
    four = [provider(f"https://p{i}.example") for i in range(4)]
    assert ids(fallback(four, quorum=2, budget=3600000)) == {"RPC-QUORUM-NO-MAJORITY": "minor"}  # 2 of 4 is not a majority
    assert fallback(four, quorum=3, budget=3600000)["findings"] == []


def test_rpc_fallback_timeout_retry_and_stall_boundaries():
    def with_provider(**kw):
        return fallback([provider("https://a.example", **kw), provider("https://b.example")], budget=3600000)
    assert ids(with_provider(timeout=999)) == {"RPC-TIMEOUT-SHORT": "minor"} and with_provider(timeout=1000)["findings"] == []
    assert with_provider(timeout=30000)["findings"] == [] and ids(with_provider(timeout=30001)) == {"RPC-TIMEOUT-LONG": "major"}
    assert with_provider(retries=3)["findings"] == [] and ids(with_provider(retries=4)) == {"RPC-RETRIES-HIGH": "minor"}
    assert ids(fallback(stall_timeout_ms=3000)) == {"RPC-STALL": "minor"}
    assert fallback(stall_timeout_ms=2999)["findings"] == []
    assert fallback(stall_timeout_ms=1500, providers=[provider("https://a.example", timeout=2000), provider("https://b.example", timeout=3000)])["findings"] == []


def test_rpc_fallback_url_hazards():
    def urls(first):
        return ids(fallback([provider(first), provider("https://rpc.ankr.com/eth")]))
    assert urls("http://eth.example.com") == {"RPC-1-INSECURE": "major"}
    assert urls("https://mainnet.infura.io/v3/9aa3d95b3bc440fa88ea12eaa4456161") == {"RPC-1-KEY": "major"}
    assert urls("https://user:pw@eth.example.com") == {"RPC-1-CREDENTIALS": "blocker"}
    assert urls("http://localhost:8545") == {"RPC-1-LOCAL": "major"}
    assert urls("wss://eth.example.com") == {}
    invalid("dapp_rpc_fallback_check", {"providers": [provider("not a url")], "quorum": 1, "backoff_ms": 0, "ui_budget_ms": 1000}, "providers")


# --- dapp_wallet_matrix -----------------------------------------------------------------------------------------

UUID4 = "350670db-19fa-4704-a166-e52e178b59d2"


def wallet(name="Alpha", **changes) -> dict:
    entry = {"name": name, "eip1193": True, "walletconnect": "v2", "announced_6963": True, "rdns": "io.alpha", "uuid": UUID4,
             "icon": "data:image/png;base64,AAAA", "methods": ["eth_requestAccounts", "personal_sign", "eth_signTypedData_v4"], "chain_ids": [1, 10]}
    entry.update(changes)
    return entry


def matrix(*wallets, methods=("personal_sign", "eth_signTypedData_v4"), chains=(1,)) -> dict:
    return ok("dapp_wallet_matrix", {"wallets": list(wallets), "required_methods": list(methods), "required_chain_ids": list(chains)})


def test_wallet_matrix_clean_and_coverage_counts():
    result = matrix(wallet(), wallet("Beta", rdns="io.beta", uuid="450670db-19fa-4704-b166-e52e178b59d2", methods=["personal_sign"], chain_ids=[1]),
                    methods=("personal_sign", "eth_signTypedData_v4"), chains=(10, 1))
    assert result["wallet_count"] == 2 and result["compatible_count"] == 1 and result["compatible_wallets"] == ["Alpha"]
    assert result["method_coverage"] == [{"method": "eth_signTypedData_v4", "wallet_count": 1}, {"method": "personal_sign", "wallet_count": 2}]
    assert result["chain_coverage"] == [{"chain_id": 1, "wallet_count": 2}, {"chain_id": 10, "wallet_count": 1}]
    assert ids(result) == {"WALLET-METHODS-MISSING": "major", "WALLET-CHAINS-MISSING": "major"}
    clean = matrix(wallet())
    assert clean["findings"] == [] and clean["verdict"] == "pass" and clean["compatible_wallets"] == ["Alpha"]


def test_wallet_matrix_reachability_and_walletconnect():
    assert ids(matrix(wallet(eip1193=False, walletconnect="none"))) == {"WALLET-UNREACHABLE": "blocker", "WALLET-NONE-COMPATIBLE": "major"}
    assert matrix(wallet(eip1193=False, walletconnect="v2"))["compatible_wallets"] == ["Alpha"]  # reachable through WalletConnect only
    v1 = matrix(wallet(eip1193=False, walletconnect="v1", announced_6963=False, rdns="", uuid="", icon=""))
    assert ids(v1) == {"WALLET-WALLETCONNECT-V1": "major", "WALLET-NONE-COMPATIBLE": "major"} and v1["compatible_count"] == 0
    assert ids(matrix(wallet(announced_6963=False, rdns="", uuid="", icon=""))) == {"WALLET-LEGACY-INJECTION": "minor"}


def test_wallet_matrix_eip6963_announcement_rules():
    assert ids(matrix(wallet(uuid="350670db-19fa-1704-a166-e52e178b59d2"))) == {"WALLET-6963-UUID": "major"}  # not version 4
    assert ids(matrix(wallet(uuid="350670db-19fa-4704-c166-e52e178b59d2"))) == {"WALLET-6963-UUID": "major"}  # wrong variant
    assert ids(matrix(wallet(uuid=""))) == {"WALLET-6963-UUID": "major"}
    for rdns in ("MetaMask", "metamask", "io..alpha", "io.alpha.", ".io", "io.al pha", ""):
        assert ids(matrix(wallet(rdns=rdns))) == {"WALLET-6963-RDNS": "major"}, rdns
    assert matrix(wallet(rdns="io.meta-mask"))["findings"] == [] and matrix(wallet(rdns="com.Example.Wallet"))["findings"] == []
    assert ids(matrix(wallet(icon="https://x/icon.png"))) == {"WALLET-6963-ICON": "major"}
    assert ids(matrix(wallet(icon=""))) == {"WALLET-6963-ICON": "major"}
    duplicate = matrix(wallet("A"), wallet("B"))
    assert ids(duplicate) == {"WALLET-6963-UUID-DUPLICATE": "major", "WALLET-6963-RDNS-DUPLICATE": "minor"}
    assert ids(matrix(wallet("A"), wallet("A", rdns="io.other", uuid="450670db-19fa-4704-b166-e52e178b59d2"))) == {"WALLET-NAME-DUPLICATE": "minor"}


def test_wallet_matrix_required_method_policy():
    sign = matrix(wallet(methods=["eth_sign", "personal_sign"]), methods=("eth_sign",))
    assert ids(sign) == {"WALLET-METHOD-DISCOURAGED": "blocker"} and sign["verdict"] == "block" and sign["compatible_count"] == 1
    assert ids(matrix(wallet(methods=["eth_signTypedData"]), methods=("eth_signTypedData",))) == {"WALLET-METHOD-DISCOURAGED": "minor"}
    assert ids(matrix(wallet(methods=["eth_signTypedData_v3"]), methods=("eth_signTypedData_v3",))) == {"WALLET-METHOD-DISCOURAGED": "minor"}
    assert matrix(wallet(), methods=("personal_sign",))["findings"] == []
    assert ok("dapp_wallet_matrix", {"wallets": [wallet()], "required_methods": [], "required_chain_ids": []})["method_coverage"] == []


# --- dapp_ens_name_check ----------------------------------------------------------------------------------------


def ens(name, **extra) -> dict:
    return ok("dapp_ens_name_check", {"name": name, **extra})


def test_ens_ascii_name_normalizes_and_hashes():
    result = ens("foo.eth")
    assert result == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "normalized": "foo.eth", "normalization": "ascii-verified", "label_count": 2,
        "namehash": "0xde9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f", "reverse_name": "", "reverse_node": "", "reverse_verified": False,
    }
    assert ens("eth")["namehash"] == "0x93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    upper = ens("Foo.ETH")
    assert ids(upper) == {"ENS-NOT-NORMALIZED": "major"} and upper["normalized"] == "foo.eth" and upper["namehash"] == result["namehash"]


def test_ens_label_rules():
    empty = ens("foo..eth")
    assert ids(empty) == {"ENS-EMPTY-LABEL": "blocker"} and empty["namehash"] == ""
    assert ids(ens(".eth")) == {"ENS-EMPTY-LABEL": "blocker"}
    assert ids(ens("fo o.eth")) == {"ENS-FORBIDDEN-CHARACTER": "blocker"}
    assert ids(ens("foo/bar.eth")) == {"ENS-FORBIDDEN-CHARACTER": "blocker"}
    assert ens("_foo.eth")["findings"] == [] and ens("__foo.eth")["findings"] == []
    assert ids(ens("f_oo.eth")) == {"ENS-UNDERSCORE": "major"}
    assert ids(ens("xn--80ak6aa92e.com")) == {"ENS-PUNYCODE": "major"}
    assert ids(ens("ab--cd.eth")) == {"ENS-LABEL-EXTENSION": "major"}
    assert ens("a-b-c.eth")["findings"] == [] and ens("ab-cd.eth")["findings"] == []


def test_ens_eth_second_level_minimum_length():
    assert ids(ens("ab.eth")) == {"ENS-ETH-SHORT-LABEL": "major"}
    assert ens("abc.eth")["findings"] == []
    assert ids(ens("sub.ab.eth")) == {"ENS-ETH-SHORT-LABEL": "major"}
    assert ens("ab.com")["findings"] == [] and ens("a.b.c")["findings"] == []


def test_ens_non_ascii_names_need_a_library_and_flag_homographs():
    cyrillic_a = ens("vit\u0430lik.eth")  # one Cyrillic 'а' inside a Latin label
    assert ids(cyrillic_a) == {"ENS-NON-ASCII": "major", "ENS-MIXED-SCRIPT": "blocker"}
    assert cyrillic_a["normalization"] == "needs-library" and cyrillic_a["namehash"] == "" and cyrillic_a["verdict"] == "block"
    assert "CYRILLIC" in cyrillic_a["findings"][0]["summary"] and "LATIN" in cyrillic_a["findings"][0]["summary"]
    whole = ens("\u0430\u0440\u0440.eth")  # a Cyrillic label that reads as 'app'
    assert ids(whole) == {"ENS-NON-ASCII": "major"}
    assert ids(ens("foo\u200b.eth")) == {"ENS-NON-ASCII": "major", "ENS-INVISIBLE-CHARACTER": "blocker"}
    assert "ENS-INVISIBLE-CHARACTER" in ids(ens("foo\u00a0bar.eth")) and "ENS-INVISIBLE-CHARACTER" in ids(ens("foo\u202ebar.eth"))
    decomposed = ens("e\u0301.eth")
    assert ids(decomposed) == {"ENS-NON-ASCII": "major", "ENS-NOT-NFC": "major"} and decomposed["normalized"] == "\u00e9.eth"


def test_ens_reverse_record_verification():
    verified = ens("foo.eth", address=ALICE, forward_address=ALICE.lower())
    assert verified["reverse_verified"] is True and verified["findings"] == []
    assert verified["reverse_name"] == f"{ALICE[2:].lower()}.addr.reverse"
    assert verified["reverse_node"] == a29_tools()._namehash(verified["reverse_name"]) and len(verified["reverse_node"]) == 66
    assert ids(ens("foo.eth", address=ALICE, forward_address=BOB)) == {"ENS-REVERSE-SPOOF": "blocker"}
    assert ens("foo.eth", address=ALICE, forward_address=BOB)["reverse_verified"] is False
    assert ids(ens("foo.eth", address=ALICE, forward_address=ZERO)) == {"ENS-FORWARD-UNSET": "blocker"}
    assert ids(ens("foo.eth", address=ALICE)) == {"ENS-REVERSE-UNVERIFIED": "major"}
    assert ids(ens("foo.eth", forward_address=ALICE)) == {"ENS-FORWARD-WITHOUT-ADDRESS": "info"}
    assert ids(ens("foo.eth", address=BAD_CHECKSUM, forward_address=BAD_CHECKSUM)) == {"ENS-ADDRESS-CHECKSUM": "major"}
    invalid("dapp_ens_name_check", {"name": "foo.eth", "address": "0x12"}, "address")
    invalid("dapp_ens_name_check", {"name": "foo.eth", "address": ALICE, "forward_address": "nope"}, "forward_address")


# --- dapp_signing_request_scan ----------------------------------------------------------------------------------


def word_address(address: str) -> str:
    return "0" * 24 + address[2:].lower()


def word_uint(number: int) -> str:
    return f"{number:064x}"


def scan(*requests, known=(USDC, BOB), domains=("example.com",), links=(), hardcoded=()) -> dict:
    return ok("dapp_signing_request_scan", {"requests": list(requests), "known_contracts": list(known), "expected_domains": list(domains),
                                            "link_urls": list(links), "hardcoded_addresses": list(hardcoded)})


def test_signing_scan_clean_request_and_summary():
    approve = "0x095ea7b3" + word_address(BOB) + word_uint(1_000_000)
    result = scan({"method": "eth_sendTransaction", "to": USDC, "origin": "https://app.example.com", "data": approve},
                  links=["https://docs.example.com"], hardcoded=[USDC])
    assert result == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "request_count": 1, "link_count": 1, "decoded_calls": [{"index": 0, "selector": "0x095ea7b3", "signature": "approve(address,uint256)"}],
    }


def test_signing_scan_method_policy():
    assert ids(scan({"method": "eth_sign"})) == {"SIGN-METHOD": "blocker"}
    assert ids(scan({"method": "eth_signTransaction"})) == {"SIGN-METHOD": "major"}
    assert ids(scan({"method": "eth_signTypedData"})) == {"SIGN-METHOD": "minor"}
    assert ids(scan({"method": "eth_signTypedData_v3"})) == {"SIGN-METHOD": "minor"}
    assert scan({"method": "personal_sign"})["findings"] == []
    assert scan({"method": "eth_signTypedData_v4", "typed_domain_verifying_contract": USDC})["findings"] == []
    assert ids(scan({"method": "eth_signTypedData_v4", "typed_domain_verifying_contract": CAROL})) == {"SIGN-VERIFYING-UNKNOWN": "major"}
    assert ids(scan({"method": "eth_sendTransaction", "to": CAROL})) == {"SIGN-TARGET-UNKNOWN": "major"}


def test_signing_scan_decodes_approvals_and_judges_spenders():
    unlimited = "0x095ea7b3" + word_address(CAROL) + word_uint(2**256 - 1)
    unknown = scan({"method": "eth_sendTransaction", "to": USDC, "data": unlimited})
    assert ids(unknown) == {"SIGN-SPENDER-UNKNOWN": "blocker", "SIGN-UNLIMITED": "blocker"}
    known = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x095ea7b3" + word_address(BOB) + word_uint(2**255)})
    assert ids(known) == {"SIGN-UNLIMITED": "major"}
    below = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x095ea7b3" + word_address(BOB) + word_uint(2**255 - 1)})
    assert below["findings"] == []
    increase = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x39509351" + word_address(CAROL) + word_uint(5)})
    assert ids(increase) == {"SIGN-SPENDER-UNKNOWN": "blocker"} and increase["decoded_calls"][0]["signature"] == "increaseAllowance(address,uint256)"
    permit_args = word_address(ALICE) + word_address(CAROL) + word_uint(5) + word_uint(NOW_EPOCH) + word_uint(27) + word_uint(1) + word_uint(2)
    permit_call = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0xd505accf" + permit_args})
    assert ids(permit_call) == {"SIGN-SPENDER-UNKNOWN": "blocker"}


def test_signing_scan_set_approval_for_all_and_asset_movement():
    enable = "0xa22cb465" + word_address(CAROL) + word_uint(1)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": enable})) == {"SIGN-APPROVAL-FOR-ALL": "blocker"}
    known = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0xa22cb465" + word_address(BOB) + word_uint(1)})
    assert ids(known) == {"SIGN-APPROVAL-FOR-ALL": "major"}
    revoke = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0xa22cb465" + word_address(CAROL) + word_uint(0)})
    assert revoke["findings"] == [] and revoke["decoded_calls"][0]["signature"] == "setApprovalForAll(address,bool)"
    transfer = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0xa9059cbb" + word_address(CAROL) + word_uint(5)})
    assert ids(transfer) == {"SIGN-ASSET-MOVEMENT": "info"} and transfer["verdict"] == "pass"
    transfer_from = "0x23b872dd" + word_address(ALICE) + word_address(CAROL) + word_uint(5)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": transfer_from})) == {"SIGN-ASSET-MOVEMENT": "info"}


def test_signing_scan_calldata_edge_cases():
    truncated = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x095ea7b3" + word_address(BOB)})
    assert ids(truncated) == {"SIGN-CALLDATA-TRUNCATED": "major"}
    unknown_selector = scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x12345678" + "00" * 64})
    assert unknown_selector["findings"] == [] and unknown_selector["decoded_calls"] == []
    assert scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x"})["decoded_calls"] == []
    assert scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x1234"})["decoded_calls"] == []


def test_signing_scan_origin_and_link_domains():
    def origin(value):
        return ids(scan({"method": "personal_sign", "origin": value}))
    assert origin("https://app.example.com") == {} and origin("https://example.com") == {} and origin("app.example.com") == {}
    assert origin("https://evil.org") == {"ORIGIN-UNEXPECTED": "major"}
    assert origin("https://notexample.com") == {"ORIGIN-UNEXPECTED": "major"}  # a suffix of the label text is not a subdomain
    assert origin("https://examp1e.com") == {"ORIGIN-LOOKALIKE": "blocker"}
    assert origin("https://example.com.evil.org") == {"ORIGIN-SPOOF": "blocker"}
    assert origin("https://app.example.com.evil.org") == {"ORIGIN-SPOOF": "blocker"}
    assert origin("https://xn--exmple-cua.com") == {"ORIGIN-PUNYCODE": "major", "ORIGIN-UNEXPECTED": "major"}
    assert "ORIGIN-NON-ASCII" in origin("https://ex\u00e1mple.com")
    assert origin("bad url") == {"SIGN-ORIGIN-MALFORMED": "major"}
    assert ids(scan({"method": "personal_sign", "origin": "https://evil.org"}, domains=())) == {}  # nothing to compare against
    links = scan(links=["https://docs.example.com", "http://docs.example.com", "https://twitter.com/x", "https://examp1e.com", "::"])
    assert ids(links) == {"LINK-INSECURE": "minor", "LINK-UNEXPECTED": "minor", "LINK-LOOKALIKE": "blocker", "LINK-MALFORMED": "minor"}


def test_signing_scan_hardcoded_addresses():
    assert scan(hardcoded=[USDC, BOB])["findings"] == []
    assert ids(scan(hardcoded=[CAROL])) == {"ADDRESS-NOT-IN-REGISTRY": "minor"}
    assert ids(scan(hardcoded=[BAD_CHECKSUM])) == {"ADDRESS-CHECKSUM": "major", "ADDRESS-NOT-IN-REGISTRY": "minor"}
    assert scan(hardcoded=[USDC.lower()])["findings"] == []


def test_signing_scan_invalid_inputs_name_their_field():
    base = {"requests": [], "known_contracts": [], "expected_domains": [], "link_urls": [], "hardcoded_addresses": []}
    invalid("dapp_signing_request_scan", {**base, "known_contracts": ["0x12"]}, "known_contracts")
    invalid("dapp_signing_request_scan", {**base, "hardcoded_addresses": ["nope"]}, "hardcoded_addresses")
    invalid("dapp_signing_request_scan", {**base, "expected_domains": ["https://example.com/"]}, "expected_domains")
    invalid("dapp_signing_request_scan", {**base, "expected_domains": ["a b"]}, "expected_domains")
    invalid("dapp_signing_request_scan", {**base, "requests": [{"method": "eth_sendTransaction", "to": "0x12"}]}, "requests")
    invalid("dapp_signing_request_scan", {**base, "requests": [{"method": "eth_signTypedData_v4", "typed_domain_verifying_contract": "0x12"}]}, "requests")
    invalid("dapp_signing_request_scan", {**base, "requests": [{"method": "eth_sendTransaction", "to": USDC, "data": "0xabc"}]}, "requests")
    invalid("dapp_signing_request_scan", {**base, "requests": [{"method": "eth_sendTransaction", "to": USDC, "data": "zz"}]}, "requests")


# --- dapp_gas_buffer_check --------------------------------------------------------------------------------------


def gas(**changes) -> dict:
    payload = {"estimated_gas": 100000, "gas_limit": 120000, "block_gas_limit": 30000000, "contract_call": True, "max_fee_per_gas_wei": 30 * 10**9,
               "value_wei": 0, "balance_wei": 10**18, "estimate_age_seconds": 5}
    payload.update(changes)
    return ok("dapp_gas_buffer_check", payload)


def test_gas_buffer_clean_values():
    assert gas() == {
        "state": "SUCCESS", "verdict": "pass", "findings": [], "blocker_count": 0, "major_count": 0, "minor_count": 0,
        "buffer_percent": 20.0, "max_cost_wei": "3600000000000000", "recommended_gas_limit": 120000,
    }


def test_gas_buffer_headroom_boundaries():
    assert gas(gas_limit=110000)["findings"] == [] and gas(gas_limit=110000)["buffer_percent"] == 10.0  # exactly the minimum
    low = gas(gas_limit=109999)
    assert ids(low) == {"GAS-NO-HEADROOM": "major"} and low["buffer_percent"] == 9.999
    assert ids(gas(gas_limit=100000, estimated_gas=100000, contract_call=False)) == {}  # a plain transfer needs no buffer
    assert gas(gas_limit=200000)["findings"] == []  # exactly +100%
    assert ids(gas(gas_limit=200001)) == {"GAS-BUFFER-EXCESSIVE": "minor"}


def test_gas_buffer_floor_rules():
    below_estimate = gas(gas_limit=99999)
    assert ids(below_estimate) == {"GAS-BELOW-ESTIMATE": "blocker"} and below_estimate["buffer_percent"] == -0.001
    both = gas(estimated_gas=21000, gas_limit=20999, contract_call=False)
    assert ids(both) == {"GAS-BELOW-INTRINSIC": "blocker", "GAS-BELOW-ESTIMATE": "blocker"}
    assert gas(estimated_gas=21000, gas_limit=21000, contract_call=False)["findings"] == []


def test_gas_buffer_block_and_protocol_caps():
    assert gas(estimated_gas=800000, gas_limit=900000, block_gas_limit=1000000)["findings"] == []  # exactly 90% of the block
    assert ids(gas(estimated_gas=800000, gas_limit=900001, block_gas_limit=1000000)) == {"GAS-NEAR-BLOCK-LIMIT": "major"}
    assert ids(gas(estimated_gas=800000, gas_limit=1000000, block_gas_limit=1000000)) == {"GAS-NEAR-BLOCK-LIMIT": "major"}
    assert ids(gas(estimated_gas=800000, gas_limit=1000001, block_gas_limit=1000000)) == {"GAS-ABOVE-BLOCK-LIMIT": "blocker"}
    assert gas(gas_limit=120000, tx_gas_cap=120000)["findings"] == []
    assert ids(gas(gas_limit=120000, tx_gas_cap=119999)) == {"GAS-ABOVE-TX-CAP": "blocker"}


def test_gas_buffer_balance_covers_value_plus_worst_case_fee():
    need = 120000 * 30 * 10**9 + 5 * 10**17
    assert gas(value_wei=5 * 10**17, balance_wei=need)["findings"] == []  # exactly enough
    short = gas(value_wei=5 * 10**17, balance_wei=need - 1)
    assert ids(short) == {"GAS-INSUFFICIENT-BALANCE": "blocker"} and short["max_cost_wei"] == str(need)


def test_gas_buffer_estimate_age_and_recommendation():
    assert gas(estimate_age_seconds=60)["findings"] == [] and ids(gas(estimate_age_seconds=61)) == {"GAS-ESTIMATE-STALE": "minor"}
    assert gas(estimated_gas=100001)["recommended_gas_limit"] == 120002  # ceil(100001 * 1.2)
    assert gas(estimated_gas=100000, contract_call=False)["recommended_gas_limit"] == 100000
    assert gas(estimated_gas=28000000, gas_limit=28000000, contract_call=True)["recommended_gas_limit"] == 28000000  # above the 90% ceiling: the estimate is kept and flagged
    assert ids(gas(estimated_gas=28000000, gas_limit=28000000, contract_call=True))["GAS-RECOMMEND-OVER-CEILING"] == "minor"
    assert gas(estimated_gas=26000000, gas_limit=28000000)["recommended_gas_limit"] == 27000000  # capped at 90% of 30M
    invalid("dapp_gas_buffer_check", {"estimated_gas": 100000, "gas_limit": 0, "block_gas_limit": 1, "contract_call": True, "max_fee_per_gas_wei": 1,
                                      "value_wei": 0, "balance_wei": 0, "estimate_age_seconds": 0}, "gas_limit")


# --- bounded work and output ------------------------------------------------------------------------------------


def test_oversized_names_and_numbers_never_crash_and_keep_results_small():
    document = typed_doc()
    document["types"]["Permit"].append({"name": "big", "type": "T" * 30000})
    document["types"]["Permit"].append({"name": "x" * 10000, "type": "uint" + "1" * 100})
    document["types"]["Permit"].append({"name": "arr", "type": "uint256[99999999999]"})
    document["message"].update({"big": {}, "x" * 10000: 1, "arr": [1, 2]})
    result = typed(document)
    assert ids(result)["TD-TYPE-UNDEFINED"] == "blocker" and ids(result)["TD-ARRAY-LENGTH"] == "major"
    assert all(len(f["summary"]) <= 400 for f in result["findings"])
    assert len(json.dumps(result)) < 20000
    huge_digits = typed(with_message(value="9" * 5000))
    assert ids(huge_digits) == {"TD-VALUE-RANGE": "blocker"}
    long_primary = typed({**typed_doc(), "primaryType": "P" * 20000})
    assert len(long_primary["primary_type"]) == 255 and ids(long_primary)["TD-PRIMARY-UNDEFINED"] == "blocker"
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(chain="1" * 5000)))) == {"SIWE-CHAIN-MALFORMED": "blocker"}
    assert len(ok("dapp_siwe_check", siwe(siwe_text(domain="d" * 3000), expected_domain="x"))["domain"]) == 255
    invalid("dapp_permit_check", permit(value="1" * 101), "value")


def test_findings_are_capped_at_one_hundred_with_exact_counts():
    many = typed_doc()
    many["types"]["Many"] = [{"name": f"f{i}", "type": "uint8"} for i in range(150)]
    many["types"]["Permit"].append({"name": "many", "type": "Many"})
    many["message"]["many"] = {f"f{i}": 256 for i in range(150)}  # every value is out of range for uint8
    result = typed(many)
    assert result["blocker_count"] == 150 and len(result["findings"]) == 100
    assert result["findings"][-1] == {"id": "TRUNCATED", "severity": "info", "summary": "51 further findings omitted"}


def test_siwe_uri_without_a_host_is_unbindable_not_malformed():
    did = ok("dapp_siwe_check", siwe(siwe_text(uri="did:pkh:eip155:1:" + ALICE)))
    assert ids(did) == {"SIWE-URI-NO-HOST": "minor"} and did["verdict"] == "pass"
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(uri="https://")))) == {"SIWE-URI-MALFORMED": "major"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(uri="app.example.com")))) == {"SIWE-URI-MALFORMED": "major"}


# --- independent review (A29 reviewer-fixer): regressions with exact values ------------------------------------------


def _permit_batch_doc(nonce_value=0) -> dict:
    return {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "PermitDetails": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint160"}, {"name": "expiration", "type": "uint48"},
                              {"name": "nonce", "type": "uint48"}],
            "PermitBatch": [{"name": "details", "type": "PermitDetails[]"}, {"name": "spender", "type": "address"}, {"name": "sigDeadline", "type": "uint256"}],
        },
        "primaryType": "PermitBatch",
        "domain": {"name": "Permit2", "chainId": 1, "verifyingContract": PERMIT2},
        "message": {"details": [{"token": USDC, "amount": "1000", "expiration": str(NOW_EPOCH + 3000), "nonce": nonce_value}], "spender": BOB,
                    "sigDeadline": str(NOW_EPOCH + 600)},
    }


def test_typed_data_nonce_inside_an_array_of_structs_is_found():
    # Permit2 PermitBatch carries one nonce per entry of `details[]`: it is not "missing" (replay protection exists)
    assert "TD-NONCE-MISSING" not in ids(typed(_permit_batch_doc()))
    document = _permit_batch_doc()
    document["message"]["details"][0]["nonce"] = "not-a-number"
    assert ids(typed(document))["TD-NONCE-MALFORMED"] == "major"
    # a batch whose entries all lack a nonce field still reports it
    for detail in (document := _permit_batch_doc())["message"]["details"]:
        del detail["nonce"]
    document["types"]["PermitDetails"] = [f for f in document["types"]["PermitDetails"] if f["name"] != "nonce"]
    assert ids(typed(document))["TD-NONCE-MISSING"] == "major"


def test_typed_data_deadline_far_beyond_any_millisecond_clock_is_not_called_milliseconds():
    absurd = ids(typed(with_message(deadline=str(2**200))))
    assert "TD-DEADLINE-NEVER" in absurd and "TD-DEADLINE-MILLISECONDS" not in absurd
    assert ids(typed(with_message(deadline=str(NOW_EPOCH * 1000))))["TD-DEADLINE-MILLISECONDS"] == "major"
    assert "TD-DEADLINE-NEVER" not in ids(typed(with_message(deadline=str(10**14))))  # the last value a ms clock can show (year 5138)
    assert ids(typed(with_message(deadline=str(10**14 + 1))))["TD-DEADLINE-NEVER"] == "major"


def test_typed_data_salt_must_be_32_bytes():
    document = typed_doc()
    document["types"]["EIP712Domain"].append({"name": "salt", "type": "bytes32"})
    document["domain"]["salt"] = "0x" + "11" * 32
    assert "TD-DOMAIN-SALT" not in ids(typed(document))
    document["domain"]["salt"] = "0x1234"
    assert ids(typed(document))["TD-DOMAIN-SALT"] == "major"


def test_permit2_nonce_is_a_uint48_and_erc2612_nonce_a_uint256():
    # Permit2 AllowanceTransfer stores nonce as uint48 (PermitDetails.nonce, PackedAllowance.nonce)
    for field in ("nonce", "current_nonce"):
        invalid("dapp_permit_check", permit2(**{field: str(2**48)}), field)
    assert call("dapp_permit_check", permit2(nonce=str(2**48 - 1), current_nonce=str(2**48 - 1)))["nonce_matches"] is True
    assert call("dapp_permit_check", permit(nonce=str(2**48), current_nonce=str(2**48)))["nonce_matches"] is True


def test_userop_gas_values_overflow_is_the_uint120_check_of_both_entry_points():
    # EntryPoint v0.6.0 and v0.7.0 _validatePrepayment: maxGasValues <= type(uint120).max else AA94 "gas values overflow"
    limit = 2**120
    for version in ("0.6", "0.7"):
        for field in ("call_gas_limit", "verification_gas_limit", "pre_verification_gas", "max_fee_per_gas"):
            extra = {"max_priority_fee_per_gas": "0x1"} if field == "max_fee_per_gas" else {}
            assert ids(ok("dapp_userop_check", uop(entry_point_version=version, **{field: hex(limit)}, **extra))).get("UOP-GAS-OVERFLOW") == "blocker", (version, field)
            assert "UOP-GAS-OVERFLOW" not in ids(ok("dapp_userop_check", uop(entry_point_version=version, **{field: hex(limit - 1)}, **extra))), (version, field)
    assert "UOP-GAS-OVERFLOW" in ids(ok("dapp_userop_check", uop(max_priority_fee_per_gas=hex(limit), max_fee_per_gas=hex(limit))))
    packed = "0x" + "11" * 20 + limit.to_bytes(16, "big").hex() + (1).to_bytes(16, "big").hex()
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.7", paymaster_and_data=packed)))["UOP-GAS-OVERFLOW"] == "blocker"
    packed = "0x" + "11" * 20 + (1).to_bytes(16, "big").hex() + limit.to_bytes(16, "big").hex()
    assert ids(ok("dapp_userop_check", uop(entry_point_version="0.7", paymaster_and_data=packed)))["UOP-GAS-OVERFLOW"] == "blocker"


def test_userop_prefund_matches_the_entrypoint_formulas_on_varied_values():
    # independent recomputation of _getRequiredPrefund: v0.6 call + verification * (3 with paymaster else 1) + preVerification;
    # v0.7 verification + call + paymasterVerification + paymasterPostOp + preVerification; both times maxFeePerGas
    cases = [(0, 1, 0, 1), (10**6, 10**6, 21000, 10**9), (7, 11, 13, 2**64), (2**100, 3, 5, 7)]
    for call_gas, verification, pre, fee in cases:
        base = uop(call_gas_limit=hex(call_gas), verification_gas_limit=hex(verification), pre_verification_gas=hex(pre), max_fee_per_gas=hex(fee),
                   max_priority_fee_per_gas="0x0")
        assert ok("dapp_userop_check", base)["required_prefund_wei"] == str((call_gas + verification + pre) * fee)
        sponsored = {**base, "paymaster_and_data": "0x" + "22" * 20}
        assert ok("dapp_userop_check", sponsored)["required_prefund_wei"] == str((call_gas + verification * 3 + pre) * fee)
        v07 = {**base, "entry_point_version": "0.7", "paymaster_and_data": "0x" + "22" * 20 + (17).to_bytes(16, "big").hex() + (19).to_bytes(16, "big").hex()}
        assert ok("dapp_userop_check", v07)["required_prefund_wei"] == str((verification + call_gas + 17 + 19 + pre) * fee)


def test_gas_recommendation_never_exceeds_the_protocol_cap():
    result = gas(estimated_gas=15_000_000, gas_limit=16_000_000, tx_gas_cap=16_777_216)
    assert result["recommended_gas_limit"] == 16_777_216  # 20% over the estimate would be 18,000,000, above EIP-7825's 2**24
    assert gas(estimated_gas=100_000, gas_limit=120_000, tx_gas_cap=16_777_216)["recommended_gas_limit"] == 120_000
    assert gas(estimated_gas=20_000_000, gas_limit=20_000_000, block_gas_limit=60_000_000, tx_gas_cap=16_777_216)["recommended_gas_limit"] == 20_000_000


def test_gas_headroom_message_never_rounds_a_failing_value_up_to_the_threshold():
    summary = next(f["summary"] for f in gas(gas_limit=109_999)["findings"] if f["id"] == "GAS-NO-HEADROOM")
    assert "9.999%" in summary and "10.0% headroom" not in summary


def test_wallet_with_an_injected_provider_stays_compatible_when_it_also_lists_walletconnect_v1():
    both = matrix(wallet("Alpha", walletconnect="v1"))
    assert both["compatible_wallets"] == ["Alpha"] and ids(both) == {"WALLET-WALLETCONNECT-V1": "minor"}
    only = matrix(wallet("Beta", eip1193=False, walletconnect="v1", announced_6963=False))
    assert only["compatible_wallets"] == [] and ids(only)["WALLET-WALLETCONNECT-V1"] == "major"


def test_error_map_contract_revert_text_never_selects_a_node_level_category():
    # a Solidity reason string such as "insufficient funds" or "nonce too low" is contract output, not a node error
    for code in (3, -32000, -32603):
        for text in ("execution reverted: insufficient funds for transfer", "execution reverted: nonce too low", "execution reverted: out of gas"):
            result = ok("dapp_provider_error_map", {"code": code, "message": text})
            assert (result["category"], result["suggested_action"], result["matched_by"]) == ("execution_reverted", "show_revert_reason", "message" if code != 3 else "code"), (code, text)
    assert ok("dapp_provider_error_map", {"code": -32000, "message": "nonce too low: next nonce 5"})["category"] == "nonce_too_low"


def test_4902_is_attributed_to_metamask_not_eip_3326():
    # EIP-3326 defines no error code; 4902 is MetaMask's "Unrecognized chain ID" (MetaMask/metamask-extension switch-ethereum-chain.js)
    assert a29_tools().ERROR_CODES[4902][1] == "MetaMask"
    assert ok("dapp_provider_error_map", {"code": 4902})["standard"] == "MetaMask"


def test_signing_scan_zero_amount_approval_is_a_revoke_not_a_drain():
    revoke = "0x095ea7b3" + word_address(CAROL) + word_uint(0)
    result = scan({"method": "eth_sendTransaction", "to": USDC, "data": revoke})
    assert ids(result) == {} and result["verdict"] == "pass" and result["decoded_calls"][0]["signature"] == "approve(address,uint256)"
    off = "0xa22cb465" + word_address(CAROL) + word_uint(0)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": off})) == {}
    grant = "0x095ea7b3" + word_address(CAROL) + word_uint(1)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": grant})) == {"SIGN-SPENDER-UNKNOWN": "blocker"}


def test_signing_scan_bool_word_above_one_is_malformed_not_true():
    # solidity >= 0.8 ABI decoding reverts on a bool word other than 0 or 1
    weird = "0xa22cb465" + word_address(BOB) + word_uint(2)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": weird})) == {"SIGN-CALLDATA-MALFORMED": "major"}
    true = "0xa22cb465" + word_address(BOB) + word_uint(1)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": true})) == {"SIGN-APPROVAL-FOR-ALL": "major"}


def test_signing_scan_link_and_origin_schemes_userinfo_and_trailing_dot():
    result = scan(links=["javascript:alert(1)", "data:text/html,x", "ftp://app.example.com", "https://app.example.com@evil.com/",
                         "https://user:pw@docs.example.com/", "https://APP.EXAMPLE.COM./x", "https://docs.example.com./"], domains=("app.example.com", "docs.example.com"))
    findings = [(f["id"], f["severity"]) for f in result["findings"]]
    assert findings.count(("LINK-DANGEROUS-SCHEME", "blocker")) == 2
    assert ("LINK-SCHEME", "major") in findings
    assert ("LINK-USERINFO-SPOOF", "blocker") in findings and ("LINK-USERINFO", "major") in findings
    assert not any(i in ("LINK-SPOOF", "LINK-LOOKALIKE") for i, _ in findings)  # trailing-dot FQDNs are the same host
    origin = scan({"method": "personal_sign", "origin": "https://app.example.com@evil.com"}, domains=("app.example.com",))
    assert ids(origin)["ORIGIN-USERINFO-SPOOF"] == "blocker"
    assert ids(scan({"method": "personal_sign", "origin": "https://app.example.com./"}, domains=("app.example.com",))) == {}
    assert ids(scan(links=["javascript:alert(1)"], domains=()))["LINK-DANGEROUS-SCHEME"] == "blocker"


def test_chain_config_name_impersonation_of_a_known_network():
    fake = ok("dapp_chain_config_check", chain(chain_id="0x3039", chain_name="Ethereum Mainnet", native_symbol="ETH"))
    assert ids(fake)["CHAIN-NAME-IMPERSONATION"] == "blocker" and fake["verdict"] == "block"
    assert ids(ok("dapp_chain_config_check", chain(chain_id="0xaa36a7", chain_name="Ethereum Mainnet")))["CHAIN-NAME-IMPERSONATION"] == "blocker"
    for name in ("Arbitrum One", "arbitrum  one", "OP Mainnet", "BNB Smart Chain", "Base", "Polygon"):
        assert ids(ok("dapp_chain_config_check", chain(chain_id="0x1234", chain_name=name)))["CHAIN-NAME-IMPERSONATION"] == "blocker", name
    assert "CHAIN-NAME-IMPERSONATION" not in ids(ok("dapp_chain_config_check", chain(chain_id="0x1", chain_name="Ethereum Mainnet", native_symbol="ETH")))
    assert "CHAIN-NAME-IMPERSONATION" not in ids(ok("dapp_chain_config_check", chain(chain_id="0x1234", chain_name="Base Sepolia")))


def test_chain_rpc_private_and_loopback_ranges_are_local_endpoints():
    result = ok("dapp_chain_config_check", chain(rpc_urls=["http://127.0.0.2:8545", "http://192.168.1.5:8545", "http://10.0.0.1/", "http://rpc.localhost:8545"]))
    assert {f["id"] for f in result["findings"]} == {"CHAIN-RPC-1-LOCAL", "CHAIN-RPC-2-LOCAL", "CHAIN-RPC-3-LOCAL", "CHAIN-RPC-4-LOCAL"}
    dev = ok("dapp_chain_config_check", chain(chain_id="0x7a69", rpc_urls=["http://127.0.0.2:8545"]))
    assert "CHAIN-RPC-1-LOCAL" not in ids(dev)


def test_chain_rpc_alchemy_style_key_with_dash_and_underscore_is_a_key():
    key = "Zu8mQ-aB3x9_QzPLkd0e1f2g3h4i5j6k"
    result = ok("dapp_chain_config_check", chain(rpc_urls=[f"https://eth-mainnet.g.alchemy.com/v2/{key}"]))
    assert ids(result)["CHAIN-RPC-1-KEY"] == "major" and key not in json.dumps(result)
    for slug in ("ethereum-mainnet-archive", "polygon-mainnet-fullnode-2", "v3", "eth_mainnet"):
        assert "CHAIN-RPC-1-KEY" not in ids(ok("dapp_chain_config_check", chain(rpc_urls=[f"https://rpc.example.org/{slug}"]))), slug


def test_rpc_fallback_subdomains_of_one_provider_are_one_failure_domain():
    same = fallback([provider("https://mainnet.infura.io/v3"), provider("https://sepolia.infura.io/v3"), provider("https://eu.infura.io")], quorum=2)
    assert ids(same)["RPC-SAME-DOMAIN"] == "major" and same["distinct_hosts"] == 3
    assert "RPC-SAME-DOMAIN" not in ids(fallback([provider("https://a.infura.io"), provider("https://b.alchemy.com")]))
    assert ids(fallback([provider("https://a.example.co.uk"), provider("https://b.example.co.uk")]))["RPC-SAME-DOMAIN"] == "major"
    assert "RPC-SAME-DOMAIN" not in ids(fallback([provider("https://a.example.co.uk"), provider("https://a.other.co.uk")]))
    assert "RPC-SAME-DOMAIN" not in ids(fallback([provider("https://203.0.113.5"), provider("https://203.0.113.6")]))  # IP literals are distinct hosts


def test_ens_japanese_korean_and_chinese_script_mixes_are_not_homographs():
    for label in ("ひらがなカタカナ", "日本語かな", "한국어한글", "中文注音ㄅㄆ"):
        assert "ENS-MIXED-SCRIPT" not in ids(ens(label + ".eth")), label
    assert ids(ens("раураl.eth"))["ENS-MIXED-SCRIPT"] == "blocker"  # Cyrillic + Latin
    assert ids(ens("日本語abc.eth"))["ENS-MIXED-SCRIPT"] == "blocker"  # Han + Latin is not a Japanese combination
    assert ids(ens("ひらがなカタカナ한.eth"))["ENS-MIXED-SCRIPT"] == "blocker"  # Japanese + Korean


def test_ens_zero_width_joiner_inside_an_emoji_sequence_is_not_invisible_text():
    family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
    assert "ENS-INVISIBLE-CHARACTER" not in ids(ens(family + ".eth"))
    assert ids(ens("a\u200db.eth"))["ENS-INVISIBLE-CHARACTER"] == "blocker"
    assert ids(ens("a\u200bb.eth"))["ENS-INVISIBLE-CHARACTER"] == "blocker"
    assert ids(ens("\u200d\U0001F468.eth"))["ENS-INVISIBLE-CHARACTER"] == "blocker"  # a leading ZWJ joins nothing


def test_siwe_chain_id_leading_zero_is_grammatical_but_noncanonical():
    # ABNF chain-id = 1*DIGIT, so "01" is valid grammar; it is only a non-canonical spelling
    result = ok("dapp_siwe_check", siwe(siwe_text(chain="01")))
    assert ids(result) == {"SIWE-CHAIN-LEADING-ZERO": "minor"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(chain="0")))) == {"SIWE-CHAIN-MALFORMED": "blocker"}
    assert ids(ok("dapp_siwe_check", siwe(siwe_text(chain="2"))))["SIWE-CHAIN-MISMATCH"] == "major"


# --- secret safety: a canary secret fed to every tool input that could carry one never appears in any result -----------

CANARY_KEY = "ghp_Zq8Xv2LmN4bT6yR1wK9sD3fH7jP0cA5e"
CANARY_AWS = "AKIAZ7Q2X5LMN3B8T6YR"
CANARY_HEX = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
CANARY_PHRASE = "legal winner thank year wave sausage worth useful legal winner thank yellow"
CANARY_PW = "hunter2Zq8Xv2"
CANARY_URL = f"https://user:{CANARY_PW}@node.example.org/v2/{CANARY_KEY}?apikey={CANARY_AWS}&token={CANARY_HEX[:20]}"
CANARIES = (CANARY_KEY, CANARY_AWS, CANARY_HEX, CANARY_PHRASE, CANARY_PW, CANARY_HEX[:20])


def _assert_no_canary(result) -> None:
    text = json.dumps(result)
    for canary in CANARIES:
        assert canary not in text, canary


def test_chain_config_never_echoes_url_secrets():
    result = ok("dapp_chain_config_check", chain(rpc_urls=[CANARY_URL, CANARY_URL.replace("https", "http")], explorer_urls=[CANARY_URL]))
    _assert_no_canary(result)
    assert {"CHAIN-RPC-1-CREDENTIALS", "CHAIN-RPC-1-KEY", "CHAIN-EXPLORER-1"} <= set(ids(result))


def test_rpc_fallback_never_echoes_url_secrets():
    result = fallback([provider(CANARY_URL), provider("https://b.example.net")])
    _assert_no_canary(result)
    assert {"RPC-1-CREDENTIALS", "RPC-1-KEY"} <= set(ids(result))


def test_signing_scan_never_echoes_url_secrets_origins_or_links():
    result = scan({"method": "personal_sign", "origin": CANARY_URL}, {"method": "eth_sendTransaction", "to": USDC, "origin": f"https://{CANARY_PW}@evil.example/"},
                  links=[CANARY_URL, f"https://app.example.com:{CANARY_PW}@evil.example/?next={CANARY_KEY}", f"javascript:alert('{CANARY_KEY}')"],
                  domains=("example.com",))
    _assert_no_canary(result)


def test_siwe_never_echoes_the_domain_userinfo_version_or_uri_secrets():
    text = siwe_text(domain=f"user:{CANARY_PW}@app.example.com", uri=CANARY_URL, version=CANARY_KEY, nonce=CANARY_AWS,
                     resources=[CANARY_URL, f"urn:secret:{CANARY_PHRASE.replace(' ', '-')}"], request_id=CANARY_HEX[:20])
    result = ok("dapp_siwe_check", siwe(text, expected_domain=f"user:{CANARY_PW}@app.example.com", expected_nonce=CANARY_HEX))
    _assert_no_canary(result)
    assert "SIWE-DOMAIN-USERINFO" in ids(result)
    assert ids(result)["SIWE-VERSION"] == "major"
    assert result["domain"] == "app.example.com"


def test_typed_data_and_permit_never_echo_message_values():
    document = typed_doc()
    document["message"].update(value=CANARY_HEX, nonce=CANARY_KEY, deadline=CANARY_PHRASE, memo=CANARY_AWS, owner=CANARY_HEX, spender=CANARY_KEY)
    document["domain"].update(name=CANARY_PHRASE, version=CANARY_PW, verifyingContract=CANARY_HEX[:40])
    _assert_no_canary(typed(document))
    document["domain"]["chainId"] = CANARY_KEY
    _assert_no_canary(typed(document))
    _assert_no_canary(call("dapp_typed_data_check", {"typed_data_json": CANARY_KEY + CANARY_PHRASE, "expected_chain_id": 1, "now": NOW}))
    _assert_no_canary(call("dapp_typed_data_check", {"typed_data_json": json.dumps(typed_doc()), "expected_chain_id": 1, "now": CANARY_KEY + "xxxx"}))


def test_tx_userop_error_map_and_gas_never_echo_hashes_signatures_or_messages():
    history_events = history(["draft", "simulating", "simulated", "awaiting_confirmation", "user_confirmed", "awaiting_signature", "submitted"],
                             extras={"submitted": {"tx_hash": CANARY_HEX, "replaced_by": CANARY_KEY}})
    _assert_no_canary(ok("dapp_tx_state_machine", {"events": history_events, "now": NOW}))
    _assert_no_canary(ok("dapp_userop_check", uop(signature="0x" + CANARY_HEX, call_data="0x" + CANARY_HEX[:40], nonce="0x" + CANARY_HEX[:30])))
    _assert_no_canary(ok("dapp_provider_error_map", {"code": -32000, "message": f"{CANARY_KEY} {CANARY_PHRASE} nonce too low {CANARY_AWS}"}))
    _assert_no_canary(ok("dapp_provider_error_map", {"code": 3, "revert_data": "0x" + CANARY_HEX}))
    _assert_no_canary(ok("dapp_wallet_matrix", {"wallets": [wallet("Alpha", uuid=CANARY_KEY[:30], rdns=CANARY_AWS, icon=CANARY_PHRASE)], "required_methods": ["personal_sign"],
                                                  "required_chain_ids": [1]}))


def test_typed_data_with_deeply_nested_arrays_in_the_message_never_crashes():
    document = typed_doc()
    nested = []
    for _ in range(2000):
        nested = [nested]
    document["message"]["extra"] = nested
    result = typed(document)
    assert result["state"] == "SUCCESS" and ids(result)["TD-FIELD-EXTRA"] == "minor"


def test_wallet_matrix_duplicate_6963_ids_are_echoed_only_when_well_formed():
    leaked = matrix(wallet("Alpha", uuid=CANARY_KEY, rdns=CANARY_AWS), wallet("Beta", uuid=CANARY_KEY, rdns=CANARY_AWS))
    _assert_no_canary(leaked)
    assert ids(leaked)["WALLET-6963-UUID-DUPLICATE"] == "major" and ids(leaked)["WALLET-6963-RDNS-DUPLICATE"] == "minor"
    clean = matrix(wallet("Alpha"), wallet("Beta"))
    assert UUID4 in next(f["summary"] for f in clean["findings"] if f["id"] == "WALLET-6963-UUID-DUPLICATE")
    assert "io.alpha" in next(f["summary"] for f in clean["findings"] if f["id"] == "WALLET-6963-RDNS-DUPLICATE")


def test_gas_buffer_matches_an_independent_exact_fraction_computation():
    import random
    from fractions import Fraction
    rng = random.Random(2910)
    for _ in range(300):
        estimate = rng.choice([1, 21000, rng.randint(1, 3_000_000)])
        limit = max(1, estimate + rng.choice([-1, 0, 1, rng.randint(-estimate // 2, estimate * 3)]))
        block = rng.choice([30_000_000, 36_000_000, max(limit, estimate) * rng.choice([1, 2, 10])])
        contract = rng.choice([True, False])
        result = gas(estimated_gas=estimate, gas_limit=limit, block_gas_limit=block, contract_call=contract)
        buffer = Fraction((limit - estimate) * 100, estimate)
        assert result["buffer_percent"] == round(float(buffer), 4), (estimate, limit)
        found = ids(result)
        assert ("GAS-BELOW-ESTIMATE" in found) == (limit < estimate)
        assert ("GAS-NO-HEADROOM" in found) == (contract and limit >= estimate and buffer < 10)
        assert ("GAS-BUFFER-EXCESSIVE" in found) == (limit >= estimate and not (contract and buffer < 10) and buffer > 100)
        assert ("GAS-ABOVE-BLOCK-LIMIT" in found) == (limit > block)
        assert ("GAS-NEAR-BLOCK-LIMIT" in found) == (limit <= block and limit * 100 > block * 90)
        ceiling = block * 90 // 100
        wanted = max(estimate, min(-(-estimate * 120 // 100), ceiling)) if contract else estimate
        assert result["recommended_gas_limit"] == wanted
        assert ("GAS-RECOMMEND-OVER-CEILING" in found) == (estimate > ceiling)


def test_rpc_worst_case_and_quorum_match_an_independent_computation():
    import random
    rng = random.Random(2911)
    for _ in range(200):
        count = rng.randint(1, 5)
        providers = [provider(f"https://p{i}.example{i}.com/rpc", timeout=rng.randint(500, 40000), retries=rng.randint(0, 5), weight=rng.randint(1, 4))
                     for i in range(count)]
        backoff, quorum = rng.randint(0, 3000), rng.randint(1, 12)
        result = fallback(providers, quorum=quorum, backoff=backoff, budget=rng.randint(1000, 120000))
        expected = sum(p["timeout_ms"] * (p["retries"] + 1) + backoff * p["retries"] for p in providers)
        weight = sum(p["weight"] for p in providers)
        assert (result["worst_case_ms"], result["total_weight"], result["majority_quorum"]) == (expected, weight, quorum * 2 > weight)
        found = ids(result)
        assert ("RPC-QUORUM-UNREACHABLE" in found) == (quorum > weight)
        assert ("RPC-QUORUM-NO-MAJORITY" in found) == (quorum <= weight and quorum * 2 <= weight)


def test_userop_calldata_floor_is_four_per_zero_byte_and_sixteen_per_nonzero_byte():
    import random
    rng = random.Random(2912)
    for _ in range(100):
        blobs = {key: bytes(rng.choice([0, 0, 1, 255, rng.randint(0, 255)]) for _ in range(rng.randint(0, 40)))
                 for key in ("call_data", "signature")}
        result = ok("dapp_userop_check", uop(call_data="0x" + blobs["call_data"].hex(), signature="0x" + blobs["signature"].hex(), pre_verification_gas="0x" + "ff" * 8))
        assert result["calldata_gas_floor"] == sum(4 if b == 0 else 16 for blob in blobs.values() for b in blob)


def test_known_transaction_fragment_does_not_match_inside_unknown_transaction():
    def refine(code, message):
        return ok("dapp_provider_error_map", {"code": code, "message": message})
    unrelated = refine(-32000, "unknown transaction")
    assert (unrelated["category"], unrelated["message_class"], unrelated["show_error_toast"], unrelated["matched_by"]) == ("server_error", "error", True, "code")
    assert refine(-32000, "already known")["category"] == "already_known"
    assert refine(-32000, "known transaction: 0xabc")["category"] == "already_known"


# --- QA-03 remediation: regressions for the blocker and major fixes ----------------------------------------------------


def test_dangerous_link_schemes_block_even_when_urlsplit_refuses_the_url():
    result = scan(links=["javascript://[%0aalert(document.cookie)", "vbscript://[x", "DATA://[x", "javascript:alert(1)"], domains=("app.example.com",))
    assert ids(result) == {"LINK-DANGEROUS-SCHEME": "blocker"} and result["verdict"] == "block"
    assert [f["id"] for f in result["findings"]].count("LINK-DANGEROUS-SCHEME") == 4


def test_unlisted_chain_fails_the_mainnet_gate_closed():
    cronos = ok("dapp_chain_config_check", chain(chain_id="0x19", chain_name="Cronos Mainnet", native_symbol="CRO", rpc_urls=["https://rpc.cronos.org"],
                                                  explorer_urls=["https://cronoscan.com"], expected_chain_id=25))
    assert cronos["network_class"] == "unknown" and cronos["mainnet_enable_requires_approval"] is True
    zora = ok("dapp_chain_config_check", chain(chain_id="0x76adf1", chain_name="Zora", native_symbol="ETH", rpc_urls=["https://rpc.zora.energy"]))
    assert zora["mainnet_enable_requires_approval"] is True
    testnet = ok("dapp_chain_config_check", chain(chain_id="0x14a34", chain_name="Base Sepolia", native_symbol="ETH", rpc_urls=["https://sepolia.base.org"]))
    assert testnet["network_class"] == "testnet" and testnet["mainnet_enable_requires_approval"] is False


def test_lookalike_links_and_origins_block_consistently():
    result = scan(links=["https://app.example.com-login.io/x", "https://app-example.com/login", "https://appexample.com/login"], domains=("app.example.com",))
    assert [f["id"] for f in result["findings"]] == ["LINK-LOOKALIKE"] * 3 and result["verdict"] == "block"
    assert ids(scan({"method": "personal_sign", "origin": "https://app-example.com"}, domains=("app.example.com",))) == {"ORIGIN-LOOKALIKE": "blocker"}
    assert ids(scan(links=["https://twitter.com/x"], domains=("app.example.com",))) == {"LINK-UNEXPECTED": "minor"}
    assert scan(links=["https://docs.app.example.com/x"], domains=("app.example.com",))["findings"] == []


def test_permit2_approve_and_dai_permit_allowances_are_decoded():
    unknown = "0x87517c45" + word_address(USDC) + word_address(CAROL) + word_uint(2**160 - 1) + word_uint(2**48 - 1)
    result = scan({"method": "eth_sendTransaction", "to": USDC, "data": unknown})
    assert ids(result) == {"SIGN-SPENDER-UNKNOWN": "blocker", "SIGN-UNLIMITED": "blocker"}
    assert result["decoded_calls"][0]["signature"] == "approve(address,address,uint160,uint48)"
    known = "0x87517c45" + word_address(USDC) + word_address(BOB) + word_uint(2**159) + word_uint(0)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": known})) == {"SIGN-UNLIMITED": "major"}
    # a Permit2 amount is a uint160: 2**159 is unlimited under the Permit2 threshold, 2**159 - 1 is not
    below = "0x87517c45" + word_address(USDC) + word_address(BOB) + word_uint(2**159 - 1) + word_uint(0)
    assert scan({"method": "eth_sendTransaction", "to": USDC, "data": below})["findings"] == []
    revoke = "0x87517c45" + word_address(USDC) + word_address(CAROL) + word_uint(0) + word_uint(0)
    assert scan({"method": "eth_sendTransaction", "to": USDC, "data": revoke})["findings"] == []
    short = "0x87517c45" + word_address(USDC) + word_address(BOB) + word_uint(1)
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": short})) == {"SIGN-CALLDATA-TRUNCATED": "major"}

    def dai(allowed: int) -> str:
        return "0x8fcbaf0c" + word_address(ALICE) + word_address(CAROL) + word_uint(0) + word_uint(NOW_EPOCH) + word_uint(allowed) + word_uint(27) + word_uint(1) + word_uint(2)

    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": dai(1)})) == {"SIGN-SPENDER-UNKNOWN": "blocker", "SIGN-UNLIMITED": "blocker"}
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": dai(1)}, known=(USDC, BOB, CAROL))) == {"SIGN-UNLIMITED": "major"}
    assert scan({"method": "eth_sendTransaction", "to": USDC, "data": dai(0)})["findings"] == []
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": dai(2)})) == {"SIGN-CALLDATA-MALFORMED": "major"}
    assert ids(scan({"method": "eth_sendTransaction", "to": USDC, "data": "0x8fcbaf0c" + "00" * 32 * 7})) == {"SIGN-CALLDATA-TRUNCATED": "major"}


def test_malformed_origin_is_reported_without_expected_domains():
    result = scan({"method": "personal_sign", "origin": "javascript://[%0aalert(1)"}, domains=())
    assert ids(result) == {"SIGN-ORIGIN-MALFORMED": "major"} and result["verdict"] == "review"


def test_dangerous_explorer_scheme_is_a_blocker():
    assert ids(ok("dapp_chain_config_check", chain(explorer_urls=["javascript:alert(1)"]))) == {"CHAIN-EXPLORER-1-DANGEROUS-SCHEME": "blocker"}
    assert ids(ok("dapp_chain_config_check", chain(explorer_urls=["data:text/html,x"]))) == {"CHAIN-EXPLORER-1-DANGEROUS-SCHEME": "blocker"}


def test_urls_that_urlsplit_rejects_never_echo_the_scheme_text():
    token = "sk1a2b3c4d5e6f7g8h9i0jKLMNOP"
    result = ok("dapp_chain_config_check", chain(rpc_urls=[f"{token}://polygon-rpc.com"]))
    assert ids(result)["CHAIN-RPC-1-SCHEME"] == "blocker" and token.lower() not in json.dumps(result)
    link = scan(links=[f"{token}://app.example.com"], domains=())
    assert ids(link) == {"LINK-SCHEME": "major"} and token.lower() not in json.dumps(link) and token not in json.dumps(link)


# --- QA-03 re-review: N9 and N10 ----------------------------------------------------------------------------------------------


def test_link_with_an_embedded_newline_or_tab_is_read_as_the_browser_reads_it():  # N9 (WHATWG parser; spec-based, not run in a browser)
    newline = scan(links=["https://app.example.com\n.evil.io"], domains=("app.example.com",))
    assert ids(newline) == {"LINK-SPOOF": "blocker", "LINK-CONTROL-CHARACTER": "major"} and newline["verdict"] == "block"
    tab = scan(links=["https://app.example\t.com/"], domains=("app.example.com",))
    assert ids(tab) == {"LINK-CONTROL-CHARACTER": "major"} and tab["verdict"] == "review"  # the stripped host is the expected one
    origin = scan({"method": "personal_sign", "origin": "https://app.example.com\n.evil.io"}, domains=("app.example.com",))
    assert ids(origin) == {"ORIGIN-SPOOF": "blocker", "ORIGIN-CONTROL-CHARACTER": "major"}


def test_single_label_rpc_host_is_shown_by_length_only():  # N10
    host = "localnode-private-host"
    result = ok("dapp_chain_config_check", chain(rpc_urls=[f"http://{host}:8545"]))
    assert ids(result) == {"CHAIN-RPC-1-LOCAL": "major"}
    assert "host of 22 chars" in next(f["summary"] for f in result["findings"] if f["id"] == "CHAIN-RPC-1-LOCAL")
    assert host not in json.dumps(result)


# --- re-review: ND5 (WHATWG parser strips leading and trailing C0 controls and spaces; spec-based, not run in a browser) -------------


def test_link_with_a_leading_space_still_has_its_host_checked():
    result = scan(links=[" https://app.example.com.evil.io/x"], domains=("app.example.com",))
    assert ids(result) == {"LINK-SPOOF": "blocker"} and result["verdict"] == "block"
    origin = scan({"method": "personal_sign", "origin": " https://app.example.com.evil.io"}, domains=("app.example.com",))
    assert ids(origin) == {"ORIGIN-SPOOF": "blocker"} and origin["verdict"] == "block"


def test_link_with_a_trailing_space_still_has_its_host_checked():
    result = scan(links=["https://app.example.com.evil.io/x "], domains=("app.example.com",))
    assert ids(result) == {"LINK-SPOOF": "blocker"} and result["verdict"] == "block"
