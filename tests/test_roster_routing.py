"""Routing of the thirty-agent roster: capability ownership, the three domain patterns, file-path owners and custom-plan checks."""
from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest
from conftest import ROOT, run_script

from swarm.manifest import load_manifest
from swarm.taskstore import GATES_BY_RISK

GRAPH_ID = "ut-mv00tcgl-d8215d3e"
NEW_AGENTS = [f"A{n}" for n in range(16, 31)]
GATE_OWNERS = {"quality": "A08", "review": "A09", "security": "A10", "release": "A12"}

# pattern -> (task count, implementer suffixes that the gate tasks target, implementer suffixes pinned to risk high)
PATTERN_SHAPE = {
    "cloud-native": (16, {"be", "events"}, set()),
    "cluster": (14, {"gitops"}, {"gitops"}),
    "web3": (15, {"sol", "dapp"}, {"sol", "dapp"}),
}


def _load(stem: str):
    spec = importlib.util.spec_from_file_location(f"_roster_{stem}", ROOT / "scripts" / f"{stem}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict]:
    return {agent["id"]: agent for agent in load_manifest()}


def _plan(tmp_path: Path, pattern: str, *extra: str) -> list[dict]:
    done = run_script("orch_plan.py", "--brief-text", "demo", "--pattern", pattern, "--prefix", "P", "--json",
                      *extra, env={"SWARM_DIR": str(tmp_path / ".swarm")})
    assert done.returncode == 0, done.stdout + done.stderr
    body = json.loads(done.stdout)
    assert body["pattern"] == pattern
    return body["tasks"]


# --- (a) capability ownership ------------------------------------------------------------------------------------------
def test_the_swarm_has_thirty_agents_with_unique_ids(manifest):
    assert sorted(manifest, key=lambda a: int(a[1:])) == [f"A{n:02d}" for n in range(1, 31)]


def test_a_capability_names_one_owner_apart_from_the_shared_patch_capability(manifest):
    owners: dict[str, list[str]] = defaultdict(list)
    for agent_id, agent in manifest.items():
        for capability in agent["capabilities"]:
            owners[capability].append(agent_id)
    shared = {capability: ids for capability, ids in owners.items() if len(ids) > 1}
    assert shared == {"code.patch": ["A05", "A06"]}


def test_a_new_agent_never_shadows_or_is_shadowed_by_an_older_capability(manifest):
    from swarm.manifest import by_capability

    for agent_id in NEW_AGENTS:
        assert manifest[agent_id]["capabilities"]
        for capability in manifest[agent_id]["capabilities"]:
            assert [a["id"] for a in by_capability(capability)] == [agent_id]


# --- (b) the three domain patterns -------------------------------------------------------------------------------------
@pytest.mark.parametrize("pattern", PATTERN_SHAPE)
def test_a_domain_pattern_plans_an_acyclic_dag_of_agents_that_own_their_capability(tmp_path, manifest, pattern):
    count, _targets, _high = PATTERN_SHAPE[pattern]
    tasks = _plan(tmp_path, pattern)
    assert len(tasks) == count
    by_id = {t["task_id"]: t for t in tasks}
    assert len(by_id) == count
    for task in tasks:
        assert task["agent_id"] in manifest, task["task_id"]
        assert task["capability"] in manifest[task["agent_id"]]["capabilities"], task["task_id"]
        assert set(task["depends_on"]) <= set(by_id), task["task_id"]
    pending = {t["task_id"]: set(t["depends_on"]) for t in tasks}
    while pending:  # Kahn: every round must release at least one task, or the graph has a cycle
        ready = [tid for tid, deps in pending.items() if not deps]
        assert ready, f"cycle among {sorted(pending)}"
        for tid in ready:
            del pending[tid]
        for deps in pending.values():
            deps.difference_update(ready)
    for task in tasks:
        depth = 1 + max((by_id[d]["dag_depth"] for d in task["depends_on"]), default=-1)
        assert task["dag_depth"] == depth, task["task_id"]


@pytest.mark.parametrize("pattern", PATTERN_SHAPE)
def test_gate_tasks_target_the_implementer_rows_and_belong_to_the_four_gate_agents(tmp_path, pattern):
    _count, targets, _high = PATTERN_SHAPE[pattern]
    tasks = _plan(tmp_path, pattern)
    gate_tasks = [t for t in tasks if "gate" in t["notes"]]
    assert sorted(t["notes"]["gate"] for t in gate_tasks) == sorted(GATE_OWNERS)
    for task in gate_tasks:
        assert task["agent_id"] == GATE_OWNERS[task["notes"]["gate"]]
        assert set(task["notes"]["gate_for"]) == {f"P-{suffix}" for suffix in targets}


@pytest.mark.parametrize("pattern", PATTERN_SHAPE)
def test_every_implementer_has_a_gate_task_for_each_gate_its_risk_class_requires(tmp_path, pattern):
    _count, targets, _high = PATTERN_SHAPE[pattern]
    for risk in ("low", "medium", "high"):
        tasks = _plan(tmp_path / risk, pattern, "--risk-class", risk)
        by_id = {t["task_id"]: t for t in tasks}
        for suffix in targets:
            implementer = by_id[f"P-{suffix}"]
            covered = {t["notes"]["gate"] for t in tasks if f"P-{suffix}" in t["notes"].get("gate_for", [])}
            assert set(implementer["gates"]) == set(GATES_BY_RISK[implementer["risk_class"]])
            assert set(implementer["gates"]) <= covered, (pattern, risk, suffix)


@pytest.mark.parametrize("pattern", ["cluster", "web3"])
@pytest.mark.parametrize("plan_risk", ["low", "medium", "high"])
def test_cluster_and_web3_implementers_are_high_risk_at_any_plan_risk(tmp_path, pattern, plan_risk):
    _count, targets, high = PATTERN_SHAPE[pattern]
    tasks = _plan(tmp_path, pattern, "--risk-class", plan_risk)
    by_id = {t["task_id"]: t for t in tasks}
    for suffix in high:
        assert by_id[f"P-{suffix}"]["risk_class"] == "high"
        assert by_id[f"P-{suffix}"]["gates"] == ["review", "quality", "security", "release"]
    for task in tasks:
        if task["task_id"].removeprefix("P-") not in high:
            assert task["risk_class"] == plan_risk, task["task_id"]


@pytest.mark.parametrize("plan_risk", ["low", "medium", "high"])
def test_cloud_native_follows_the_plan_risk_class(tmp_path, plan_risk):
    tasks = _plan(tmp_path, "cloud-native", "--risk-class", plan_risk)
    assert {t["risk_class"] for t in tasks} == {plan_risk}


def test_domain_owners_sit_upstream_of_the_rows_that_consume_them(tmp_path):
    cluster = {t["task_id"]: t for t in _plan(tmp_path / "c", "cluster")}
    assert cluster["P-gitops"]["depends_on"] == ["P-cap", "P-net", "P-pol"]
    assert cluster["P-roll"]["depends_on"] == ["P-qa", "P-rev", "P-sec", "P-res", "P-netchk"]
    web3 = {t["task_id"]: t for t in _plan(tmp_path / "w", "web3")}
    assert web3["P-sol"]["depends_on"] == ["P-arch", "P-cust"] and web3["P-sec"]["depends_on"] == ["P-sol", "P-dapp", "P-aud"]
    assert web3["P-qa"]["depends_on"] == ["P-sol", "P-dapp", "P-aud"]
    assert web3["P-rel"]["depends_on"] == ["P-build", "P-chain", "P-comp"]
    assert web3["P-dapp"]["depends_on"] == ["P-arch", "P-sol", "P-chain"]
    cloud = {t["task_id"]: t for t in _plan(tmp_path / "k", "cloud-native")}
    assert cloud["P-be"]["depends_on"] == ["P-arch", "P-cloud", "P-events", "P-plat"]
    assert cloud["P-events"]["depends_on"] == ["P-arch", "P-cloud", "P-plat"]
    assert cloud["P-img"]["depends_on"] == ["P-be"] and cloud["P-prov"]["depends_on"] == ["P-build"]
    assert cloud["P-rel"]["depends_on"] == ["P-build", "P-prov", "P-plat", "P-cost"]


def test_post_manifest_scans_run_after_the_manifests_they_audit(tmp_path):
    """A23 and A24 audit the manifests A22 renders, so their scans follow gitops and gate the rollout."""
    cluster = {t["task_id"]: t for t in _plan(tmp_path, "cluster")}
    assert cluster["P-netchk"]["agent_id"] == "A23" and cluster["P-netchk"]["capability"] == "mesh.netpol"
    assert cluster["P-polchk"]["agent_id"] == "A24" and cluster["P-polchk"]["capability"] == "kpol.admission"
    assert cluster["P-netchk"]["depends_on"] == ["P-gitops"] and cluster["P-polchk"]["depends_on"] == ["P-gitops"]
    assert cluster["P-sec"]["depends_on"] == ["P-gitops", "P-polchk"]


def test_custody_compliance_waits_for_the_audit_and_feeds_the_release(tmp_path):
    """custody.policy needs audit.report for its thresholds, so the evidence pack is a row after the audit."""
    web3 = {t["task_id"]: t for t in _plan(tmp_path, "web3")}
    assert web3["P-cust"]["depends_on"] == ["P-arch"]
    assert web3["P-comp"]["agent_id"] == "A30" and web3["P-comp"]["capability"] == "custody.compliance"
    assert web3["P-comp"]["depends_on"] == ["P-cust", "P-aud"]
    assert "P-comp" in web3["P-rel"]["depends_on"]


def _ancestors(by_id: dict[str, dict], task_id: str) -> set[str]:
    seen: set[str] = set()
    stack = list(by_id[task_id]["depends_on"])
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(by_id[current]["depends_on"])
    return seen


@pytest.mark.parametrize("pattern", PATTERN_SHAPE)
def test_a_gate_runs_after_its_targets_and_only_the_targets_carry_gates(tmp_path, pattern):
    _count, targets, _high = PATTERN_SHAPE[pattern]
    by_id = {t["task_id"]: t for t in _plan(tmp_path, pattern, "--risk-class", "high")}
    for task in by_id.values():
        if "gate" in task["notes"]:
            assert set(task["notes"]["gate_for"]) <= _ancestors(by_id, task["task_id"]), task["task_id"]
            assert task["gates"] == [], task["task_id"]
        elif task["task_id"].removeprefix("P-") not in targets:
            assert task["gates"] == [], task["task_id"]


@pytest.mark.parametrize("pattern", PATTERN_SHAPE)
def test_every_row_feeds_the_release_gate_or_follows_it(tmp_path, pattern):
    by_id = {t["task_id"]: t for t in _plan(tmp_path, pattern)}
    release = next(tid for tid, t in by_id.items() if t["notes"].get("gate") == "release")
    upstream = _ancestors(by_id, release)
    for tid in by_id:
        assert tid == release or tid in upstream or release in _ancestors(by_id, tid), f"{tid} is dangling"


def test_an_unknown_pattern_is_refused_by_the_planner(tmp_path):
    refused = run_script("orch_plan.py", "--brief-text", "demo", "--pattern", "mainnet", "--json",
                         env={"SWARM_DIR": str(tmp_path / ".swarm")})
    assert refused.returncode != 0 and "mainnet" in refused.stderr + refused.stdout


# --- (c) file paths route to the owning agent --------------------------------------------------------------------------
ROUTES = [
    ("contracts/Vault.sol", "A26", "web3.contract"),
    ("foundry.toml", "A26", "web3.contract"),
    ("hardhat.config.ts", "A26", "web3.contract"),
    ("contracts/script/Deploy.s.sol", "A26", "web3.contract"),
    ("web3/hooks/useVault.ts", "A29", "dapp.wallet"),
    ("app/wallet/Connect.tsx", "A29", "dapp.wallet"),
    ("src/wagmi/config.ts", "A29", "dapp.wallet"),
    ("k8s/api/deployment.yaml", "A22", "gitops.manifest"),
    ("charts/api/values.yaml", "A22", "gitops.manifest"),
    ("helm/api/Chart.yaml", "A22", "gitops.manifest"),
    ("kubernetes/ns.yaml", "A22", "gitops.manifest"),
    ("manifests/base/svc.yaml", "A22", "gitops.manifest"),
    ("deploy/overlays/prod/patch.yaml", "A22", "gitops.manifest"),
    ("kustomization.yaml", "A22", "gitops.manifest"),
    ("infra/k8s/ingress.yaml", "A22", "gitops.manifest"),
    ("Dockerfile", "A11", "iac.change"),
    ("services/api/Dockerfile.prod", "A11", "iac.change"),
    ("asyncapi.yaml", "A19", "event.topology"),
    ("asyncapi.json", "A19", "event.topology"),
    ("serverless.yml", "A19", "event.topology"),
    ("serverless.ts", "A19", "event.topology"),
    ("catalog-info.yaml", "A17", "platform.catalog"),
    # unchanged routes
    ("README.md", "A15", "docs.bundle"),
    ("contracts/README.md", "A15", "docs.bundle"),
    ("db/migrations/001_init.sql", "A07", "data.migration"),
    ("components/Button.tsx", "A06", "code.frontend"),
    ("infra/main.tf", "A11", "iac.change"),
    (".github/workflows/ci.yml", "A11", "iac.change"),
    ("scripts/run.sh", "A11", "iac.change"),
    ("src/api.py", "A05", "code.backend"),
    ("pyproject.toml", "A05", "code.backend"),
    # a name that merely contains a domain word keeps its old owner: UI code in a charts/overlays directory, a
    # dockerfile parser, a pact or API `contracts/` tree, a fintech `wallet/` service, a test that names serverless
    ("src/components/charts/Bar.tsx", "A06", "code.frontend"),
    ("src/components/overlays/Modal.tsx", "A06", "code.frontend"),
    ("src/ui/charts/data.json", "A06", "code.frontend"),
    ("src/charts/Pie.tsx", "A06", "code.frontend"),
    ("scripts/dockerfile_lint.py", "A05", "code.backend"),
    ("src/dockerfile-parser.ts", "A05", "code.backend"),
    ("src/contracts/user.ts", "A05", "code.backend"),
    ("tests/contracts/test_pact.py", "A05", "code.backend"),
    ("contracts/openapi.yaml", "A05", "code.backend"),
    ("contracts/abi/Vault.json", "A26", "web3.contract"),
    ("wallet/balance.py", "A05", "code.backend"),
    ("src/wallet/service.go", "A05", "code.backend"),
    ("serverless.test.ts", "A05", "code.backend"),
    ("asyncapi.py", "A05", "code.backend"),
    ("k8s/main.tf", "A11", "iac.change"),
    ("k8s/apply.sh", "A11", "iac.change"),
    ("k8s/operator.py", "A05", "code.backend"),
]


@pytest.mark.parametrize("path,agent,capability", ROUTES)
def test_a_file_routes_to_its_owner_with_a_capability_that_owner_offers(path, agent, capability, manifest):
    graph = _load("orch_from_graph")
    data = {"graphId": GRAPH_ID, "nodes": [{"id": "n1", "kind": "generate", "title": "t", "depends_on": [],
                                            "steps": [f"Edit {path}"]}]}
    (task,) = graph.convert(data, "0" * 64)["tasks"]
    assert (task["agent"], task["capability"]) == (agent, capability)
    assert capability in manifest[agent]["capabilities"]
    medium = agent not in {"A15"}
    assert (task["risk_class"], task["gates"]) == (("medium", ["review", "quality"]) if medium else ("low", ["review"]))
    assert task["acceptance"] == [f"Edit {path}"]


# --- (d) custom plans are checked against agents.json ------------------------------------------------------------------
def _custom(tmp_path: Path, task: dict) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"tasks": [{"id": "t1", "title": "t", "depends_on": [], **task}]}), encoding="utf-8")
    return path


def test_load_custom_accepts_an_agent_that_offers_the_capability_and_normalizes_its_id(tmp_path):
    orch_plan = _load("orch_plan")
    (row,) = orch_plan.load_custom(_custom(tmp_path, {"capability": "web3.contract", "agent": "a26-smart-contract"}))
    assert (row[0], row[1], row[2]) == ("t1", "web3.contract", "A26")


def test_load_custom_resolves_a_missing_agent_to_the_capability_owner(tmp_path):
    orch_plan = _load("orch_plan")
    (row,) = orch_plan.load_custom(_custom(tmp_path, {"capability": "kpol.admission"}))
    assert row[2] == "A24"


def test_load_custom_rejects_an_unknown_agent(tmp_path):
    orch_plan = _load("orch_plan")
    with pytest.raises(orch_plan.SwarmError, match="A99") as info:
        orch_plan.load_custom(_custom(tmp_path, {"capability": "web3.contract", "agent": "A99"}))
    assert info.value.code.value == "E-INPUT"


def test_load_custom_rejects_a_capability_the_agent_does_not_own(tmp_path):
    orch_plan = _load("orch_plan")
    with pytest.raises(orch_plan.SwarmError, match="A05 does not offer web3.contract") as info:
        orch_plan.load_custom(_custom(tmp_path, {"capability": "web3.contract", "agent": "A05"}))
    assert info.value.code.value == "E-INPUT"


def test_load_custom_rejects_a_capability_nobody_offers(tmp_path):
    orch_plan = _load("orch_plan")
    with pytest.raises(orch_plan.SwarmError, match="no agent offers capability chain.mint"):
        orch_plan.load_custom(_custom(tmp_path, {"capability": "chain.mint"}))


def test_the_planner_exits_with_an_input_error_for_a_mismatched_custom_task(tmp_path):
    done = run_script("orch_plan.py", "--plan", str(_custom(tmp_path, {"capability": "dapp.wallet", "agent": "A26"})),
                      "--prefix", "C", "--json", env={"SWARM_DIR": str(tmp_path / ".swarm")})
    assert done.returncode == 2 and "A26 does not offer dapp.wallet" in done.stdout


# --- (e) the registered orch_plan preview agrees with the planner -------------------------------------------------------
@pytest.mark.parametrize("pattern", ["cluster", "web3"])
@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_the_plan_preview_lists_the_gates_the_pinned_implementer_rows_require(pattern, risk):
    from swarm.tools.batch_01_05 import plan

    dag = plan({"brief": "b", "pattern": pattern, "risk_class": risk})["dag"]
    assert dag["risk"] == risk and dag["gates"] == list(GATES_BY_RISK["high"])


@pytest.mark.parametrize("pattern", ["feature", "hotfix", "dependency", "cloud-native"])
@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_the_plan_preview_keeps_the_plan_risk_gates_for_the_other_patterns(pattern, risk):
    from swarm.tools.batch_01_05 import plan

    assert plan({"brief": "b", "pattern": pattern, "risk_class": risk})["dag"]["gates"] == list(GATES_BY_RISK[risk])


def test_the_plan_preview_counts_every_stored_pattern():
    from swarm.tools.batch_01_05 import plan

    counts = {pattern: plan({"brief": "b", "pattern": pattern})["dag"]["tasks"] for pattern in PATTERN_SHAPE}
    assert counts == {pattern: shape[0] for pattern, shape in PATTERN_SHAPE.items()}
