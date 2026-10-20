"""Behaviour tests for the A28 Blockchain Infrastructure Engineer tools: client diversity, node sizing, RPC failover, confirmation
depth, L2 withdrawal window, indexer lag, backfill plan, fee policy, stuck transactions, validator slashing risk, bridge monitor,
network guard and mempool privacy.

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
AGENT, SLUG, PREFIX = "A28", "a28-chain-infra", "chain_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "chain_client_diversity", "chain_node_sizing", "chain_rpc_failover_plan", "chain_confirmation_depth", "chain_l2_withdrawal_window",
    "chain_indexer_lag", "chain_backfill_plan", "chain_fee_policy", "chain_stuck_transactions", "chain_validator_slashing_risk",
    "chain_bridge_monitor", "chain_network_guard", "chain_mempool_privacy",
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


def ids(result: dict) -> dict:
    return {f["id"]: f["severity"] for f in result["findings"]}


# --- chain_client_diversity -----------------------------------------------------------------------------------


def diversity(*nodes: int, layer: str = "execution", names: list[str] | None = None) -> dict:
    names = names or [f"client{i}" for i in range(len(nodes))]
    return ok("chain_client_diversity", {"layer": layer, "clients": [{"name": n, "nodes": c} for n, c in zip(names, nodes)]})


def test_diversity_values_for_a_geth_majority():
    result = diversity(600, 250, 100, 50, names=["geth", "nethermind", "besu", "reth"])
    assert result["total_nodes"] == 1000 and result["active_clients"] == 4
    assert (result["largest_client"], result["largest_share_pct"], result["hhi"], result["effective_clients"]) == ("geth", 60.0, 4350.0, 2.3)
    assert result["shares"][1] == {"client": "nethermind", "nodes": 250, "share_pct": 25.0}
    assert ids(result) == {"majority:geth": "major"} and result["verdict"] == "fail"


def test_diversity_one_third_boundary_is_strictly_greater():
    even = diversity(33, 33, 33)
    assert even["findings"] == [] and even["verdict"] == "pass" and even["hhi"] == 3333.3 and even["effective_clients"] == 3.0
    over = diversity(34, 33, 33)
    assert ids(over) == {"stall-risk:client0": "minor"} and over["verdict"] == "warn"


def test_diversity_one_half_boundary_is_strictly_greater():
    half = diversity(50, 50)
    assert ids(half) == {"stall-risk:client0": "minor", "stall-risk:client1": "minor"}
    over = diversity(51, 49)
    assert ids(over) == {"majority:client0": "major", "stall-risk:client1": "minor"}


def test_diversity_two_thirds_boundary_is_inclusive():
    # consensus-specs phase0 process_justification_and_finalization: attesting balance * 3 >= total * 2 finalizes, so a client
    # holding exactly 2/3 can already finalize its own chain; the 1/3 client is not above 1/3 and stays clean
    assert ids(diversity(2, 1)) == {"supermajority:client0": "blocker"}
    assert ids(diversity(200, 100)) == {"supermajority:client0": "blocker"}
    assert ids(diversity(66, 34)) == {"majority:client0": "major", "stall-risk:client1": "minor"}
    assert ids(diversity(67, 33)) == {"supermajority:client0": "blocker"}
    assert ids(diversity(665, 335)) == {"majority:client0": "major", "stall-risk:client1": "minor"}


def test_diversity_single_client_and_zero_node_clients():
    result = diversity(5, 0, names=["lighthouse", "teku"], layer="consensus")
    assert (result["active_clients"], result["hhi"], result["effective_clients"]) == (1, 10000.0, 1.0)
    assert ids(result) == {"supermajority:lighthouse": "blocker"} and "consensus layer" in result["findings"][0]["summary"]


def test_diversity_shares_are_ordered_by_nodes_then_name():
    result = diversity(10, 30, 30, names=["b", "c", "a"])
    assert [s["client"] for s in result["shares"]] == ["a", "c", "b"]


def test_diversity_rejects_duplicate_blank_and_empty_clients():
    invalid("chain_client_diversity", {"layer": "execution", "clients": [{"name": "Geth", "nodes": 1}, {"name": "geth ", "nodes": 2}]}, "clients")
    invalid("chain_client_diversity", {"layer": "execution", "clients": [{"name": "  ", "nodes": 1}]}, "clients")
    invalid("chain_client_diversity", {"layer": "execution", "clients": [{"name": "geth", "nodes": 0}]}, "clients")


# --- chain_node_sizing ----------------------------------------------------------------------------------------

SIZING = {
    "node_type": "full", "sync_mode": "snap", "validating": False, "disk_kind": "nvme", "disk_used_gb": 1200, "disk_provisioned_gb": 4000,
    "growth_gb_per_day": 2, "horizon_days": 180, "memory_gb": 32, "cpu_cores": 8, "uplink_mbps": 100, "peak_iops": 8000, "provisioned_iops": 16000,
}


def sizing(**over) -> dict:
    return ok("chain_node_sizing", {**SIZING, **over})


def test_sizing_runway_values():
    assert sizing() == {
        "state": "SUCCESS", "projected_disk_gb": 1560.0, "required_disk_gb": 1950.0, "disk_fill_pct": 30.0, "days_until_80pct": 1000.0,
        "days_until_full": 1400.0, "iops_utilization_pct": 50.0, "verdict": "pass", "findings": [],
    }


def test_sizing_no_growth_gives_sentinel_runway():
    result = sizing(growth_gb_per_day=0, disk_used_gb=3199)
    assert (result["days_until_80pct"], result["days_until_full"]) == (-1.0, -1.0) and result["findings"] == []


def test_sizing_disk_fill_boundaries_at_80_and_90_percent():
    soft = sizing(growth_gb_per_day=0, disk_used_gb=3200)
    assert ids(soft) == {"disk-fill-high": "major"} and soft["days_until_80pct"] == 0.0
    assert ids(sizing(growth_gb_per_day=0, disk_used_gb=3599)) == {"disk-fill-high": "major"}
    hard = sizing(growth_gb_per_day=0, disk_used_gb=3600)
    assert ids(hard) == {"disk-nearly-full": "blocker"}
    full = sizing(growth_gb_per_day=1, disk_used_gb=4000)
    assert full["days_until_full"] == 0.0 and ids(full) == {"disk-nearly-full": "blocker"}


def test_sizing_horizon_boundary_and_required_volume():
    base = {"disk_used_gb": 1000, "disk_provisioned_gb": 2000, "growth_gb_per_day": 10}
    assert sizing(**base, horizon_days=60)["findings"] == []
    result = sizing(**base, horizon_days=61)
    assert ids(result) == {"disk-horizon": "major"} and "at least 2013 GB" in result["findings"][0]["summary"]
    assert (result["projected_disk_gb"], result["required_disk_gb"]) == (1610.0, 2012.5)


def test_sizing_below_floor_and_disk_class():
    assert ids(sizing(disk_provisioned_gb=1999, disk_used_gb=100, growth_gb_per_day=0)) == {"disk-below-floor": "minor"}
    assert ids(sizing(disk_kind="hdd")) == {"disk-hdd": "blocker"}
    assert ids(sizing(disk_kind="network_ssd")) == {"disk-network": "minor"}


def test_sizing_memory_cores_and_uplink_floors():
    assert ids(sizing(memory_gb=15.9)) == {"memory-below-min": "major"}
    assert sizing(memory_gb=16)["findings"] == []
    assert ids(sizing(cpu_cores=1)) == {"cores-below-min": "major"}
    assert ids(sizing(uplink_mbps=24.9)) == {"uplink-below-min": "major"}
    assert sizing(uplink_mbps=25)["findings"] == []


def test_sizing_validating_host_gets_the_higher_floors():
    assert ids(sizing(validating=True, memory_gb=63.9, cpu_cores=7)) == {"memory-below-validating": "minor", "cores-below-validating": "minor"}
    assert sizing(validating=True, memory_gb=64, cpu_cores=8)["findings"] == []
    assert ids(sizing(validating=True, memory_gb=8)) == {"memory-below-min": "major"}


def test_sizing_iops_headroom_boundary_is_strictly_greater_than_70_percent():
    assert sizing(peak_iops=7000, provisioned_iops=10000)["findings"] == []
    result = sizing(peak_iops=7001, provisioned_iops=10000)
    assert ids(result) == {"iops-saturation": "major"} and result["iops_utilization_pct"] == 70.01


def test_sizing_rejects_archive_snap_and_used_above_provisioned():
    invalid("chain_node_sizing", {**SIZING, "node_type": "archive", "sync_mode": "snap"}, "sync_mode")
    assert sizing(node_type="archive", sync_mode="full")["verdict"] == "pass"
    invalid("chain_node_sizing", {**SIZING, "disk_used_gb": 4000.5}, "disk_used_gb")


# --- chain_rpc_failover_plan ----------------------------------------------------------------------------------


def provider(name, p95=100, err=0.1, rps=100, lag=0, archive=True) -> dict:
    return {"name": name, "p95_latency_ms": p95, "error_rate_pct": err, "rate_limit_rps": rps, "lag_blocks": lag, "archive": archive}


def failover(providers, **over) -> dict:
    return ok("chain_rpc_failover_plan", {"providers": providers, "demand_rps": 100, "max_error_rate_pct": 1, "max_lag_blocks": 2, "need_archive": False, **over})


def test_failover_orders_by_p95_and_excludes_unhealthy_providers():
    result = failover([provider("alpha", 120, 0.2, 200), provider("beta", 90, 0.5, 150, 1, False), provider("gamma", 60, 4.0, 300)])
    assert result["order"] == ["beta", "alpha"] and result["primary"] == "beta" and result["hedge_after_ms"] == 90.0
    assert result["ineligible"] == [{"name": "gamma", "reason": "error-rate"}]
    assert (result["capacity_rps"], result["capacity_after_loss_rps"], result["primary_share_pct"]) == (350.0, 150.0, 100.0)
    assert result["meets_demand"] is True and result["survives_single_loss"] is True
    assert ids(result) == {"provider-excluded:gamma": "info"} and result["verdict"] == "pass"


def test_failover_error_rate_and_lag_boundaries_are_inclusive_limits():
    at_limit = failover([provider("a", err=1.0, lag=2), provider("b")])
    assert at_limit["eligible_count"] == 2
    over = failover([provider("a", err=1.01), provider("b", lag=3), provider("c")])
    assert over["order"] == ["c"] and over["ineligible"] == [{"name": "a", "reason": "error-rate"}, {"name": "b", "reason": "block-lag"}]


def test_failover_combined_exclusion_reasons_and_archive_requirement():
    result = failover([provider("a", err=5, lag=9, archive=False), provider("b", archive=False), provider("c")], need_archive=True)
    assert result["order"] == ["c"]
    assert result["ineligible"] == [{"name": "a", "reason": "error-rate+block-lag+no-archive"}, {"name": "b", "reason": "no-archive"}]


def test_failover_single_eligible_provider_has_no_hedge():
    result = failover([provider("a", 80.5), provider("b", err=9)])
    assert result["hedge_after_ms"] == -1.0 and ids(result)["single-provider"] == "major" and result["verdict"] == "fail"
    assert result["survives_single_loss"] is False


def test_failover_no_eligible_provider_is_a_blocker():
    result = failover([provider("a", err=9)])
    assert (result["primary"], result["order"], result["capacity_rps"], result["primary_share_pct"]) == ("", [], 0.0, 0.0)
    assert ids(result)["no-eligible-provider"] == "blocker" and result["meets_demand"] is False


def test_failover_hedge_load_makes_105_percent_the_capacity_boundary():
    pair = lambda rate: [provider("a", rps=rate / 2), provider("b", rps=rate / 2)]  # noqa: E731
    assert failover(pair(105))["meets_demand"] is True
    short = failover(pair(104.9))
    assert short["meets_demand"] is False and ids(short)["capacity-short"] == "major"


def test_failover_n_minus_1_uses_the_remaining_providers_need():
    result = failover([provider("a", rps=60), provider("b", 200, rps=45)])
    assert result["meets_demand"] is True and result["survives_single_loss"] is False and ids(result)["no-n-minus-1"] == "minor"
    three = failover([provider("a", rps=60), provider("b", rps=45), provider("c", rps=60)])
    assert three["survives_single_loss"] is True and three["capacity_after_loss_rps"] == 105.0


def test_failover_ties_break_by_error_rate_then_name_and_hedge_rounds_up():
    result = failover([provider("zed", 90.2, 0.3), provider("bee", 90.2, 0.3), provider("cat", 90.2, 0.1)])
    assert result["order"] == ["cat", "bee", "zed"] and result["hedge_after_ms"] == 91.0


def test_failover_rejects_duplicate_names():
    invalid("chain_rpc_failover_plan", {"providers": [provider("A"), provider("a")], "demand_rps": 1, "max_error_rate_pct": 1, "max_lag_blocks": 1, "need_archive": False}, "providers")


# --- chain_confirmation_depth ---------------------------------------------------------------------------------


def depth(model="probabilistic", value=1000, tolerance=1, q=0.1, reorg=0, block_time=12) -> dict:
    return ok("chain_confirmation_depth", {"finality_model": model, "value_at_risk_usd": value, "loss_tolerance_usd": tolerance, "block_time_s": block_time,
                                          "attacker_share": q, "observed_max_reorg_blocks": reorg})


# Nakamoto (2008) section 11 Table: z blocks AFTER the block that holds the transaction, P < 0.001 -> q=0.10 z=5, 0.15 z=8, 0.20 z=11,
# 0.25 z=15, 0.30 z=24, 0.35 z=41, 0.40 z=89, 0.45 z=340. The block that holds the transaction is the first confirmation: z + 1.
@pytest.mark.parametrize("share,after", [(0.1, 5), (0.15, 8), (0.2, 11), (0.25, 15), (0.3, 24), (0.35, 41), (0.4, 89), (0.45, 340)])
def test_nakamoto_confirmations_are_the_whitepaper_z_plus_the_block_holding_the_transaction(share, after):
    result = depth(q=share)
    assert result["required_confirmations"] == after + 1 and result["basis"] == "nakamoto" and result["block_tag"] == "latest"


def test_nakamoto_depth_wait_and_risk_ratio():
    result = depth(value=50000, tolerance=50, block_time=600)
    assert (result["required_confirmations"], result["wait_seconds"], result["risk_ratio"]) == (6, 3600.0, 1000.0)


def test_nakamoto_value_within_tolerance_needs_one_confirmation():
    assert depth(value=1, tolerance=1)["required_confirmations"] == 1
    assert depth(value=0, tolerance=1)["required_confirmations"] == 1
    assert depth(value=1, tolerance=1, reorg=3)["required_confirmations"] == 4  # a 3-block reorg still removes a 3-confirmation transaction
    assert depth(value=1000, tolerance=999.99)["required_confirmations"] == 2  # P(z=1) at q=0.1 is 0.2046 (whitepaper), under 0.99999


def test_observed_reorg_depth_can_dominate_the_attacker_model():
    result = depth(reorg=10)  # a 10-block reorg removes the 10 newest blocks: 11 confirmations survive it; the model asks 6
    assert result["required_confirmations"] == 11 and result["basis"] == "reorg-history" and ids(result) == {"reorg-history-dominates": "info"}
    assert "needs 11 confirmations, more than the attacker model (6)" in result["findings"][0]["summary"]
    for reorg in (4, 5):  # at most 5 blocks reorganised: the model's 6 confirmations already cover it
        equal = depth(reorg=reorg)
        assert equal["required_confirmations"] == 6 and equal["basis"] == "nakamoto" and equal["findings"] == []
    assert depth(reorg=6)["required_confirmations"] == 7


def test_nakamoto_rejects_a_share_with_no_depth_below_2000():
    invalid("chain_confirmation_depth", {"finality_model": "probabilistic", "value_at_risk_usd": 1e6, "loss_tolerance_usd": 1, "block_time_s": 12,
                                         "attacker_share": 0.49, "observed_max_reorg_blocks": 0}, "attacker_share")


def test_ethereum_pos_picks_the_tag_by_value_to_tolerance_ratio():
    low = depth("ethereum_pos", value=100, tolerance=100, reorg=2)
    assert (low["block_tag"], low["required_confirmations"], low["wait_seconds"], low["basis"]) == ("latest", 3, 36.0, "loss-within-tolerance")
    assert depth("ethereum_pos", value=100, tolerance=100)["required_confirmations"] == 1
    safe = depth("ethereum_pos", value=10000, tolerance=100)
    assert (safe["block_tag"], safe["required_confirmations"], safe["wait_seconds"], safe["risk_ratio"]) == ("safe", 64, 768.0, 100.0)
    assert depth("ethereum_pos", value=100.5, tolerance=100)["block_tag"] == "safe"
    final = depth("ethereum_pos", value=10001, tolerance=100)
    assert (final["block_tag"], final["required_confirmations"], final["wait_seconds"], final["basis"]) == ("finalized", 96, 1152.0, "economic-finality")


def test_instant_bft_needs_one_block_and_flags_an_observed_reorg():
    result = depth("instant_bft", block_time=6)
    assert (result["block_tag"], result["required_confirmations"], result["wait_seconds"], result["findings"]) == ("n/a", 1, 6.0, [])
    assert ids(depth("instant_bft", reorg=1)) == {"bft-reorg-observed": "blocker"}


# --- chain_l2_withdrawal_window -------------------------------------------------------------------------------

L2 = {"rollup_type": "optimistic", "initiated_at": "2026-10-01T00:00:00Z", "as_of": "2026-10-03T00:00:00Z", "batch_post_delay_min": 30,
      "proof_delay_hours": 1, "challenge_window_hours": 168, "l1_finality_min": 15, "max_wait_hours": 200}


def l2(**over) -> dict:
    return ok("chain_l2_withdrawal_window", {**L2, **over})


def test_l2_timeline_values():
    assert l2() == {"state": "SUCCESS", "withdrawable_at": "2026-10-08T01:45:00Z", "total_wait_hours": 169.75, "remaining_hours": 121.75, "ready": False,
                    "stage": "challenge-window", "verdict": "pass", "findings": []}


@pytest.mark.parametrize("as_of,stage,ready", [
    ("2026-10-01T00:29:59Z", "batch-posting", False), ("2026-10-01T00:30:00Z", "proof-pending", False), ("2026-10-01T01:29:59Z", "proof-pending", False),
    ("2026-10-01T01:30:00Z", "challenge-window", False), ("2026-10-08T01:29:59Z", "challenge-window", False), ("2026-10-08T01:30:00Z", "l1-finality", False),
    ("2026-10-08T01:44:59Z", "l1-finality", False), ("2026-10-08T01:45:00Z", "claimable", True), ("2026-10-20T00:00:00Z", "claimable", True),
])
def test_l2_stage_boundaries_are_inclusive_at_the_start_of_the_next_stage(as_of, stage, ready):
    result = l2(as_of=as_of)
    assert (result["stage"], result["ready"]) == (stage, ready)
    assert (result["remaining_hours"] == 0.0) is ready


def test_l2_validity_rollup_skips_the_challenge_stage_and_normalises_offsets():
    result = l2(rollup_type="validity", challenge_window_hours=0, initiated_at="2026-10-01T02:00:00+02:00", as_of="2026-10-01T00:00:00Z", proof_delay_hours=4)
    assert result["withdrawable_at"] == "2026-10-01T04:45:00Z" and result["total_wait_hours"] == 4.75 and result["stage"] == "batch-posting"
    assert result["findings"] == []
    skip = {"rollup_type": "validity", "challenge_window_hours": 0, "proof_delay_hours": 4}
    assert l2(**skip, as_of="2026-10-01T04:00:00Z")["stage"] == "proof-pending"
    assert l2(**skip, as_of="2026-10-01T04:30:00Z")["stage"] == "l1-finality"


def test_l2_challenge_window_findings_and_boundaries():
    assert ids(l2(challenge_window_hours=23.9)) == {"challenge-window-too-short": "major"}
    assert ids(l2(challenge_window_hours=24)) == {"challenge-window-below-7-days": "info"}
    assert ids(l2(challenge_window_hours=167.9)) == {"challenge-window-below-7-days": "info"}
    assert l2(challenge_window_hours=168)["findings"] == []
    assert ids(l2(rollup_type="validity", challenge_window_hours=24)) == {"validity-exit-delay": "info"}


def test_l2_wait_policy_boundary_is_strictly_greater():
    assert l2(max_wait_hours=169.75)["findings"] == []
    result = l2(max_wait_hours=169.7)
    assert ids(result) == {"wait-exceeds-policy": "minor"} and result["verdict"] == "warn"


def test_l2_rejects_bad_timestamps_order_and_zero_challenge_window():
    invalid("chain_l2_withdrawal_window", {**L2, "as_of": "2026-09-30T23:59:59Z"}, "as_of")
    invalid("chain_l2_withdrawal_window", {**L2, "challenge_window_hours": 0}, "challenge_window_hours")
    invalid("chain_l2_withdrawal_window", {**L2, "initiated_at": "2026-10-01T00:00:00"}, "initiated_at")
    invalid("chain_l2_withdrawal_window", {**L2, "initiated_at": "not-a-timestamp-at-all"}, "initiated_at")
    invalid("chain_l2_withdrawal_window", {**L2, "initiated_at": "9999-12-31T23:59:59Z", "as_of": "9999-12-31T23:59:59Z"}, "initiated_at")


# --- chain_indexer_lag ----------------------------------------------------------------------------------------


def sample(minute: int, head: int, indexed: int) -> dict:
    return {"observed_at": f"2026-10-20T10:{minute:02d}:00Z", "head_block": head, "indexed_block": indexed}


def lag(samples, block_time_s=12, slo=120) -> dict:
    return ok("chain_indexer_lag", {"samples": samples, "block_time_s": block_time_s, "freshness_slo_s": slo})


def test_indexer_lag_values_trend_and_eta():
    result = lag([sample(0, 1000, 900), sample(1, 1005, 960)])
    assert result == {"state": "SUCCESS", "lag_blocks": 45, "lag_seconds": 540.0, "head_rate_bps": 0.0833, "index_rate_bps": 1.0, "trend": "closing",
                      "eta_catch_up_s": 49.1, "within_slo": False, "verdict": "fail",
                      "findings": [{"id": "slo-breach-severe", "severity": "blocker", "summary": "indexed data is 540 s behind, more than 3x the 120 s freshness SLO"}]}


def test_indexer_slo_boundaries_at_1x_and_3x():
    at_slo = lag([sample(0, 1000, 990), sample(1, 1005, 995)])  # 10 blocks x 12 s = 120 s
    assert at_slo["within_slo"] is True and at_slo["findings"] == [] and at_slo["trend"] == "steady" and at_slo["eta_catch_up_s"] == -1.0
    assert ids(lag([sample(0, 1000, 989), sample(1, 1005, 994)])) == {"slo-breach": "major"}
    assert ids(lag([sample(0, 1000, 970), sample(1, 1005, 975)])) == {"slo-breach": "major"}  # 30 blocks = 360 s = 3x
    assert ids(lag([sample(0, 1000, 969), sample(1, 1005, 974)])) == {"slo-breach-severe": "blocker"}


def test_indexer_caught_up_has_zero_eta():
    result = lag([sample(0, 1000, 1000), sample(1, 1005, 1005)])
    assert (result["trend"], result["eta_catch_up_s"], result["lag_blocks"], result["verdict"]) == ("caught-up", 0.0, 0, "pass")


def test_indexer_stalled_while_the_head_moves():
    result = lag([sample(0, 1000, 990), sample(1, 1010, 990)], slo=1000)
    assert ids(result) == {"indexer-stalled": "blocker"} and result["trend"] == "growing" and result["index_rate_bps"] == 0.0


def test_indexer_growing_lag_severity_depends_on_the_slo():
    growing = [sample(0, 1000, 990), sample(1, 1010, 995)]
    assert ids(lag(growing, slo=1000)) == {"lag-growing": "minor"}
    assert ids(lag(growing, slo=60)) == {"lag-growing": "major", "slo-breach": "major"}


def test_indexer_head_stalled_and_rewound_indexer():
    assert ids(lag([sample(0, 1000, 1000), sample(1, 1000, 1000)])) == {"head-stalled": "major"}
    rewound = lag([sample(0, 1000, 990), sample(1, 1010, 980)], slo=1000)
    assert ids(rewound) == {"indexer-rewound": "info", "indexer-stalled": "blocker"} and rewound["index_rate_bps"] == -0.1667


def test_indexer_rates_use_first_and_last_sample():
    result = lag([sample(0, 1000, 900), sample(1, 1001, 905), sample(2, 1012, 960)])
    assert (result["head_rate_bps"], result["index_rate_bps"]) == (0.1, 0.5)


def test_indexer_rejects_unordered_ahead_and_regressing_samples():
    invalid("chain_indexer_lag", {"samples": [sample(1, 10, 9), sample(1, 11, 10)], "block_time_s": 12, "freshness_slo_s": 60}, "samples")
    invalid("chain_indexer_lag", {"samples": [sample(0, 10, 11), sample(1, 12, 12)], "block_time_s": 12, "freshness_slo_s": 60}, "samples")
    invalid("chain_indexer_lag", {"samples": [sample(0, 10, 9), sample(1, 9, 9)], "block_time_s": 12, "freshness_slo_s": 60}, "samples")
    invalid("chain_indexer_lag", {"samples": [{**sample(0, 1, 1), "observed_at": "2026-10-20T10:00:00"}, sample(1, 2, 2)], "block_time_s": 12, "freshness_slo_s": 60}, "samples")


# --- chain_backfill_plan --------------------------------------------------------------------------------------

BACKFILL = {"from_block": 18000000, "to_block": 18100000, "max_block_range": 10000, "max_logs_per_response": 10000, "est_logs_per_block": 0.5,
            "requests_per_chunk": 1, "workers": 8, "request_latency_ms": 200, "provider_rate_limit_rps": 25, "block_time_s": 12}


def backfill(**over) -> dict:
    return ok("chain_backfill_plan", {**BACKFILL, **over})


def test_backfill_values_for_a_rate_limited_range():
    assert backfill() == {
        "state": "SUCCESS", "blocks": 100001, "chunk_size_blocks": 10000, "chunks": 11, "requests": 11, "expected_logs_per_chunk": 5000.0, "effective_rps": 25.0,
        "limited_by": "rate-limit", "workers_needed": 5, "duration_s": 0.4, "catches_up": True, "time_to_head_s": 0.4, "bisect_depth": 14, "verdict": "pass",
        "findings": [{"id": "workers-exceed-rate-limit", "severity": "info", "summary": "8 workers exceed what the rate limit feeds: 5 saturate it and extra workers only trigger 429s"}],
    }


def test_backfill_chunk_is_limited_by_the_result_cap_with_a_2x_safety_factor():
    assert backfill(est_logs_per_block=5)["chunk_size_blocks"] == 1000
    assert backfill(est_logs_per_block=0)["chunk_size_blocks"] == 10000
    assert backfill(est_logs_per_block=5000)["chunk_size_blocks"] == 1
    assert backfill(est_logs_per_block=5000.1)["chunk_size_blocks"] == 1
    assert backfill(est_logs_per_block=0.001)["chunk_size_blocks"] == 10000
    assert backfill(max_block_range=500)["chunk_size_blocks"] == 500


def test_backfill_block_above_response_cap_is_a_blocker():
    result = backfill(est_logs_per_block=10000.5)
    assert ids(result) == {"block-exceeds-response-cap": "blocker", "workers-exceed-rate-limit": "info"} and result["chunk_size_blocks"] == 1
    assert result["bisect_depth"] == 0
    assert "block-exceeds-response-cap" not in ids(backfill(est_logs_per_block=10000))


def test_backfill_worker_limited_throughput_and_workers_needed():
    result = backfill(workers=2, request_latency_ms=500)
    assert (result["limited_by"], result["effective_rps"], result["workers_needed"]) == ("workers", 4.0, 13) and result["findings"] == []
    assert backfill(workers=5)["findings"] == []  # 5 workers x 5 rps saturate 25 rps exactly
    assert backfill(workers=5)["limited_by"] == "workers"


def test_backfill_requests_per_chunk_divide_the_block_throughput():
    result = backfill(requests_per_chunk=3, est_logs_per_block=5)
    assert (result["chunks"], result["requests"], result["effective_rps"]) == (101, 303, 25.0)
    assert result["duration_s"] == 12.0  # 100001 blocks / (25 rps / 3 requests x 1000 blocks)


def test_backfill_catch_up_needs_strictly_more_throughput_than_the_head():
    slow = {"max_block_range": 1, "workers": 1, "request_latency_ms": 1000, "provider_rate_limit_rps": 10, "from_block": 0, "to_block": 99}
    equal = backfill(**slow, block_time_s=1)
    assert (equal["effective_rps"], equal["catches_up"], equal["time_to_head_s"]) == (1.0, False, -1.0) and ids(equal) == {"never-catches-up": "blocker"}
    assert backfill(**slow, block_time_s=0.999)["catches_up"] is False
    faster = backfill(**slow, block_time_s=2)
    assert faster["catches_up"] is True and faster["time_to_head_s"] == 200.0 and faster["duration_s"] == 100.0


def test_backfill_single_block_and_bisect_depth():
    result = backfill(from_block=5, to_block=5)
    assert (result["blocks"], result["chunks"]) == (1, 1)
    assert backfill(est_logs_per_block=5)["bisect_depth"] == 10  # (1000 - 1).bit_length()
    assert backfill(max_block_range=1)["bisect_depth"] == 0


def test_backfill_rejects_reversed_range():
    invalid("chain_backfill_plan", {**BACKFILL, "from_block": 10, "to_block": 9}, "to_block")


# --- chain_fee_policy -----------------------------------------------------------------------------------------

FEE = {"latest_base_fee_gwei": 100, "latest_gas_used": 15000000, "gas_limit": 30000000, "elasticity_multiplier": 2, "base_fee_change_denominator": 8,
       "horizon_blocks": 6, "assumed_fullness": 0.5, "priority_fee_gwei": 1.5, "priority_cap_gwei": 5, "max_fee_cap_gwei": 1000}


def fee(**over) -> dict:
    return ok("chain_fee_policy", {**FEE, **over})


def test_fee_policy_worst_case_after_five_full_blocks():
    result = fee()
    assert result == {"state": "SUCCESS", "next_base_fee_gwei": 100.0, "projected_base_fee_gwei": 100.0, "worst_case_base_fee_gwei": 180.203247069,
                      "priority_fee_gwei": 1.5, "max_fee_gwei": 181.703247069, "capped": False, "full_blocks_survived": 6, "effective_tip_next_gwei": 1.5,
                      "verdict": "pass", "findings": []}


def test_fee_policy_eip1559_update_rule():
    assert fee(latest_gas_used=30000000)["next_base_fee_gwei"] == 112.5  # full block: +12.5%
    assert fee(latest_gas_used=0)["next_base_fee_gwei"] == 87.5  # empty block: -12.5%
    assert fee(latest_gas_used=15000000)["next_base_fee_gwei"] == 100.0  # at the target: unchanged
    assert fee(latest_gas_used=22500000)["next_base_fee_gwei"] == 106.25  # halfway to full: +6.25%


def test_fee_policy_increase_is_at_least_one_wei_and_decrease_has_no_floor():
    assert fee(latest_base_fee_gwei=0.000000007, latest_gas_used=30000000)["next_base_fee_gwei"] == 0.000000008
    assert fee(latest_base_fee_gwei=0.000000007, latest_gas_used=0)["next_base_fee_gwei"] == 0.000000007


def test_fee_policy_optimism_style_parameters():
    result = fee(elasticity_multiplier=6, base_fee_change_denominator=250, latest_gas_used=30000000)
    assert result["next_base_fee_gwei"] == 102.0  # target 5M, full block: +2%
    assert fee(elasticity_multiplier=6, base_fee_change_denominator=250, latest_gas_used=5000000)["next_base_fee_gwei"] == 100.0


def test_fee_policy_projection_uses_the_assumed_fullness():
    result = fee(horizon_blocks=3, assumed_fullness=1)
    assert (result["projected_base_fee_gwei"], result["worst_case_base_fee_gwei"]) == (126.5625, 126.5625)
    low = fee(horizon_blocks=3, assumed_fullness=0)
    assert (low["projected_base_fee_gwei"], low["worst_case_base_fee_gwei"]) == (76.5625, 126.5625)
    assert fee(horizon_blocks=1, assumed_fullness=0)["worst_case_base_fee_gwei"] == 100.0


def test_fee_policy_cap_binds_and_reports_the_runway():
    result = fee(max_fee_cap_gwei=150)
    assert (result["max_fee_gwei"], result["capped"], result["full_blocks_survived"]) == (150.0, True, 4)
    assert ids(result) == {"max-fee-capped": "major"} and "covers the base fee for 4 consecutive full blocks, the horizon asks for 6" in result["findings"][0]["summary"]
    exact = fee(max_fee_cap_gwei=181.703247069)
    assert exact["capped"] is False and exact["findings"] == []


def test_fee_policy_cap_between_worst_base_fee_and_worst_base_fee_plus_tip_only_trims_the_tip():
    result = fee(max_fee_cap_gwei=181)  # worst-case base fee 180.203247069 fits the whole 6-block horizon, the 1.5 gwei tip does not
    assert (result["max_fee_gwei"], result["capped"], result["full_blocks_survived"], result["verdict"]) == (181.0, True, 6, "warn")
    assert ids(result) == {"max-fee-trims-tip": "minor"}
    assert "leaves only 0.796752931 gwei of the 1.5 gwei tip" in result["findings"][0]["summary"]
    edge = fee(max_fee_cap_gwei=180.203247069)  # equal to the worst-case base fee: still covered, tip fully trimmed
    assert ids(edge) == {"max-fee-trims-tip": "minor"} and edge["full_blocks_survived"] == 6
    assert ids(fee(max_fee_cap_gwei=180.203247068)) == {"max-fee-capped": "major"}  # one wei below: the 6th block is out of reach


def test_fee_policy_cap_below_next_base_fee_is_a_blocker():
    result = fee(max_fee_cap_gwei=90)
    assert ids(result) == {"cap-below-next-base-fee": "blocker"} and result["full_blocks_survived"] == 0 and result["effective_tip_next_gwei"] == 0.0
    tight = fee(max_fee_cap_gwei=100.5)
    assert tight["effective_tip_next_gwei"] == 0.5 and ids(tight) == {"max-fee-capped": "major"}


def test_fee_policy_priority_cap_and_rising_base_fee_notes():
    result = fee(priority_fee_gwei=10, latest_gas_used=15000001)
    assert result["priority_fee_gwei"] == 5.0 and ids(result) == {"priority-capped": "info", "base-fee-rising": "info"}


def test_fee_policy_rejects_inconsistent_inputs():
    invalid("chain_fee_policy", {**FEE, "priority_cap_gwei": 2000}, "priority_cap_gwei")
    invalid("chain_fee_policy", {**FEE, "latest_gas_used": 30000001}, "latest_gas_used")
    invalid("chain_fee_policy", {**FEE, "gas_limit": 3, "elasticity_multiplier": 4}, "gas_limit")


# --- chain_stuck_transactions ---------------------------------------------------------------------------------


def tx(nonce, fee=50, prio=2, seen="2026-10-20T11:59:00Z") -> dict:
    return {"nonce": nonce, "max_fee_gwei": fee, "max_priority_fee_gwei": prio, "first_seen_at": seen}


def stuck(accounts, base=30, after=30, as_of="2026-10-20T12:00:00Z") -> dict:
    return ok("chain_stuck_transactions", {"as_of": as_of, "base_fee_gwei": base, "stuck_after_minutes": after, "accounts": accounts})


def test_stuck_gap_underpriced_and_replacement_values():
    result = stuck([{"account": "Treasury-Hot", "confirmed_nonce": 7, "pending": [tx(7, 20, 1, "2026-10-20T11:00:00Z"), tx(9)]}])
    assert (result["accounts_checked"], result["gap_accounts"], result["missing_nonces"], result["queued_count"], result["stuck_count"]) == (1, 1, 1, 1, 1)
    assert result["replacements"] == [{"account": "treasury-hot", "nonce": 7, "min_max_fee_gwei": 31.1, "min_priority_fee_gwei": 1.1}]
    assert ids(result) == {"nonce-gap:treasury-hot": "major", "stuck:treasury-hot:7": "major"} and result["verdict"] == "fail"


def test_stuck_healthy_account_passes():
    result = stuck([{"account": "ops", "confirmed_nonce": 3, "pending": [tx(3), tx(4)]}, {"account": "idle", "confirmed_nonce": 9, "pending": []}])
    assert result["findings"] == [] and result["verdict"] == "pass" and result["replacements"] == []
    assert (result["gap_accounts"], result["missing_nonces"], result["queued_count"], result["stuck_count"]) == (0, 0, 0, 0)


def test_stuck_age_boundary_is_inclusive_and_aged_is_minor():
    fresh = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, seen="2026-10-20T11:30:01Z")]}])
    assert fresh["findings"] == [] and fresh["stuck_count"] == 0
    aged = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, seen="2026-10-20T11:30:00Z")]}])
    assert ids(aged) == {"stuck:a:0": "minor"} and aged["verdict"] == "warn" and aged["stuck_count"] == 1


def test_stuck_underpriced_boundary_is_strictly_below_the_base_fee():
    at_base = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=30)]}])
    assert at_base["findings"] == []
    below = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=29.999999999)]}])
    assert ids(below) == {"stuck:a:0": "major"}


def test_stuck_gap_ranges_and_queue_counts():
    result = stuck([{"account": "a", "confirmed_nonce": 5, "pending": [tx(5), tx(8), tx(9), tx(20)]}])
    assert (result["missing_nonces"], result["queued_count"], result["gap_accounts"]) == (12, 3, 1)
    assert "gaps 6-7,10-19" in result["findings"][0]["summary"] and "misses 12 nonce(s)" in result["findings"][0]["summary"]


def test_stuck_gap_starting_at_the_confirmed_nonce_leaves_nothing_executable():
    result = stuck([{"account": "a", "confirmed_nonce": 3, "pending": [tx(5, fee=1, prio=0.5, seen="2026-10-19T00:00:00Z")]}])
    assert (result["missing_nonces"], result["queued_count"], result["stuck_count"]) == (2, 1, 0)
    assert "gaps 3-4" in result["findings"][0]["summary"]


def test_stuck_many_gaps_are_summarised():
    pending = [tx(n) for n in (1, 3, 5, 7, 9, 11, 13)]
    result = stuck([{"account": "a", "confirmed_nonce": 0, "pending": pending}])
    assert "gaps 0,2,4,6,8,..." in result["findings"][0]["summary"] and result["missing_nonces"] == 7


def test_stuck_replacement_bump_is_ten_percent_rounded_up_and_covers_the_base_fee():
    one = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=20, prio=1.05)]}], base=10, after=1, as_of="2026-10-20T12:00:00Z")
    assert one["replacements"] == [{"account": "a", "nonce": 0, "min_max_fee_gwei": 22.0, "min_priority_fee_gwei": 1.155}]
    wei = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=0.000000005, prio=0.000000001)]}])
    assert wei["replacements"] == [{"account": "a", "nonce": 0, "min_max_fee_gwei": 30.000000002, "min_priority_fee_gwei": 0.000000002}]


def test_stuck_blocked_account_replacements_are_sorted_by_account_then_nonce():
    old = "2026-10-20T10:00:00Z"
    result = stuck([{"account": "b", "confirmed_nonce": 0, "pending": [tx(0, seen=old)]}, {"account": "a", "confirmed_nonce": 4, "pending": [tx(5, seen=old), tx(4, seen=old)]}])
    assert [(r["account"], r["nonce"]) for r in result["replacements"]] == [("a", 4), ("a", 5), ("b", 0)]


def test_stuck_rejects_inconsistent_accounts_without_echoing_secret_looking_labels():
    good = {"as_of": "2026-10-20T12:00:00Z", "base_fee_gwei": 30, "stuck_after_minutes": 30}
    invalid("chain_stuck_transactions", {**good, "accounts": [{"account": "a", "confirmed_nonce": 0, "pending": [tx(1), tx(1)]}]}, "accounts")
    invalid("chain_stuck_transactions", {**good, "accounts": [{"account": "a", "confirmed_nonce": 5, "pending": [tx(4)]}]}, "accounts")
    invalid("chain_stuck_transactions", {**good, "accounts": [{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, seen="2026-10-20T12:00:01Z")]}]}, "accounts")
    invalid("chain_stuck_transactions", {**good, "accounts": [{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=1, prio=2)]}]}, "accounts")
    invalid("chain_stuck_transactions", {**good, "accounts": [{"account": "A", "confirmed_nonce": 0, "pending": []}, {"account": "a", "confirmed_nonce": 0, "pending": []}]}, "accounts")
    invalid("chain_stuck_transactions", {**good, "as_of": "2026-10-20T12:00:00", "accounts": [{"account": "a", "confirmed_nonce": 0, "pending": []}]}, "as_of")
    secret = "0x" + "ab" * 32
    result = invalid("chain_stuck_transactions", {**good, "accounts": [{"account": secret, "confirmed_nonce": 0, "pending": []}]}, "accounts")
    assert secret not in json.dumps(result) and "ab" * 32 not in json.dumps(result)


# --- chain_validator_slashing_risk ----------------------------------------------------------------------------


def inst(key="val-1", host="h1", role="active", guard="local_db", doppel=True, wait=3) -> dict:
    return {"key_ref": key, "host": host, "role": role, "slashing_protection": guard, "doppelganger_check": doppel, "failover_wait_epochs": wait}


def slashing(*instances) -> dict:
    return ok("chain_validator_slashing_risk", {"instances": list(instances)})


def test_slashing_safe_active_standby_pair():
    result = slashing(inst(), inst(host="h2", role="standby", guard="interchange_imported"))
    assert (result["key_count"], result["instance_count"], result["double_sign_keys"], result["unprotected_count"], result["slashing_exposed"]) == (1, 2, [], 0, False)
    assert result["findings"] == [] and result["verdict"] == "pass"


def test_slashing_same_key_active_on_two_hosts_is_a_blocker():
    result = slashing(inst(), inst(host="h2", guard="shared_signer"), inst(key="val-2", host="h1"))
    assert result["double_sign_keys"] == ["val-1"] and result["slashing_exposed"] is True and result["key_count"] == 2
    assert ids(result) == {"double-sign:val-1": "blocker"} and "h1,h2" in result["findings"][0]["summary"]


def test_slashing_multi_active_behind_one_shared_signer_is_only_informational():
    result = slashing(inst(guard="shared_signer"), inst(host="h2", guard="shared_signer"))
    assert result["slashing_exposed"] is False and ids(result) == {"multi-active-shared-signer:val-1": "info"} and result["verdict"] == "pass"


def test_slashing_active_without_a_database_is_major():
    result = slashing(inst(guard="none"))
    assert ids(result) == {"no-slashing-db:val-1:h1": "major"} and result["unprotected_count"] == 1 and result["slashing_exposed"] is False


def test_slashing_standby_protection_levels():
    none = slashing(inst(), inst(host="h2", role="standby", guard="none"))
    assert ids(none) == {"standby-unprotected:val-1:h2": "major"} and none["unprotected_count"] == 1
    stale = slashing(inst(), inst(host="h2", role="standby", guard="local_db"))
    assert ids(stale) == {"standby-stale-history:val-1:h2": "major"} and "EIP-3076" in stale["findings"][0]["summary"]
    assert slashing(inst(), inst(host="h2", role="standby", guard="shared_signer", doppel=False, wait=0))["findings"] == []


def test_slashing_standby_doppelganger_and_wait_boundaries():
    assert slashing(inst(), inst(host="h2", role="standby", guard="interchange_imported", wait=2))["findings"] == []
    fast = slashing(inst(), inst(host="h2", role="standby", guard="interchange_imported", wait=1))
    assert ids(fast) == {"failover-too-fast:val-1:h2": "major"}
    nodop = slashing(inst(), inst(host="h2", role="standby", guard="interchange_imported", doppel=False))
    assert ids(nodop) == {"standby-no-doppelganger:val-1:h2": "minor"} and nodop["verdict"] == "warn"


def test_slashing_standby_only_key_has_no_active_instance():
    result = slashing(inst(role="standby", guard="interchange_imported"))
    assert ids(result) == {"no-active-instance:val-1": "info"}


def test_slashing_rejects_duplicates_and_key_material_without_echoing_it():
    invalid("chain_validator_slashing_risk", {"instances": [inst(), inst()]}, "instances")
    secret = "ab" * 32
    result = invalid("chain_validator_slashing_risk", {"instances": [inst(key=secret)]}, "instances")
    assert secret not in json.dumps(result)
    mnemonic = " ".join(["abandon"] * 11 + ["about"])
    result = invalid("chain_validator_slashing_risk", {"instances": [inst(host=mnemonic)]}, "instances")
    assert "abandon" not in json.dumps(result)
    pubkey = "0x" + "ab" * 48  # a 48-byte BLS public key is public data
    assert slashing(inst(key=pubkey))["key_count"] == 1


# --- chain_bridge_monitor -------------------------------------------------------------------------------------

BRIDGE = {"as_of": "2026-10-20T12:00:00Z", "max_lag_minutes": 30, "pause_overdue_ratio": 0.2, "paused": False, "locked_amount": "1000.5", "minted_amount": "900", "messages": []}


def bridge(**over) -> dict:
    return ok("chain_bridge_monitor", {**BRIDGE, **over})


def msg(mid, sent_min_ago, delivered_after_min=None) -> dict:
    from datetime import datetime, timedelta, timezone

    sent = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=sent_min_ago)
    out = {"id": mid, "sent_at": sent.isoformat().replace("+00:00", "Z")}
    if delivered_after_min is not None:
        out["delivered_at"] = (sent + timedelta(minutes=delivered_after_min)).isoformat().replace("+00:00", "Z")
    return out


def test_bridge_healthy_values():
    result = bridge(messages=[msg("m-1", 60, 10), msg("m-2", 5)])
    assert result == {"state": "SUCCESS", "message_count": 2, "delivered_count": 1, "pending_count": 1, "overdue_count": 0, "late_delivered_count": 0,
                      "p95_delivery_lag_min": 10.0, "max_pending_age_min": 5.0, "collateral_surplus": 100.5, "undercollateralized": False,
                      "pause_recommended": False, "verdict": "pass", "findings": []}


def test_bridge_empty_traffic_is_healthy():
    result = bridge()
    assert (result["message_count"], result["p95_delivery_lag_min"], result["max_pending_age_min"], result["findings"]) == (0, 0.0, 0.0, [])


def test_bridge_minted_above_locked_is_a_blocker_and_recommends_a_pause():
    result = bridge(locked_amount="1000", minted_amount="1000.1")
    assert (result["undercollateralized"], result["pause_recommended"], result["collateral_surplus"]) == (True, True, -0.1)
    assert ids(result) == {"undercollateralized": "blocker", "pause-recommended": "blocker"}
    equal = bridge(locked_amount="1000", minted_amount="1000")
    assert (equal["undercollateralized"], equal["collateral_surplus"], equal["findings"]) == (False, 0.0, [])


def test_bridge_already_paused_bridge_does_not_get_a_pause_finding():
    result = bridge(locked_amount="1", minted_amount="2", paused=True)
    assert ids(result) == {"undercollateralized": "blocker"} and result["pause_recommended"] is True
    calm = bridge(paused=True)
    assert ids(calm) == {"paused-without-trigger": "info"} and calm["verdict"] == "pass"


def test_bridge_overdue_boundary_is_strictly_older_than_the_limit():
    assert bridge(messages=[msg("m", 30)])["overdue_count"] == 0
    result = bridge(messages=[msg("m", 31)])
    assert result["overdue_count"] == 1 and result["max_pending_age_min"] == 31.0 and ids(result)["messages-overdue"] == "major"


def test_bridge_pause_ratio_boundary_is_inclusive():
    four_ok = [msg(f"ok-{i}", 5) for i in range(4)]
    one_in_five = bridge(messages=[*four_ok, msg("late", 40)])
    assert one_in_five["pause_recommended"] is True and ids(one_in_five)["pause-recommended"] == "blocker"
    one_in_six = bridge(messages=[*four_ok, msg("ok-5", 5), msg("late", 40)])
    assert one_in_six["pause_recommended"] is False and ids(one_in_six) == {"messages-overdue": "major"}


def test_bridge_p95_is_nearest_rank_and_late_deliveries_are_counted():
    messages = [msg(f"m{i:02d}", 200, i) for i in range(1, 21)]  # delivery lags 1..20 minutes
    result = bridge(messages=messages, max_lag_minutes=19)
    assert result["p95_delivery_lag_min"] == 19.0 and result["late_delivered_count"] == 1 and ids(result) == {"late-deliveries": "minor"}
    assert bridge(messages=messages, max_lag_minutes=20)["late_delivered_count"] == 0


def test_bridge_rejects_inconsistent_messages_and_amounts():
    invalid("chain_bridge_monitor", {**BRIDGE, "messages": [msg("a", 5), msg("a", 6)]}, "messages")
    invalid("chain_bridge_monitor", {**BRIDGE, "messages": [{"id": "a", "sent_at": "2026-10-20T12:00:01Z"}]}, "messages")
    invalid("chain_bridge_monitor", {**BRIDGE, "messages": [msg("a", 10, -1)]}, "messages")
    invalid("chain_bridge_monitor", {**BRIDGE, "messages": [msg("a", 10, 11)]}, "messages")
    for field in ("locked_amount", "minted_amount"):
        for bad in ("abc", "-1", "1e31", "NaN", "Infinity"):
            invalid("chain_bridge_monitor", {**BRIDGE, field: bad}, field)
    assert bridge(locked_amount="1000000000000000000000000000000", minted_amount="0")["collateral_surplus"] == 1e30


# --- chain_network_guard --------------------------------------------------------------------------------------

CHAIN_TABLE = [
    (1, "Ethereum Mainnet", "mainnet"), (10, "OP Mainnet", "mainnet"), (56, "BNB Smart Chain Mainnet", "mainnet"), (100, "Gnosis", "mainnet"),
    (137, "Polygon PoS", "mainnet"), (324, "ZKsync Era Mainnet", "mainnet"), (8453, "Base", "mainnet"), (42161, "Arbitrum One", "mainnet"),
    (42220, "Celo Mainnet", "mainnet"), (43114, "Avalanche C-Chain", "mainnet"), (59144, "Linea", "mainnet"), (534352, "Scroll", "mainnet"),
    (5, "Goerli", "testnet"), (97, "BNB Smart Chain Testnet", "testnet"), (300, "ZKsync Sepolia Testnet", "testnet"), (10200, "Gnosis Chiado", "testnet"),
    (17000, "Holesky", "testnet"), (43113, "Avalanche Fuji", "testnet"), (59141, "Linea Sepolia", "testnet"), (80002, "Polygon Amoy", "testnet"),
    (84532, "Base Sepolia", "testnet"), (421614, "Arbitrum Sepolia", "testnet"), (534351, "Scroll Sepolia", "testnet"), (560048, "Hoodi", "testnet"),
    (11155111, "Sepolia", "testnet"), (11155420, "OP Sepolia", "testnet"), (1337, "Local development chain", "local"), (31337, "Hardhat/Anvil local chain", "local"),
]


def guard(environment="testnet", operation="read", chain=11155111, rpc=None, approved=False, level="L0") -> dict:
    payload = {"environment": environment, "operation": operation, "target_chain_id": chain, "mainnet_approved": approved, "approved_level": level}
    if rpc is not None:
        payload["rpc_chain_id"] = rpc
    return ok("chain_network_guard", payload)


@pytest.mark.parametrize("chain_id,name,network_class", CHAIN_TABLE)
def test_guard_reference_table_rows(chain_id, name, network_class):
    result = guard(chain=chain_id)
    assert (result["chain_name"], result["network_class"]) == (name, network_class)
    assert "unknown-chain-id" not in ids(result)


def test_guard_unknown_chain_id_is_treated_as_mainnet():
    result = guard(environment="production", operation="node_change", chain=999999, approved=True, level="L3")
    assert (result["network_class"], result["chain_name"], result["required_level"], result["decision"]) == ("unknown", "", "L3", "allow")
    assert ids(result) == {"unknown-chain-id": "minor"} and result["verdict"] == "warn"
    blocked = guard(environment="testnet", chain=999999)
    assert blocked["decision"] == "block" and ids(blocked)["environment-mismatch"] == "blocker"


def test_guard_testnet_node_change_needs_l2():
    allowed = guard(operation="node_change", level="L2", rpc=11155111)
    assert (allowed["decision"], allowed["required_level"], allowed["findings"]) == ("allow", "L2", [])
    short = guard(operation="node_change", level="L1")
    assert (short["decision"], ids(short)) == ("needs_approval", {"insufficient-approval": "blocker"})


def test_guard_mainnet_change_needs_explicit_approval_and_l3():
    ok_case = guard("production", "node_change", 1, approved=True, level="L3")
    assert (ok_case["decision"], ok_case["findings"], ok_case["required_level"]) == ("allow", [], "L3")
    no_flag = guard("production", "node_change", 1, approved=False, level="L3")
    assert (no_flag["decision"], ids(no_flag)) == ("needs_approval", {"mainnet-not-approved": "blocker"})
    low = guard("production", "node_change", 1, approved=True, level="L2")
    assert (low["decision"], ids(low)) == ("needs_approval", {"insufficient-approval": "blocker"})
    both = guard("production", "node_change", 1)
    assert both["decision"] == "needs_approval" and set(ids(both)) == {"mainnet-not-approved", "insufficient-approval"}


def test_guard_validator_key_operations_need_l4_everywhere():
    assert guard(operation="validator_key_op", level="L3")["decision"] == "needs_approval"
    assert guard(operation="validator_key_op", level="L4")["decision"] == "allow"
    mainnet = guard("production", "validator_key_op", 1, approved=False, level="L4")
    assert ids(mainnet) == {"mainnet-not-approved": "blocker"} and mainnet["required_level"] == "L4"


def test_guard_reads_need_no_approval_even_on_mainnet_in_production():
    result = guard("production", "read", 1)
    assert (result["decision"], result["required_level"], result["findings"], result["verdict"]) == ("allow", "L0", [], "pass")


def test_guard_environment_and_endpoint_mismatches_block():
    mainnet_in_staging = guard("staging", "read", 8453, approved=True, level="L4")
    assert (mainnet_in_staging["decision"], ids(mainnet_in_staging)) == ("block", {"environment-mismatch": "blocker"})
    testnet_in_prod = guard("production", "read", 11155111)
    assert (testnet_in_prod["decision"], ids(testnet_in_prod)) == ("block", {"production-on-testnet": "major"})
    wrong_rpc = guard("testnet", "read", 11155111, rpc=1)
    assert (wrong_rpc["decision"], ids(wrong_rpc)) == ("block", {"rpc-chain-mismatch": "blocker"})
    assert guard("local", "node_change", 31337, level="L2")["decision"] == "allow"


def test_guard_rejects_chain_id_zero():
    result = call("chain_network_guard", {"environment": "local", "operation": "read", "target_chain_id": 0, "mainnet_approved": False, "approved_level": "L0"})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "target_chain_id"


# --- chain_mempool_privacy ------------------------------------------------------------------------------------

MEMPOOL = {"network_kind": "l1_public_mempool", "tx_kind": "swap", "value_usd": 100000, "slippage_bps": 50, "max_acceptable_loss_usd": 100,
           "private_route_available": True, "revert_protection": True, "has_deadline": True, "submitted_route": "public"}


def mempool(**over) -> dict:
    return ok("chain_mempool_privacy", {**MEMPOOL, **over})


def test_mempool_swap_with_exposed_slippage_goes_private():
    assert mempool() == {"state": "SUCCESS", "route": "private_relay", "exposure": "high", "max_sandwich_loss_usd": 500.0, "slippage_bps_cap": 10,
                         "route_change_required": True, "verdict": "pass", "findings": []}
    assert mempool(submitted_route="private")["route_change_required"] is False


def test_mempool_sandwich_loss_equal_to_tolerance_is_acceptable():
    equal = mempool(max_acceptable_loss_usd=500)
    assert (equal["route"], equal["exposure"], equal["route_change_required"]) == ("public_mempool", "low", False)
    assert mempool(max_acceptable_loss_usd=499.99)["exposure"] == "high"
    assert mempool(slippage_bps=0)["exposure"] == "none"


def test_mempool_without_a_private_route_is_a_major_finding():
    result = mempool(private_route_available=False)
    assert (result["route"], result["exposure"], result["verdict"]) == ("public_mempool", "high", "fail") and ids(result) == {"no-private-route": "major"}
    assert "lower the slippage" in result["findings"][0]["summary"]


def test_mempool_front_run_kinds_are_medium_exposure_even_without_slippage():
    for kind in ("liquidation", "nft_mint", "arbitrage"):
        result = mempool(tx_kind=kind)
        assert (result["route"], result["exposure"], result["max_sandwich_loss_usd"], result["slippage_bps_cap"]) == ("private_relay", "medium", 0.0, -1)
    assert ids(mempool(tx_kind="liquidation", private_route_available=False)) == {"no-private-route": "major"}


def test_mempool_plain_transfers_and_votes_stay_public():
    for kind in ("transfer", "governance"):
        result = mempool(tx_kind=kind, has_deadline=False)
        assert (result["route"], result["exposure"], result["findings"], result["route_change_required"]) == ("public_mempool", "none", [], False)


def test_mempool_missing_deadline_and_revert_protection():
    assert ids(mempool(has_deadline=False)) == {"no-deadline": "major"}
    assert ids(mempool(revert_protection=False)) == {"no-revert-protection": "minor"}


def test_mempool_slippage_cap_values():
    assert mempool(value_usd=3, max_acceptable_loss_usd=1)["slippage_bps_cap"] == 3333
    assert mempool(value_usd=100, max_acceptable_loss_usd=100)["slippage_bps_cap"] == 10000
    assert mempool(value_usd=0)["slippage_bps_cap"] == 10000


def test_mempool_l2_sequencer_has_no_public_mempool():
    result = mempool(network_kind="l2_sequencer")
    assert (result["route"], result["exposure"], result["route_change_required"]) == ("sequencer_native", "low", False)
    assert ids(result) == {"no-public-mempool": "info"} and result["verdict"] == "pass"
    assert mempool(network_kind="l2_sequencer", tx_kind="transfer")["exposure"] == "none"
    assert ids(mempool(network_kind="l2_sequencer", has_deadline=False)) == {"no-public-mempool": "info", "no-deadline": "major"}


# --- independent review: secret safety, exact values and boundaries -------------------------------------------

# credential-shaped values a caller might paste into a name field; none may ever appear in a result
CANARIES = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "https://user:hunter2pass@rpc.example.org/v2/abc",
    "https://eth-mainnet.g.alchemy.com/v2/AbCdEf123456",
    "ab" * 32,
    "0x" + "cd" * 32,
    "abandon ability able about above absent absorb abstract absurd abuse access accident",
    "Abandon Ability Able About Above Absent Absorb Abstract Absurd Abuse Access Accident",
    "Bearer eyJhbGciOiJIUzI1NiJ9.e30.abcdef",
    "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
    "password=hunter2",
    "AIzaSyA-1234567890abcdefghijklmnopqrstuv",
    "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6",  # 128-bit hex API key
]


def _carries(result: dict, canary: str) -> bool:
    text = json.dumps(result)
    return canary in text or canary.lower() in text.lower()


@pytest.mark.parametrize("canary", CANARIES)
def test_credential_shaped_names_are_refused_by_field_and_never_echoed(canary):
    name = canary  # a value past the 40-character name limit is refused by the schema, which never echoes it either
    probes = [
        ("chain_client_diversity", {"layer": "execution", "clients": [{"name": name, "nodes": 90}, {"name": "b", "nodes": 10}]}, "clients"),
        ("chain_rpc_failover_plan", {"providers": [provider(name), provider("b")], "demand_rps": 5, "max_error_rate_pct": 1, "max_lag_blocks": 2, "need_archive": False}, "providers"),
        ("chain_stuck_transactions", {"as_of": "2026-10-20T12:00:00Z", "base_fee_gwei": 30, "stuck_after_minutes": 30,
                                      "accounts": [{"account": canary, "confirmed_nonce": 1, "pending": [tx(3)]}]}, "accounts"),
        ("chain_validator_slashing_risk", {"instances": [inst(key=canary), inst(key=canary, host="h2")]}, "instances"),
        ("chain_validator_slashing_risk", {"instances": [inst(host=canary), inst(host="h2")]}, "instances"),
    ]
    for tool, payload, field in probes:
        result = invalid(tool, payload, field)
        assert not _carries(result, name) and not _carries(result, canary), (tool, result)


@pytest.mark.parametrize("canary", CANARIES)
def test_bridge_message_ids_are_never_echoed_even_in_errors(canary):
    sent_after = {**BRIDGE, "messages": [{"id": canary[:64], "sent_at": "2026-10-20T12:00:01Z"}]}
    result = invalid("chain_bridge_monitor", sent_after, "messages")
    assert result["message"] == "message #0 was sent after as_of" and not _carries(result, canary[:64])
    ok_result = bridge(messages=[{"id": canary[:64], "sent_at": "2026-10-20T11:00:00Z"}])
    assert ok_result["pending_count"] == 1 and not _carries(ok_result, canary[:64])


def test_public_identifiers_and_plain_labels_are_not_mistaken_for_credentials():
    address, bls = "0x" + "ab" * 20, "0x" + "ab" * 48
    for label in ("geth", "Treasury-Hot", address, bls, bls[2:], "ip-10-0-12-34.eu-west-1.compute.internal", "prod-validator-eu-west-1-key-group-0001-primary", "val-1"):
        assert slashing(inst(key=label))["key_count"] == 1, label
    assert stuck([{"account": address, "confirmed_nonce": 0, "pending": []}])["accounts_checked"] == 1
    assert diversity(1, 1, names=["Lighthouse", "Nimbus"])["total_nodes"] == 2


def test_findings_about_long_keys_keep_distinct_ids():
    bls = "0x" + "ab" * 48
    result = slashing(inst(key=bls), inst(key=bls, host="h2", role="standby", guard="interchange_imported", doppel=False),
                      inst(key=bls, host="h3", role="standby", guard="interchange_imported", doppel=False))
    found = [f["id"] for f in result["findings"]]
    assert len(found) == 2 and len(set(found)) == 2 and all(len(i) <= 120 for i in found), found
    assert found == sorted(found) and all(i.startswith("standby-no-doppelganger:0xabab") for i in found)


def test_stuck_zero_tip_replacement_must_raise_the_tip_by_at_least_one_wei():
    # go-ethereum legacypool list.Add rejects a replacement unless BOTH fee fields are strictly higher (and >= 10% higher)
    zero = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=50, prio=0, seen="2026-10-20T10:00:00Z")]}])
    assert zero["replacements"] == [{"account": "a", "nonce": 0, "min_max_fee_gwei": 55.0, "min_priority_fee_gwei": 0.000000001}]
    free = stuck([{"account": "a", "confirmed_nonce": 0, "pending": [tx(0, fee=0, prio=0, seen="2026-10-20T10:00:00Z")]}], base=0)
    assert free["replacements"] == [{"account": "a", "nonce": 0, "min_max_fee_gwei": 0.000000001, "min_priority_fee_gwei": 0.000000001}]


def test_bridge_overdue_and_late_use_exact_seconds_not_truncated_ones():
    limit = {"max_lag_minutes": 1}  # 60 s
    pending = bridge(**limit, messages=[{"id": "p", "sent_at": "2026-10-20T11:58:59.500000Z"}])  # 60.5 s old
    assert (pending["overdue_count"], pending["max_pending_age_min"]) == (1, 1.01)
    exact = bridge(**limit, messages=[{"id": "p", "sent_at": "2026-10-20T11:59:00Z"}])
    assert exact["overdue_count"] == 0
    late = bridge(**limit, messages=[{"id": "d", "sent_at": "2026-10-20T11:00:00Z", "delivered_at": "2026-10-20T11:01:00.250000Z"}])
    assert late["late_delivered_count"] == 1


def test_bridge_undercollateralized_amount_is_written_without_exponent():
    result = bridge(locked_amount="0", minted_amount="1e3")
    summary = next(f["summary"] for f in result["findings"] if f["id"] == "undercollateralized")
    assert summary.startswith("1000 more is minted than locked")


def test_l2_ready_time_and_delays_round_up_never_down():
    sub = l2(initiated_at="2026-10-01T00:00:00.400000Z", as_of="2026-10-01T00:00:00.400000Z", batch_post_delay_min=1, proof_delay_hours=0,
             challenge_window_hours=168, l1_finality_min=0)
    assert sub["withdrawable_at"] == "2026-10-08T00:01:01Z"  # exact moment is 00:01:00.400
    tiny = l2(proof_delay_hours=0.0001, batch_post_delay_min=0, l1_finality_min=0)  # 0.36 s of proof wait counts as 1 s, not 0
    assert tiny["withdrawable_at"] == "2026-10-08T00:00:01Z"
    assert l2(initiated_at="2026-10-01T00:00:00Z", as_of="2026-10-01T00:00:59.900000Z", batch_post_delay_min=1, challenge_window_hours=24,
              proof_delay_hours=0, l1_finality_min=0)["ready"] is False


def test_l2_validity_rollup_with_an_execution_delay_names_that_stage():
    stage = l2(rollup_type="validity", challenge_window_hours=24, proof_delay_hours=1, as_of="2026-10-01T12:00:00Z")
    assert stage["stage"] == "execution-delay"
    assert l2(rollup_type="optimistic", as_of="2026-10-01T12:00:00Z")["stage"] == "challenge-window"


def test_l2_arbitrum_one_style_window_is_reported_below_seven_days_without_calling_it_a_default():
    result = l2(challenge_window_hours=153.6)
    assert ids(result) == {"challenge-window-below-7-days": "info"} and "Arbitrum One about 6.4 days" in result["findings"][0]["summary"]


def test_fee_policy_floors_the_target_for_an_odd_gas_limit_and_matches_hand_computed_wei():
    # target = 30000001 // 2 = 15000000; +1 gas over it: 100e9 * 1 // 15000000 // 8 = 833 wei
    assert fee(gas_limit=30000001, latest_gas_used=15000001)["next_base_fee_gwei"] == 100.000000833
    assert fee(gas_limit=30000001, latest_gas_used=15000000)["next_base_fee_gwei"] == 100.0
    assert fee(latest_base_fee_gwei=15, latest_gas_used=21000000)["next_base_fee_gwei"] == 15.75  # 70% full: +5%


def test_indexer_eta_uses_the_exact_span_between_first_and_last_sample():
    fractional = [{"observed_at": "2026-10-20T10:00:00.000000Z", "head_block": 100, "indexed_block": 0},
                  {"observed_at": "2026-10-20T10:00:00.500000Z", "head_block": 101, "indexed_block": 50}]
    result = lag(fractional, block_time_s=1, slo=1000)
    assert (result["head_rate_bps"], result["index_rate_bps"], result["trend"], result["eta_catch_up_s"]) == (2.0, 100.0, "closing", 0.5)  # 51 blocks behind, closing at 98 blocks/s


def test_diversity_reports_only_clients_that_exist_and_ranks_ties_by_name():
    result = diversity(0, 0, 3, names=["a", "b", "c"])
    assert [s["client"] for s in result["shares"]] == ["c", "a", "b"] and result["active_clients"] == 1
