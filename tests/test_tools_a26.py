"""Behaviour tests for the A26 Smart Contract Engineer tools (sol_*).

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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A26", "a26-smart-contract", "sol_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "sol_selector_check", "sol_abi_diff", "sol_storage_layout", "sol_erc_conformance", "sol_upgrade_safety", "sol_access_matrix",
    "sol_event_coverage", "sol_natspec_coverage", "sol_gas_regression", "sol_deploy_plan_check", "sol_pragma_policy", "sol_test_gate",
    "sol_eip712_typehash",
]


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




def ids(result: dict) -> list[str]:
    return [f["id"] for f in result["findings"]]


def severity_of(result: dict, rule: str) -> str:
    matches = [f["severity"] for f in result["findings"] if f["id"] == rule]
    assert matches, (rule, result["findings"])
    return matches[0]


def has(result: dict, rule: str, text: str = "") -> bool:
    return any(f["id"] == rule and text in f["summary"] for f in result["findings"])


def tools_module():
    return importlib.import_module("swarm.tools.a26_tools")


# --- keccak-256 (pure Python, Ethereum padding) ----------------------------------------------------------------


@pytest.mark.parametrize("data, digest", [
    (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
    (b"a" * 135, "34367dc248bbd832f4e3e69dfaac2f92638bd0bbd18f2912ba4ef454919cf446"),  # last byte of the block is the 0x81 pad
    (b"a" * 136, "a6c4d403279fe3e0af03729caada8374b5ca54d8065329a3ebcaeb4b60aa386e"),  # exactly one rate block: pad fills a whole block
    (b"a" * 137, "d869f639c7046b4929fc92a4d988a8b22c55fbadb802c0c66ebcd484f1915f39"),
    (b"a" * 200, "96ea54061def936c4be90b518992fdc6f12f535068a256229aca54267b4d084d"),
])
def test_keccak256_matches_reference_vectors(data, digest):
    assert tools_module()._keccak256(data).hex() == digest


# --- sol_selector_check ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("signature, selector", [
    ("transfer(address,uint256)", "0xa9059cbb"), ("balanceOf(address)", "0x70a08231"), ("approve(address,uint256)", "0x095ea7b3"),
    ("transferFrom(address,address,uint256)", "0x23b872dd"), ("totalSupply()", "0x18160ddd"), ("allowance(address,address)", "0xdd62ed3e"),
    ("ownerOf(uint256)", "0x6352211e"), ("safeTransferFrom(address,address,uint256)", "0x42842e0e"),
    ("permit(address,address,uint256,uint256,uint8,bytes32,bytes32)", "0xd505accf"), ("nonces(address)", "0x7ecebe00"),
    ("DOMAIN_SEPARATOR()", "0x3644e515"), ("supportsInterface(bytes4)", "0x01ffc9a7"), ("deposit(uint256,address)", "0x6e553f65"),
    ("redeem(uint256,address,address)", "0xba087652"), ("decimals()", "0x313ce567"), ("name()", "0x06fdde03"), ("symbol()", "0x95d89b41"),
])
def test_selector_check_known_selectors(signature, selector):
    result = ok("sol_selector_check", {"functions": [signature]})
    assert result["selectors"] == [{"signature": signature, "selector": selector}]
    assert result["verdict"] == "clean" and result["findings"] == [] and result["interface_id"] == selector


@pytest.mark.parametrize("signature, topic", [
    ("Transfer(address,address,uint256)", "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
    ("Approval(address,address,uint256)", "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"),
    ("ApprovalForAll(address,address,bool)", "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"),
    ("TransferSingle(address,address,address,uint256,uint256)", "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"),
    ("TransferBatch(address,address,address,uint256[],uint256[])", "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"),
])
def test_selector_check_event_topics(signature, topic):
    result = ok("sol_selector_check", {"functions": ["name()"], "events": [signature]})
    assert result["topics"] == [{"signature": signature, "topic0": topic}] and result["event_count"] == 1


def test_selector_check_interface_id_is_the_xor_of_selectors_erc721_and_erc165():
    erc721 = ["balanceOf(address)", "ownerOf(uint256)", "safeTransferFrom(address,address,uint256,bytes)", "safeTransferFrom(address,address,uint256)",
              "transferFrom(address,address,uint256)", "approve(address,uint256)", "setApprovalForAll(address,bool)", "getApproved(uint256)",
              "isApprovedForAll(address,address)"]
    assert ok("sol_selector_check", {"functions": erc721})["interface_id"] == "0x80ac58cd"
    # duplicates and ordering do not change the id
    assert ok("sol_selector_check", {"functions": erc721[::-1] + [erc721[0]]})["interface_id"] == "0x80ac58cd"
    assert ok("sol_selector_check", {"functions": ["supportsInterface(bytes4)"]})["interface_id"] == "0x01ffc9a7"


def test_selector_check_famous_collision_is_a_blocker_and_listing_is_sorted():
    result = ok("sol_selector_check", {"functions": ["transfer(address,uint256)", "collate_propagate_storage(bytes16)", "burn(uint256)"]})
    assert result["verdict"] == "conflict" and result["function_count"] == 3
    assert [s["signature"] for s in result["selectors"]] == ["burn(uint256)", "collate_propagate_storage(bytes16)", "transfer(address,uint256)"]
    assert ids(result) == ["SEL-COLLISION"] and severity_of(result, "SEL-COLLISION") == "blocker"
    assert "0x42966c68" in result["findings"][0]["summary"]


def test_selector_check_duplicate_signature_is_minor_and_counted_once():
    result = ok("sol_selector_check", {"functions": ["burn(uint256)", "burn(uint256)"]})
    assert result["function_count"] == 1 and ids(result) == ["SEL-DUPLICATE"] and result["verdict"] == "clean"


def test_selector_check_proxy_clash_and_shadow():
    clash = ok("sol_selector_check", {"functions": ["burn(uint256)"], "other_functions": ["collate_propagate_storage(bytes16)"]})
    assert ids(clash) == ["SEL-PROXY-CLASH"] and severity_of(clash, "SEL-PROXY-CLASH") == "blocker" and clash["verdict"] == "conflict"
    shadow = ok("sol_selector_check", {"functions": ["upgradeTo(address)", "x()"], "other_functions": ["upgradeTo(address)"]})
    assert ids(shadow) == ["SEL-PROXY-SHADOW"] and severity_of(shadow, "SEL-PROXY-SHADOW") == "major" and shadow["verdict"] == "conflict"
    assert ids(ok("sol_selector_check", {"functions": ["a()"], "other_functions": ["b()"]})) == []


def test_selector_check_declared_constants():
    good = ok("sol_selector_check", {"functions": ["name()"], "expected_selectors": [{"signature": "transfer(address,uint256)", "selector": "0xA9059CBB"}]})
    assert good["findings"] == []
    bad = ok("sol_selector_check", {"functions": ["name()"], "expected_selectors": [{"signature": "transfer(address,uint256)", "selector": "0xa9059cbc"}]})
    assert ids(bad) == ["SEL-CONSTANT-MISMATCH"] and "0xa9059cbb" in bad["findings"][0]["summary"] and bad["verdict"] == "conflict"


@pytest.mark.parametrize("payload, field", [
    ({"functions": ["transfer(address, uint256)"]}, "functions"),
    ({"functions": ["foo(uint)"]}, "functions"),
    ({"functions": ["foo(uint7)"]}, "functions"),
    ({"functions": ["foo(bytes33)"]}, "functions"),
    ({"functions": ["foo(Token)"]}, "functions"),
    ({"functions": ["foo(uint256[0])"]}, "functions"),
    ({"functions": ["foo(,uint256)"]}, "functions"),
    ({"functions": ["foo()"], "events": ["Transfer(address, address)"]}, "events"),
    ({"functions": ["foo()"], "other_functions": ["bar"]}, "other_functions"),
    ({"functions": ["foo()"], "expected_selectors": [{"signature": "foo()", "selector": "0x1234"}]}, "expected_selectors"),
    ({"functions": ["foo()"], "expected_selectors": [{"signature": "foo()", "selector": "0xzzzzzzzz"}]}, "expected_selectors"),
    ({"functions": ["foo()"], "expected_selectors": [{"signature": "foo(uint)", "selector": "0x12345678"}]}, "expected_selectors"),
])
def test_selector_check_rejects_non_canonical_signatures_naming_the_field(payload, field):
    invalid("sol_selector_check", payload, field)


def test_selector_check_accepts_tuples_and_arrays():
    result = ok("sol_selector_check", {"functions": ["f((uint256,address[])[],bytes32[2])"]})
    assert result["function_count"] == 1 and result["selectors"][0]["selector"] == "0xf50194b4"  # keccak256 cross-checked with pycryptodome


# --- sol_abi_diff ----------------------------------------------------------------------------------------------


def fn(signature, mutability=None, returns=None):
    entry = {"kind": "function", "signature": signature}
    if mutability:
        entry["mutability"] = mutability
    if returns is not None:
        entry["returns"] = returns
    return entry


def ev(signature, indexed=None):
    return {"kind": "event", "signature": signature, **({"indexed": indexed} if indexed else {})}


def err(signature):
    return {"kind": "error", "signature": signature}


def test_abi_diff_identical_abis_are_compatible():
    abi = [fn("a()", "view", "uint256"), ev("E(uint256)", [True]), err("Bad()")]
    result = ok("sol_abi_diff", {"old_abi": abi, "new_abi": abi})
    assert (result["added"], result["removed"], result["changed"], result["breaking"]) == (0, 0, 0, 0)
    assert result["verdict"] == "compatible" and result["findings"] == []


def test_abi_diff_removed_function_is_a_blocker_and_added_is_info():
    result = ok("sol_abi_diff", {"old_abi": [fn("a()"), fn("b()")], "new_abi": [fn("a()"), fn("c()")]})
    assert (result["added"], result["removed"], result["changed"], result["breaking"]) == (1, 1, 0, 1)
    assert result["verdict"] == "breaking"
    assert severity_of(result, "FN-REMOVED") == "blocker" and severity_of(result, "FN-ADDED") == "info"
    assert ids(result) == ["FN-REMOVED", "FN-ADDED"]  # worst first


def test_abi_diff_same_name_new_parameters_is_a_selector_change_with_both_selectors():
    result = ok("sol_abi_diff", {"old_abi": [fn("b(uint256)")], "new_abi": [fn("b(uint256,address)")]})
    assert (result["added"], result["removed"], result["changed"]) == (0, 0, 1)
    assert ids(result) == ["FN-SELECTOR-CHANGED"] and severity_of(result, "FN-SELECTOR-CHANGED") == "blocker"
    assert "0xcd580ff3" in result["findings"][0]["summary"] and "0x3d488b7f" in result["findings"][0]["summary"]


@pytest.mark.parametrize("old, new, expected", [
    ("pure", "view", "minor"), ("pure", "nonpayable", "major"), ("pure", "payable", "major"),
    ("view", "pure", "minor"), ("view", "nonpayable", "major"), ("view", "payable", "major"),
    ("nonpayable", "pure", "minor"), ("nonpayable", "view", "minor"), ("nonpayable", "payable", "minor"),
    ("payable", "pure", "major"), ("payable", "view", "major"), ("payable", "nonpayable", "major"),
])
def test_abi_diff_mutability_matrix(old, new, expected):
    result = ok("sol_abi_diff", {"old_abi": [fn("f()", old)], "new_abi": [fn("f()", new)]})
    assert severity_of(result, "FN-MUTABILITY-CHANGED") == expected
    assert result["changed"] == 1 and result["verdict"] == ("breaking" if expected == "major" else "compatible")


def test_abi_diff_omitted_mutability_means_nonpayable():
    result = ok("sol_abi_diff", {"old_abi": [fn("f()")], "new_abi": [fn("f()", "view")]})
    assert severity_of(result, "FN-MUTABILITY-CHANGED") == "minor"


def test_abi_diff_return_types_compared_only_when_both_sides_state_them():
    changed = ok("sol_abi_diff", {"old_abi": [fn("a()", "view", "uint256")], "new_abi": [fn("a()", "view", "uint256,bool")]})
    assert severity_of(changed, "FN-RETURNS-CHANGED") == "major" and changed["verdict"] == "breaking"
    assert ok("sol_abi_diff", {"old_abi": [fn("a()", "view", "uint256")], "new_abi": [fn("a()", "view")]})["findings"] == []
    assert ok("sol_abi_diff", {"old_abi": [fn("a()", "view", "")], "new_abi": [fn("a()", "view", "")]})["findings"] == []


def test_abi_diff_events_and_errors():
    result = ok("sol_abi_diff", {
        "old_abi": [ev("Gone(uint256)"), ev("Moved(address)"), ev("Same(address,uint256)", [True, False]), err("OldErr()"), err("Sig(uint256)")],
        "new_abi": [ev("Moved(address,uint256)"), ev("Same(address,uint256)", [False, False]), err("Sig(uint256,uint256)"), ev("Fresh()")],
    })
    assert severity_of(result, "EVENT-REMOVED") == "major"
    assert severity_of(result, "EVENT-SIGNATURE-CHANGED") == "major"
    assert severity_of(result, "EVENT-INDEXED-CHANGED") == "major"
    assert severity_of(result, "ERROR-REMOVED") == "minor"
    assert severity_of(result, "ERROR-SIGNATURE-CHANGED") == "minor"
    assert severity_of(result, "EVENT-ADDED") == "info"
    assert (result["added"], result["removed"], result["changed"], result["breaking"]) == (1, 2, 3, 3)


def test_abi_diff_indexed_flags_are_ignored_when_a_side_omits_them():
    result = ok("sol_abi_diff", {"old_abi": [ev("E(address)", [True])], "new_abi": [ev("E(address)")]})
    assert result["findings"] == []


@pytest.mark.parametrize("payload, field", [
    ({"old_abi": [fn("a(uint)")], "new_abi": []}, "old_abi"),
    ({"old_abi": [], "new_abi": [fn("a b()")]}, "new_abi"),
    ({"old_abi": [fn("a()"), fn("a()")], "new_abi": []}, "old_abi"),
    ({"old_abi": [], "new_abi": [fn("a()", "view", "uint")]}, "new_abi"),
    ({"old_abi": [ev("E(address,uint256)", [True])], "new_abi": []}, "old_abi"),
    ({"old_abi": [], "new_abi": [{"kind": "function", "signature": "f(address)", "indexed": [True]}]}, "new_abi"),
])
def test_abi_diff_rejects_malformed_entries_naming_the_list(payload, field):
    invalid("sol_abi_diff", payload, field)


# --- sol_storage_layout ----------------------------------------------------------------------------------------


def var(label, slot, offset, type_, size, **extra):
    return {"label": label, "slot": slot, "offset": offset, "type": type_, "bytes": size, **extra}


OWNER = var("owner", 0, 0, "t_address", 20)
FLAG = var("flag", 0, 20, "t_bool", 1)
TOTAL = var("total", 1, 0, "t_uint256", 32)


def layout(old, new):
    return ok("sol_storage_layout", {"old_layout": old, "new_layout": new})


def test_storage_layout_identical_layout_is_compatible():
    result = layout([OWNER, FLAG, TOTAL], [OWNER, FLAG, TOTAL])
    assert (result["old_variables"], result["new_variables"], result["appended"], result["breaking"]) == (3, 3, 0, 0)
    assert result["verdict"] == "compatible" and result["findings"] == []


def test_storage_layout_append_after_old_end_including_packing_into_the_same_slot():
    packed = layout([OWNER], [OWNER, FLAG])  # flag starts exactly where owner ends (slot 0, offset 20)
    assert packed["appended"] == 1 and packed["verdict"] == "compatible" and ids(packed) == ["STORAGE-APPENDED"]
    later = layout([OWNER, FLAG], [OWNER, FLAG, TOTAL])
    assert later["appended"] == 1 and severity_of(later, "STORAGE-APPENDED") == "info"


def test_storage_layout_moved_variable_is_a_blocker():
    result = layout([OWNER, TOTAL], [OWNER, var("total", 2, 0, "t_uint256", 32)])
    assert severity_of(result, "STORAGE-MOVED") == "blocker" and result["verdict"] == "incompatible"
    assert "slot 1 offset 0 to slot 2 offset 0" in result["findings"][0]["summary"]
    assert ids(layout([OWNER, FLAG], [OWNER, var("flag", 0, 24, "t_bool", 1)])) == ["STORAGE-MOVED"]  # offset-only move


def test_storage_layout_type_change_same_size_is_major_different_size_blocker():
    same = layout([TOTAL], [var("total", 1, 0, "t_int256", 32)])
    assert severity_of(same, "STORAGE-TYPE-CHANGED") == "major" and same["verdict"] == "incompatible"
    smaller = layout([TOTAL], [var("total", 1, 0, "t_uint128", 16)])
    assert severity_of(smaller, "STORAGE-TYPE-CHANGED") == "blocker"


def test_storage_layout_rename_is_minor_and_replacement_is_a_blocker():
    renamed = layout([OWNER], [var("admin", 0, 0, "t_address", 20)])
    assert ids(renamed) == ["STORAGE-RENAMED"] and severity_of(renamed, "STORAGE-RENAMED") == "minor" and renamed["verdict"] == "compatible"
    replaced = layout([OWNER], [var("count", 0, 0, "t_uint160", 20)])
    assert ids(replaced) == ["STORAGE-REPLACED"] and replaced["verdict"] == "incompatible"


def test_storage_layout_removal_collision_and_free_space_insertion():
    removed = layout([OWNER, TOTAL], [OWNER])
    assert ids(removed) == ["STORAGE-REMOVED"] and severity_of(removed, "STORAGE-REMOVED") == "major" and removed["verdict"] == "incompatible"
    collided = layout([OWNER, var("a", 1, 0, "t_uint256", 32), var("b", 2, 0, "t_uint256", 32)],
                      [OWNER, var("b", 2, 0, "t_uint256", 32), var("c", 1, 0, "t_uint128", 16)])
    assert ids(collided) == ["STORAGE-REPLACED"]  # a different type took over the exact old position
    overlapped = layout([OWNER, var("a", 1, 0, "t_uint256", 32)], [OWNER, var("c", 1, 16, "t_uint128", 16)])
    assert ids(overlapped) == ["STORAGE-COLLISION"] and "a at slot 1 offset 0 was dropped and c now occupies" in overlapped["findings"][0]["summary"]
    hole = layout([OWNER, var("c", 2, 0, "t_uint256", 32)], [OWNER, var("b", 1, 0, "t_uint256", 32), var("c", 2, 0, "t_uint256", 32)])
    assert ids(hole) == ["STORAGE-FILLED-FREE-SPACE"] and severity_of(hole, "STORAGE-FILLED-FREE-SPACE") == "minor" and hole["verdict"] == "compatible"


def test_storage_layout_insertion_before_existing_variable_shifts_it():
    result = layout([OWNER, TOTAL], [OWNER, var("fresh", 1, 0, "t_uint256", 32), var("total", 2, 0, "t_uint256", 32)])
    assert set(ids(result)) == {"STORAGE-COLLISION", "STORAGE-MOVED"} and result["verdict"] == "incompatible"


GAP50 = var("__gap", 3, 0, "t_array(t_uint256)50_storage", 1600)


def test_storage_layout_gap_consumption_keeps_the_end_and_is_safe():
    old = [OWNER, TOTAL, var("map", 2, 0, "t_mapping(t_address,t_uint256)", 32), GAP50]
    new = [OWNER, TOTAL, var("map", 2, 0, "t_mapping(t_address,t_uint256)", 32), var("extra", 3, 0, "t_uint256", 32),
           var("__gap", 4, 0, "t_array(t_uint256)49_storage", 1568)]
    result = layout(old, new)
    assert ids(result) == ["STORAGE-GAP-CONSUMED"] and result["verdict"] == "compatible" and result["appended"] == 0


def test_storage_layout_gap_resizes():
    base = [OWNER, GAP50.copy() | {"slot": 1}]
    grew = layout(base, [OWNER, var("__gap", 1, 0, "t_array(t_uint256)60_storage", 1920)])
    assert severity_of(grew, "STORAGE-GAP-GREW") == "blocker"
    shrunk = layout(base, [OWNER, var("__gap", 1, 0, "t_array(t_uint256)40_storage", 1280)])
    assert ids(shrunk) == ["STORAGE-GAP-SHRUNK"] and severity_of(shrunk, "STORAGE-GAP-SHRUNK") == "minor"
    gone = layout(base, [OWNER])
    assert ids(gone) == ["STORAGE-GAP-REMOVED"] and severity_of(gone, "STORAGE-GAP-REMOVED") == "major"
    after = layout(base, base + [var("late", 51, 0, "t_uint256", 32)])
    assert ids(after) == ["STORAGE-APPENDED-AFTER-GAP"] and severity_of(after, "STORAGE-APPENDED-AFTER-GAP") == "major"


def test_storage_layout_ignores_ast_ids_but_not_array_lengths():
    old = [var("cfg", 0, 0, "t_struct(Config)12_storage", 64), var("c", 2, 0, "t_contract(IERC20)45", 20)]
    new = [var("cfg", 0, 0, "t_struct(Config)931_storage", 64), var("c", 2, 0, "t_contract(IERC20)88", 20)]
    assert layout(old, new)["findings"] == []
    arrays = layout([var("a", 0, 0, "t_array(t_uint256)3_storage", 96)], [var("a", 0, 0, "t_array(t_uint256)2_storage", 64)])
    assert severity_of(arrays, "STORAGE-TYPE-CHANGED") == "blocker"


def test_storage_layout_same_type_with_changed_size_is_a_blocker():
    # a struct member added to Config keeps the type string but changes the bytes every later element is laid out by
    grown = layout([var("cfg", 0, 0, "t_struct(Config)12_storage", 64)], [var("cfg", 0, 0, "t_struct(Config)931_storage", 96)])
    assert ids(grown) == ["STORAGE-TYPE-CHANGED"] and severity_of(grown, "STORAGE-TYPE-CHANGED") == "blocker"
    assert grown["verdict"] == "incompatible" and "changed size (64 -> 96 bytes)" in grown["findings"][0]["summary"]
    renamed = layout([var("cfg", 0, 0, "t_struct(Config)12_storage", 64)], [var("settings", 0, 0, "t_struct(Config)12_storage", 96)])
    assert ids(renamed) == ["STORAGE-REPLACED"] and severity_of(renamed, "STORAGE-REPLACED") == "blocker"
    assert layout([OWNER, FLAG], [OWNER, FLAG])["findings"] == []
    assert layout([OWNER], [OWNER, var("late", 1, 0, "t_uint256", 32)])["verdict"] == "compatible"


def test_storage_layout_shadowed_labels_are_told_apart_by_contract():
    old = [var("x", 0, 0, "t_uint256", 32, contract="Base"), var("x", 1, 0, "t_uint256", 32, contract="Child")]
    assert layout(old, old)["findings"] == []
    swapped = [var("x", 0, 0, "t_uint256", 32, contract="Child"), var("x", 1, 0, "t_uint256", 32, contract="Base")]
    assert ids(layout(old, swapped)).count("STORAGE-MOVED") == 2


@pytest.mark.parametrize("payload, field", [
    ({"old_layout": [OWNER, var("b", 0, 10, "t_uint256", 32)], "new_layout": [OWNER]}, "old_layout"),
    ({"old_layout": [OWNER], "new_layout": [OWNER, OWNER]}, "new_layout"),
    ({"old_layout": [var("a", 0, 20, "t_uint256", 16)], "new_layout": [OWNER]}, "old_layout"),
    ({"old_layout": [OWNER], "new_layout": [var("a", 0, 4, "t_array(t_uint256)2_storage", 64)]}, "new_layout"),
    ({"old_layout": [OWNER], "new_layout": [var("a", 0, 0, "t_bytes_storage", 40)]}, "new_layout"),
    ({"old_layout": [var("a", 0, 0, "t_uint256", 32), var("b", 0, 31, "t_bool", 1)], "new_layout": [OWNER]}, "old_layout"),
])
def test_storage_layout_rejects_impossible_layouts_naming_the_field(payload, field):
    invalid("sol_storage_layout", payload, field)


# --- sol_erc_conformance ---------------------------------------------------------------------------------------

ERC20_FNS = [("totalSupply()", "view", "uint256"), ("balanceOf(address)", "view", "uint256"), ("transfer(address,uint256)", "nonpayable", "bool"),
             ("transferFrom(address,address,uint256)", "nonpayable", "bool"), ("approve(address,uint256)", "nonpayable", "bool"),
             ("allowance(address,address)", "view", "uint256")]
ERC20_EVENTS = [("Transfer(address,address,uint256)", [True, True, False]), ("Approval(address,address,uint256)", [True, True, False])]
ERC721_FNS = [("balanceOf(address)", "view", "uint256"), ("ownerOf(uint256)", "view", "address"),
              ("safeTransferFrom(address,address,uint256,bytes)", "nonpayable", ""), ("safeTransferFrom(address,address,uint256)", "nonpayable", ""),
              ("transferFrom(address,address,uint256)", "nonpayable", ""), ("approve(address,uint256)", "nonpayable", ""),
              ("setApprovalForAll(address,bool)", "nonpayable", ""), ("getApproved(uint256)", "view", "address"),
              ("isApprovedForAll(address,address)", "view", "bool"), ("supportsInterface(bytes4)", "view", "bool")]
ERC721_EVENTS = [("Transfer(address,address,uint256)", [True, True, True]), ("Approval(address,address,uint256)", [True, True, True]),
                 ("ApprovalForAll(address,address,bool)", [True, True, False])]
ERC1155_FNS = [("safeTransferFrom(address,address,uint256,uint256,bytes)", "nonpayable", ""),
               ("safeBatchTransferFrom(address,address,uint256[],uint256[],bytes)", "nonpayable", ""), ("balanceOf(address,uint256)", "view", "uint256"),
               ("balanceOfBatch(address[],uint256[])", "view", "uint256[]"), ("setApprovalForAll(address,bool)", "nonpayable", ""),
               ("isApprovedForAll(address,address)", "view", "bool"), ("supportsInterface(bytes4)", "view", "bool")]
ERC1155_EVENTS = [("TransferSingle(address,address,address,uint256,uint256)", [True, True, True, False, False]),
                  ("TransferBatch(address,address,address,uint256[],uint256[])", [True, True, True, False, False]),
                  ("ApprovalForAll(address,address,bool)", [True, True, False])]
ERC4626_OWN = [("asset()", "view", "address"), ("totalAssets()", "view", "uint256"), ("convertToShares(uint256)", "view", "uint256"),
               ("convertToAssets(uint256)", "view", "uint256"), ("maxDeposit(address)", "view", "uint256"), ("previewDeposit(uint256)", "view", "uint256"),
               ("deposit(uint256,address)", "nonpayable", "uint256"), ("maxMint(address)", "view", "uint256"), ("previewMint(uint256)", "view", "uint256"),
               ("mint(uint256,address)", "nonpayable", "uint256"), ("maxWithdraw(address)", "view", "uint256"), ("previewWithdraw(uint256)", "view", "uint256"),
               ("withdraw(uint256,address,address)", "nonpayable", "uint256"), ("maxRedeem(address)", "view", "uint256"),
               ("previewRedeem(uint256)", "view", "uint256"), ("redeem(uint256,address,address)", "nonpayable", "uint256")]
ERC4626_EVENTS = ERC20_EVENTS + [("Deposit(address,address,uint256,uint256)", [True, True, False, False]),
                                 ("Withdraw(address,address,address,uint256,uint256)", [True, True, True, False, False])]
# EIP-4626: "All EIP-4626 tokenized Vaults MUST implement EIP-20's optional metadata extensions" (name, symbol, decimals)
ERC20_METADATA = [("name()", "view", "string"), ("symbol()", "view", "string"), ("decimals()", "view", "uint8")]
ERC2612_FNS = [("permit(address,address,uint256,uint256,uint8,bytes32,bytes32)", "nonpayable", ""), ("nonces(address)", "view", "uint256"),
               ("DOMAIN_SEPARATOR()", "view", "bytes32")]
STANDARDS = {
    "erc20": (ERC20_FNS, ERC20_EVENTS, "0x36372b07"),
    "erc721": (ERC721_FNS, ERC721_EVENTS, "0x80ac58cd"),
    "erc1155": (ERC1155_FNS, ERC1155_EVENTS, "0xd9b67a26"),
    "erc4626": (ERC20_FNS + ERC20_METADATA + ERC4626_OWN, ERC4626_EVENTS, "0x87dfe5a0"),
    "erc2612": (ERC2612_FNS, [], "0x9d8ff7da"),
}


def abi_of(fns, events):
    return [fn(s, m, r) for s, m, r in fns] + [ev(s, i) for s, i in events]


def erc(standard, abi, **extra):
    return ok("sol_erc_conformance", {"standard": standard, "abi": abi, **extra})


@pytest.mark.parametrize("standard", sorted(STANDARDS))
def test_erc_conformant_abi_passes_and_reports_the_interface_id(standard):
    fns, events, interface_id = STANDARDS[standard]
    result = erc(standard, abi_of(fns, events))
    assert result["verdict"] == "conformant" and result["missing"] == 0 and result["standard"] == standard
    assert result["interface_id"] == interface_id
    assert not [f for f in result["findings"] if SEVERITY_ORDER[f["severity"]] >= 2]


SEVERITY_ORDER = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


@pytest.mark.parametrize("standard, required_functions, required_events", [
    ("erc20", 6, 2), ("erc721", 10, 3), ("erc1155", 7, 3), ("erc4626", 25, 4), ("erc2612", 3, 0)])
def test_erc_required_counts_and_an_empty_abi_misses_everything(standard, required_functions, required_events):
    result = erc(standard, [])
    assert (result["required_functions"], result["required_events"]) == (required_functions, required_events)
    assert result["missing"] == required_functions + required_events and result["verdict"] == "non_conformant"
    assert ids(result).count("ERC-MISSING-FN") == required_functions and ids(result).count("ERC-MISSING-EVENT") == required_events


@pytest.mark.parametrize("standard", sorted(STANDARDS))
def test_erc_each_required_function_and_event_is_enforced(standard):
    fns, events, _ = STANDARDS[standard]
    for i, (signature, _, _) in enumerate(fns):
        result = erc(standard, abi_of(fns[:i] + fns[i + 1:], events))
        assert result["missing"] == 1 and has(result, "ERC-MISSING-FN", signature), signature
    for i, (signature, _) in enumerate(events):
        result = erc(standard, abi_of(fns, events[:i] + events[i + 1:]))
        assert result["missing"] == 1 and has(result, "ERC-MISSING-EVENT", signature), signature


def test_erc_mutability_rules_per_class():
    # view functions may be pure; a nonpayable view function is wrong
    pure_ok = [fn(s, "pure" if s == "totalSupply()" else m, r) for s, m, r in ERC20_FNS] + [ev(s, i) for s, i in ERC20_EVENTS]
    assert erc("erc20", pure_ok)["verdict"] == "conformant"
    wrong_view = [fn(s, "nonpayable" if s == "balanceOf(address)" else m, r) for s, m, r in ERC20_FNS] + [ev(s, i) for s, i in ERC20_EVENTS]
    result = erc("erc20", wrong_view)
    assert severity_of(result, "ERC-MUTABILITY") == "major" and "balanceOf(address)" in result["findings"][0]["summary"]
    # ERC-20 transfer must not be payable, ERC-721 transfers may be (the EIP declares them payable)
    payable20 = [fn(s, "payable" if s == "transfer(address,uint256)" else m, r) for s, m, r in ERC20_FNS] + [ev(s, i) for s, i in ERC20_EVENTS]
    assert has(erc("erc20", payable20), "ERC-MUTABILITY", "transfer(address,uint256)")
    payable721 = [fn(s, "payable" if s == "transferFrom(address,address,uint256)" else m, r) for s, m, r in ERC721_FNS] + [ev(s, i) for s, i in ERC721_EVENTS]
    assert erc("erc721", payable721)["verdict"] == "conformant"
    view_for_write = [fn(s, "view" if s == "approve(address,uint256)" else m, r) for s, m, r in ERC721_FNS] + [ev(s, i) for s, i in ERC721_EVENTS]
    assert has(erc("erc721", view_for_write), "ERC-MUTABILITY", "approve(address,uint256)")


def test_erc_return_types_are_compared_when_given_and_skipped_when_omitted():
    no_bool = [fn(s, m, "" if s == "transfer(address,uint256)" else r) for s, m, r in ERC20_FNS] + [ev(s, i) for s, i in ERC20_EVENTS]
    result = erc("erc20", no_bool)
    assert severity_of(result, "ERC-RETURNS") == "major" and "returns ()" in result["findings"][0]["summary"] and result["verdict"] == "non_conformant"
    omitted = [fn(s, m) for s, m, _ in ERC20_FNS] + [ev(s, i) for s, i in ERC20_EVENTS]
    assert erc("erc20", omitted)["verdict"] == "conformant"


def test_erc_event_indexing_distinguishes_erc20_and_erc721_transfer():
    # ERC-20 Transfer indexes 2 parameters, ERC-721 Transfer indexes 3: same topic0, different layout
    as_721 = abi_of(ERC721_FNS, [("Transfer(address,address,uint256)", [True, True, False])] + ERC721_EVENTS[1:])
    result = erc("erc721", as_721)
    assert severity_of(result, "ERC-EVENT-INDEXED") == "major" and result["verdict"] == "non_conformant"
    assert "[1, 2, 3]" in result["findings"][0]["summary"] and "[1, 2]" in result["findings"][0]["summary"]
    as_20 = abi_of(ERC20_FNS, [("Transfer(address,address,uint256)", [True, True, True])] + ERC20_EVENTS[1:])
    assert has(erc("erc20", as_20), "ERC-EVENT-INDEXED", "Transfer(address,address,uint256)")
    no_flags = [fn(s, m, r) for s, m, r in ERC20_FNS] + [ev(s) for s, _ in ERC20_EVENTS]
    assert erc("erc20", no_flags)["verdict"] == "conformant"


def test_erc_optional_members_are_info_only():
    result = erc("erc20", abi_of(ERC20_FNS, ERC20_EVENTS))
    assert sorted(f["summary"] for f in result["findings"] if f["id"] == "ERC-OPTIONAL-FN-MISSING") == [
        "optional function decimals() is not implemented (wallets and explorers read it)",
        "optional function name() is not implemented (wallets and explorers read it)",
        "optional function symbol() is not implemented (wallets and explorers read it)"]
    assert {f["severity"] for f in result["findings"]} == {"info"}
    uri = erc("erc1155", abi_of(ERC1155_FNS, ERC1155_EVENTS))
    assert has(uri, "ERC-OPTIONAL-EVENT-MISSING", "URI(string,uint256)") and uri["verdict"] == "conformant"
    with_uri = erc("erc1155", abi_of(ERC1155_FNS, ERC1155_EVENTS + [("URI(string,uint256)", [False, True])]))
    assert "ERC-OPTIONAL-EVENT-MISSING" not in ids(with_uri)
    bad_uri = erc("erc1155", abi_of(ERC1155_FNS, ERC1155_EVENTS + [("URI(string,uint256)", [True, True])]))
    assert has(bad_uri, "ERC-EVENT-INDEXED", "URI(string,uint256)")


def test_erc165_ids_for_721_and_1155():
    abi721 = abi_of(ERC721_FNS, ERC721_EVENTS)
    unchecked = erc("erc721", abi721)
    assert severity_of(unchecked, "ERC165-UNCHECKED") == "info" and unchecked["verdict"] == "conformant"
    good = erc("erc721", abi721, erc165_ids=["0x01ffc9a7", "0x80AC58CD"])
    assert "ERC165-UNCHECKED" not in ids(good) and good["verdict"] == "conformant"
    missing = erc("erc721", abi721, erc165_ids=["0x01ffc9a7"])
    assert has(missing, "ERC165-ID-MISSING", "0x80ac58cd") and missing["verdict"] == "non_conformant"
    no165 = erc("erc1155", abi_of(ERC1155_FNS, ERC1155_EVENTS), erc165_ids=["0xd9b67a26"])
    assert has(no165, "ERC165-ID-MISSING", "0x01ffc9a7")
    invalid_id = erc("erc1155", abi_of(ERC1155_FNS, ERC1155_EVENTS), erc165_ids=["0x01ffc9a7", "0xd9b67a26", "0xffffffff"])
    assert severity_of(invalid_id, "ERC165-INVALID-ID") == "blocker"
    # standards without a normative ERC-165 id never complain about ids
    assert "ERC165-UNCHECKED" not in ids(erc("erc20", abi_of(ERC20_FNS, ERC20_EVENTS)))


@pytest.mark.parametrize("claimed, functions", [
    ("0x5b5e139f", [("name()", "string"), ("symbol()", "string"), ("tokenURI(uint256)", "string")]),
    ("0x780e9d63", [("totalSupply()", "uint256"), ("tokenOfOwnerByIndex(address,uint256)", "uint256"), ("tokenByIndex(uint256)", "uint256")]),
    ("0x0e89341c", [("uri(uint256)", "string")]),
])
def test_erc165_extension_ids_promise_their_functions(claimed, functions):
    standard = "erc1155" if claimed == "0x0e89341c" else "erc721"
    base = abi_of(*(STANDARDS[standard][:2]))
    ids_claimed = ["0x01ffc9a7", STANDARDS[standard][2], claimed]
    # the XOR of the promised selectors is the id itself
    selectors = ok("sol_selector_check", {"functions": [s for s, _ in functions]})
    assert selectors["interface_id"] == claimed
    incomplete = erc(standard, base, erc165_ids=ids_claimed)
    assert ids(incomplete).count("ERC165-EXTENSION-INCOMPLETE") == len(functions)
    complete = erc(standard, base + [fn(s, "view", r) for s, r in functions], erc165_ids=ids_claimed)
    assert "ERC165-EXTENSION-INCOMPLETE" not in ids(complete)
    wrong_return = erc(standard, base + [fn(s, "view", "bool") for s, _ in functions], erc165_ids=ids_claimed)
    assert ids(wrong_return).count("ERC165-EXTENSION-INCOMPLETE") == len(functions)


@pytest.mark.parametrize("payload, field", [
    ({"standard": "erc20", "abi": [fn("transfer(uint,uint)")]}, "abi"),
    ({"standard": "erc20", "abi": [fn("a()"), fn("a()")]}, "abi"),
    ({"standard": "erc20", "abi": [], "erc165_ids": ["0x1234"]}, "erc165_ids"),
    ({"standard": "erc20", "abi": [], "erc165_ids": ["0xgggggggg"]}, "erc165_ids"),
])
def test_erc_rejects_malformed_input_naming_the_field(payload, field):
    invalid("sol_erc_conformance", payload, field)


# --- sol_upgrade_safety ----------------------------------------------------------------------------------------

CLEAN_UUPS = """
// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

contract TokenV1 is Initializable, OwnableUpgradeable, UUPSUpgradeable {
    uint256 private _supply;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address owner_) public initializer {
        __Ownable_init(owner_);
        __UUPSUpgradeable_init();
    }

    function _authorizeUpgrade(address) internal override onlyOwner {}
}
"""


def upgrade(source, pattern="uups", **extra):
    return ok("sol_upgrade_safety", {"source": source, "pattern": pattern, **extra})


def test_upgrade_safety_clean_uups_implementation_is_safe():
    result = upgrade(CLEAN_UUPS)
    assert result["verdict"] == "safe" and result["findings"] == [] and result["contracts_checked"] == 1 and result["pattern"] == "uups"


def test_upgrade_safety_constructor_state_and_missing_disable_initializers():
    source = "contract A is Initializable { uint256 x; constructor() { x = 1; } function initialize() public initializer {} }"
    result = upgrade(source, "transparent")
    assert severity_of(result, "UPG-CONSTRUCTOR-STATE") == "major" and severity_of(result, "UPG-NO-DISABLE-INITIALIZERS") == "minor"
    assert severity_of(upgrade(source, "uups"), "UPG-NO-DISABLE-INITIALIZERS") == "major"
    # immutables assigned in the constructor are not "constructor state" (they are reported as immutables)
    immutable = upgrade("contract A { address immutable weth; constructor(address w) { weth = w; _disableInitializers(); } function initialize() public initializer {} }", "transparent")
    assert "UPG-CONSTRUCTOR-STATE" not in ids(immutable) and severity_of(immutable, "UPG-IMMUTABLE") == "major"


def test_upgrade_safety_immutable_annotation_downgrades_to_info():
    source = ("contract A {\n  /// @custom:oz-upgrades-unsafe-allow state-variable-immutable\n  address immutable weth;\n"
              "  constructor(address w) { weth = w; _disableInitializers(); }\n  function initialize() public initializer {}\n}")
    result = upgrade(source, "transparent")
    assert severity_of(result, "UPG-IMMUTABLE") == "info" and result["verdict"] == "safe"


def test_upgrade_safety_initializer_guards():
    open_init = "contract A { uint256 x; constructor() { _disableInitializers(); } function initialize(uint256 v) public { x = v; } }"
    result = upgrade(open_init, "transparent")
    assert severity_of(result, "UPG-INIT-UNGUARDED") == "blocker" and result["verdict"] == "unsafe"
    owner_only = "contract A { uint256 x; constructor() { _disableInitializers(); } function initialize(uint256 v) public onlyOwner { x = v; } }"
    assert "UPG-INIT-UNGUARDED" not in ids(upgrade(owner_only, "transparent"))
    helper = "contract A is Initializable { constructor() { _disableInitializers(); } function __A_init() internal { } function initialize() public initializer { __A_init(); } }"
    assert severity_of(upgrade(helper, "transparent"), "UPG-INIT-HELPER-UNGUARDED") == "minor"
    guarded = helper.replace("internal { }", "internal onlyInitializing { }")
    assert "UPG-INIT-HELPER-UNGUARDED" not in ids(upgrade(guarded, "transparent"))


def test_upgrade_safety_no_initializer_needs_state_or_upgradeable_base():
    stateful = "contract A { uint256 x; constructor() { _disableInitializers(); } }"
    assert severity_of(upgrade(stateful, "transparent"), "UPG-NO-INITIALIZER") == "minor"
    stateless = "contract A { constructor() { _disableInitializers(); } function f() external pure returns (uint256) { return 1; } }"
    assert "UPG-NO-INITIALIZER" not in ids(upgrade(stateless, "transparent"))
    based = "contract A is OwnableUpgradeable { constructor() { _disableInitializers(); } }"
    assert "UPG-NO-INITIALIZER" in ids(upgrade(based, "transparent"))


def test_upgrade_safety_selfdestruct_delegatecall_and_inline_initializers():
    source = """contract A {
        uint256 public total = 5;
        uint256 constant LIMIT = 10;
        constructor() { _disableInitializers(); }
        function initialize() public initializer {}
        function kill() external onlyOwner { selfdestruct(payable(msg.sender)); }
        function run(address t, bytes calldata d) external onlyOwner { t.delegatecall(d); }
        function lib(address t) external onlyOwner { Address.functionDelegateCall(t, ""); }
    }"""
    result = upgrade(source, "transparent")
    assert severity_of(result, "UPG-SELFDESTRUCT") == "blocker" and has(result, "UPG-SELFDESTRUCT", "A.kill()")
    assert ids(result).count("UPG-DELEGATECALL") == 2 and severity_of(result, "UPG-DELEGATECALL") == "major"
    inline = [f for f in result["findings"] if f["id"] == "UPG-INLINE-INIT"]
    assert len(inline) == 1 and "A.total" in inline[0]["summary"]  # constants are fine
    assert result["verdict"] == "unsafe"


def test_upgrade_safety_storage_gap_rules_only_for_abstract_bases():
    abstract = "abstract contract Base is Initializable { uint256 a; }"
    assert severity_of(upgrade(abstract, "transparent"), "UPG-NO-GAP") == "minor"
    with_gap = "abstract contract Base is Initializable { uint256 a; uint256[49] private __gap; }"
    assert "UPG-NO-GAP" not in ids(upgrade(with_gap, "transparent"))
    bad_type = "abstract contract Base is Initializable { uint256 a; uint256 __gap; }"
    assert "UPG-GAP-TYPE" in ids(upgrade(bad_type, "transparent"))
    concrete = "contract C is Initializable { uint256 a; constructor() { _disableInitializers(); } function initialize() public initializer {} }"
    assert "UPG-NO-GAP" not in ids(upgrade(concrete, "transparent"))


def test_upgrade_safety_uups_authorization():
    unprotected = CLEAN_UUPS.replace("internal override onlyOwner {}", "internal override {}")
    result = upgrade(unprotected)
    assert severity_of(result, "UPG-UUPS-UNPROTECTED") == "blocker" and result["verdict"] == "unsafe"
    inline = CLEAN_UUPS.replace("internal override onlyOwner {}", 'internal override { require(msg.sender == admin, "no"); }')
    assert "UPG-UUPS-UNPROTECTED" not in ids(upgrade(inline))
    role = CLEAN_UUPS.replace("onlyOwner {}", "onlyRole(UPGRADER_ROLE) {}")
    assert upgrade(role)["findings"] == []


def test_upgrade_safety_uups_inheritance():
    no_uups = "contract A { uint256 x; constructor() { _disableInitializers(); } function initialize() public initializer {} }"
    result = upgrade(no_uups)
    assert severity_of(result, "UPG-UUPS-NOT-INHERITED") == "major" and result["verdict"] == "unsafe"
    own_upgrade = no_uups.replace("function initialize()", "function upgradeToAndCall(address, bytes memory) external { } function initialize()")
    assert "UPG-UUPS-NOT-INHERITED" not in ids(upgrade(own_upgrade))
    unresolved = "contract A is Initializable, SomethingElse { constructor() { _disableInitializers(); } function initialize() public initializer {} }"
    assert severity_of(upgrade(unresolved), "UPG-UUPS-INFO") == "info"
    via_local_base = "abstract contract Base is UUPSUpgradeable { } contract A is Base { constructor() { _disableInitializers(); } function initialize() public initializer {} function _authorizeUpgrade(address) internal override onlyOwner {} }"
    inherited = upgrade(via_local_base)
    assert "UPG-UUPS-NOT-INHERITED" not in ids(inherited) and "UPG-UUPS-INFO" not in ids(inherited)
    wrong_pattern = upgrade(CLEAN_UUPS, "transparent")
    assert severity_of(wrong_pattern, "UPG-UUPS-IN-NON-UUPS") == "minor" and wrong_pattern["verdict"] == "safe"


def test_upgrade_safety_focus_contract_and_errors():
    two = CLEAN_UUPS + "\ncontract Other { uint256 x = 1; }"
    assert upgrade(two)["contracts_checked"] == 2
    focused = upgrade(two, contract="TokenV1")
    assert focused["contracts_checked"] == 1 and focused["findings"] == []
    invalid("sol_upgrade_safety", {"source": two, "pattern": "uups", "contract": "Nope"}, "contract")
    invalid("sol_upgrade_safety", {"source": "interface I { function f() external; } library L { }", "pattern": "uups"}, "source")
    invalid("sol_upgrade_safety", {"source": "pragma solidity 0.8.0;", "pattern": "uups"}, "source")


def test_upgrade_safety_ignores_comments_and_strings():
    source = '''contract A is Initializable {
        // selfdestruct(payable(msg.sender));
        /* t.delegatecall(d); */
        string constant S = "selfdestruct(x)";
        constructor() { _disableInitializers(); }
        function initialize() public initializer {}
    }'''
    assert upgrade(source, "transparent")["findings"] == []


# --- sol_access_matrix -----------------------------------------------------------------------------------------

ACCESS_SRC = """
contract Vault {
    address public owner;
    mapping(address => bool) public isOperator;
    uint256 public total;

    function deposit(uint256 amount) external { total += amount; }
    function mint(address to, uint256 amount) external { total += amount; }
    function setFee(uint256 fee) external { total = fee; }
    function poke() external { total += 1; }
    function sweep() external onlyOwner { total = 0; }
    function grant(address a) external onlyRole(DEFAULT_ADMIN_ROLE) { isOperator[a] = true; }
    function rebalance() external { require(msg.sender == owner, "no"); total = 0; }
    function checked() external { _checkOwner(); total = 0; }
    function viaRole() external { _checkRole(KEEPER_ROLE); total = 0; }
    function viaHas() external { require(hasRole(PAUSER_ROLE, msg.sender)); total = 0; }
    function viaMap() external { require(isOperator[msg.sender], "no"); total = 0; }
    function viaHelper() external { _authorized(); }
    function _authorized() internal onlyOwner { total = 0; }
    function viaOrigin() external { require(tx.origin == owner); total = 0; }
    function guarded() external nonReentrant whenNotPaused { total = 0; }
    function initialize() external initializer { owner = msg.sender; }
    function show() external view returns (uint256) { return total; }
    receive() external payable {}
}
"""


def test_access_matrix_roles_and_unprotected_writers():
    result = ok("sol_access_matrix", {"source": ACCESS_SRC})
    roles = {r["role"]: r["functions"] for r in result["roles"]}
    assert roles == {
        "DEFAULT_ADMIN_ROLE": ["Vault.grant(address)"], "KEEPER_ROLE": ["Vault.viaRole()"], "PAUSER_ROLE": ["Vault.viaHas()"],
        "isOperator[msg.sender]": ["Vault.viaMap()"], "msg.sender==owner": ["Vault.rebalance()"],
        "onlyOwner": ["Vault.sweep()", "Vault.viaHelper()"], "owner": ["Vault.checked()"],
    }
    assert [r["role"] for r in result["roles"]] == sorted(roles)  # sorted
    unprotected = {f["summary"].split()[0]: f["id"] for f in result["findings"]}
    assert unprotected == {
        "Vault.deposit(uint256)": "ACCESS-UNPROTECTED-WRITER", "Vault.mint(address,uint256)": "ACCESS-UNPROTECTED-PRIVILEGED",
        "Vault.setFee(uint256)": "ACCESS-UNPROTECTED-ADMIN", "Vault.poke()": "ACCESS-UNPROTECTED-WRITER",
        "Vault.viaOrigin()": "ACCESS-UNPROTECTED-WRITER", "Vault.guarded()": "ACCESS-UNPROTECTED-WRITER",
        "Vault.initialize()": "ACCESS-INITIALIZER-OPEN",
    }
    assert severity_of(result, "ACCESS-UNPROTECTED-PRIVILEGED") == "blocker" and severity_of(result, "ACCESS-UNPROTECTED-ADMIN") == "major"
    assert severity_of(result, "ACCESS-INITIALIZER-OPEN") == "minor"
    assert result["verdict"] == "fail" and result["state_changing"] == 15 and result["guarded"] == 8 and result["unprotected"] == 7
    assert result["contracts_checked"] == 1


def test_access_matrix_internal_and_view_functions_are_not_listed_and_public_by_design_is_skipped():
    result = ok("sol_access_matrix", {"source": ACCESS_SRC, "public_by_design": ["deposit", "mint", "setFee", "poke", "viaOrigin", "guarded", "initialize"]})
    assert result["findings"] == [] and result["verdict"] == "pass" and result["unprotected"] == 0 and result["guarded"] == 8
    assert all("show" not in f for r in result["roles"] for f in r["functions"])


def test_access_matrix_minor_findings_alone_pass():
    result = ok("sol_access_matrix", {"source": "contract A { uint256 x; function poke() external { x += 1; } }"})
    assert result["verdict"] == "pass" and ids(result) == ["ACCESS-UNPROTECTED-WRITER"] and result["unprotected"] == 1


@pytest.mark.parametrize("name, rule", [
    ("upgradeToAndCall", "ACCESS-UNPROTECTED-PRIVILEGED"), ("pauseAll", "ACCESS-UNPROTECTED-PRIVILEGED"), ("transferOwnership", "ACCESS-UNPROTECTED-PRIVILEGED"),
    ("grantRole", "ACCESS-UNPROTECTED-PRIVILEGED"), ("emergencyExit", "ACCESS-UNPROTECTED-PRIVILEGED"), ("setOwner", "ACCESS-UNPROTECTED-PRIVILEGED"),
    ("withdraw", "ACCESS-UNPROTECTED-ADMIN"), ("updateOracle", "ACCESS-UNPROTECTED-ADMIN"), ("setFee", "ACCESS-UNPROTECTED-ADMIN"),
    ("settle", "ACCESS-UNPROTECTED-WRITER"), ("swap", "ACCESS-UNPROTECTED-WRITER"), ("burn", "ACCESS-UNPROTECTED-WRITER"),
])
def test_access_matrix_name_risk_classes(name, rule):
    result = ok("sol_access_matrix", {"source": f"contract A {{ uint256 x; function {name}() external {{ x += 1; }} }}"})
    assert ids(result) == [rule]


def test_access_matrix_errors_and_focus():
    invalid("sol_access_matrix", {"source": "library L { function f() public { } }"}, "source")
    invalid("sol_access_matrix", {"source": ACCESS_SRC, "contract": "Missing"}, "contract")
    two = "contract A { uint256 x; function a() external onlyOwner { x = 1; } } contract B { uint256 y; function b() external { y = 1; } }"
    assert ok("sol_access_matrix", {"source": two, "contract": "A"})["findings"] == []
    both = ok("sol_access_matrix", {"source": two})
    assert both["contracts_checked"] == 2 and ids(both) == ["ACCESS-UNPROTECTED-WRITER"] and "B.b()" in both["findings"][0]["summary"]


def test_access_matrix_modifier_defined_in_source_is_judged_by_its_body():
    source = """contract A {
        address admin; uint256 x;
        modifier teamOnly() { require(msg.sender == admin, "no"); _; }
        modifier noop() { _; }
        function a() external teamOnly { x = 1; }
        function b() external noop { x = 2; }
        function c() external onlyTeam { x = 3; }
    }"""
    result = ok("sol_access_matrix", {"source": source})
    assert {r["role"]: r["functions"] for r in result["roles"]} == {"teamOnly": ["A.a()"], "onlyTeam": ["A.c()"]}
    assert [f["summary"].split()[0] for f in result["findings"]] == ["A.b()"] and result["guarded"] == 2 and result["unprotected"] == 1


def test_access_matrix_follows_call_chains_through_cycles_without_hanging():
    source = """contract A {
        uint256 x;
        function open() external { _a(); }
        function _a() internal { _b(); }
        function _b() internal { x += 1; if (x < 3) { _a(); } }
        function admin() external { _c(); }
        function _c() internal { _d(); }
        function _d() internal onlyOwner { x = 0; _c(); }
    }"""
    result = ok("sol_access_matrix", {"source": source})
    assert {r["role"]: r["functions"] for r in result["roles"]} == {"onlyOwner": ["A.admin()"]}
    assert [f["id"] for f in result["findings"]] == ["ACCESS-UNPROTECTED-WRITER"] and "A.open()" in result["findings"][0]["summary"]


# --- sol_event_coverage ----------------------------------------------------------------------------------------

EVENT_SRC = """
contract Token {
    event Transfer(address indexed from, address indexed to, uint256 value);
    mapping(address => uint256) balances;
    function transfer(address to, uint256 v) external { balances[to] += v; emit Transfer(msg.sender, to, v); }
    function viaInternal(address to, uint256 v) external { _move(to, v); }
    function _move(address to, uint256 v) internal { balances[to] += v; emit Transfer(msg.sender, to, v); }
    function deep(address to, uint256 v) external { _outer(to, v); }
    function _outer(address to, uint256 v) internal { _move(to, v); }
    function silent(uint256 v) external { balances[msg.sender] = v; }
    function inherited(uint256 v) external { _mint(msg.sender, v); }
    function viaSuper() external { super.hook(); }
    function setup() external initializer { balances[msg.sender] = 1; }
    function quiet() external view returns (uint256) { return 1; }
    function noop() external { }
}
"""


def test_event_coverage_direct_transitive_delegated_and_missing():
    result = ok("sol_event_coverage", {"source": EVENT_SRC, "min_coverage_pct": 50})
    # emitting: transfer, viaInternal, deep, setup (initializer emits Initialized); delegated: inherited, viaSuper; missing: silent
    assert (result["functions_checked"], result["emitting"], result["delegated"], result["missing"]) == (7, 4, 2, 1)
    assert result["coverage_pct"] == 80.0 and result["verdict"] == "pass"
    assert ids(result) == ["EVENT-MISSING"] and "Token.silent(uint256)" in result["findings"][0]["summary"]


def test_event_coverage_threshold_is_inclusive():
    equal = ok("sol_event_coverage", {"source": EVENT_SRC, "min_coverage_pct": 80})
    assert equal["verdict"] == "pass" and equal["coverage_pct"] == 80.0
    above = ok("sol_event_coverage", {"source": EVENT_SRC, "min_coverage_pct": 80.01})
    assert above["verdict"] == "fail" and severity_of(above, "EVENT-COVERAGE-BELOW-MIN") == "major"


def test_event_coverage_exempt_names_and_no_candidates():
    result = ok("sol_event_coverage", {"source": EVENT_SRC, "min_coverage_pct": 100, "exempt": ["silent"]})
    assert result["missing"] == 0 and result["coverage_pct"] == 100.0 and result["verdict"] == "pass"
    view_only = ok("sol_event_coverage", {"source": "contract A { function f() external view returns (uint256) { return 1; } }", "min_coverage_pct": 100})
    assert (view_only["functions_checked"], view_only["coverage_pct"], view_only["verdict"]) == (0, 100.0, "pass")
    invalid("sol_event_coverage", {"source": "interface I { }", "min_coverage_pct": 10}, "source")


def test_event_coverage_ignores_emit_inside_comments_and_strings():
    source = 'contract A { uint256 x; function f() external { x = 1; // emit Changed(x);\n string memory s = "emit Fake(1)"; } }'
    result = ok("sol_event_coverage", {"source": source, "min_coverage_pct": 0})
    assert result["missing"] == 1


# --- sol_natspec_coverage --------------------------------------------------------------------------------------

NATSPEC_SRC = """
interface IVault {
    /// @notice Deposit assets
    /// @param amount amount to deposit
    /// @param receiver share receiver
    /// @return shares minted shares
    function deposit(uint256 amount, address receiver) external returns (uint256 shares);

    /**
     * @notice Total assets
     * @return assets the total
     */
    function totalAssets() external view returns (uint256 assets);

    function undocumented(uint256 a) external;
}
contract Vault is IVault {
    /// @inheritdoc IVault
    function deposit(uint256 amount, address receiver) external returns (uint256 shares) { }
    /// @notice Emitted on deposit
    /// @param who the depositor
    event Deposited(address indexed who, uint256 amount);
    /// @notice Bad amount
    error Bad(uint256 amount);
    /// @notice Wrong docs
    /// @param nonexistent not a parameter
    /// @return one
    /// @return two
    function wrong(uint256 a) external returns (uint256) { }
    function _internal() internal { }
    function hidden() private { }
    constructor() { }
}
"""


def test_natspec_counts_items_and_findings_exactly():
    result = ok("sol_natspec_coverage", {"source": NATSPEC_SRC, "min_coverage_pct": 0})
    # items: IVault.deposit, totalAssets, undocumented; Vault.deposit, Deposited, Bad, wrong  => 7; fully documented: 3 (IVault.deposit, totalAssets, Vault.deposit)
    assert (result["items_total"], result["items_documented"]) == (7, 3)
    assert result["coverage_pct"] == 42.86
    found = {(f["id"], f["summary"].split(" ", 1)[1].split(" ")[0]) for f in result["findings"]}
    assert ("NATSPEC-NOTICE-MISSING", "IVault.undocumented(uint256)") in found
    assert ("NATSPEC-PARAM-MISSING", "IVault.undocumented(uint256)") in found
    assert ("NATSPEC-PARAM-MISSING", "Vault.Deposited(address,uint256)") in found  # amount not documented
    assert ("NATSPEC-PARAM-UNKNOWN", "Vault.wrong(uint256)") in found
    assert ("NATSPEC-PARAM-MISSING", "Vault.wrong(uint256)") in found
    assert ("NATSPEC-RETURN-EXTRA", "Vault.wrong(uint256)") in found
    assert ("NATSPEC-NOTICE-MISSING", "Vault.Bad(uint256)") not in found  # error has @notice
    assert ("NATSPEC-PARAM-MISSING", "Vault.Bad(uint256)") in found
    assert not any("_internal" in f["summary"] or "hidden" in f["summary"] or "constructor" in f["summary"] for f in result["findings"])
    assert result["verdict"] == "fail"  # PARAM-UNKNOWN and RETURN-EXTRA are major (solc rejects them)


def test_natspec_return_missing_and_inheritdoc():
    source = "contract A {\n /// @notice get\n function a() external view returns (uint256, bool) { }\n /// @inheritdoc B\n function b(uint256 x) external { }\n}"
    result = ok("sol_natspec_coverage", {"source": source, "min_coverage_pct": 0})
    assert ids(result) == ["NATSPEC-RETURN-MISSING"] and "documents 0 of 2" in result["findings"][0]["summary"]
    assert (result["items_total"], result["items_documented"]) == (2, 1) and result["verdict"] == "pass"


def test_natspec_threshold_is_inclusive_and_unnamed_params_need_no_tag():
    source = "contract A {\n /// @notice one\n function a(uint256) external { }\n function b() external { }\n}"
    equal = ok("sol_natspec_coverage", {"source": source, "min_coverage_pct": 50})
    assert equal["coverage_pct"] == 50.0 and equal["verdict"] == "pass"
    below = ok("sol_natspec_coverage", {"source": source, "min_coverage_pct": 50.01})
    assert below["verdict"] == "fail" and severity_of(below, "NATSPEC-COVERAGE-BELOW-MIN") == "major"


def test_natspec_blank_line_detaches_the_comment_and_block_comments_are_not_natspec():
    source = "contract A {\n /// @notice detached\n\n function a() external { }\n /* @notice plain */\n function b() external { }\n}"
    result = ok("sol_natspec_coverage", {"source": source, "min_coverage_pct": 0})
    assert result["items_documented"] == 0 and ids(result) == ["NATSPEC-NOTICE-MISSING", "NATSPEC-NOTICE-MISSING"]


def test_natspec_rejects_source_without_public_items():
    invalid("sol_natspec_coverage", {"source": "contract A { function f() internal { } }", "min_coverage_pct": 10}, "source")


# --- sol_gas_regression ----------------------------------------------------------------------------------------


def gas(baseline, current, budget, **extra):
    return ok("sol_gas_regression", {"baseline": [{"name": n, "gas": g} for n, g in baseline.items()],
                                     "current": [{"name": n, "gas": g} for n, g in current.items()], "max_regression_pct": budget, **extra})


def test_gas_regression_budget_boundary_is_inclusive():
    equal = gas({"transfer": 50000}, {"transfer": 52500}, 5)  # exactly +5.00%
    assert equal["regressions"] == 0 and equal["verdict"] == "pass" and equal["findings"] == []
    over = gas({"transfer": 50000}, {"transfer": 52501}, 5)
    assert over["regressions"] == 1 and over["verdict"] == "fail" and ids(over) == ["GAS-REGRESSION"] and severity_of(over, "GAS-REGRESSION") == "major"
    assert "+5.0%" in over["findings"][0]["summary"]
    zero_budget = gas({"f": 1000}, {"f": 1001}, 0)
    assert zero_budget["verdict"] == "fail"
    assert gas({"f": 1000}, {"f": 1000}, 0)["verdict"] == "pass"


def test_gas_regression_min_delta_ignores_small_absolute_changes():
    ignored = gas({"f": 1000}, {"f": 1100}, 5, min_delta_gas=100)  # +10% but exactly 100 gas
    assert ignored["regressions"] == 0 and ignored["verdict"] == "pass"
    flagged = gas({"f": 1000}, {"f": 1101}, 5, min_delta_gas=100)
    assert flagged["regressions"] == 1 and flagged["verdict"] == "fail"


def test_gas_regression_totals_worst_function_and_listing():
    result = gas({"a": 1000, "b": 2000, "gone": 500}, {"a": 1100, "b": 1900, "c": 700}, 5)
    assert (result["compared"], result["regressions"], result["improved"]) == (2, 1, 1)
    assert (result["total_baseline_gas"], result["total_current_gas"], result["total_change_pct"]) == (3000, 3000, 0.0)
    assert (result["worst_function"], result["worst_increase_pct"]) == ("a", 10.0)
    assert ids(result) == ["GAS-REGRESSION", "GAS-MISSING-FUNCTION", "GAS-NEW-FUNCTION"]
    assert severity_of(result, "GAS-MISSING-FUNCTION") == "minor" and severity_of(result, "GAS-NEW-FUNCTION") == "info"
    assert result["verdict"] == "fail"


def test_gas_regression_improvement_only_and_rounding():
    result = gas({"a": 3000}, {"a": 2000}, 1)
    assert (result["improved"], result["total_change_pct"], result["worst_function"], result["worst_increase_pct"], result["verdict"]) == (1, -33.3333, "", 0.0, "pass")
    third = gas({"a": 3}, {"a": 4}, 50)
    assert third["worst_increase_pct"] == 33.3333 and third["verdict"] == "pass"


def test_gas_regression_ceiling_is_strict():
    equal = gas({"f": 100}, {"f": 100}, 5, max_function_gas=100)
    assert equal["verdict"] == "pass"
    over = gas({"f": 100}, {"f": 101}, 50, max_function_gas=100)
    assert ids(over) == ["GAS-OVER-CEILING"] and severity_of(over, "GAS-OVER-CEILING") == "major" and over["verdict"] == "fail"
    new_over = gas({"f": 100}, {"f": 100, "g": 500}, 5, max_function_gas=400)
    assert has(new_over, "GAS-OVER-CEILING", "g uses 500") and has(new_over, "GAS-NEW-FUNCTION")


def test_gas_regression_disjoint_runs_compare_nothing():
    result = gas({"a": 10}, {"b": 20}, 5)
    assert (result["compared"], result["total_change_pct"], result["verdict"]) == (0, 0.0, "pass")


def test_gas_regression_rejects_repeated_names_naming_the_list():
    invalid("sol_gas_regression", {"baseline": [{"name": "a", "gas": 1}, {"name": "a", "gas": 2}], "current": [{"name": "a", "gas": 1}], "max_regression_pct": 5}, "baseline")
    invalid("sol_gas_regression", {"baseline": [{"name": "a", "gas": 1}], "current": [{"name": "a", "gas": 1}, {"name": "a", "gas": 2}], "max_regression_pct": 5}, "current")
    invalid("sol_gas_regression", {"baseline": [{"name": "a", "gas": 0}], "current": [{"name": "a", "gas": 1}], "max_regression_pct": 5}, "baseline")


# --- sol_deploy_plan_check -------------------------------------------------------------------------------------

GOOD = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # EIP-55 test vector
BASE_PLAN = {
    "chain_id": 11155111, "expected_chain_id": 11155111, "network_kind": "testnet", "script_asserts_chain_id": True, "simulated": True,
    "broadcast": False, "verify_source": True, "constructor_types": ["address", "uint256"], "constructor_args": [GOOD, "1000"],
    "owner_kind": "multisig", "owner_address": GOOD, "multisig_threshold": 2, "multisig_signers": 3, "signer_kind": "hardware",
    "rpc_url": "https://rpc.sepolia.example.org", "is_proxy_upgrade": False, "human_approval": False,
}


def plan(**over):
    payload = {**BASE_PLAN, **over}
    return ok("sol_deploy_plan_check", {k: v for k, v in payload.items() if v is not None})


def mainnet(**over):
    base = {"chain_id": 1, "expected_chain_id": 1, "network_kind": "mainnet", "human_approval": True, "multisig_threshold": 3, "multisig_signers": 5}
    return plan(**{**base, **over})


def test_deploy_plan_clean_testnet_and_mainnet_plans_pass():
    result = plan()
    assert result["verdict"] == "pass" and result["findings"] == [] and result["network_name"] == "Sepolia"
    assert result["chain_known"] is True and result["constructor_args_ok"] is True and result["blocking"] == 0
    result = mainnet()
    assert result["verdict"] == "pass" and result["network_name"] == "Ethereum Mainnet" and result["findings"] == []


@pytest.mark.parametrize("chain_id, name, kind", [
    (1, "Ethereum Mainnet", "mainnet"), (10, "OP Mainnet", "mainnet"), (56, "BNB Smart Chain", "mainnet"), (137, "Polygon PoS", "mainnet"),
    (8453, "Base", "mainnet"), (42161, "Arbitrum One", "mainnet"), (43114, "Avalanche C-Chain", "mainnet"),
    (11155111, "Sepolia", "testnet"), (17000, "Holesky", "testnet"), (560048, "Hoodi", "testnet"), (84532, "Base Sepolia", "testnet"),
    (421614, "Arbitrum Sepolia", "testnet"), (80002, "Polygon Amoy", "testnet"), (11155420, "OP Sepolia", "testnet"),
    (31337, "Hardhat/Anvil", "local"), (1337, "Local development chain", "local"),
])
def test_deploy_plan_known_chain_table(chain_id, name, kind):
    result = plan(chain_id=chain_id, expected_chain_id=chain_id, network_kind=kind, human_approval=True, multisig_threshold=3, multisig_signers=5)
    assert result["network_name"] == name and result["chain_known"] is True and result["verdict"] == "pass", result["findings"]
    wrong_kind = {"mainnet": "testnet", "testnet": "mainnet", "local": "testnet"}[kind]
    assert has(plan(chain_id=chain_id, expected_chain_id=chain_id, network_kind=wrong_kind), "DEPLOY-NETWORK-KIND-MISMATCH")


def test_deploy_plan_chain_guard_and_kind_mismatch_and_unknown_chain():
    mismatch = plan(chain_id=1, expected_chain_id=11155111)
    assert severity_of(mismatch, "DEPLOY-CHAIN-MISMATCH") == "blocker" and mismatch["verdict"] == "fail"
    kind = plan(chain_id=1, expected_chain_id=1, network_kind="testnet")
    assert severity_of(kind, "DEPLOY-NETWORK-KIND-MISMATCH") == "blocker"
    unknown_testnet = plan(chain_id=999999, expected_chain_id=999999)
    assert severity_of(unknown_testnet, "DEPLOY-CHAIN-UNKNOWN") == "info" and unknown_testnet["chain_known"] is False and unknown_testnet["network_name"] == ""
    assert unknown_testnet["verdict"] == "pass"
    unknown_mainnet = mainnet(chain_id=999999, expected_chain_id=999999)
    assert severity_of(unknown_mainnet, "DEPLOY-CHAIN-UNKNOWN") == "major" and unknown_mainnet["verdict"] == "fail"


def test_deploy_plan_chain_id_assertion_in_the_script():
    assert severity_of(plan(script_asserts_chain_id=False), "DEPLOY-NO-CHAIN-GUARD") == "major"
    assert severity_of(plan(script_asserts_chain_id=False, chain_id=31337, expected_chain_id=31337, network_kind="local"), "DEPLOY-NO-CHAIN-GUARD") == "minor"
    assert plan(script_asserts_chain_id=False, chain_id=31337, expected_chain_id=31337, network_kind="local")["verdict"] == "pass"


def test_deploy_plan_simulation_first():
    assert severity_of(mainnet(simulated=False, broadcast=True), "DEPLOY-NOT-SIMULATED") == "blocker"
    assert severity_of(mainnet(simulated=False, broadcast=False), "DEPLOY-NOT-SIMULATED") == "major"
    assert severity_of(plan(simulated=False, broadcast=True), "DEPLOY-NOT-SIMULATED") == "minor"
    assert plan(simulated=False, broadcast=True)["verdict"] == "pass"
    local = plan(chain_id=31337, expected_chain_id=31337, network_kind="local", simulated=False, broadcast=True)
    assert "DEPLOY-NOT-SIMULATED" not in ids(local)


def test_deploy_plan_source_verification():
    assert severity_of(mainnet(verify_source=False), "DEPLOY-NOT-VERIFIED") == "major"
    assert severity_of(plan(verify_source=False), "DEPLOY-NOT-VERIFIED") == "minor"
    local = plan(chain_id=31337, expected_chain_id=31337, network_kind="local", verify_source=False)
    assert "DEPLOY-NOT-VERIFIED" not in ids(local)


def test_deploy_plan_human_approval_for_mainnet():
    deploy = plan(chain_id=1, expected_chain_id=1, network_kind="mainnet", multisig_threshold=3, multisig_signers=5)
    assert severity_of(deploy, "DEPLOY-NO-APPROVAL") == "blocker" and "L4" in deploy["findings"][0]["summary"] and "mainnet deployment" in deploy["findings"][0]["summary"]
    upgrade_plan = plan(chain_id=1, expected_chain_id=1, network_kind="mainnet", multisig_threshold=3, multisig_signers=5, is_proxy_upgrade=True)
    assert "L3" in upgrade_plan["findings"][0]["summary"] and "live proxy upgrade" in upgrade_plan["findings"][0]["summary"]
    assert "DEPLOY-NO-APPROVAL" not in ids(plan(human_approval=False))  # testnets need no approval


def test_deploy_plan_constructor_arity_and_zero_address():
    arity = plan(constructor_args=[GOOD])
    assert severity_of(arity, "DEPLOY-CTOR-ARITY") == "blocker" and arity["constructor_args_ok"] is False and arity["verdict"] == "fail"
    zero = plan(constructor_args=["0x" + "0" * 40, "1"])
    assert severity_of(zero, "DEPLOY-CTOR-ZERO-ADDRESS") == "major" and zero["constructor_args_ok"] is True and zero["verdict"] == "fail"
    assert plan(constructor_types=[], constructor_args=[])["constructor_args_ok"] is True


@pytest.mark.parametrize("type_name, value, valid", [
    ("address", GOOD, True), ("address", GOOD.lower(), True), ("address", "0x" + GOOD[2:].upper(), True),
    ("address", "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAEd", False),  # one letter flipped: checksum fails
    ("address", "0x1234", False), ("address", "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", False),
    ("uint256", "0", True), ("uint256", "0xff", True), ("uint256", str(2**256 - 1), True), ("uint256", str(2**256), False), ("uint256", "-1", False), ("uint256", "1.5", False),
    ("uint8", "255", True), ("uint8", "256", False), ("int8", "-128", True), ("int8", "-129", False), ("int8", "127", True), ("int8", "128", False),
    ("bool", "true", True), ("bool", "false", True), ("bool", "True", False), ("bool", "1", False),
    ("bytes32", "0x" + "ab" * 32, True), ("bytes32", "0x" + "ab" * 31, False), ("bytes4", "0x12345678", True), ("bytes4", "0x1234567", False),
    ("bytes", "0x", True), ("bytes", "0xabcd", True), ("bytes", "0xabc", False), ("bytes", "abcd", False),
    ("string", "any text, even 0x or empty", True), ("string", "", True),
])
def test_deploy_plan_constructor_argument_forms(type_name, value, valid):
    result = plan(constructor_types=[type_name], constructor_args=[value])
    assert result["constructor_args_ok"] is valid
    assert has(result, "DEPLOY-CTOR-ARG") is (not valid)
    if not valid:
        assert severity_of(result, "DEPLOY-CTOR-ARG") == "blocker" and result["verdict"] == "fail"


@pytest.mark.parametrize("field, value", [
    ("constructor_types", ["uint"]), ("constructor_types", ["uint256[]"]), ("constructor_types", ["(uint256,address)"]), ("constructor_types", ["Token"]),
    ("owner_address", "0x1234"), ("multisig_threshold", 4), ("multisig_threshold", 0), ("rpc_url", "ftp://rpc.example.org"), ("rpc_url", "not a url"),
])
def test_deploy_plan_semantic_input_errors_name_the_field(field, value):
    payload = {**BASE_PLAN, field: value}
    if field == "constructor_types":
        payload["constructor_args"] = ["1"]
    invalid("sol_deploy_plan_check", payload, field)


def test_deploy_plan_owner_rules():
    zero = plan(owner_address="0x" + "0" * 40)
    assert severity_of(zero, "DEPLOY-OWNER-ZERO") == "blocker"
    bad_sum = plan(owner_address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAEd")
    assert severity_of(bad_sum, "DEPLOY-OWNER-CHECKSUM") == "blocker"
    assert severity_of(mainnet(owner_kind="eoa"), "DEPLOY-OWNER-EOA") == "blocker"
    assert severity_of(plan(owner_kind="eoa"), "DEPLOY-OWNER-EOA") == "minor"
    assert "DEPLOY-OWNER-EOA" not in ids(plan(chain_id=31337, expected_chain_id=31337, network_kind="local", owner_kind="eoa"))
    assert "DEPLOY-OWNER-EOA" not in ids(mainnet(owner_kind="timelock"))
    one_of_one = plan(chain_id=1, expected_chain_id=1, network_kind="mainnet", human_approval=True, multisig_threshold=1, multisig_signers=1)
    assert severity_of(one_of_one, "DEPLOY-MULTISIG-THRESHOLD") == "major"
    two = plan(chain_id=1, expected_chain_id=1, network_kind="mainnet", human_approval=True, multisig_threshold=2, multisig_signers=2)
    assert "DEPLOY-MULTISIG-THRESHOLD" not in ids(two)
    assert plan(owner_address=None, owner_kind="timelock", multisig_threshold=None, multisig_signers=None)["verdict"] == "pass"


def test_deploy_plan_signer_kind():
    raw = plan(signer_kind="raw_private_key")
    assert severity_of(raw, "DEPLOY-RAW-KEY") == "blocker" and raw["verdict"] == "fail"
    local = plan(chain_id=31337, expected_chain_id=31337, network_kind="local", signer_kind="raw_private_key")
    assert severity_of(local, "DEPLOY-RAW-KEY") == "info" and local["verdict"] == "pass"
    for kind in ("hardware", "keystore", "remote_signer"):
        assert "DEPLOY-RAW-KEY" not in ids(plan(signer_kind=kind))


@pytest.mark.parametrize("url, rule, severity", [
    ("https://user:hunter2@rpc.example.org", "DEPLOY-RPC-CREDENTIAL", "blocker"),
    ("https://rpc.example.org/?apikey=abcdef", "DEPLOY-RPC-CREDENTIAL", "blocker"),
    ("https://rpc.example.org/?chain=1&access_token=abcdef", "DEPLOY-RPC-CREDENTIAL", "blocker"),
    ("https://rpc.example.org/?KEY=abcdef", "DEPLOY-RPC-CREDENTIAL", "blocker"),
    ("https://mainnet.infura.example/v3/9aa3d95b3bc440fa88ea12eaa4456161", "DEPLOY-RPC-PATH-KEY", "major"),
    ("https://rpc.example.org/v2/AbCdEfGhIjKlMnOpQrStUv12", "DEPLOY-RPC-PATH-KEY", "major"),
    ("http://rpc.example.org", "DEPLOY-RPC-INSECURE", "major"),
])
def test_deploy_plan_rpc_url_credentials_are_flagged_without_echoing_them(url, rule, severity):
    result = plan(rpc_url=url)
    assert severity_of(result, rule) == severity and result["verdict"] == "fail"
    text = json.dumps(result)
    for secret in ("hunter2", "abcdef", "9aa3d95b3bc440fa88ea12eaa4456161", "AbCdEfGhIjKlMnOpQrStUv12"):
        assert secret not in text


@pytest.mark.parametrize("url", [
    "https://rpc.example.org", "https://rpc.example.org/v1/public", "https://rpc.example.org/rpc/mainnet-archive-node-endpoint", "https://rpc.example.org:8545/?chain=1",
    "http://localhost:8545", "http://127.0.0.1:8545", "http://[::1]:8545",
])
def test_deploy_plan_clean_rpc_urls_pass(url):
    assert "DEPLOY-RPC" not in "".join(ids(plan(rpc_url=url)))
    assert plan(rpc_url=url)["verdict"] == "pass"


def test_deploy_plan_blocking_counts_major_and_worse():
    result = plan(simulated=False, broadcast=True, verify_source=False, script_asserts_chain_id=False, signer_kind="raw_private_key")
    assert result["blocking"] == 2 and result["verdict"] == "fail"  # raw key (blocker) and missing chain guard (major); minor findings do not count


# --- sol_pragma_policy -----------------------------------------------------------------------------------------


def pragma(pragmas, min_version="0.8.20", require_pinned=True, via_ir=False, optimizer_enabled=True):
    return ok("sol_pragma_policy", {"pragmas": pragmas, "min_version": min_version, "require_pinned": require_pinned, "via_ir": via_ir,
                                    "optimizer_enabled": optimizer_enabled})


def test_pragma_pinned_version_passes():
    result = pragma(["0.8.28"])
    assert (result["pragmas_checked"], result["pinned"], result["floating"], result["buggy"], result["lowest_admitted"]) == (1, 1, 0, 0, "0.8.28")
    assert result["verdict"] == "pass" and result["findings"] == []
    assert pragma(["=0.8.28"])["pinned"] == 1 and pragma(["0.8.28 || 0.8.28"])["pinned"] == 1 and pragma([">=0.8.28 <0.8.29"])["pinned"] == 1


def test_pragma_floating_ranges_follow_solidity_semver():
    for expr in ("^0.8.20", "~0.8.20", ">=0.8.20 <0.9.0", "0.8.x", "0.8", "0.8.20 - 0.8.25", ">0.8.19", "0.8.20 || 0.8.21", ">=0.8.20"):
        result = pragma([expr])
        assert result["floating"] == 1 and result["pinned"] == 0, expr
        assert severity_of(result, "PRAGMA-NOT-PINNED") == "major" and result["verdict"] == "fail", expr
    relaxed = pragma(["^0.8.20"], require_pinned=False)
    assert severity_of(relaxed, "PRAGMA-NOT-PINNED") == "info" and relaxed["verdict"] == "pass"


@pytest.mark.parametrize("expr, lowest", [
    ("^0.8.20", "0.8.20"), ("~0.8.5", "0.8.5"), (">0.8.19", "0.8.20"), (">=0.8.20 <0.9.0", "0.8.20"), ("0.8.x", "0.8.0"), ("0.8", "0.8.0"),
    ("0.8.20 - 0.8.25", "0.8.20"), ("<=0.8.5", "0.0.0"), ("<0.8.5", "0.0.0"), ("0.7.6 || 0.8.20", "0.7.6"), ("*", "0.0.0"), ("^0.8", "0.8.0"),
    ("> 0.8.19", "0.8.20"), (">= 0.8.20", "0.8.20"), (">0.8", "0.9.0"),
])
def test_pragma_range_lower_bounds(expr, lowest):
    assert pragma([expr], min_version="0.0.0", require_pinned=False)["lowest_admitted"] == lowest


def test_pragma_minimum_version_boundary_is_inclusive():
    assert "PRAGMA-BELOW-MIN" not in ids(pragma(["^0.8.20"]))
    below = pragma(["^0.8.20"], min_version="0.8.21")
    assert severity_of(below, "PRAGMA-BELOW-MIN") == "major" and below["verdict"] == "fail"
    pinned_below = pragma(["0.8.19"])
    assert severity_of(pinned_below, "PRAGMA-BELOW-MIN") == "major"
    assert "PRAGMA-BELOW-MIN" not in ids(pragma(["0.8.20"]))
    assert has(pragma([">=0.4.22 <0.9.0"], require_pinned=False), "PRAGMA-BELOW-MIN", "0.4.22")


def test_pragma_empty_range_and_unknown_version():
    empty = pragma([">=0.9.0 <0.8.0"], require_pinned=False)
    assert severity_of(empty, "PRAGMA-EMPTY-RANGE") == "major" and empty["verdict"] == "fail" and empty["lowest_admitted"] == ""
    unknown = pragma(["0.8.99"])
    assert severity_of(unknown, "PRAGMA-UNKNOWN-VERSION") == "info" and unknown["verdict"] == "pass" and unknown["pinned"] == 1


def test_pragma_lowest_admitted_is_the_minimum_over_all_pragmas():
    result = pragma(["0.8.28", "^0.8.22", "0.8.21"], require_pinned=False)
    assert result["lowest_admitted"] == "0.8.21" and (result["pragmas_checked"], result["pinned"], result["floating"]) == (3, 2, 1)


# Medium-or-worse compiler bugs from docs.soliditylang.org/en/latest/bugs.html (bugs.json), one row per table entry:
# (name, first affected, last affected, needs)
BUG_ROWS = [
    ("KeccakCaching", (0, 8, 0), (0, 8, 2), "optimizer"),
    ("InlineAssemblyMemorySideEffects", (0, 8, 13), (0, 8, 14), "optimizer"),
    ("AbiReencodingHeadOverflowWithStaticArrayCleanup", (0, 8, 0), (0, 8, 15), ""),
    ("StorageWriteRemovalBeforeConditionalTermination", (0, 8, 13), (0, 8, 16), "optimizer"),
    ("TransientStorageClearingHelperCollision", (0, 8, 28), (0, 8, 33), "via_ir"),
    ("UnsoundSpillInMutualRecursion", (0, 8, 0), (0, 8, 35), "via_ir"),
]


def affected(name, version, via_ir, optimizer):
    result = pragma([version], min_version="0.0.0", require_pinned=False, via_ir=via_ir, optimizer_enabled=optimizer)
    return has(result, "PRAGMA-KNOWN-BUG", name)


@pytest.mark.parametrize("name, first, last, needs", BUG_ROWS)
def test_pragma_known_bug_table_boundaries(name, first, last, needs):
    on = {"via_ir": needs == "via_ir", "optimizer": needs == "optimizer"}
    fmt = lambda v: "{}.{}.{}".format(*v)  # noqa: E731
    before = (first[0], first[1], first[2] - 1)
    after = (last[0], last[1], last[2] + 1)
    assert affected(name, fmt(first), **on) and affected(name, fmt(last), **on)
    if first[2] > 0:
        assert not affected(name, fmt(before), **on)
    assert not affected(name, fmt(after), **on)
    if needs:  # the bug only applies to builds that use the feature
        off = {"via_ir": False, "optimizer": False}
        assert not affected(name, fmt(first), **off)


def test_pragma_known_bug_severity_pinned_major_range_minor():
    pinned = pragma(["0.8.28"], via_ir=True)
    assert severity_of(pinned, "PRAGMA-KNOWN-BUG") == "major" and pinned["buggy"] == 1 and pinned["verdict"] == "fail"
    assert "TransientStorageClearingHelperCollision" in pinned["findings"][0]["summary"] and "SOL-2026-1, high, fixed in 0.8.34" in pinned["findings"][0]["summary"]
    ranged = pragma(["^0.8.20"], via_ir=True, require_pinned=False)
    assert severity_of(ranged, "PRAGMA-KNOWN-BUG") == "minor" and ranged["buggy"] == 1 and ranged["verdict"] == "pass"
    clean = pragma(["0.8.28"], via_ir=False)
    assert clean["buggy"] == 0 and "PRAGMA-KNOWN-BUG" not in ids(clean)
    fixed = pragma(["0.8.37"], via_ir=True)
    assert fixed["buggy"] == 0


def test_pragma_old_pinned_version_lists_several_bugs():
    result = pragma(["0.8.13"], min_version="0.8.0", via_ir=False)
    summary = result["findings"][0]["summary"]
    assert "3 compiler bug(s)" in summary and "InlineAssemblyMemorySideEffects" in summary and "StorageWriteRemovalBeforeConditionalTermination" in summary
    assert "AbiReencodingHeadOverflowWithStaticArrayCleanup" in summary
    four = pragma(["0.8.13"], min_version="0.8.0", via_ir=True)
    assert "4 compiler bug(s)" in four["findings"][0]["summary"]


@pytest.mark.parametrize("payload, field", [
    ({"pragmas": ["garbage"], "min_version": "0.8.20", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "pragmas"),
    ({"pragmas": ["^0.8.20", ">="], "min_version": "0.8.20", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "pragmas"),
    ({"pragmas": ["^^0.8.20"], "min_version": "0.8.20", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "pragmas"),
    ({"pragmas": ["0.8.20 foo"], "min_version": "0.8.20", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "pragmas"),
    ({"pragmas": ["0.8.20"], "min_version": "0.8", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "min_version"),
    ({"pragmas": ["0.8.20"], "min_version": "v0.8.20", "require_pinned": True, "via_ir": False, "optimizer_enabled": True}, "min_version"),
])
def test_pragma_rejects_unparseable_input_naming_the_field(payload, field):
    invalid("sol_pragma_policy", payload, field)


# --- sol_test_gate ---------------------------------------------------------------------------------------------


def cov(path="src/Vault.sol", lines=(100, 96), branches=(20, 19), functions=(10, 10)):
    return {"path": path, "lines_found": lines[0], "lines_hit": lines[1], "branches_found": branches[0], "branches_hit": branches[1],
            "functions_found": functions[0], "functions_hit": functions[1]}


def gate(files=None, **over):
    payload = {"files": files or [cov()], "min_line_pct": 95, "min_branch_pct": 90, "tests_total": 40, "tests_failed": 0, "tests_skipped": 0,
               "fuzz_tests": 6, "fuzz_runs": 1000, "invariant_tests": 2, "require_invariants": True, **over}
    return ok("sol_test_gate", {k: v for k, v in payload.items() if v is not None})


def test_test_gate_clean_run_passes_and_reports_percentages():
    result = gate()
    assert (result["files_checked"], result["line_pct"], result["branch_pct"], result["function_pct"], result["files_below_floor"]) == (1, 96.0, 95.0, 100.0, 0)
    assert result["verdict"] == "pass" and result["findings"] == []


def test_test_gate_coverage_boundaries_are_inclusive():
    assert gate([cov(lines=(100, 95))])["verdict"] == "pass"
    below = gate([cov(lines=(100, 94))])
    assert severity_of(below, "TEST-LINE-COVERAGE") == "major" and below["verdict"] == "fail" and below["line_pct"] == 94.0
    assert gate([cov(branches=(20, 18))])["verdict"] == "pass"  # exactly 90%
    low_branch = gate([cov(branches=(20, 17))])
    assert severity_of(low_branch, "TEST-BRANCH-COVERAGE") == "major" and low_branch["branch_pct"] == 85.0
    assert gate([cov(lines=(3, 2))], min_line_pct=66.66)["verdict"] == "pass"  # 66.666...% against 66.66
    assert gate([cov(lines=(3, 2))], min_line_pct=66.67)["verdict"] == "fail"


def test_test_gate_totals_are_weighted_by_lines_not_averaged_per_file():
    result = gate([cov("a.sol", lines=(10, 10)), cov("b.sol", lines=(90, 0))], min_line_pct=0)
    assert result["line_pct"] == 10.0  # 10 of 100, not the 50% mean of 100% and 0%
    assert gate([cov("a.sol", lines=(50, 50)), cov("b.sol", lines=(50, 45))], min_line_pct=95)["line_pct"] == 95.0


def test_test_gate_per_file_floor():
    files = [cov("a.sol", lines=(100, 100)), cov("b.sol", lines=(100, 80)), cov("c.sol", lines=(100, 90)), cov("empty.sol", lines=(0, 0))]
    result = gate(files, min_line_pct=0, min_file_line_pct=90)
    assert result["files_below_floor"] == 1 and ids(result) == ["TEST-FILE-LINES"] and "b.sol" in result["findings"][0]["summary"]
    assert severity_of(result, "TEST-FILE-LINES") == "minor" and result["verdict"] == "pass"  # minor alone does not fail
    assert gate(files, min_line_pct=0)["files_below_floor"] == 0


def test_test_gate_no_instrumented_lines_and_zero_branches():
    nothing = gate([cov(lines=(0, 0), branches=(0, 0), functions=(0, 0))])
    assert severity_of(nothing, "TEST-NO-COVERAGE") == "blocker" and nothing["verdict"] == "fail"
    vacuous = gate([cov(branches=(0, 0))])
    assert vacuous["branch_pct"] == 100.0 and severity_of(vacuous, "TEST-NO-BRANCHES") == "info" and vacuous["verdict"] == "pass"
    assert "TEST-NO-BRANCHES" not in ids(gate([cov(branches=(0, 0))], min_branch_pct=0))


def test_test_gate_test_outcomes():
    assert severity_of(gate(tests_failed=1), "TEST-FAILING") == "blocker"
    skipped = gate(tests_skipped=2)
    assert severity_of(skipped, "TEST-SKIPPED") == "minor" and skipped["verdict"] == "pass"
    none = gate(tests_total=0, fuzz_tests=0, invariant_tests=0)
    assert severity_of(none, "TEST-NONE") == "blocker"


def test_test_gate_fuzz_and_invariant_floors():
    assert severity_of(gate(fuzz_tests=0, fuzz_runs=0), "TEST-NO-FUZZ") == "major"
    assert gate(fuzz_runs=256)["verdict"] == "pass"  # Foundry's default runs is the default floor
    assert severity_of(gate(fuzz_runs=255), "TEST-FUZZ-RUNS") == "major"
    assert gate(fuzz_runs=1000, min_fuzz_runs=1000)["verdict"] == "pass"
    assert gate(fuzz_runs=999, min_fuzz_runs=1000)["verdict"] == "fail"
    assert severity_of(gate(invariant_tests=0), "TEST-NO-INVARIANT") == "major"
    assert gate(invariant_tests=0, require_invariants=False)["verdict"] == "pass"


@pytest.mark.parametrize("override, field", [
    ({"files": [{**cov(), "lines_hit": 101}]}, "files"),
    ({"files": [{**cov(), "branches_hit": 21}]}, "files"),
    ({"files": [{**cov(), "functions_hit": 11}]}, "files"),
    ({"files": [cov("x.sol"), cov("x.sol")]}, "files"),
    ({"tests_failed": 30, "tests_skipped": 11}, "tests_failed"),
    ({"fuzz_tests": 41}, "fuzz_tests"),
    ({"invariant_tests": 41}, "invariant_tests"),
])
def test_test_gate_rejects_inconsistent_numbers_naming_the_field(override, field):
    payload = {"files": [cov()], "min_line_pct": 95, "min_branch_pct": 90, "tests_total": 40, "tests_failed": 0, "tests_skipped": 0,
               "fuzz_tests": 6, "fuzz_runs": 1000, "invariant_tests": 2, "require_invariants": True, **override}
    invalid("sol_test_gate", payload, field)


# --- sol_eip712_typehash ---------------------------------------------------------------------------------------

MAIL = "Mail(Person from,Person to,string contents)Person(string name,address wallet)"
PERMIT = "Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"
VC = "0x" + "11" * 20


def typed(**payload):
    return ok("sol_eip712_typehash", payload)


def test_eip712_spec_example_vectors():
    # EIP-712 reference example: Mail / Person and the Ether Mail domain
    result = typed(encode_type=MAIL, declared_type_hash="0xa0cedeb2dc280ba39b857546d74f5549c3a1d7bdc2dd96bf881f76108e23dac2",
                   domain_name="Ether Mail", domain_version="1", domain_chain_id=1, verifying_contract="0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
                   declared_domain_separator="0xf2cee375fa42b42143804025fc449deafd50cc031ca257e0b194a650a912090f")
    assert result["primary_type"] == "Mail" and result["canonical_encode_type"] == MAIL
    assert result["type_hash"] == result["canonical_type_hash"] == "0xa0cedeb2dc280ba39b857546d74f5549c3a1d7bdc2dd96bf881f76108e23dac2"
    assert result["domain_type_string"] == "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    assert result["domain_separator"] == "0xf2cee375fa42b42143804025fc449deafd50cc031ca257e0b194a650a912090f"
    assert (result["type_hash_status"], result["domain_separator_status"], result["verdict"], result["findings"]) == ("match", "match", "pass", [])


def test_eip712_erc2612_permit_typehash_and_pinned_domain_encodings():
    result = typed(encode_type=PERMIT, domain_name="Token", domain_version="1", domain_chain_id=1, verifying_contract=VC)
    assert result["type_hash"] == "0x6e71edae12b1b97f4d1f60370fef10105fa2faae0126114a169c64845d6126c9"
    assert result["domain_separator"] == "0x45f1736c8c5a69a22725cf6d99bea7523f54e17fad73d0951813d4686cf501e1"
    assert (result["type_hash_status"], result["domain_separator_status"]) == ("not_declared", "not_declared")
    salted = typed(encode_type=PERMIT, domain_name="Token", domain_chain_id=137, verifying_contract=VC, domain_salt="0x" + "ab" * 32)
    assert salted["domain_type_string"] == "EIP712Domain(string name,uint256 chainId,address verifyingContract,bytes32 salt)"
    assert salted["domain_separator"] == "0x1ec668fe1a9e41d8f4bf2b0af0ad6210b46bc9f1cc1128d991b0c6d53e4d9464"


def test_eip712_domain_replay_fields():
    partial = typed(encode_type=PERMIT, domain_name="Token", domain_version="2")
    assert partial["domain_type_string"] == "EIP712Domain(string name,string version)"
    assert partial["domain_separator"] == "0xb9774029dfa0a0e0825aebaaba549668297ddd64a52d31cab78a798a0d4981ca"
    assert set(ids(partial)) == {"EIP712-DOMAIN-NO-CHAIN-ID", "EIP712-DOMAIN-NO-CONTRACT"} and partial["verdict"] == "fail"
    assert severity_of(partial, "EIP712-DOMAIN-NO-CHAIN-ID") == "major" and severity_of(partial, "EIP712-DOMAIN-NO-CONTRACT") == "major"
    chain_only = typed(encode_type=PERMIT, domain_chain_id=5)
    assert chain_only["domain_separator"] == "0xac52226c2b00108de2d4018b89d5aec063d38a77d443f6ac34b7fc90580d1608" and ids(chain_only) == ["EIP712-DOMAIN-NO-CONTRACT"]
    zero = typed(encode_type=PERMIT, verifying_contract="0x" + "0" * 40, domain_chain_id=1)
    assert ids(zero) == ["EIP712-DOMAIN-ZERO-CONTRACT"] and zero["verdict"] == "fail"
    only_contract = typed(encode_type=PERMIT, verifying_contract="0x" + "00" * 20)
    assert only_contract["domain_separator"] == "0x27e52f026ec8d00b61942e6f2cb4b35d2e6a75e8c0db26bde0b4946d885d8e87"
    no_domain = typed(encode_type=PERMIT)
    assert (no_domain["domain_separator"], no_domain["domain_type_string"], no_domain["domain_separator_status"], no_domain["findings"]) == ("", "", "not_declared", [])


def test_eip712_declared_hashes_that_do_not_match_are_blockers():
    wrong = typed(encode_type=PERMIT, declared_type_hash="0x" + "00" * 32, domain_name="Token", domain_chain_id=1, verifying_contract=VC,
                  declared_domain_separator="0x" + "11" * 32)
    assert (wrong["type_hash_status"], wrong["domain_separator_status"]) == ("mismatch", "mismatch")
    assert set(ids(wrong)) == {"EIP712-TYPEHASH-MISMATCH", "EIP712-DOMAIN-MISMATCH"} and wrong["verdict"] == "fail"
    assert severity_of(wrong, "EIP712-TYPEHASH-MISMATCH") == "blocker"
    upper = typed(encode_type=PERMIT, declared_type_hash="0x6E71EDAE12B1B97F4D1F60370FEF10105FA2FAAE0126114A169C64845D6126C9")
    assert upper["type_hash_status"] == "match"


def test_eip712_referenced_structs_must_be_sorted_after_the_primary():
    unsorted = typed(encode_type="Mail(Person p,Zed z)Zed(uint256 a)Person(uint256 b)")
    assert ids(unsorted) == ["EIP712-TYPE-ORDER"] and severity_of(unsorted, "EIP712-TYPE-ORDER") == "blocker" and unsorted["verdict"] == "fail"
    assert unsorted["canonical_encode_type"] == "Mail(Person p,Zed z)Person(uint256 b)Zed(uint256 a)"
    assert unsorted["type_hash"] != unsorted["canonical_type_hash"]
    sorted_ok = typed(encode_type="Mail(Person p,Zed z)Person(uint256 b)Zed(uint256 a)")
    assert sorted_ok["findings"] == [] and sorted_ok["type_hash"] == sorted_ok["canonical_type_hash"]
    # the primary type stays first even when it sorts last
    assert typed(encode_type="Zed(Alpha a)Alpha(uint256 x)")["findings"] == []


def test_eip712_whitespace_and_closure():
    spaced = typed(encode_type="Permit(address owner, address spender)")
    assert ids(spaced) == ["EIP712-NON-CANONICAL-WHITESPACE"] and spaced["canonical_encode_type"] == "Permit(address owner,address spender)"
    assert spaced["type_hash"] != spaced["canonical_type_hash"]
    unreferenced = typed(encode_type="A(uint256 x)B(uint256 y)")
    assert ids(unreferenced) == ["EIP712-UNREFERENCED-TYPE"] and unreferenced["canonical_encode_type"] == "A(uint256 x)"
    undefined = typed(encode_type="A(Foo f)")
    assert ids(undefined) == ["EIP712-UNDEFINED-TYPE"] and severity_of(undefined, "EIP712-UNDEFINED-TYPE") == "blocker"
    arrays = typed(encode_type="A(B[] bs,uint256[3] xs,bytes32 h,string s)B(uint256 x)")
    assert arrays["findings"] == [] and arrays["verdict"] == "pass"
    chained = typed(encode_type="A(B b)B(C c)C(uint8 v)")
    assert chained["findings"] == []
    duplicate = typed(encode_type="A(uint256 x,uint256 x)")
    assert ids(duplicate) == ["EIP712-DUPLICATE-FIELD"] and severity_of(duplicate, "EIP712-DUPLICATE-FIELD") == "major"
    assert typed(encode_type="Empty()")["findings"] == []


@pytest.mark.parametrize("payload, field", [
    ({"encode_type": "Mail("}, "encode_type"),
    ({"encode_type": "Mail(address)"}, "encode_type"),
    ({"encode_type": "Mail(address a b)"}, "encode_type"),
    ({"encode_type": "Mail(address 1a)"}, "encode_type"),
    ({"encode_type": "A(uint256 x)A(uint256 y)"}, "encode_type"),
    ({"encode_type": "A(uint256 x) trailing"}, "encode_type"),
    ({"encode_type": PERMIT, "declared_type_hash": "0x1234" + "0" * 60 + "zz"}, "declared_type_hash"),
    ({"encode_type": PERMIT, "declared_domain_separator": "0x" + "11" * 32}, "declared_domain_separator"),
    ({"encode_type": PERMIT, "domain_name": "T", "declared_domain_separator": "0x" + "zz" * 32}, "declared_domain_separator"),
    ({"encode_type": PERMIT, "verifying_contract": "0x" + "zz" * 20}, "verifying_contract"),
    ({"encode_type": PERMIT, "verifying_contract": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAEd"}, "verifying_contract"),
    ({"encode_type": PERMIT, "domain_salt": "0x" + "zz" * 32}, "domain_salt"),
])
def test_eip712_rejects_malformed_input_naming_the_field(payload, field):
    invalid("sol_eip712_typehash", payload, field)


# --- independent review regressions (every expected value below comes from the cited primary source, not from the handlers) ---

# EIP-4626 (eips.ethereum.org/EIPS/eip-4626): "All EIP-4626 tokenized Vaults MUST implement EIP-20's optional metadata extensions".
def test_erc4626_requires_the_erc20_metadata_functions_that_erc20_leaves_optional():
    without = erc("erc4626", abi_of(ERC20_FNS + ERC4626_OWN, ERC4626_EVENTS))
    assert without["verdict"] == "non_conformant" and without["missing"] == 3 and without["required_functions"] == 25
    for signature in ("name()", "symbol()", "decimals()"):
        assert has(without, "ERC-MISSING-FN", signature) and severity_of(without, "ERC-MISSING-FN") == "blocker"
    assert "ERC-OPTIONAL-FN-MISSING" not in ids(without)
    assert erc("erc4626", abi_of(ERC20_FNS + ERC20_METADATA + ERC4626_OWN, ERC4626_EVENTS))["verdict"] == "conformant"
    assert has(erc("erc20", abi_of(ERC20_FNS, ERC20_EVENTS)), "ERC-OPTIONAL-FN-MISSING", "name()")  # still optional for a plain ERC-20


# --- Solidity pragma ranges: oracle is a port of liblangutil/SemVerHandler.cpp (SemVerMatchExpression::matches) ---------------

_INF = 0xFFFFFFFF


def _solc_comparator(prefix, numbers, levels, version):
    numbers = numbers + [0] * (3 - len(numbers))
    if prefix == "~":
        return _solc_comparator(">=", numbers, levels, version) and _solc_comparator("<=", numbers, 2 if levels >= 2 else 1, version)
    if prefix == "^":
        return (_solc_comparator(">=", numbers, levels, version)
                and _solc_comparator("<=", numbers, 2 if numbers[0] == 0 and levels != 1 else 1, version))
    cmp = 0
    for i in range(levels):
        if cmp == 0 and numbers[i] != _INF:
            cmp = (version[i] > numbers[i]) - (version[i] < numbers[i])
    return {"=": cmp == 0, "<": cmp < 0, "<=": cmp <= 0, ">": cmp > 0, ">=": cmp >= 0}[prefix]


def _solc_matches(expression, version):
    import re
    for group in expression.split("||"):
        group = group.strip()
        hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", group)
        tokens = [">=" + hyphen.group(1), "<=" + hyphen.group(2)] if hyphen else [
            re.sub(r"\s+", "", t) for t in re.findall(r"(?:\^|~|<=|>=|<|>|=)?\s*[\dxX*][\w.*]*", group)]
        ok_group = True
        for token in tokens:
            m = re.fullmatch(r"(\^|~|<=|>=|<|>|=)?(.*)", token)
            parts = m.group(2).split(".")
            numbers = [_INF if p in ("x", "X", "*") else int(p) for p in parts]
            ok_group = ok_group and _solc_comparator(m.group(1) or "=", numbers, len(parts), version)
        if ok_group:
            return True
    return False


PRAGMA_EXPRESSIONS = [
    f"{op}{v}" for op in ("", "^", "~", ">=", "<=", ">", "<", "=")
    for v in ("0", "0.0", "0.4", "0.7.6", "0.8", "0.8.x", "0.8.20", "0.8.37", "0.9.0", "0.0.3", "1", "1.2.3", "0.x", "1.x", "0.8.*")
] + [">=0.8.0 <0.9.0", "^0.8.20 <0.8.30", ">0.8.4 <=0.8.22", "0.8.20 - 0.8.25", "0.7 - 0.8.0", ">=0.6.0 <0.7.0 || ^0.8.20", "^0.8.0 || ^0.7.0",
     ">= 0.8.0 < 0.9.0", "~0.8.5 >=0.8.7", "0.4.22 - 0.6"]


def test_pragma_ranges_admit_exactly_the_versions_the_solidity_compiler_admits():
    lint = tools_module()._lint()
    versions = [(0, m, p) for m in range(4, 10) for p in range(0, 40)] + [(1, 0, 0), (1, 2, 3), (1, 9, 9), (2, 0, 0)]
    wrong = []
    for expression in PRAGMA_EXPRESSIONS:
        groups = lint.parse_range(expression)
        wrong += [(expression, v) for v in versions if any(lo <= v < hi for lo, hi in groups) != _solc_matches(expression, v)]
    assert wrong == []


def test_pragma_caret_and_tilde_on_a_single_component_follow_the_compiler():
    lint = tools_module()._lint()
    assert lint.parse_range("^0") == [((0, 0, 0), (1, 0, 0))]  # levelsPresent == 1: the major is kept, not the minor
    assert lint.parse_range("^0.x") == [((0, 0, 0), (1, 0, 0))]
    assert lint.parse_range("~1.x") == [((1, 0, 0), (2, 0, 0))]
    assert lint.parse_range("^0.0.3") == [((0, 0, 3), (0, 1, 0))]  # not npm's <0.0.4
    assert lint.parse_range("^0.8.20") == [((0, 8, 20), (0, 9, 0))] and lint.parse_range("~0.8.5") == [((0, 8, 5), (0, 9, 0))]
    assert lint.parse_range("^1.2.3") == [((1, 2, 3), (2, 0, 0))] and lint.parse_range("~1.2.3") == [((1, 2, 3), (1, 3, 0))]


# --- bugs.json (github.com/ethereum/solidity docs/bugs.json): SOL-2026-3 is a miscompilation, 0.8.29 <= affected < 0.8.36 ----------


def _bug_hit(version, **flags):
    review = tools_module()._lint().pragma_findings(version, min_version=(0, 8, 0), floating_severity="minor", via_ir=False, optimizer=False, **flags)
    return [f for f in review["findings"] if f["id"] == "PRAGMA-KNOWN-BUG"]


@pytest.mark.parametrize("version, hit", [("0.8.28", False), ("0.8.29", True), ("0.8.35", True), ("0.8.36", False), ("0.8.37", False)])
def test_known_bug_sol_2026_3_applies_to_layout_at_builds_only(version, hit):
    found = _bug_hit(version, layout_at=True)
    assert bool(found) == hit
    if hit:
        assert found[0]["severity"] == "major" and "InheritanceOrderReversalOnStorageEndWarning (SOL-2026-3, medium, fixed in 0.8.36)" in found[0]["summary"]
    assert _bug_hit(version) == []  # no `layout at` specifier: not affected


def test_lint_reports_sol_2026_3_only_for_a_source_that_uses_layout_at():
    lint = tools_module()._lint()
    with_layout = "pragma solidity 0.8.30;\ncontract C layout at 0x1000 { uint256 x; }\n"
    without = "pragma solidity 0.8.30;\ncontract C { uint256 x; }\n"
    assert [f["id"] for f in lint.lint_source("a.sol", with_layout)] == ["SOL-PRAGMA-KNOWN-BUG"]
    assert lint.lint_source("a.sol", without) == []


def test_pragma_policy_never_claims_a_layout_at_bug_without_the_source():
    assert "InheritanceOrderReversalOnStorageEndWarning" not in json.dumps(pragma(["0.8.30"]))


# --- storage layout: gap names and contract/address types (OpenZeppelin Upgrades packages/core/src/storage/{gap,compare}.ts) -----


def test_storage_a_variable_that_merely_starts_with_gap_is_not_a_gap():
    # compare.ts / gap.ts: a gap is a t_array labelled `__gap` or `__gap_*`; `gapStart` is an ordinary variable
    old = [var("a", 0, 0, "t_uint256", 32), var("gapStart", 1, 0, "t_uint256", 32)]
    removed = layout(old, [old[0]])
    assert ids(removed) == ["STORAGE-REMOVED"] and severity_of(removed, "STORAGE-REMOVED") == "major"
    moved = layout(old, [old[0], var("gapStart", 2, 0, "t_uint256", 32)])
    assert ids(moved) == ["STORAGE-MOVED"] and moved["verdict"] == "incompatible"
    not_an_array = layout([old[0], var("__gap", 1, 0, "t_uint256", 32)], [old[0]])
    assert ids(not_an_array) == ["STORAGE-REMOVED"]
    suffixed = [old[0], var("__gap_v2", 1, 0, "t_array(t_uint256)10_storage", 320)]
    consumed = layout(suffixed, [old[0], var("b", 1, 0, "t_uint256", 32), var("__gap_v2", 2, 0, "t_array(t_uint256)9_storage", 288)])
    assert ids(consumed) == ["STORAGE-GAP-CONSUMED"] and consumed["verdict"] == "compatible"


def test_storage_address_and_contract_types_are_the_same_storage_but_address_payable_is_not():
    # compare.ts: `t_contract` <-> `t_address` is "equivalent to just addresses"; contract -> contract adds no layout error
    plain = [var("token", 0, 0, "t_address", 20)]
    assert ids(layout(plain, [var("token", 0, 0, "t_contract(IERC20)1234", 20)])) == []
    assert ids(layout([var("token", 0, 0, "t_contract(IERC20)1234", 20)], plain)) == []
    assert ids(layout([var("token", 0, 0, "t_contract(IERC20)1234", 20)], [var("token", 0, 0, "t_contract(IERC721)99", 20)])) == []
    nested = layout([var("m", 0, 0, "t_mapping(t_address,t_contract(IERC20)1)", 32)], [var("m", 0, 0, "t_mapping(t_address,t_address)", 32)])
    assert ids(nested) == [] and nested["verdict"] == "compatible"
    payable = layout(plain, [var("token", 0, 0, "t_address_payable", 20)])
    assert ids(payable) == ["STORAGE-TYPE-CHANGED"] and payable["verdict"] == "incompatible"


def test_storage_a_new_variable_of_another_contract_after_a_trailing_gap_shifts_nobody():
    base = [var("a", 0, 0, "t_uint256", 32, contract="Base"), var("__gap", 1, 0, "t_array(t_uint256)49_storage", 1568, contract="Base")]
    derived_first_variable = layout(base, base + [var("x", 50, 0, "t_uint256", 32, contract="Derived")])
    assert ids(derived_first_variable) == ["STORAGE-APPENDED"] and derived_first_variable["verdict"] == "compatible"
    same_contract = layout(base, base + [var("x", 50, 0, "t_uint256", 32, contract="Base")])
    assert ids(same_contract) == ["STORAGE-APPENDED-AFTER-GAP"] and same_contract["verdict"] == "incompatible"
    unnamed = layout([v | {"contract": ""} if False else {k: x for k, x in v.items() if k != "contract"} for v in base],
                     [{k: x for k, x in v.items() if k != "contract"} for v in base] + [var("x", 50, 0, "t_uint256", 32)])
    assert ids(unnamed) == ["STORAGE-APPENDED-AFTER-GAP"]


# --- access control: what is and is not a caller check ---------------------------------------------------------------------


def access_of(body="", modifiers="", extra=""):
    source = ("pragma solidity 0.8.28;\ncontract C { address owner; uint256 x; uint256 end; address keeper; mapping(address => bool) admins;\n"
              f"mapping(address => uint256) balances; {extra}\nfunction mint() external {modifiers} {{ {body} x = 1; }} }}")
    return ok("sol_access_matrix", {"source": source})


@pytest.mark.parametrize("body, modifiers, extra", [
    ("require(msg.sender != address(0));", "", ""),
    ("require(msg.sender != tx.origin);", "", ""),
    ("require(msg.sender == tx.origin);", "", ""),
    ("require(tx.origin == owner);", "", ""),
    ("require(msg.sender != owner);", "", ""),
    ("assert(msg.sender != owner);", "", ""),
    ("if (msg.sender == owner) { x = 2; }", "", ""),
    ("if (msg.sender != address(0)) revert();", "", ""),
    ("require(balances[msg.sender] > 0);", "", ""),
    ("require(!admins[msg.sender]);", "", ""),
    ("require(msg.sender == address(0));", "", ""),
    ("", "nonReentrant", ""),
    ("", "whenNotPaused", ""),
    ("", "onlyWhileOpen", "modifier onlyWhileOpen() { require(block.timestamp < end); _; }"),
    ("", "onlyAfter(5)", "modifier onlyAfter(uint256 t) { require(block.timestamp > t); _; }"),
    ("", "onlyProxy", ""),
    ("", "onlyInitializing", ""),
])
def test_access_matrix_does_not_count_checks_that_leave_the_writer_open(body, modifiers, extra):
    result = access_of(body, modifiers, extra)
    assert (result["guarded"], result["unprotected"]) == (0, 1), (body, modifiers)
    assert result["verdict"] == "fail" and severity_of(result, "ACCESS-UNPROTECTED-PRIVILEGED") == "blocker"  # `mint` is a privileged name


@pytest.mark.parametrize("body, modifiers, extra, role", [
    ("require(msg.sender == owner);", "", "", "msg.sender==owner"),
    ("require(owner == msg.sender, \"no\");", "", "", "msg.sender==owner"),
    ("require(msg.sender == owner());", "", "", "msg.sender==owner()"),
    ("require(msg.sender == address(this));", "", "", "msg.sender==address(this)"),
    ("require(_msgSender() == keeper);", "", "", "msg.sender==keeper"),
    ("require(x < 5 && msg.sender == owner);", "", "", "msg.sender==owner"),
    ("assert(msg.sender == owner);", "", "", "msg.sender==owner"),
    ("if (msg.sender != owner) revert();", "", "", "msg.sender==owner"),
    ("if (msg.sender != owner) { revert(); }", "", "", "msg.sender==owner"),
    ("if (owner != msg.sender) return;", "", "", "msg.sender==owner"),
    ("require(hasRole(ADMIN_ROLE, msg.sender));", "", "", "ADMIN_ROLE"),
    ("require(hasRole(keccak256(\"X\"), msg.sender));", "", "", "keccak256(\" \")"),  # string contents are blanked by the reader
    ("if (!hasRole(ADMIN_ROLE, _msgSender())) revert();", "", "", "ADMIN_ROLE"),
    ("require(admins[msg.sender]);", "", "", "admins[msg.sender]"),
    ("if (!admins[msg.sender]) revert();", "", "", "admins[msg.sender]"),
    ("require(isOwner(msg.sender));", "", "", "isOwner(msg.sender)"),
    ("_checkOwner();", "", "", "owner"),
    ("", "onlyOwner", "", "onlyOwner"),
    ("", "onlyRole(MINTER_ROLE)", "", "MINTER_ROLE"),
    ("", "onlyOwner", "modifier onlyOwner() { require(msg.sender == owner); _; }", "onlyOwner"),
    ("", "whenOwner", "modifier whenOwner() { require(msg.sender == owner); _; }", "whenOwner"),
])
def test_access_matrix_counts_real_caller_checks(body, modifiers, extra, role):
    result = access_of(body, modifiers, extra)
    assert (result["guarded"], result["unprotected"]) == (1, 0), (body, modifiers)
    assert [r["role"] for r in result["roles"]] == [role]


def test_access_matrix_guard_scan_is_linear_in_the_source_size():
    import time
    for body in ("require(" + "a" * 90000, "require(msg.sender == a || " * 3000, "require(hasRole(a, " * 5000):
        started = time.perf_counter()
        ok("sol_access_matrix", {"source": "pragma solidity 0.8.28;\ncontract C { function f() external { " + body[:99000] + " } }"})
        assert time.perf_counter() - started < 10, body[:20]  # was quadratic: tens of seconds, then the 30 s tool timeout


# --- bounded output: a source-derived name can be as long as the source ---------------------------------------------------


def test_source_tools_stay_small_when_names_are_huge():
    name = "A" * 40000
    source = "pragma solidity 0.8.28;\ncontract " + name + " {\n" + "\n".join(f"function mint{i}(address t) external {{ }}" for i in range(50)) + "\n}"
    for tool, extra in (("sol_access_matrix", {}), ("sol_natspec_coverage", {"min_coverage_pct": 100}), ("sol_upgrade_safety", {"pattern": "uups"}),
                        ("sol_event_coverage", {"min_coverage_pct": 100})):
        result = ok(tool, {"source": source, **extra})  # was a 2-4 MB result, withheld as PARTIAL_SUCCESS
        assert len(json.dumps(result)) < 80_000, tool
        assert all(len(f["summary"]) <= 360 for f in result["findings"]), tool
        assert name not in json.dumps(result)


# --- secrets: no input that could be a credential is ever echoed ---------------------------------------------------------------

CANARIES = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "private_key": "0x" + "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318",
    "bare_private_key": "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318",
    "mnemonic": "test test test test test test test test test test test junk",
    "url_password": "https://alice:hunter2hunter2@rpc.example.com/",
}
# optional fields that the registry examples leave out
EXTRA_FIELDS = {
    "sol_selector_check": {"expected_selectors": [{"signature": "transfer(address,uint256)", "selector": "0xa9059cbb"}], "other_functions": ["x()"]},
    "sol_erc_conformance": {"erc165_ids": ["0x01ffc9a7"]},
    "sol_eip712_typehash": {"declared_type_hash": "0x" + "00" * 32, "domain_name": "T", "domain_version": "1", "domain_chain_id": 1,
                            "verifying_contract": "0x" + "11" * 20, "domain_salt": "0x" + "00" * 32, "declared_domain_separator": "0x" + "00" * 32},
    "sol_deploy_plan_check": {"owner_address": "0x" + "11" * 20, "rpc_url": "https://rpc.example.org"},
    "sol_test_gate": {"min_file_line_pct": 90},
}


def _string_paths(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _string_paths(child, path + (key,))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _string_paths(child, path + (i,))
    elif isinstance(value, str):
        yield path


def _set_path(root, path, new):
    for step in path[:-1]:
        root = root[step]
    root[path[-1]] = new


def _leaked(canary, text):
    return canary in text or any(canary[i:i + 16] in text for i in range(0, len(canary) - 15, 8))


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_no_tool_echoes_a_secret_shaped_value_from_any_string_field(name):
    example = copy.deepcopy(next(t for t in lane_tools() if t["name"] == name)["examples"]["success"])
    example.update(EXTRA_FIELDS.get(name, {}))
    probes = 0
    for path in list(_string_paths(example)):
        for canary in CANARIES.values():
            for variant in (canary, f"x{canary}"):
                payload = copy.deepcopy(example)
                _set_path(payload, path, variant)
                result = call(name, payload)
                probes += 1
                assert not _leaked(canary, json.dumps(result)), (name, path, canary, result)
    assert probes >= 10


@pytest.mark.parametrize("rpc_url, rule, absent", [
    ("https://alice:hunter2hunter2@rpc.example.com/v1", "DEPLOY-RPC-CREDENTIAL", "hunter2hunter2"),
    ("https://rpc.example.com/v1?apikey=" + CANARIES["github"], "DEPLOY-RPC-CREDENTIAL", CANARIES["github"]),
    ("https://rpc.example.com/?" + CANARIES["github"], "DEPLOY-RPC-PATH-KEY", CANARIES["github"]),
    ("https://rpc.example.com/?token_" + CANARIES["aws"] + "=1", "DEPLOY-RPC-CREDENTIAL", CANARIES["aws"]),
    ("https://rpc.example.com/?v=" + CANARIES["bare_private_key"][:32], "DEPLOY-RPC-PATH-KEY", CANARIES["bare_private_key"][:20]),
    ("https://eth-mainnet.g.alchemy.com/v2/" + CANARIES["github"], "DEPLOY-RPC-PATH-KEY", CANARIES["github"]),
])
def test_deploy_plan_reports_the_location_of_a_rpc_credential_never_its_value(rpc_url, rule, absent):
    result = plan(rpc_url=rpc_url)
    assert has(result, rule) and result["verdict"] == "fail"
    assert absent not in json.dumps(result)


def test_deploy_plan_quotes_a_plain_query_parameter_name_but_only_the_position_of_anything_else():
    assert has(plan(rpc_url="https://rpc.example.com/?apikey=x"), "DEPLOY-RPC-CREDENTIAL", "query parameter 'apikey'")
    assert has(plan(rpc_url="https://rpc.example.com/?a=1&" + CANARIES["github"]), "DEPLOY-RPC-PATH-KEY", "query parameter #2 carries 40 characters")
    assert has(plan(rpc_url="https://rpc.example.com/?v=" + "0123456789" * 3), "DEPLOY-RPC-PATH-KEY", "query parameter 'v' carries 30 characters")
    assert "DEPLOY-RPC-PATH-KEY" not in ids(plan(rpc_url="https://rpc.example.com/?chain=mainnet&v=1234567890"))  # short values are not keys


def test_eip712_mismatch_findings_carry_no_hash_the_declared_value_stays_out_of_the_result():
    declared = "0x" + "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
    result = ok("sol_eip712_typehash", {"encode_type": PERMIT, "declared_type_hash": declared, "domain_name": "T", "domain_chain_id": 1,
                                        "verifying_contract": "0x" + "11" * 20, "declared_domain_separator": declared})
    assert severity_of(result, "EIP712-TYPEHASH-MISMATCH") == "blocker" and severity_of(result, "EIP712-DOMAIN-MISMATCH") == "blocker"
    assert declared[2:] not in json.dumps(result)
    assert result["type_hash_status"] == "mismatch" and result["domain_separator_status"] == "mismatch"
    assert result["type_hash"] == "0x6e71edae12b1b97f4d1f60370fef10105fa2faae0126114a169c64845d6126c9"  # EIP-2612 Permit type hash


def test_secret_shaped_text_in_a_solidity_source_never_reaches_a_finding():
    source = ("pragma solidity 0.8.28;\ncontract C {\n"
              f"    bytes32 constant KEY = {CANARIES['private_key']};\n    string constant TOKEN = \"{CANARIES['github']}\";\n"
              f"    // {CANARIES['aws']}\n"
              f"    function sweep_{CANARIES['bare_private_key']}() external {{ }}\n"
              f"    function mint() external {{ require(msg.sender == address(0)); }}\n}}")
    for tool, extra in (("sol_access_matrix", {}), ("sol_event_coverage", {"min_coverage_pct": 100}), ("sol_natspec_coverage", {"min_coverage_pct": 100}),
                        ("sol_upgrade_safety", {"pattern": "uups"})):
        dumped = json.dumps(ok(tool, {"source": source, **extra}))
        for canary in (CANARIES["private_key"][2:], CANARIES["github"], CANARIES["aws"]):
            assert not _leaked(canary, dumped), (tool, canary)


# --- deploy plan: chain table (chainid.network, checked) and the unknown-chain boundary --------------------------------------


@pytest.mark.parametrize("chain_id, name, kind", [
    (1, "Ethereum Mainnet", "mainnet"), (100, "Gnosis", "mainnet"), (324, "zkSync Era", "mainnet"), (59144, "Linea", "mainnet"),
    (534352, "Scroll", "mainnet"), (81457, "Blast", "mainnet"), (5000, "Mantle", "mainnet"), (42220, "Celo", "mainnet"),
    (97, "BNB Smart Chain Testnet", "testnet"), (43113, "Avalanche Fuji", "testnet"), (59141, "Linea Sepolia", "testnet"),
    (534351, "Scroll Sepolia", "testnet"), (300, "zkSync Sepolia", "testnet"), (10200, "Gnosis Chiado", "testnet"),
    (31337, "Hardhat/Anvil", "local"),
])
def test_deploy_plan_knows_the_listed_chains_and_their_class(chain_id, name, kind):
    right = plan(chain_id=chain_id, expected_chain_id=chain_id, network_kind=kind, human_approval=True, simulated=True)
    assert (right["network_name"], right["chain_known"]) == (name, True) and "DEPLOY-CHAIN-UNKNOWN" not in ids(right)
    wrong_kind = "testnet" if kind == "mainnet" else "mainnet"
    assert has(plan(chain_id=chain_id, expected_chain_id=chain_id, network_kind=wrong_kind), "DEPLOY-NETWORK-KIND-MISMATCH", f"({kind})")


# --- EIP-6780: the selfdestruct finding states what the chain does now ------------------------------------------------------


def test_upgrade_safety_selfdestruct_text_names_eip_6780_and_stays_a_blocker():
    source = "pragma solidity 0.8.28;\ncontract C { function kill() external { selfdestruct(payable(msg.sender)); } }"
    finding = next(f for f in upgrade(source, "transparent")["findings"] if f["id"] == "UPG-SELFDESTRUCT")
    assert finding["severity"] == "blocker" and "EIP-6780" in finding["summary"] and "OpenZeppelin Upgrades plugin still rejects" in finding["summary"]


def test_eip712_domain_text_that_is_not_unicode_is_invalid_input_not_a_handler_crash():
    # a lone surrogate parses as JSON but has no UTF-8 encoding: EIP-712 hashes the UTF-8 bytes of name and version
    for field in ("domain_name", "domain_version"):
        invalid("sol_eip712_typehash", {"encode_type": PERMIT, field: "\ud800"}, field)
    unicode_name = ok("sol_eip712_typehash", {"encode_type": PERMIT, "domain_name": "T\u00f8ken \u4ee4\u724c", "domain_version": "1"})
    assert unicode_name["domain_type_string"] == "EIP712Domain(string name,string version)"


def test_redaction_of_a_long_unbroken_run_is_linear_and_normal_tokens_are_unchanged():
    import time
    tools = tools_module()
    started = time.perf_counter()
    clipped = tools._text("g" * 40000)
    assert time.perf_counter() - started < 2
    assert clipped == "g" * 72 + "..." and tools._text("g" * 40000 + "1") == "<redacted 40001 chars>"
    assert tools._text("a" * 200 + "1") == "<redacted 201 chars>"
    assert tools._text("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8") == "<redacted 40 chars>"
