"""Behaviour tests for the A27 Smart Contract Auditor tools (13 audit_* tools).

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
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A27", "a27-contract-audit", "audit_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "audit_classify_vulnerability",
    "audit_score_severity",
    "audit_reentrancy_order",
    "audit_oracle_risk",
    "audit_flashloan_profit",
    "audit_invariant_coverage",
    "audit_fuzz_adequacy",
    "audit_signature_replay",
    "audit_role_centralization",
    "audit_vault_inflation",
    "audit_dedupe_findings",
    "audit_report_completeness",
    "audit_fix_verification",
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


def sev(result: dict) -> dict:
    """finding id -> severity"""
    return {f["id"]: f["severity"] for f in result["findings"]}


# --- audit_classify_vulnerability -----------------------------------------------------------------------------
# One pinned row per embedded SWC table entry (SWC Registry identifiers and titles; severities are this agent's defaults).
SWC_ROWS = [
    ("reentrant-external-call", "SWC-107", "Reentrancy", "high"),
    ("tx-origin-auth", "SWC-115", "Authorization through tx.origin", "high"),
    ("unchecked-call-return", "SWC-104", "Unchecked Call Return Value", "medium"),
    ("unprotected-selfdestruct", "SWC-106", "Unprotected SELFDESTRUCT Instruction", "critical"),
    ("delegatecall-untrusted", "SWC-112", "Delegatecall to Untrusted Callee", "critical"),
    ("timestamp-dependence", "SWC-116", "Block values as a proxy for time", "low"),
    ("weak-randomness", "SWC-120", "Weak Sources of Randomness from Chain Attributes", "high"),
    ("floating-pragma", "SWC-103", "Floating Pragma", "info"),
    ("outdated-compiler", "SWC-102", "Outdated Compiler Version", "low"),
    ("default-visibility", "SWC-100", "Function Default Visibility", "high"),
    ("state-default-visibility", "SWC-108", "State Variable Default Visibility", "low"),
    ("unprotected-ether-withdrawal", "SWC-105", "Unprotected Ether Withdrawal", "critical"),
    ("signature-replay", "SWC-121", "Missing Protection against Signature Replay Attacks", "high"),
    ("signature-malleability", "SWC-117", "Signature Malleability", "medium"),
    ("missing-signature-verification", "SWC-122", "Lack of Proper Signature Verification", "high"),
    ("unbounded-loop", "SWC-128", "DoS With Block Gas Limit", "medium"),
    ("failed-call-dos", "SWC-113", "DoS with Failed Call", "medium"),
    ("arbitrary-storage-write", "SWC-124", "Write to Arbitrary Storage Location", "critical"),
    ("integer-overflow", "SWC-101", "Integer Overflow and Underflow", "high"),
    ("deprecated-functions", "SWC-111", "Use of Deprecated Solidity Functions", "info"),
    ("assert-violation", "SWC-110", "Assert Violation", "low"),
    ("shadowed-state-variable", "SWC-119", "Shadowing State Variables", "low"),
    ("hardcoded-gas-call", "SWC-134", "Message call with hardcoded gas amount", "low"),
    ("transaction-order-dependence", "SWC-114", "Transaction Order Dependence", "medium"),
    ("private-data-onchain", "SWC-136", "Unencrypted Private Data On-Chain", "medium"),
    ("hash-collision-varlen", "SWC-133", "Hash Collisions With Multiple Variable Length Arguments", "medium"),
    ("unexpected-ether-balance", "SWC-132", "Unexpected Ether balance", "low"),
    ("uninitialized-storage-pointer", "SWC-109", "Uninitialized Storage Pointer", "high"),
    ("incorrect-constructor-name", "SWC-118", "Incorrect Constructor Name", "high"),
    ("insufficient-gas-griefing", "SWC-126", "Insufficient Gas Griefing", "low"),
]


@pytest.mark.parametrize("signal,swc,title,severity", SWC_ROWS)
def test_classify_pins_every_swc_row(signal, swc, title, severity):
    result = ok("audit_classify_vulnerability", {"observations": [{"id": "X", "signal": signal}]})
    assert result["findings"] == [{"id": "X", "severity": severity, "summary": f"{swc} {title}"}]
    assert result["swc_ids"] == [swc] and result["total"] == 1 and result["counts"][severity] == 1


def test_classify_signal_enum_is_exactly_the_pinned_table():
    enum = next(t for t in lane_tools() if t["name"] == "audit_classify_vulnerability")["input_schema"]["properties"]["observations"]["items"]["properties"]["signal"]["enum"]
    assert sorted(enum) == sorted(row[0] for row in SWC_ROWS)


def test_classify_aggregates_orders_and_judges():
    result = ok("audit_classify_vulnerability", {"observations": [
        {"id": "B", "signal": "floating-pragma"},
        {"id": "A", "signal": "reentrant-external-call", "location": "Vault.sol:88"},
        {"id": "C", "signal": "reentrant-external-call"},
        {"id": "D", "signal": "unchecked-call-return"},
    ]})
    assert result["counts"] == {"critical": 0, "high": 2, "medium": 1, "low": 0, "info": 1}
    assert result["swc_ids"] == ["SWC-103", "SWC-104", "SWC-107"]
    assert [f["id"] for f in result["findings"]] == ["A", "C", "D", "B"]
    assert result["findings"][0]["summary"] == "SWC-107 Reentrancy at Vault.sol:88"
    assert result["verdict"] == "fail"


@pytest.mark.parametrize("signal,verdict", [("floating-pragma", "pass"), ("assert-violation", "pass"), ("unbounded-loop", "warn"), ("tx-origin-auth", "fail")])
def test_classify_verdict_by_worst_severity(signal, verdict):
    assert ok("audit_classify_vulnerability", {"observations": [{"id": "X", "signal": signal}]})["verdict"] == verdict


def test_classify_rejects_duplicate_ids_and_unknown_signals():
    invalid("audit_classify_vulnerability", {"observations": [{"id": "X", "signal": "floating-pragma"}, {"id": "X", "signal": "tx-origin-auth"}]}, "observations")
    invalid("audit_classify_vulnerability", {"observations": [{"id": "X", "signal": "made-up"}]}, "observations")


# --- audit_score_severity -------------------------------------------------------------------------------------
LOSS = {"low": 0, "medium": 10_000, "high": 100_000}  # of a 1,000,000 TVL: 0%, 1%, 10%
LIKELIHOOD = {"low": ("privileged", 0), "medium": ("participant", 0), "high": ("permissionless", 0)}
OWASP = {  # (impact, likelihood) -> severity, OWASP Risk Rating Methodology
    ("low", "low"): "info", ("low", "medium"): "low", ("low", "high"): "medium",
    ("medium", "low"): "low", ("medium", "medium"): "medium", ("medium", "high"): "high",
    ("high", "low"): "medium", ("high", "medium"): "high", ("high", "high"): "critical",
}


def score(loss=0, privilege="permissionless", preconditions=0, irrecoverable=False, **extra):
    return ok("audit_score_severity", {"loss_usd": loss, "tvl_usd": 1_000_000, "attacker_privilege": privilege,
                                       "preconditions": preconditions, "irrecoverable": irrecoverable, **extra})


@pytest.mark.parametrize("cell", sorted(OWASP))
def test_score_matrix_cell(cell):
    impact, likelihood = cell
    privilege, preconditions = LIKELIHOOD[likelihood]
    result = score(LOSS[impact], privilege, preconditions)
    assert (result["impact"], result["likelihood"], result["severity"]) == (impact, likelihood, OWASP[cell])


def test_score_impact_thresholds_are_inclusive():
    assert score(9_999)["impact"] == "low" and score(10_000)["impact"] == "medium"
    assert score(99_999)["impact"] == "medium" and score(100_000)["impact"] == "high"
    assert score(100_000)["loss_fraction"] == 0.1


def test_score_precondition_penalty_steps():
    assert [score(100_000, "permissionless", n)["likelihood"] for n in (0, 1, 2, 3, 4, 20)] == ["high", "high", "medium", "medium", "low", "low"]
    assert score(100_000, "privileged", 20)["likelihood"] == "low"  # floors at low


def test_score_irrecoverable_bumps_impact_one_level_up_to_high():
    assert score(0, irrecoverable=True)["impact"] == "medium"
    assert score(10_000, irrecoverable=True)["impact"] == "high"
    assert score(100_000, irrecoverable=True)["impact"] == "high"


def test_score_override_needs_l3_only_when_the_proposal_differs():
    same = score(100_000, proposed_severity="critical")
    assert same["requires_l3_approval"] is False and sev(same) == {"SEV-COMPUTED": "critical"} and same["verdict"] == "fail"  # a computed critical is a finding, not a pass
    other = score(100_000, proposed_severity="high")
    assert other["requires_l3_approval"] is True and other["severity"] == "critical"
    assert sev(other) == {"SEV-OVERRIDE": "medium", "SEV-COMPUTED": "critical"} and other["verdict"] == "fail"
    assert score(100_000)["requires_l3_approval"] is False


def test_a_computed_medium_high_or_critical_severity_is_a_finding_so_it_is_never_a_pass():
    assert sev(score(1_000_000)) == {"SEV-COMPUTED": "critical"} and score(1_000_000)["verdict"] == "fail"
    assert sev(score(10_000)) == {"SEV-COMPUTED": "high"} and score(10_000)["verdict"] == "fail"
    assert sev(score(0)) == {"SEV-COMPUTED": "medium"} and score(0)["verdict"] == "warn"
    assert score(0, "privileged")["findings"] == [] and score(0, "privileged")["verdict"] == "pass"


def test_score_rejects_loss_above_tvl_but_accepts_equal():
    invalid("audit_score_severity", {"loss_usd": 1_000_001, "tvl_usd": 1_000_000, "attacker_privilege": "privileged", "preconditions": 0, "irrecoverable": False}, "loss_usd")
    assert score(1_000_000)["loss_fraction"] == 1.0


# --- audit_reentrancy_order -----------------------------------------------------------------------------------
def step(kind, target, trusted=None):
    return {"kind": kind, "target": target, **({} if trusted is None else {"trusted": trusted})}


def fn(name, steps, guarded=False):
    return {"name": name, "non_reentrant": guarded, "steps": steps}


def reent(*functions):
    return ok("audit_reentrancy_order", {"functions": list(functions)})


def test_reentrancy_stale_check_then_call_then_write_is_high():
    result = reent(fn("withdraw", [step("check", "balances"), step("interaction", "msg.sender.call"), step("effect", "balances")]))
    assert sev(result) == {"REENT-withdraw-balances": "high"}
    assert result["findings"][0]["summary"] == "withdraw: state balances checked before the external call and written after it (stale check) (msg.sender.call)"
    assert (result["functions_checked"], result["cei_compliant"], result["untrusted_interactions"], result["verdict"]) == (1, 0, 1, "fail")


def test_reentrancy_checks_effects_interactions_order_passes():
    result = reent(fn("withdraw", [step("check", "balances"), step("effect", "balances"), step("interaction", "msg.sender.call")]))
    assert result["findings"] == [] and result["cei_compliant"] == 1 and result["verdict"] == "pass"


def test_reentrancy_write_not_checked_before_the_call_is_medium():
    assert sev(reent(fn("f", [step("interaction", "token.transfer"), step("effect", "totalPaid")]))) == {"REENT-f-totalPaid": "medium"}


def test_reentrancy_check_after_the_call_is_not_a_stale_check():
    assert sev(reent(fn("f", [step("interaction", "x.call"), step("check", "v"), step("effect", "v")]))) == {"REENT-f-v": "medium"}


def test_reentrancy_guard_downgrades_the_same_function_finding_to_low():
    result = reent(fn("withdraw", [step("check", "balances"), step("interaction", "x.call"), step("effect", "balances")], guarded=True))
    assert sev(result) == {"REENT-withdraw-balances": "low"} and result["verdict"] == "pass"
    assert result["findings"][0]["summary"].endswith("; nonReentrant guard present")


def test_reentrancy_trusted_interactions_are_ignored_and_not_counted():
    result = reent(fn("f", [step("interaction", "weth.deposit", True), step("effect", "v")]))
    assert result["findings"] == [] and result["untrusted_interactions"] == 0 and result["cei_compliant"] == 1


def test_reentrancy_uses_the_checks_made_before_the_first_untrusted_call():
    result = reent(fn("f", [step("interaction", "weth.deposit", True), step("check", "b"), step("interaction", "x.call"), step("effect", "b")]))
    assert sev(result) == {"REENT-f-b": "high"} and result["untrusted_interactions"] == 1


def test_reentrancy_reports_each_written_state_once():
    result = reent(fn("f", [step("interaction", "x.call"), step("effect", "v"), step("effect", "v"), step("effect", "w")]))
    assert sev(result) == {"REENT-f-v": "medium", "REENT-f-w": "medium"}


def test_reentrancy_cross_function_reach_needs_an_unguarded_second_function():
    vulnerable = fn("withdraw", [step("check", "balances"), step("interaction", "x.call"), step("effect", "balances")])
    reader = fn("transfer", [step("check", "balances"), step("effect", "balances")])
    result = reent(vulnerable, reader)
    assert sev(result) == {"REENT-X-withdraw-balances": "high", "REENT-withdraw-balances": "high"}
    assert "unguarded transfer" in next(f for f in result["findings"] if f["id"].startswith("REENT-X"))["summary"]
    # an unguarded caller never takes the lock, so a guarded second function is still enterable: see the review regressions below
    assert sev(reent(vulnerable, {**reader, "non_reentrant": True})) == {"REENT-X-withdraw-balances": "high", "REENT-withdraw-balances": "high"}
    # a guarded caller is still reachable through an unguarded function that touches the same state
    guarded_result = reent({**vulnerable, "non_reentrant": True}, reader)
    assert sev(guarded_result) == {"REENT-X-withdraw-balances": "high", "REENT-withdraw-balances": "low"}
    assert reent(vulnerable, fn("other", [step("check", "owner")]))["findings"][0]["id"] == "REENT-withdraw-balances"


def test_reentrancy_rejects_duplicate_function_names():
    invalid("audit_reentrancy_order", {"functions": [fn("f", [step("check", "a")]), fn("f", [step("check", "b")])]}, "functions")


# --- audit_oracle_risk ----------------------------------------------------------------------------------------
def spot(**extra):
    base = {"oracle_type": "spot_amm", "pool_liquidity_usd": 2_000_000, "target_deviation_pct": 300, "extractable_profit_usd": 1_000_000,
            "fee_bps": 0, "flashloan_fee_bps": 0, "flashloan_liquidity_usd": 10_000_000}
    return ok("audit_oracle_risk", {**base, **extra})


def test_oracle_spot_closed_form_capital_and_zero_cost_without_fees():
    # R = 1,000,000 per side, +300% price => s = sqrt(4) = 2, push = R (s - 1) = 1,000,000; with no fee the round trip is free
    result = spot()
    assert (result["required_capital_usd"], result["attack_cost_usd"], result["net_gain_usd"]) == (1_000_000.0, 0.0, 1_000_000.0)
    assert result["modelled"] is True and result["feasible"] is True and result["profitable"] is True and result["risk"] == "critical"
    assert sev(result) == {"ORACLE-ATTACK": "critical", "ORACLE-SPOT": "high"} and result["verdict"] == "fail"


def test_oracle_spot_fee_is_part_of_the_exact_push_size():
    result = spot(target_deviation_pct=20, fee_bps=30)
    capital = Decimal(str(result["required_capital_usd"]))
    reserve, fee = Decimal(1_000_000), Decimal("0.003")
    price = (reserve + capital) * (reserve + (1 - fee) * capital) / reserve**2
    assert abs(price - Decimal("1.2")) < Decimal("0.000001")


def test_oracle_spot_profit_must_strictly_exceed_cost():
    # 1,000,000 borrowed at 5 bps costs exactly 500 USD
    equal = spot(flashloan_fee_bps=5, extractable_profit_usd=500)
    assert equal["attack_cost_usd"] == 500.0 and equal["profitable"] is False and equal["net_gain_usd"] == 0.0
    assert sev(equal) == {"ORACLE-SPOT": "high"} and equal["risk"] == "high"
    assert spot(flashloan_fee_bps=5, extractable_profit_usd=500.01)["profitable"] is True


def test_oracle_spot_default_flashloan_fee_is_5_bps_and_own_capital_is_not_borrowed():
    default_fee = ok("audit_oracle_risk", {"oracle_type": "spot_amm", "pool_liquidity_usd": 2_000_000, "target_deviation_pct": 300,
                                           "extractable_profit_usd": 0, "fee_bps": 0, "flashloan_liquidity_usd": 10_000_000})
    assert default_fee["attack_cost_usd"] == 500.0  # 1,000,000 borrowed x 5 bps (Aave V3 premium)
    result = ok("audit_oracle_risk", {"oracle_type": "spot_amm", "pool_liquidity_usd": 2_000_000, "target_deviation_pct": 300,
                                      "extractable_profit_usd": 0, "fee_bps": 0, "attacker_capital_usd": 400_000, "flashloan_liquidity_usd": 600_000})
    assert result["attack_cost_usd"] == 300.0  # 600,000 borrowed x 5 bps


def test_oracle_spot_feasibility_boundary_is_inclusive():
    assert spot(flashloan_liquidity_usd=1_000_000)["feasible"] is True
    result = spot(flashloan_liquidity_usd=999_999.99)
    assert result["feasible"] is False and result["risk"] == "high"
    assert spot(flashloan_liquidity_usd=400_000, attacker_capital_usd=600_000)["feasible"] is True


def twap(**extra):
    base = {"oracle_type": "twap", "pool_liquidity_usd": 2_000_000, "target_deviation_pct": 1, "extractable_profit_usd": 1_000_000,
            "twap_window_s": 3600, "hold_blocks": 1, "block_time_s": 12, "fee_bps": 0, "attacker_capital_usd": 10_000_000}
    return ok("audit_oracle_risk", {**base, **extra})


def test_oracle_twap_one_block_hold_scales_the_spot_shift_by_window_over_hold():
    # +1% on a 3600 s mean held for 12 s needs +300% spot: push 1,000,000, arbitrage loss per block 1,000,000 - 500,000
    result = twap()
    assert (result["required_capital_usd"], result["attack_cost_usd"], result["net_gain_usd"]) == (1_000_000.0, 500_000.0, 500_000.0)
    assert result["feasible"] is True and result["profitable"] is True and result["risk"] == "critical"
    assert sev(result) == {"ORACLE-ATTACK": "critical"}


def test_oracle_twap_multi_block_hold_pays_the_arbitrage_loss_every_block():
    # 3 blocks x 1.5 s = 4.5 s held, +1% needs +800% spot => s = 3, push 2,000,000, loss per block 2,000,000 - 2,000,000/3
    result = twap(block_time_s=1.5, hold_blocks=3, extractable_profit_usd=5_000_000)
    assert (result["required_capital_usd"], result["attack_cost_usd"]) == (2_000_000.0, 4_000_000.0)


def test_oracle_twap_hold_beyond_the_window_is_capped_at_the_window():
    # 1000 controlled blocks but only 300 fit the 3600 s window; the full +300% is needed for the whole window
    result = twap(target_deviation_pct=300, hold_blocks=1000)
    assert (result["required_capital_usd"], result["attack_cost_usd"]) == (1_000_000.0, 150_000_000.0)


def test_oracle_twap_window_floor_is_1800_seconds_inclusive():
    assert "ORACLE-TWAP-WINDOW" not in sev(twap(target_deviation_pct=2, twap_window_s=1800))
    short = twap(twap_window_s=1799)
    assert sev(short)["ORACLE-TWAP-WINDOW"] == "medium"


def test_oracle_twap_outcomes_by_feasibility_and_profit():
    assert twap(attacker_capital_usd=999_999)["risk"] == "medium" and twap(attacker_capital_usd=999_999)["verdict"] == "warn"
    assert sev(twap(attacker_capital_usd=999_999)) == {"ORACLE-ATTACK-CAPITAL": "medium"}
    unprofitable = twap(extractable_profit_usd=500_000)  # equal to the cost: not profitable
    assert unprofitable["profitable"] is False and sev(unprofitable) == {"ORACLE-ATTACK-UNPROFITABLE": "low"} and unprofitable["verdict"] == "pass"
    quiet = twap(attacker_capital_usd=0, extractable_profit_usd=0)
    assert quiet["findings"] == [] and quiet["risk"] == "none" and quiet["verdict"] == "pass"


def test_oracle_chainlink_hygiene_findings():
    good = ok("audit_oracle_risk", {"oracle_type": "chainlink", "staleness_check": True, "answer_bounds_check": True})
    assert good["findings"] == [] and good["modelled"] is False and good["risk"] == "none" and good["verdict"] == "pass"
    assert (good["required_capital_usd"], good["attack_cost_usd"], good["net_gain_usd"], good["feasible"], good["profitable"]) == (0.0, 0.0, 0.0, False, False)
    bad = ok("audit_oracle_risk", {"oracle_type": "chainlink", "staleness_check": False, "answer_bounds_check": False, "l2": True})
    assert sev(bad) == {"ORACLE-STALE": "high", "ORACLE-SEQUENCER": "high", "ORACLE-BOUNDS": "medium"} and bad["verdict"] == "fail"
    only_bounds = ok("audit_oracle_risk", {"oracle_type": "chainlink", "staleness_check": True, "answer_bounds_check": False})
    assert sev(only_bounds) == {"ORACLE-BOUNDS": "medium"} and only_bounds["verdict"] == "warn"
    l2_ok = ok("audit_oracle_risk", {"oracle_type": "chainlink", "staleness_check": True, "answer_bounds_check": True, "l2": True, "sequencer_uptime_check": True})
    assert l2_ok["findings"] == []


def test_oracle_admin_set_price_is_high():
    result = ok("audit_oracle_risk", {"oracle_type": "admin_set"})
    assert sev(result) == {"ORACLE-ADMIN": "high"} and result["risk"] == "high" and result["modelled"] is False


@pytest.mark.parametrize("payload,field", [
    ({"oracle_type": "spot_amm", "target_deviation_pct": 10, "extractable_profit_usd": 1}, "pool_liquidity_usd"),
    ({"oracle_type": "spot_amm", "pool_liquidity_usd": 1, "extractable_profit_usd": 1}, "target_deviation_pct"),
    ({"oracle_type": "spot_amm", "pool_liquidity_usd": 1, "target_deviation_pct": 10}, "extractable_profit_usd"),
    ({"oracle_type": "twap", "pool_liquidity_usd": 1, "target_deviation_pct": 10, "extractable_profit_usd": 1}, "twap_window_s"),
    ({"oracle_type": "chainlink", "answer_bounds_check": True}, "staleness_check"),
    ({"oracle_type": "chainlink", "staleness_check": True}, "answer_bounds_check"),
])
def test_oracle_requires_the_inputs_of_its_type(payload, field):
    invalid("audit_oracle_risk", payload, field)


# --- audit_flashloan_profit -----------------------------------------------------------------------------------
def flash(**extra):
    base = {"reserve_in": 1_000_000, "reserve_out": 1_000_000, "borrow_amount": 1_000_000, "fee_bps": 0, "flashloan_fee_bps": 5,
            "in_token_price_usd": 2, "exploit_gain_usd": 1100, "fixed_cost_usd": 100}
    return ok("audit_flashloan_profit", {**base, **extra})


def test_flashloan_exact_values_without_pool_fee():
    # out = 1e6 * 1e6 / 2e6 = 500,000; spot price 1 -> 4 (+300%); unwinding returns exactly the 1,000,000 borrowed;
    # flash fee 5 bps = 500 tokens = 1,000 USD, plus 100 USD fixed
    result = flash()
    assert result == {
        "state": "SUCCESS", "amount_out": 500000.0, "price_impact_pct": 300.0, "borrow_pct_of_reserve": 100.0, "round_trip_loss": 0.0,
        "flashloan_fee": 500.0, "total_cost_usd": 1100.0, "net_profit_usd": 0.0, "break_even_gain_usd": 1100.0, "profitable": False,
        "findings": [{"id": "FLASH-UNPROFITABLE", "severity": "info", "summary": "cost 1100.0 USD >= gain 1100.0 USD at this borrow size"}],
        "verdict": "pass",
    }


def test_flashloan_profit_must_strictly_exceed_cost():
    result = flash(exploit_gain_usd=1100.01)
    assert result["profitable"] is True and result["net_profit_usd"] == 0.01 and result["verdict"] == "fail"
    assert sev(result) == {"FLASH-PROFIT": "high"}


def test_flashloan_uniswap_v2_fee_values():
    # Uniswap V2 getAmountOut with 30 bps on 1,000,000 / 500: out = 499,500 * 500 / 1,499,500 ... independent Decimal derivation
    result = flash(reserve_out=500, borrow_amount=500_000, fee_bps=30, flashloan_fee_bps=5, in_token_price_usd=1, exploit_gain_usd=400_000, fixed_cost_usd=200)
    assert result["amount_out"] == 166.333 and result["price_impact_pct"] == 124.775 and result["borrow_pct_of_reserve"] == 50.0
    assert result["round_trip_loss"] == 2000.995989 and result["flashloan_fee"] == 250.0
    assert result["total_cost_usd"] == 2451.0 and result["net_profit_usd"] == 397549.0


def test_flashloan_zero_flash_fee_and_no_pool_fee_costs_only_the_fixed_part():
    result = flash(flashloan_fee_bps=0, fixed_cost_usd=0, exploit_gain_usd=0.5)
    assert result["total_cost_usd"] == 0.0 and result["profitable"] is True


# --- audit_invariant_coverage ---------------------------------------------------------------------------------
def inv(id, kind="bound", variables=(), functions=()):
    return {"id": id, "kind": kind, "variables": list(variables), "functions": list(functions)}


BASE_INV = {
    "state_variables": ["totalSupply", "balances", "owner", "DECIMALS"],
    "functions": [{"name": "deposit", "writes": ["totalSupply", "balances"]}, {"name": "setOwner", "writes": ["owner"]}, {"name": "view", "writes": []}],
    "invariants": [inv("I1", "conservation", ["totalSupply", "balances"], ["deposit"]), inv("I2", "access", ["owner"], ["setOwner"])],
    "holds_funds": True,
}


def test_invariant_full_coverage_ignores_never_written_variables_and_pure_functions():
    result = ok("audit_invariant_coverage", BASE_INV)
    assert result["mutable_variables"] == 3 and result["covered_variables"] == 3 and result["variable_coverage"] == 1.0
    assert result["state_changing_functions"] == 2 and result["covered_functions"] == 2 and result["function_coverage"] == 1.0
    assert result["uncovered_variables"] == [] and result["uncovered_functions"] == [] and result["findings"] == [] and result["verdict"] == "pass"


def test_invariant_uncovered_written_variable_and_function_are_medium():
    result = ok("audit_invariant_coverage", {**BASE_INV, "invariants": [inv("I1", "conservation", ["totalSupply"], ["deposit"])]})
    assert result["variable_coverage"] == 0.3333 and result["function_coverage"] == 0.5
    assert result["uncovered_variables"] == ["balances", "owner"] and result["uncovered_functions"] == ["setOwner"]
    assert sev(result) == {"INV-VAR-balances": "medium", "INV-VAR-owner": "medium", "INV-FN-setOwner": "medium"} and result["verdict"] == "warn"


def test_invariant_uncovered_function_writing_only_covered_state_is_low():
    result = ok("audit_invariant_coverage", {**BASE_INV, "invariants": [inv("I1", "conservation", ["totalSupply", "balances", "owner"], ["deposit"])]})
    assert sev(result) == {"INV-FN-setOwner": "low"} and result["verdict"] == "pass"


def test_invariant_solvency_kinds_are_required_when_funds_are_held():
    weak = {**BASE_INV, "invariants": [inv("I1", "bound", ["totalSupply", "balances"], ["deposit"]), inv("I2", "access", ["owner"], ["setOwner"])]}
    assert sev(ok("audit_invariant_coverage", weak)) == {"INV-SOLVENCY": "high"}
    assert ok("audit_invariant_coverage", {**weak, "holds_funds": False})["findings"] == []
    solvent = {**weak, "invariants": [inv("I1", "solvency", ["totalSupply", "balances"], ["deposit"]), inv("I2", "access", ["owner"], ["setOwner"])]}
    assert ok("audit_invariant_coverage", solvent)["findings"] == []


def test_invariant_without_writers_has_full_coverage():
    result = ok("audit_invariant_coverage", {"state_variables": ["x"], "functions": [{"name": "f", "writes": []}], "invariants": [], "holds_funds": False})
    assert result["variable_coverage"] == 1.0 and result["function_coverage"] == 1.0 and result["findings"] == []


@pytest.mark.parametrize("patch,field", [
    ({"state_variables": ["a", "a"]}, "state_variables"),
    ({"functions": [{"name": "f", "writes": ["ghost"]}]}, "functions"),
    ({"functions": [{"name": "f", "writes": []}, {"name": "f", "writes": []}]}, "functions"),
    ({"invariants": [inv("I1", variables=["ghost"])]}, "invariants"),
    ({"invariants": [inv("I1", functions=["ghost"])]}, "invariants"),
    ({"invariants": [inv("I1"), inv("I1")]}, "invariants"),
])
def test_invariant_rejects_inconsistent_references(patch, field):
    base = {"state_variables": ["a"], "functions": [{"name": "f", "writes": ["a"]}], "invariants": [], "holds_funds": False}
    invalid("audit_invariant_coverage", {**base, **patch}, field)


# --- audit_fuzz_adequacy --------------------------------------------------------------------------------------
GOOD_FUZZ = {"mode": "stateless", "runs": 10_000, "distinct_seeds": 3, "line_coverage_pct": 95, "branch_coverage_pct": 90, "functions_total": 10, "functions_exercised": 10}


def fuzz(**patch):
    return ok("audit_fuzz_adequacy", {**GOOD_FUZZ, **patch})


def test_fuzz_policy_minimums_pass_at_the_boundary():
    result = fuzz()
    assert result == {"state": "SUCCESS", "total_calls": 10_000, "checks_total": 5, "checks_passed": 5, "adequacy_score": 100.0, "findings": [], "verdict": "pass"}


@pytest.mark.parametrize("runs,expected", [(9_999, "medium"), (1_000, "medium"), (999, "high")])
def test_fuzz_stateless_run_thresholds(runs, expected):
    result = fuzz(runs=runs)
    assert sev(result) == {"FUZZ-RUNS": expected}
    assert result["adequacy_score"] == 80.0


@pytest.mark.parametrize("seeds,severity", [(3, None), (2, "low")])
def test_fuzz_seed_diversity(seeds, severity):
    assert sev(fuzz(distinct_seeds=seeds)) == ({"FUZZ-SEEDS": severity} if severity else {})


@pytest.mark.parametrize("value,expected", [(95, None), (94.99, "medium"), (80, "medium"), (79.99, "high")])
def test_fuzz_line_coverage_thresholds(value, expected):
    assert sev(fuzz(line_coverage_pct=value)) == ({"FUZZ-LINE": expected} if expected else {})


@pytest.mark.parametrize("value,expected", [(90, None), (89.99, "medium"), (75, "medium"), (74.99, "high")])
def test_fuzz_branch_coverage_thresholds(value, expected):
    assert sev(fuzz(branch_coverage_pct=value)) == ({"FUZZ-BRANCH": expected} if expected else {})


@pytest.mark.parametrize("exercised,expected", [(10, None), (9, "medium"), (8, "high")])
def test_fuzz_function_coverage_thresholds(exercised, expected):
    assert sev(fuzz(functions_exercised=exercised)) == ({"FUZZ-FUNCTIONS": expected} if expected else {})


def invariant(**patch):
    return ok("audit_fuzz_adequacy", {**GOOD_FUZZ, "mode": "invariant", "runs": 2000, "depth": 50, **patch})


def test_fuzz_invariant_depth_and_total_call_thresholds():
    assert invariant()["findings"] == [] and invariant()["total_calls"] == 100_000
    assert sev(invariant(depth=49, runs=2041)) == {"FUZZ-DEPTH": "medium"}  # 49 * 2041 = 100,009 calls
    assert sev(invariant(runs=1999)) == {"FUZZ-CALLS": "medium"}  # 99,950 calls
    assert sev(invariant(runs=200)) == {"FUZZ-CALLS": "medium"}  # 10,000 = a tenth of the minimum
    assert sev(invariant(runs=199)) == {"FUZZ-CALLS": "high"}
    assert sev(invariant(depth=0)) == {"FUZZ-CALLS": "high", "FUZZ-DEPTH": "medium"}


def test_fuzz_invariant_min_total_calls_override_and_revert_ratio():
    assert sev(invariant(runs=10, min_total_calls=500)) == {"FUZZ-FLOOR": "medium"}  # a caller-lowered floor is reported, not accepted silently
    assert sev(invariant(runs=9, min_total_calls=500)) == {"FUZZ-CALLS": "medium", "FUZZ-FLOOR": "medium"}
    assert invariant(revert_ratio_pct=50)["findings"] == []
    assert sev(invariant(revert_ratio_pct=50.01)) == {"FUZZ-REVERTS": "medium"}
    assert fuzz(revert_ratio_pct=99)["findings"] == []  # the revert ratio only applies to invariant campaigns


def test_fuzz_adequacy_score_counts_passed_checks():
    result = invariant(revert_ratio_pct=80, distinct_seeds=1)
    assert (result["checks_total"], result["checks_passed"], result["adequacy_score"]) == (7, 5, 71.43)
    assert result["verdict"] == "warn"


def test_fuzz_rejects_impossible_or_missing_inputs():
    invalid("audit_fuzz_adequacy", {**GOOD_FUZZ, "functions_exercised": 11}, "functions_exercised")
    invalid("audit_fuzz_adequacy", {**GOOD_FUZZ, "mode": "invariant"}, "depth")


# --- audit_signature_replay -----------------------------------------------------------------------------------
GOOD_SIG = {"scheme": "eip712_typed", "nonce_consumed": True, "chain_id_bound": True, "contract_bound": True, "deadline_in_message": True,
            "deadline_enforced": True, "zero_address_check": True, "malleability_protected": True, "domain_separator_cached": False,
            "chainid_recomputed": False, "multichain_deployment": False}


def sig(**patch):
    return ok("audit_signature_replay", {**GOOD_SIG, **patch})


def test_signature_hardened_eip712_flow_is_clean():
    assert sig() == {"state": "SUCCESS", "replay_protected": True, "finding_count": 0, "findings": [], "verdict": "pass"}


@pytest.mark.parametrize("patch,expected", [
    ({"nonce_consumed": False}, {"SIG-REPLAY": "high"}),
    ({"contract_bound": False}, {"SIG-CONTRACT": "high"}),
    ({"chain_id_bound": False}, {"SIG-CHAINID": "medium"}),
    ({"chain_id_bound": False, "multichain_deployment": True}, {"SIG-CHAINID": "high"}),
    ({"scheme": "raw_hash"}, {"SIG-SCHEME": "medium"}),
    ({"scheme": "eip191_personal"}, {"SIG-SCHEME": "low"}),
    ({"deadline_in_message": False, "deadline_enforced": False}, {"SIG-DEADLINE": "medium"}),
    ({"deadline_enforced": False}, {"SIG-DEADLINE-UNENFORCED": "high"}),
    ({"zero_address_check": False}, {"SIG-ZERO": "high"}),
    ({"malleability_protected": False}, {"SIG-MALLEABLE": "low"}),
    ({"malleability_protected": False, "nonce_consumed": False}, {"SIG-REPLAY": "high", "SIG-MALLEABLE": "medium"}),
    ({"domain_separator_cached": True}, {"SIG-FORK": "medium"}),
    ({"domain_separator_cached": True, "chainid_recomputed": True}, {}),
])
def test_signature_findings(patch, expected):
    result = sig(**patch)
    assert sev(result) == expected and result["finding_count"] == len(expected)


def test_signature_replay_protected_needs_nonce_chain_and_contract_binding():
    assert sig(nonce_consumed=False)["replay_protected"] is False
    assert sig(chain_id_bound=False)["replay_protected"] is False
    assert sig(contract_bound=False)["replay_protected"] is False
    assert sig(zero_address_check=False)["replay_protected"] is True  # separate finding, not a replay path
    assert sig(chain_id_bound=False)["verdict"] == "warn" and sig(zero_address_check=False)["verdict"] == "fail"


def test_signature_rejects_contradictory_inputs():
    invalid("audit_signature_replay", {**GOOD_SIG, "deadline_in_message": False, "deadline_enforced": True}, "deadline_enforced")
    invalid("audit_signature_replay", {**GOOD_SIG, "domain_separator_cached": False, "chainid_recomputed": True}, "chainid_recomputed")


# --- audit_role_centralization --------------------------------------------------------------------------------
def roles(*items):
    return ok("audit_role_centralization", {"roles": list(items)})


def role(name="owner", holder="eoa", powers=("upgrade_implementation",), **extra):
    return {"name": name, "holder": holder, "powers": list(powers), **extra}


POWER_ROWS = [
    ("drain_funds", "critical"), ("upgrade_implementation", "critical"), ("mint_unbounded", "critical"), ("freeze_user_funds", "high"),
    ("set_oracle", "high"), ("set_fee_uncapped", "high"), ("set_parameters_unbounded", "medium"), ("pause_deposits", "low"), ("set_fee_capped", "low"),
]


@pytest.mark.parametrize("power,severity", POWER_ROWS)
def test_role_power_base_severity_on_an_eoa(power, severity):
    result = roles(role(powers=[power]))
    assert sev(result) == {f"ROLE-owner-{power}": severity}


def test_role_power_enum_is_exactly_the_pinned_table():
    enum = next(t for t in lane_tools() if t["name"] == "audit_role_centralization")["input_schema"]["properties"]["roles"]["items"]["properties"]["powers"]["items"]["enum"]
    assert sorted(enum) == sorted(p for p, _ in POWER_ROWS)


def test_role_eoa_critical_power_fails_and_counts_exposure():
    result = roles(role(powers=["upgrade_implementation", "set_oracle"]))
    assert result["critical_exposures"] == 1 and result["highest_severity"] == "critical" and result["verdict"] == "fail"
    assert (result["roles_assessed"], result["powers_assessed"]) == (1, 2)


@pytest.mark.parametrize("threshold,signers,expected", [(3, 5, "high"), (2, 3, "critical"), (2, 5, "critical"), (3, 6, "critical"), (4, 7, "high"), (1, 5, "critical"), (5, 5, "high")])
def test_role_multisig_is_strong_from_3_of_5_with_a_majority(threshold, signers, expected):
    result = roles(role(holder="multisig", multisig_threshold=threshold, multisig_signers=signers))
    assert sev(result) == {"ROLE-owner-upgrade_implementation": expected}


@pytest.mark.parametrize("delay,expected", [(172_799, "critical"), (172_800, "high")])
def test_role_timelock_minimum_is_48_hours_inclusive(delay, expected):
    assert sev(roles(role(timelock_delay_s=delay))) == {"ROLE-owner-upgrade_implementation": expected}


def test_role_mitigations_stack_and_floor_at_info():
    strong = role(holder="multisig", multisig_threshold=3, multisig_signers=5, timelock_delay_s=172_800)
    assert sev(roles(strong)) == {"ROLE-owner-upgrade_implementation": "medium"}
    assert sev(roles({**strong, "powers": ["pause_deposits"]})) == {"ROLE-owner-pause_deposits": "info"}
    assert sev(roles(role(holder="governance", powers=["drain_funds"]))) == {"ROLE-owner-drain_funds": "high"}
    assert sev(roles(role(holder="governance", powers=["drain_funds"], timelock_delay_s=172_800))) == {"ROLE-owner-drain_funds": "medium"}
    assert sev(roles(role(holder="contract", powers=["set_oracle"])))["ROLE-owner-set_oracle"] == "high"


def test_role_timelock_bypass_voids_the_delay():
    result = roles(role(timelock_delay_s=172_800, bypasses_timelock=True))
    assert sev(result) == {"ROLE-owner-upgrade_implementation": "critical"}
    assert "timelock bypassable" in result["findings"][0]["summary"]
    assert sev(roles(role(bypasses_timelock=True))) == {"ROLE-owner-upgrade_implementation": "critical"}


def test_role_renounced_roles_carry_no_risk_and_findings_sort_by_severity():
    result = roles(role("admin", "renounced", ["drain_funds"]), role("fees", "eoa", ["set_fee_capped"]), role("ops", "eoa", ["set_oracle"]))
    assert [f["id"] for f in result["findings"]] == ["ROLE-ops-set_oracle", "ROLE-fees-set_fee_capped"]
    assert result["powers_assessed"] == 2 and result["roles_assessed"] == 3 and result["highest_severity"] == "high"
    assert roles(role("admin", "renounced", ["drain_funds"]))["highest_severity"] == "none"


def test_role_rejects_bad_multisig_and_duplicate_names():
    invalid("audit_role_centralization", {"roles": [role(holder="multisig")]}, "roles")
    invalid("audit_role_centralization", {"roles": [role(holder="multisig", multisig_threshold=3)]}, "roles")
    invalid("audit_role_centralization", {"roles": [role(holder="multisig", multisig_threshold=6, multisig_signers=5)]}, "roles")
    invalid("audit_role_centralization", {"roles": [role(), role()]}, "roles")


# --- audit_vault_inflation ------------------------------------------------------------------------------------
def vault(**patch):
    return ok("audit_vault_inflation", {"mode": "legacy", "attacker_deposit": 1, "donation": 10**18, "victim_deposit": 10**18, **patch})


def test_vault_legacy_first_depositor_attack_zeroes_the_victim():
    result = vault()
    assert (result["attacker_shares"], result["victim_shares"], result["victim_value"]) == (1, 0, 0)
    assert result["victim_loss"] == 10**18 and result["victim_loss_pct"] == 100.0 and result["attacker_profit"] == 10**18
    assert result["donation_to_zero_shares"] == 10**18 and result["attacker_cost_to_zero_shares"] == 10**18 + 1
    assert sev(result) == {"VAULT-ZERO-SHARES": "critical"} and result["verdict"] == "fail"


def test_vault_minimum_zeroing_donation_is_exact():
    needed = vault()["donation_to_zero_shares"]
    assert vault(donation=needed)["victim_shares"] == 0
    assert vault(donation=needed - 1)["victim_shares"] == 1


def test_vault_openzeppelin_offset_zero_halves_the_attack():
    result = vault(mode="openzeppelin_virtual")
    assert (result["attacker_shares"], result["victim_shares"], result["victim_value"]) == (1, 1, 666666666666666667)
    assert result["victim_loss"] == 333333333333333333 and result["victim_loss_pct"] == 33.333333
    assert result["attacker_profit"] == -333333333333333334 and result["donation_to_zero_shares"] == 1999999999999999999
    assert sev(result) == {"VAULT-INFLATION": "high"}
    assert vault(mode="openzeppelin_virtual", donation=1999999999999999999)["victim_shares"] == 0


def test_vault_decimals_offset_makes_the_attack_cost_the_attacker():
    result = vault(mode="openzeppelin_virtual", decimals_offset=6)
    assert (result["attacker_shares"], result["victim_shares"], result["victim_value"]) == (1_000_000, 1_999_999, 999999749999937500)
    assert result["victim_loss"] == 250000062500 and result["victim_loss_pct"] == 0.000025
    assert result["attacker_profit"] == -499999874999968751 and result["donation_to_zero_shares"] == 1999999999999999999999999
    assert sev(result) == {} and result["verdict"] == "pass"


def test_vault_loss_thresholds_are_one_percent_high_and_one_basis_point_medium():
    at_one_percent = vault(attacker_deposit=1, donation=2, victim_deposit=100)  # loses 1 of 100
    assert at_one_percent["victim_loss"] == 1 and sev(at_one_percent) == {"VAULT-INFLATION": "high"}
    below = vault(attacker_deposit=1, donation=2, victim_deposit=101)  # loses 1 of 101
    assert below["victim_loss"] == 1 and sev(below) == {"VAULT-ROUNDING": "medium"} and below["verdict"] == "warn"
    assert vault(attacker_deposit=1, donation=2, victim_deposit=99)["findings"] == []
    # 1 wei of 10**18 is under one basis point
    assert vault(attacker_deposit=1000, donation=1, victim_deposit=10**18)["findings"] == []


def test_vault_legacy_rounding_example_and_existing_vault_state():
    result = vault(attacker_deposit=1000, donation=1, victim_deposit=1500)
    assert (result["victim_shares"], result["victim_value"], result["victim_loss"]) == (1498, 1499, 1) and result["donation_to_zero_shares"] == 1499001
    existing = vault(mode="openzeppelin_virtual", initial_shares=1000, initial_assets=1000, attacker_deposit=1000, donation=0, victim_deposit=1500)
    assert (existing["attacker_shares"], existing["victim_shares"], existing["victim_loss"]) == (1000, 1500, 0) and existing["donation_to_zero_shares"] == 2999500


def test_vault_rejects_legacy_state_that_cannot_convert():
    invalid("audit_vault_inflation", {"mode": "legacy", "initial_shares": 10, "initial_assets": 0, "attacker_deposit": 1, "donation": 0, "victim_deposit": 1}, "initial_assets")


# --- audit_dedupe_findings ------------------------------------------------------------------------------------
def item(id, title="T", category="SWC-107", file="src/V.sol", severity="high", **extra):
    return {"id": id, "title": title, "category": category, "file": file, "severity": severity, **extra}


def dedupe(*items):
    return ok("audit_dedupe_findings", {"findings": list(items)})


def test_dedupe_groups_by_category_file_and_function_with_path_normalisation():
    result = dedupe(item("A", "x one", function="withdraw"), item("B", "completely different", file="./src/V.sol", function="Withdraw"),
                    item("C", "other", file="src\\V.sol", function="withdraw"), item("D", "other", file="src/W.sol", function="withdraw"))
    assert result["groups"] == [{"primary_id": "A", "duplicate_ids": ["B", "C"], "severity": "high", "severity_conflict": False}]
    assert (result["input_count"], result["unique_count"], result["duplicate_count"]) == (4, 2, 2) and result["verdict"] == "fail"  # four high inputs: the verdict is fail


def test_dedupe_root_cause_takes_precedence_over_function():
    split = dedupe(item("A", "aa", function="f", root_cause="missing guard"), item("B", "bb", function="f", root_cause="rounding"))
    assert split["groups"] == []
    joined = dedupe(item("A", "aa", function="f", root_cause="Missing Guard"), item("B", "bb", function="g", root_cause="missing guard"))
    assert [g["duplicate_ids"] for g in joined["groups"]] == [["B"]]


def test_dedupe_title_similarity_threshold_is_80_percent_inclusive():
    four_of_five = dedupe(item("A", "alpha beta gamma delta epsilon"), item("B", "Alpha beta gamma delta"))
    assert four_of_five["duplicate_count"] == 1  # 4 shared of 5 words = 0.8
    four_of_six = dedupe(item("A", "alpha beta gamma delta epsilon"), item("B", "alpha beta gamma delta zeta"))
    assert four_of_six["duplicate_count"] == 0  # 4 shared of 6 words
    assert dedupe(item("A", "!!!"), item("B", "???"))["duplicate_count"] == 0  # no tokens: never similar


def test_dedupe_requires_same_category_and_file():
    assert dedupe(item("A", "same title"), item("B", "same title", category="SWC-104"))["duplicate_count"] == 0
    assert dedupe(item("A", "same title"), item("B", "same title", file="src/W.sol"))["duplicate_count"] == 0
    assert dedupe(item("A", "same title"), item("B", "SAME title", category=" swc-107 "))["duplicate_count"] == 1


def test_dedupe_primary_is_the_highest_severity_then_lowest_id_and_conflicts_are_flagged():
    result = dedupe(item("C", "same title", severity="low"), item("B", "same title", severity="high"), item("A", "same title", severity="high"))
    assert result["groups"] == [{"primary_id": "A", "duplicate_ids": ["B", "C"], "severity": "high", "severity_conflict": True}]
    assert sev(result) == {"DEDUP-A": "medium", "INPUT-SEVERITY": "high"} and result["verdict"] == "fail"  # the verdict covers the highest input severity
    assert next(f for f in result["findings"] if f["id"] == "DEDUP-A")["summary"] == "duplicates of A disagree on severity (low, high); resolving needs L3 approval"


def test_dedupe_is_transitive():
    # A-B share the function, B-C have the same title only: one group of three
    result = dedupe(item("A", "x", function="f"), item("B", "y", function="f"), item("C", "y", function="g"))
    assert result["groups"] == [{"primary_id": "A", "duplicate_ids": ["B", "C"], "severity": "high", "severity_conflict": False}]


def test_dedupe_rejects_duplicate_ids():
    invalid("audit_dedupe_findings", {"findings": [item("A"), item("A")]}, "findings")


# --- audit_report_completeness --------------------------------------------------------------------------------
ALL_SECTIONS = ["executive_summary", "scope", "methodology", "severity_definitions", "findings_summary", "findings_detail", "disclaimer"]
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def rfind(id="A-1", severity="high", location=True, recommendation=True, poc=True, status="fixed"):
    return {"id": id, "severity": severity, "has_location": location, "has_recommendation": recommendation, "has_poc": poc, "fix_status": status}


def report(findings=(), sections=ALL_SECTIONS, stage="final", scope_commit=COMMIT):
    return ok("audit_report_completeness", {"stage": stage, "scope_commit": scope_commit, "sections": list(sections), "findings": list(findings)})


def test_report_complete_report_scores_100():
    result = report([rfind()])
    assert result == {"state": "SUCCESS", "sections_missing": [], "findings_total": 1, "findings_incomplete": 0, "poc_missing": 0,
                      "risk_acceptances_needed": 0, "completeness_pct": 100.0, "findings": [], "verdict": "pass"}


def test_report_missing_sections_are_listed_in_required_order_with_severities():
    result = report(sections=["scope", "appendix", "disclaimer"])
    assert result["sections_missing"] == ["executive_summary", "methodology", "severity_definitions", "findings_summary", "findings_detail"]
    assert sev(result) == {"REP-SECTION-findings_detail": "high", "REP-SECTION-executive_summary": "medium", "REP-SECTION-methodology": "medium",
                           "REP-SECTION-severity_definitions": "medium", "REP-SECTION-findings_summary": "medium"}
    assert result["completeness_pct"] == 28.57 and result["verdict"] == "fail"


def test_report_high_and_critical_findings_need_a_poc_but_lower_severities_do_not():
    result = report([rfind("H", "high", poc=False), rfind("C", "critical", poc=False), rfind("M", "medium", poc=False)])
    assert sev(result) == {"REP-C-poc": "high", "REP-H-poc": "high"} and result["poc_missing"] == 2 and result["findings_incomplete"] == 2


def test_report_location_and_recommendation_are_required_for_every_finding():
    result = report([rfind("L", "low", location=False, recommendation=False, poc=False)])
    assert sev(result) == {"REP-L-location": "medium", "REP-L-recommendation": "medium"} and result["findings_incomplete"] == 1


def test_report_acceptance_and_open_status_rules():
    result = report([rfind("A", status="acknowledged"), rfind("B", "critical", status="wontfix"), rfind("C", "medium", status="acknowledged")])
    assert result["risk_acceptances_needed"] == 2 and sev(result) == {"REP-A-acceptance": "medium", "REP-B-acceptance": "medium"}
    final = report([rfind("O", "critical", status="open")])
    assert sev(final) == {"REP-O-open": "critical"} and final["verdict"] == "fail"
    draft = report([rfind("O", "critical", status="open")], stage="draft")
    assert sev(draft) == {"REP-O-open": "medium"} and draft["verdict"] == "warn"  # an open critical is a finding in a draft too
    assert report([rfind("O", "medium", status="open")])["findings"] == []


def test_report_completeness_percentage_counts_sections_and_finding_checks():
    # 6 of 7 sections + (location, recommendation) of a high finding without PoC: 8 of 10 checks
    result = report([rfind(poc=False)], sections=ALL_SECTIONS[:-1])
    assert result["completeness_pct"] == 80.0


@pytest.mark.parametrize("commit,valid", [(COMMIT, True), ("0" * 64, True), ("A" * 40, True), ("0" * 39, False), ("0" * 41, False), ("g" * 40, False), ("", False), ("0" * 63, False)])
def test_report_scope_commit_must_be_a_full_object_id(commit, valid):
    result = call("audit_report_completeness", {"stage": "final", "scope_commit": commit, "sections": ALL_SECTIONS, "findings": []})
    assert (result["state"] == "SUCCESS") is valid
    if not valid:
        assert result["state"] == "INVALID_INPUT" and result["field"] == "scope_commit"


def test_report_rejects_duplicate_finding_ids():
    invalid("audit_report_completeness", {"stage": "final", "scope_commit": COMMIT, "sections": ALL_SECTIONS, "findings": [rfind("A"), rfind("A")]}, "findings")


# --- audit_fix_verification -----------------------------------------------------------------------------------
def ffind(id="A", severity="high", status="fixed", commit="abc1234", test=True, reaudited=True):
    return {"id": id, "severity": severity, "fix_status": status, "fix_commit": commit, "regression_test": test, "reaudited": reaudited}


def fixes(*items):
    return ok("audit_fix_verification", {"findings": list(items)})


def test_fix_fully_verified_fix_leaves_no_residual_risk():
    result = fixes(ffind())
    assert result == {"state": "SUCCESS", "verified_fixed": 1, "verified_mitigated": 0, "unverified": 0, "open": 0, "accepted": 0, "residual_score": 0.0,
                      "residual_pct": 0.0, "residual_level": "none", "release_ready": True, "findings": [], "verdict": "pass"}


@pytest.mark.parametrize("patch,reason", [
    ({"commit": ""}, "no fix commit"), ({"test": False}, "no regression test"), ({"reaudited": False}, "not re-audited"),
    ({"commit": "", "test": False, "reaudited": False}, "no fix commit, no regression test, not re-audited"),
])
def test_fix_needs_commit_test_and_reaudit_to_count_as_verified(patch, reason):
    result = fixes(ffind(**patch))
    assert (result["unverified"], result["verified_fixed"]) == (1, 0)
    assert result["findings"] == [{"id": "FIX-A", "severity": "high", "summary": f"A fixed but not verified: {reason}"}]
    assert result["verdict"] == "fail" and result["release_ready"] is False and result["residual_score"] == 4.0 and result["residual_pct"] == 50.0


def test_fix_residual_weights_and_factors():
    result = fixes(ffind("C", "critical", "open", ""), ffind("H", "high", "fixed"), ffind("M", "medium", "mitigated"), ffind("L", "low", "fixed", test=False))
    # 16 x 1 + 8 x 0 + 4 x 0.25 + 2 x 0.5 = 18 of total weight 30
    assert (result["residual_score"], result["residual_pct"], result["residual_level"]) == (18.0, 60.0, "critical")
    assert (result["verified_fixed"], result["verified_mitigated"], result["unverified"], result["open"], result["accepted"]) == (1, 1, 1, 1, 0)
    assert [f["id"] for f in result["findings"]] == ["FIX-C", "FIX-M", "FIX-L"] and result["release_ready"] is False


def test_fix_acknowledged_and_wontfix_need_l4_and_count_in_full():
    result = fixes(ffind("A", "high", "acknowledged", ""), ffind("B", "low", "wontfix", ""))
    assert result["accepted"] == 2 and result["residual_score"] == 10.0
    assert result["findings"][0]["summary"] == "A acknowledged: needs an L4 risk acceptance"


def test_fix_release_ready_is_false_only_for_unresolved_high_or_critical():
    medium = fixes(ffind("M", "medium", "open", ""))
    assert medium["verdict"] == "warn" and medium["release_ready"] is True and medium["residual_level"] == "medium"
    info = fixes(ffind("I", "info", "open", ""))
    assert info["verdict"] == "pass" and info["residual_score"] == 0.0 and info["residual_pct"] == 0.0 and info["residual_level"] == "info"


@pytest.mark.parametrize("commit,valid", [("abc1234", True), ("abc123", False), ("a" * 40, True), ("a" * 41, False), ("a" * 64, True), ("a" * 65, False), ("xyz1234", False), ("", True)])
def test_fix_commit_must_be_a_git_object_id_when_given(commit, valid):
    result = call("audit_fix_verification", {"findings": [ffind(commit=commit)]})
    assert (result["state"] == "SUCCESS") is valid
    if not valid:
        assert result["field"] in ("findings",)


def test_fix_rejects_duplicate_ids():
    invalid("audit_fix_verification", {"findings": [ffind("A"), ffind("A")]}, "findings")


# =====================================================================================================================
# Independent review regressions (A27 review). Expected values are re-derived independently of the handlers:
# exact Fraction arithmetic for the constant-product math, brute-force simulation of the ERC-4626 conversion functions.
# =====================================================================================================================
from fractions import Fraction  # noqa: E402


def _fr(value) -> Fraction:
    return Fraction(repr(value))


# --- reentrancy: a lock only protects a function that is entered while the lock is held -----------------------------
def test_reentrancy_guarded_second_function_is_still_reachable_from_an_unguarded_caller():
    # OpenZeppelin ReentrancyGuard: nonReentrant only rejects a call while the lock is already taken. An unguarded
    # `withdraw` never takes it, so the callback may enter a guarded `transfer` that touches the same balance.
    vulnerable = fn("withdraw", [step("check", "balances"), step("interaction", "x.call"), step("effect", "balances")])
    guarded_reader = fn("transfer", [step("check", "balances"), step("effect", "balances")], guarded=True)
    result = reent(vulnerable, guarded_reader)
    assert sev(result) == {"REENT-X-withdraw-balances": "high", "REENT-withdraw-balances": "high"}
    summary = next(f for f in result["findings"] if f["id"] == "REENT-X-withdraw-balances")["summary"]
    assert summary == "cross-function reentrancy: transfer (nonReentrant, but withdraw holds no lock) can read or write balances during withdraw"


def test_reentrancy_two_guarded_functions_share_one_lock_and_are_not_cross_reachable():
    vulnerable = fn("withdraw", [step("check", "balances"), step("interaction", "x.call"), step("effect", "balances")], guarded=True)
    guarded_reader = fn("transfer", [step("check", "balances"), step("effect", "balances")], guarded=True)
    assert sev(reent(vulnerable, guarded_reader)) == {"REENT-withdraw-balances": "low"}


def test_reentrancy_guarded_caller_is_reachable_only_through_unguarded_functions():
    vulnerable = fn("withdraw", [step("check", "balances"), step("interaction", "x.call"), step("effect", "balances")], guarded=True)
    reader = fn("transfer", [step("check", "balances")])
    result = reent(vulnerable, reader)
    assert sev(result) == {"REENT-X-withdraw-balances": "high", "REENT-withdraw-balances": "low"}
    assert next(f for f in result["findings"] if f["id"].startswith("REENT-X"))["summary"] == "cross-function reentrancy: unguarded transfer can read or write balances during withdraw"


# --- flash loan: exact for any reserve ratio (Decimal context precision used to divide by zero) -----------------------
def _flash_exact(reserve_in, reserve_out, borrow, fee_bps, flash_bps, price, gain, fixed):
    r_in, r_out, b = _fr(reserve_in), _fr(reserve_out), _fr(borrow)
    fee = _fr(fee_bps) / 10000
    out = b * (1 - fee) * r_out / (r_in + b * (1 - fee))
    after_in, after_out = r_in + b, r_out - out
    impact = (after_in / after_out) / (r_in / r_out) - 1
    unwind = out * (1 - fee) * after_in / (after_out + out * (1 - fee))
    loss = b - unwind
    flash_fee = b * _fr(flash_bps) / 10000
    cost = (loss + flash_fee) * _fr(price) + _fr(fixed)
    return out, impact * 100, loss, flash_fee, cost, _fr(gain) - cost


@pytest.mark.parametrize("reserve_in,reserve_out,borrow", [
    (1e-09, 1e-09, 1e24), (1e-09, 1, 1e24), (1e-09, 8.29587969904156e23, 5.279290585198566e23), (1, 1e24, 1e24), (1e24, 1e-09, 1e-09),
])
def test_flashloan_extreme_reserve_ratios_stay_exact(reserve_in, reserve_out, borrow):
    payload = {"reserve_in": reserve_in, "reserve_out": reserve_out, "borrow_amount": borrow, "fee_bps": 30, "flashloan_fee_bps": 5,
               "in_token_price_usd": 1, "exploit_gain_usd": 5, "fixed_cost_usd": 1}
    result = ok("audit_flashloan_profit", payload)
    out, impact_pct, loss, flash_fee, cost, net = _flash_exact(reserve_in, reserve_out, borrow, 30, 5, 1, 5, 1)
    assert result["amount_out"] == round(float(out), 6) and result["round_trip_loss"] == round(float(loss), 6)
    assert result["price_impact_pct"] == round(float(impact_pct), 4) and result["flashloan_fee"] == round(float(flash_fee), 6)
    assert result["total_cost_usd"] == round(float(cost), 2) and result["net_profit_usd"] == round(float(net), 2)


def test_flashloan_ratios_beyond_the_float_range_saturate_instead_of_failing():
    result = ok("audit_flashloan_profit", {"reserve_in": 5e-324, "reserve_out": 1e24, "borrow_amount": 1e24, "fee_bps": 30, "flashloan_fee_bps": 5,
                                            "in_token_price_usd": 1, "exploit_gain_usd": 5, "fixed_cost_usd": 1})
    assert result["price_impact_pct"] == 1.7976931348623157e308 and result["borrow_pct_of_reserve"] == 1.7976931348623157e308
    out = _flash_exact(5e-324, 1e24, 1e24, 30, 5, 1, 5, 1)[0]
    assert result["amount_out"] == round(float(out), 6) and result["profitable"] is False


@pytest.mark.parametrize("fee_bps,flash_bps", [(0, 0), (30, 5), (9999, 10000), (1, 9)])
def test_flashloan_matches_exact_rational_arithmetic(fee_bps, flash_bps):
    payload = {"reserve_in": 123456.789, "reserve_out": 98765.4321, "borrow_amount": 33333.3333, "fee_bps": fee_bps, "flashloan_fee_bps": flash_bps,
               "in_token_price_usd": 3000.5, "exploit_gain_usd": 777777.77, "fixed_cost_usd": 50}
    result = ok("audit_flashloan_profit", payload)
    out, impact_pct, loss, flash_fee, cost, net = _flash_exact(123456.789, 98765.4321, 33333.3333, fee_bps, flash_bps, 3000.5, 777777.77, 50)
    assert (result["amount_out"], result["round_trip_loss"], result["flashloan_fee"]) == (round(float(out), 6), round(float(loss), 6), round(float(flash_fee), 6))
    assert (result["price_impact_pct"], result["total_cost_usd"], result["net_profit_usd"]) == (round(float(impact_pct), 4), round(float(cost), 2), round(float(net), 2))
    assert result["break_even_gain_usd"] == result["total_cost_usd"]


# --- fuzz: the audit-policy override applies to stateless campaigns too ---------------------------------------------------
def test_fuzz_stateless_min_total_calls_override_replaces_the_run_minimum():
    assert sev(fuzz(runs=5_000)) == {"FUZZ-RUNS": "medium"}
    lowered = fuzz(runs=5_000, min_total_calls=5_000)
    assert sev(lowered) == {"FUZZ-FLOOR": "medium"} and lowered["checks_passed"] == 5 and lowered["verdict"] == "warn"  # a lowered floor is reported
    assert sev(fuzz(runs=20_000, min_total_calls=50_000)) == {"FUZZ-RUNS": "medium"}  # raised floor
    assert sev(fuzz(runs=4_999, min_total_calls=50_000)) == {"FUZZ-RUNS": "high"}  # under a tenth of the floor
    assert sev(fuzz(runs=5_000, min_total_calls=50_000)) == {"FUZZ-RUNS": "medium"}  # exactly a tenth


# --- fix verification: an L4 acceptance is required for high and critical findings only ----------------------------------
def test_fix_acceptance_message_names_l4_only_for_high_and_critical():
    result = fixes(ffind("H", "high", "acknowledged", ""), ffind("M", "medium", "wontfix", ""), ffind("L", "low", "acknowledged", ""))
    summaries = {f["id"]: f["summary"] for f in result["findings"]}
    assert summaries["FIX-H"] == "H acknowledged: needs an L4 risk acceptance"
    assert "L4" not in summaries["FIX-M"] and summaries["FIX-M"].startswith("M wontfix")
    assert "FIX-L" not in summaries or "L4" not in summaries["FIX-L"]
    assert result["accepted"] == 3


# --- oracle: Chainlink bounds text matches the current Chainlink documentation -----------------------------------------------
def test_oracle_chainlink_bounds_finding_does_not_claim_every_feed_clamps():
    result = ok("audit_oracle_risk", {"oracle_type": "chainlink", "staleness_check": True, "answer_bounds_check": False})
    text = next(f for f in result["findings"] if f["id"] == "ORACLE-BOUNDS")["summary"]
    assert "no longer used on most feeds" in text and "own sanity bound" in text


# --- report completeness: output never exceeds the registered findings cap -----------------------------------------------------
def test_report_with_the_maximum_number_of_failing_findings_is_truncated_not_rejected():
    items = [rfind(f"R{i:03d}", "critical", location=False, recommendation=False, poc=False, status="wontfix") for i in range(150)]
    result = report(items, sections=[], stage="final")
    assert result["findings_total"] == 150 and result["findings_incomplete"] == 150 and result["poc_missing"] == 150 and result["risk_acceptances_needed"] == 150
    assert len(result["findings"]) == 600 and result["verdict"] == "fail"
    last = result["findings"][-1]
    assert last["id"] == "REP-TRUNCATED" and last["severity"] == "info"
    assert last["summary"] == "8 lower-ranked findings omitted from this list (607 raised); every count above is exact"
    assert len(result["sections_missing"]) == 7 and result["completeness_pct"] == 0.0


# --- secret safety: no echoed free-text input may carry a secret ---------------------------------------------------------------
CANARIES = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "ghp": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "hex64": "ab12cd34" * 8,  # a raw private key; the 0x form is 66 characters and does not fit an id
    "mnemonic": "act add age ago aim air all and any arm art ask",  # 12 BIP-39 words, 47 characters
    "url": "https://u:hunter2secret@h.example/x",
}


def secret_for(kind: str, limit: int) -> str:
    """The canary of this kind, or a skip when the input field cannot hold it (the schema caps the length)."""
    value = CANARIES[kind]
    if len(value) > limit:
        pytest.skip(f"{kind} canary is {len(value)} characters, the field allows {limit}")
    return value


def _no_canary(result, canary_value):
    blob = json.dumps(result, ensure_ascii=False)
    pieces = [canary_value, "0x" + canary_value]
    if "@" in canary_value:
        pieces += ["hunter2secret", "u:hunter2secret"]
    if " " in canary_value:
        pieces += [" ".join(canary_value.split()[:8])]
    for piece in pieces:
        assert piece not in blob, (piece, blob[:300])


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_classify(kind):
    secret = secret_for(kind, 64)
    result = ok("audit_classify_vulnerability", {"observations": [{"id": secret, "signal": "tx-origin-auth", "location": f"src/A.sol:1 {secret}"}]})
    assert result["total"] == 1 and result["counts"]["high"] == 1
    _no_canary(result, secret)
    _no_canary(call("audit_classify_vulnerability", {"observations": [{"id": secret, "signal": "tx-origin-auth"}, {"id": secret, "signal": "tx-origin-auth"}]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_reentrancy(kind):
    secret = secret_for(kind, 40)
    steps = [step("check", secret), step("interaction", secret), step("effect", secret)]
    result = reent(fn("withdraw", steps), fn("transfer", [step("check", secret)]))
    assert len(result["findings"]) == 2
    _no_canary(result, secret)
    _no_canary(call("audit_reentrancy_order", {"functions": [fn(secret, steps), fn(secret, steps)]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_invariant_coverage(kind):
    secret = secret_for(kind, 47)
    body = {"state_variables": [secret, "b"], "functions": [{"name": secret, "writes": [secret, "b"]}], "invariants": [], "holds_funds": True}
    result = ok("audit_invariant_coverage", body)
    assert result["mutable_variables"] == 2 and len(result["findings"]) == 4
    _no_canary(result, secret)
    other = {"state_variables": ["b"], "functions": [{"name": "f", "writes": ["b"]}], "invariants": [], "holds_funds": False}
    _no_canary(call("audit_invariant_coverage", {**other, "functions": [{"name": "f", "writes": [secret]}]}), secret)
    _no_canary(call("audit_invariant_coverage", {**other, "invariants": [inv("I", "bound", [secret], [])]}), secret)
    _no_canary(call("audit_invariant_coverage", {**other, "invariants": [inv("I", "bound", [], [secret])]}), secret)
    _no_canary(call("audit_invariant_coverage", {**other, "invariants": [inv(secret, "bound"), inv(secret, "bound")]}), secret)
    _no_canary(call("audit_invariant_coverage", {**other, "state_variables": [secret, secret]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_role_centralization(kind):
    secret = secret_for(kind, 48)
    result = roles(role(name=secret, powers=["drain_funds"]))
    assert result["critical_exposures"] == 1
    _no_canary(result, secret)
    _no_canary(call("audit_role_centralization", {"roles": [role(name=secret, holder="multisig")]}), secret)
    _no_canary(call("audit_role_centralization", {"roles": [role(name=secret), role(name=secret)]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_dedupe(kind):
    secret = secret_for(kind, 61)
    result = dedupe(item(secret, "same title", function="f"), item("x-" + secret, "same title", function="f", severity="low"))
    assert result["duplicate_count"] == 1
    _no_canary(result, secret)
    _no_canary(call("audit_dedupe_findings", {"findings": [item(secret, "t"), item(secret, "t")]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_report_completeness(kind):
    secret = secret_for(kind, 64)
    result = report([rfind(secret, "high", poc=False, location=False, status="wontfix")], stage="final")
    assert result["findings_incomplete"] == 1
    _no_canary(result, secret)
    _no_canary(call("audit_report_completeness", {"stage": "final", "scope_commit": secret, "sections": ALL_SECTIONS, "findings": []}), secret)
    _no_canary(call("audit_report_completeness", {"stage": "final", "scope_commit": COMMIT, "sections": ALL_SECTIONS, "findings": [rfind(secret), rfind(secret)]}), secret)


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_secret_safety_fix_verification(kind):
    secret = secret_for(kind, 64)
    result = fixes(ffind(secret, "high", "open", ""))
    assert result["open"] == 1
    _no_canary(result, secret)
    _no_canary(call("audit_fix_verification", {"findings": [ffind(secret, "high", "fixed", secret)]}), secret)
    _no_canary(call("audit_fix_verification", {"findings": [ffind(secret), ffind(secret)]}), secret)


@pytest.mark.parametrize("name", [n for n in TOOL_NAMES if n not in {
    "audit_classify_vulnerability", "audit_reentrancy_order", "audit_invariant_coverage", "audit_role_centralization",
    "audit_dedupe_findings", "audit_report_completeness", "audit_fix_verification"}])
def test_secret_safety_tools_without_echoed_free_text_have_no_string_input_beyond_enums(name):
    """score_severity, oracle, flash loan, fuzz, signature and vault take numbers, booleans and enums only: nothing can be echoed."""
    props = next(t for t in lane_tools() if t["name"] == name)["input_schema"]["properties"]
    free = [key for key, spec in props.items() if key != "probe" and spec.get("type") == "string" and "enum" not in spec]
    assert free == []


# --- invariants: a solvency or conservation invariant that constrains no state variable is not one --------------------------------
def test_invariant_solvency_invariant_without_any_state_variable_does_not_count():
    vacuous = {**BASE_INV, "invariants": [inv("I1", "solvency", [], ["deposit"]), inv("I2", "bound", ["totalSupply", "balances"], ["deposit"]), inv("I3", "access", ["owner"], ["setOwner"])]}
    result = ok("audit_invariant_coverage", vacuous)
    assert sev(result) == {"INV-SOLVENCY": "high"} and result["verdict"] == "fail"
    assert result["findings"][0]["summary"] == "contract holds funds but no conservation or solvency invariant over a state variable exists"
    real = {**vacuous, "invariants": [inv("I1", "conservation", ["totalSupply"], []), *vacuous["invariants"][1:]]}
    assert ok("audit_invariant_coverage", real)["findings"] == []


# --- independent oracles for the share math and the constant-product push size --------------------------------------------------
class _Vault:
    """A line-by-line model of OpenZeppelin ERC4626 (virtual shares, rounding down) and of the legacy Solmate/OZ 4.x conversion."""

    def __init__(self, mode, offset, shares, assets):
        self.mode, self.offset, self.supply, self.total = mode, offset, shares, assets

    def to_shares(self, assets):
        if self.mode == "legacy":
            return assets if self.supply == 0 else assets * self.supply // self.total
        return assets * (self.supply + 10**self.offset) // (self.total + 1)

    def to_assets(self, shares):
        if self.mode == "legacy":
            return shares if self.supply == 0 else shares * self.total // self.supply
        return shares * (self.total + 1) // (self.supply + 10**self.offset)

    def deposit(self, assets):
        shares = self.to_shares(assets)
        self.supply, self.total = self.supply + shares, self.total + assets
        return shares

    def zeroes_victim(self, attacker, donation, victim):
        vault = _Vault(self.mode, self.offset, self.supply, self.total)
        vault.deposit(attacker)
        vault.total += donation
        return vault.to_shares(victim) == 0


@pytest.mark.parametrize("mode,offset,shares,assets,attacker,donation,victim", [
    ("legacy", 0, 0, 0, 1, 10**18, 10**18),
    ("legacy", 0, 0, 0, 1, 10**18 - 1, 10**18),
    ("legacy", 0, 0, 5, 3, 7, 11),
    ("legacy", 0, 12345, 54321, 10**6, 10**9, 10**9),
    ("openzeppelin_virtual", 0, 0, 0, 1, 10**18, 10**18),
    ("openzeppelin_virtual", 3, 0, 0, 1, 10**18, 10**18),
    ("openzeppelin_virtual", 6, 0, 0, 1, 10**18, 10**18),
    ("openzeppelin_virtual", 18, 0, 0, 1, 10**18, 10**18),
    ("openzeppelin_virtual", 6, 10**6, 10**6, 10**6, 0, 1500),
    ("openzeppelin_virtual", 2, 777, 31337, 99, 4242, 123456),
    ("openzeppelin_virtual", 0, 0, 0, 10**30, 10**30, 10**30),
])
def test_vault_matches_a_line_by_line_model_and_the_zeroing_donation_is_minimal(mode, offset, shares, assets, attacker, donation, victim):
    result = vault(mode=mode, decimals_offset=offset, initial_shares=shares, initial_assets=assets, attacker_deposit=attacker, donation=donation, victim_deposit=victim)
    model = _Vault(mode, offset, shares, assets)
    attacker_shares = model.deposit(attacker)
    model.total += donation
    victim_shares = model.deposit(victim)
    victim_value, attacker_value = model.to_assets(victim_shares), model.to_assets(attacker_shares)
    assert (result["attacker_shares"], result["victim_shares"], result["victim_value"]) == (attacker_shares, victim_shares, victim_value)
    assert (result["victim_loss"], result["attacker_profit"]) == (victim - victim_value, attacker_value - attacker - donation)
    needed = result["donation_to_zero_shares"]
    base = _Vault(mode, offset, shares, assets)
    assert base.zeroes_victim(attacker, needed, victim) is True
    assert needed == 0 or base.zeroes_victim(attacker, needed - 1, victim) is False  # one asset less and the victim still mints a share
    assert result["attacker_cost_to_zero_shares"] == attacker + needed


@pytest.mark.parametrize("pct,fee_bps", [(1, 0), (1, 30), (20, 30), (300, 0), (300, 30), (1000, 9999), (0.01, 100), (50, 5000)])
def test_oracle_spot_push_size_hits_the_target_price_in_exact_arithmetic(pct, fee_bps):
    liquidity = 4_000_000
    result = ok("audit_oracle_risk", {"oracle_type": "spot_amm", "pool_liquidity_usd": liquidity, "target_deviation_pct": pct, "extractable_profit_usd": 0,
                                       "fee_bps": fee_bps, "flashloan_liquidity_usd": 10**15})
    reserve, fee, target = Fraction(liquidity, 2), _fr(fee_bps) / 10000, _fr(pct) / 100

    def spot_price(push):  # quote reserve / base reserve after a swap of `push` quote tokens; the pool starts at price 1
        out = push * (1 - fee) * reserve / (reserve + push * (1 - fee))
        return (reserve + push) / (reserve - out)

    pushed = Fraction(repr(result["required_capital_usd"]))
    low, high = pushed - Fraction(1, 100), pushed + Fraction(1, 100)  # the output is rounded to cents
    assert spot_price(low) <= 1 + target <= spot_price(high)
    # cost: round trip through the same pool plus 5 bps on everything borrowed
    out = pushed * (1 - fee) * reserve / (reserve + pushed * (1 - fee))
    back = out * (1 - fee) * (reserve + pushed) / (reserve - out + out * (1 - fee))
    expected = pushed - back + pushed * Fraction(5, 10000)
    assert abs(Fraction(repr(result["attack_cost_usd"])) - expected) <= Fraction(2, 100)


def test_clean_redacts_secret_shapes_but_keeps_identifiers_readable():
    clean = importlib.import_module("swarm.tools.a27_tools")._clean
    for name in ("privateKey", "setApiKey", "REENT-withdraw-balances", "src/Vault.sol:88", "withdraw(uint256)", "DEFAULT_ADMIN_ROLE", "a" * 63, "ab12cd34" * 7 + "ab1"):
        assert clean(name) == name
    assert clean("0x" + "ab" * 32) == "[REDACTED]" and clean("key " + "AB" * 32 + " end") == "key [REDACTED] end"
    assert clean("act add age ago aim air all and any arm art ask") == "[REDACTED]"
    assert clean("act add age ago aim air all and any arm art") == "act add age ago aim air all and any arm art"  # 11 words: not a mnemonic
    assert clean("privateKey = 0x1234") == "privateKey = [REDACTED]" and clean("https://u:pw@h/x") == "https://[REDACTED]@h/x"


# --- audit_score_severity: a zero TVL cannot size a loss fraction ----------------------------------------------------------
def test_score_zero_tvl_is_invalid_input_not_a_crash():
    # loss 0 and TVL 0: the loss fraction is undefined, so the handler refuses it as an input error instead of dividing by zero
    invalid("audit_score_severity", {"loss_usd": 0, "tvl_usd": 0, "attacker_privilege": "permissionless", "preconditions": 0, "irrecoverable": False}, "tvl_usd")
