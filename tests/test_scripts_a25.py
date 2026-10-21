"""scripts/resil_manifest_check.py: fixture trees in tmp_path, findings, exit codes, JSON shape and the dry-run contract."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "resil_manifest_check.py"
TWIN = ROOT / "scripts" / "ts" / "resil_manifest_check.ts"
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"
ZONE = "topology.kubernetes.io/zone"
LEGACY_ZONE = "failure-domain.beta.kubernetes.io/zone"
OK_SPREAD = [{"maxSkew": 1, "topologyKey": ZONE, "whenUnsatisfiable": "DoNotSchedule"}]
PROBES = {"startupProbe": {"httpGet": {"path": "/startz", "port": 80}, "periodSeconds": 5, "failureThreshold": 30},
          "livenessProbe": {"httpGet": {"path": "/livez", "port": 80}},
          "readinessProbe": {"httpGet": {"path": "/readyz", "port": 80}}}
PRESTOP = {"preStop": {"sleep": {"seconds": 10}}}


def run_cli(root: Path, *args: str, swarm_dir: Path | None = None) -> tuple[int, dict]:
    env = {**os.environ, "SWARM_DIR": str(swarm_dir or root / ".swarm")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--json", *args], capture_output=True, text=True, env=env, cwd=ROOT)
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def workload(name="web", kind="Deployment", replicas=3, ns="shop", labels=None, ports=True, probes=None, spread=None, anti=None, grace=None,
             lifecycle=None, hpa_style=False):
    container: dict = {"name": "app", "image": "registry.example/app:1"}
    if ports:
        container["ports"] = [{"containerPort": 80}]
    container.update(PROBES if probes is None else probes)
    if lifecycle:
        container["lifecycle"] = lifecycle
    podspec: dict = {"containers": [container]}
    if spread:
        podspec["topologySpreadConstraints"] = spread
    if anti:
        podspec["affinity"] = {"podAntiAffinity": anti}
    if grace is not None:
        podspec["terminationGracePeriodSeconds"] = grace
    spec: dict = {"template": {"metadata": {"labels": labels or {"app": name}}, "spec": podspec}}
    if not hpa_style:
        spec["replicas"] = replicas
    return {"apiVersion": "apps/v1", "kind": kind, "metadata": {"name": name, "namespace": ns}, "spec": spec}


def pdb(name="web-pdb", ns="shop", selector=..., **budget):
    spec = dict(budget)
    spec["selector"] = {"matchLabels": {"app": "web"}} if selector is ... else selector
    if spec["selector"] is None:
        del spec["selector"]
    return {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": name, "namespace": ns}, "spec": spec}


def hpa(target="web", minimum=3, ns="shop"):
    return {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": f"{target}-hpa", "namespace": ns},
            "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": target}, "minReplicas": minimum, "maxReplicas": 10}}


def write(root: Path, name: str, *docs) -> None:
    (root / name).write_text(json.dumps(docs[0] if len(docs) == 1 else {"apiVersion": "v1", "kind": "List", "items": list(docs)}), encoding="utf-8")


def good_tree(root: Path) -> None:
    write(root, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, grace=60), pdb(minAvailable=2))


def by_rule(result: dict) -> dict[str, dict]:
    return {f["id"]: f for f in result["findings"]}


def load_module():
    spec = importlib.util.spec_from_file_location("_resil_manifest_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- dry-run contract -------------------------------------------------------------------------------------------


def test_dry_run_is_canned_json_and_reads_nothing(tmp_path):
    missing = tmp_path / "does-not-exist"
    state = tmp_path / "state"
    code, data = run_cli(missing, "--dry-run", swarm_dir=state)
    assert code == 0
    assert data["status"] == "ok" and data["agent"] == "A25" and data["script"] == "resil_manifest_check" and data["dry_run"] is True
    assert data["findings"] == [] and data["files_scanned"] == 0 and not missing.exists()
    assert run_cli(missing, "--dry-run", swarm_dir=state)[1] == data
    write(tmp_path, "bad.json", workload(probes={}))
    assert run_cli(tmp_path, "--dry-run", swarm_dir=state)[1] == data           # a failing manifest in the root is not read


def test_dry_run_with_no_arguments_prints_one_json_document(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--json"], capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "SWARM_DIR": str(tmp_path / "state")})
    assert proc.returncode == 0 and proc.stderr == ""
    data = json.loads(proc.stdout)
    assert data["status"] == "ok" and data["agent"] == "A25" and data["script"] == "resil_manifest_check"


@pytest.mark.skipif(not Path(BUN).exists(), reason="bun not installed")
def test_bun_twin_passes_the_dry_run_through():
    proc = subprocess.run([BUN, str(TWIN), "--dry-run", "--json"], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["status"] == "ok" and data["script"] == "resil_manifest_check"


# --- real mode: findings ------------------------------------------------------------------------------------------


def test_resilient_workload_passes_with_exit_zero(tmp_path):
    good_tree(tmp_path)
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok" and data["findings"] == []
    assert (data["files_scanned"], data["workloads_checked"], data["pdbs_found"], data["skipped"]) == (1, 1, 1, [])
    assert data["counts"] == {"blocker": 0, "major": 0, "minor": 0, "info": 0}


def test_bare_workload_reports_every_missing_protection(tmp_path):
    write(tmp_path, "web.json", workload(probes={}))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail"
    findings = by_rule(data)
    assert {r: findings[r]["severity"] for r in findings} == {"pdb-missing": "major", "spread-missing": "major", "readiness-missing": "major",
                                                              "no-prestop": "minor"}
    assert all(f["workload"] == "shop/Deployment/web" and f["location"] == "web.json" for f in data["findings"])
    assert data["counts"] == {"blocker": 0, "major": 3, "minor": 1, "info": 0}
    assert [f["severity"] for f in data["findings"]] == ["major", "major", "major", "minor"]


def test_only_minor_findings_keep_the_status_ok(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, grace=60), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok"
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("no-prestop", "minor")]


def test_pdb_matching_uses_namespace_and_selector_semantics(tmp_path):
    write(tmp_path, "a.json", workload("web", spread=OK_SPREAD, lifecycle=PRESTOP), pdb("p-other-ns", ns="other", minAvailable=2))
    assert "pdb-missing" in by_rule(run_cli(tmp_path)[1])
    write(tmp_path, "a.json", workload("web", spread=OK_SPREAD, lifecycle=PRESTOP), pdb("p-null", selector=None, minAvailable=2))
    assert "pdb-missing" in by_rule(run_cli(tmp_path)[1])                      # a null selector matches no pods
    write(tmp_path, "a.json", workload("web", spread=OK_SPREAD, lifecycle=PRESTOP), pdb("p-all", selector={}, minAvailable=2))
    assert run_cli(tmp_path)[1]["findings"] == []                              # an empty selector matches every pod of the namespace
    expressions = {"matchExpressions": [{"key": "app", "operator": "In", "values": ["web", "api"]},
                                        {"key": "canary", "operator": "DoesNotExist"}]}
    write(tmp_path, "a.json", workload("web", spread=OK_SPREAD, lifecycle=PRESTOP), pdb("p-expr", selector=expressions, minAvailable=2))
    assert run_cli(tmp_path)[1]["findings"] == []
    miss = {"matchExpressions": [{"key": "app", "operator": "NotIn", "values": ["web"]}]}
    write(tmp_path, "a.json", workload("web", spread=OK_SPREAD, lifecycle=PRESTOP), pdb("p-notin", selector=miss, minAvailable=2))
    assert "pdb-missing" in by_rule(run_cli(tmp_path)[1])


def test_blocking_and_unsatisfiable_budgets_are_reported_at_the_pdb_file(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdb.json", pdb(maxUnavailable=0))
    finding = by_rule(run_cli(tmp_path)[1])["pdb-blocks-eviction"]
    assert (finding["severity"], finding["location"]) == ("major", "pdb.json")
    write(tmp_path, "pdb.json", pdb(minAvailable=5))
    assert by_rule(run_cli(tmp_path)[1])["pdb-unsatisfiable"]["severity"] == "blocker"
    write(tmp_path, "pdb.json", pdb(minAvailable="100%"))
    assert "pdb-blocks-eviction" in by_rule(run_cli(tmp_path)[1])
    write(tmp_path, "pdb.json", pdb(maxUnavailable="3"))
    assert by_rule(run_cli(tmp_path)[1])["pdb-no-protection"]["severity"] == "major"


def test_invalid_budget_values_and_two_budgets_on_one_workload(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdb.json", pdb(minAvailable="lots"))
    assert by_rule(run_cli(tmp_path)[1])["pdb-invalid"]["location"] == "pdb.json"
    write(tmp_path, "pdb.json", pdb(minAvailable=1, maxUnavailable=1))
    assert "pdb-invalid" in by_rule(run_cli(tmp_path)[1])
    write(tmp_path, "pdb.json", pdb())
    assert "pdb-invalid" in by_rule(run_cli(tmp_path)[1])
    write(tmp_path, "pdb.json", pdb("one", minAvailable=2), pdb("two", maxUnavailable=1))
    code, data = run_cli(tmp_path)
    assert code == 1 and by_rule(data)["pdb-multiple"]["severity"] == "blocker"


def test_hpa_minimum_replaces_the_missing_replica_count(tmp_path):
    write(tmp_path, "web.json", workload(hpa_style=True, spread=OK_SPREAD, lifecycle=PRESTOP))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["single-replica"]      # replicas default to 1
    write(tmp_path, "hpa.json", hpa(minimum=3))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["pdb-missing"]
    write(tmp_path, "hpa.json", hpa(minimum=1))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["single-replica"]


def test_statefulset_and_daemonset_handling(tmp_path):
    write(tmp_path, "db.json", workload("db", kind="StatefulSet", replicas=3, spread=OK_SPREAD, lifecycle=PRESTOP, labels={"app": "db"}))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["pdb-missing"]
    write(tmp_path, "db.json", workload("agent", kind="DaemonSet", probes={}, ports=False))
    data = run_cli(tmp_path)[1]
    assert data["workloads_checked"] == 1 and [f["id"] for f in data["findings"]] == []     # no PDB/spread rules, nothing serves traffic


@pytest.mark.parametrize(("spread", "anti", "expected"), [
    (None, None, {"spread-missing": "major"}),
    ([{"maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "DoNotSchedule"}], None, {"zone-spread-missing": "major"}),
    ([{"maxSkew": 1, "topologyKey": ZONE, "whenUnsatisfiable": "ScheduleAnyway"}], None, {"zone-spread-soft-only": "minor"}),
    ([{"maxSkew": 1, "topologyKey": ZONE, "whenUnsatisfiable": "DoNotSchedule"}], None, {}),
    (None, {"requiredDuringSchedulingIgnoredDuringExecution": [{"topologyKey": ZONE, "labelSelector": {}}]}, {}),
    (None, {"preferredDuringSchedulingIgnoredDuringExecution": [{"weight": 100, "podAffinityTerm": {"topologyKey": ZONE}}]},
     {"zone-spread-soft-only": "minor"}),
    ([{"maxSkew": 1, "topologyKey": LEGACY_ZONE, "whenUnsatisfiable": "DoNotSchedule"}], None, {"deprecated-zone-label": "minor"}),
])
def test_topology_spread_rules(tmp_path, spread, anti, expected):
    write(tmp_path, "web.json", workload(spread=spread, anti=anti, lifecycle=PRESTOP), pdb(minAvailable=2))
    assert {f["id"]: f["severity"] for f in run_cli(tmp_path)[1]["findings"]} == expected


def test_spread_is_not_required_for_a_single_replica(tmp_path):
    write(tmp_path, "web.json", workload(replicas=1, lifecycle=PRESTOP))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["single-replica"]


def test_probe_rules_per_container(tmp_path):
    aggressive = {"readinessProbe": {"httpGet": {"path": "/r", "port": 80}},
                  "livenessProbe": {"httpGet": {"path": "/l", "port": 80}, "periodSeconds": 2, "failureThreshold": 2}}
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=aggressive), pdb(minAvailable=2))
    finding = by_rule(run_cli(tmp_path)[1])["liveness-too-aggressive"]
    assert finding["severity"] == "major" and finding["summary"].startswith("app: ")
    same = {"readinessProbe": {"httpGet": {"path": "/h", "port": 80}}, "livenessProbe": {"httpGet": {"path": "/h", "port": 80}}}
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=same), pdb(minAvailable=2))
    assert [f["id"] for f in run_cli(tmp_path)[1]["findings"]] == ["liveness-equals-readiness"]


def test_workers_without_ports_do_not_need_readiness_and_liveness_is_never_demanded(tmp_path):
    write(tmp_path, "w.json", workload("worker", ports=False, probes={}, spread=OK_SPREAD, labels={"app": "worker"}), pdb("p", selector={"matchLabels": {"app": "worker"}},
                                                                                                                     minAvailable=2))
    assert run_cli(tmp_path)[1]["findings"] == []


def test_portless_sidecar_without_probes_does_not_fail_a_serving_workload(tmp_path):
    # pod readiness is the AND of its containers' readiness: a log shipper next to a probed web container needs no readiness probe
    deployment = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    deployment["spec"]["template"]["spec"]["containers"].append({"name": "log-shipper", "image": "registry.example/shipper:1"})
    write(tmp_path, "web.json", deployment, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert (code, data["findings"], data["status"]) == (0, [], "ok")
    # the serving container still needs its own readiness probe
    deployment["spec"]["template"]["spec"]["containers"][0].pop("readinessProbe")
    write(tmp_path, "web.json", deployment, pdb(minAvailable=2))
    findings = run_cli(tmp_path)[1]["findings"]
    assert [(f["id"], f["severity"], f["summary"].split(":")[0]) for f in findings] == [("readiness-missing", "major", "app")]


@pytest.mark.parametrize("canary", ["AKIAIOSFODNN7EXAMPLE", "ghp_aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "https://svc-user:Sup3rS3cret@db.internal/x"])
def test_exec_probe_commands_with_credentials_never_reach_the_output(tmp_path, canary):
    command = ["sh", "-c", f"curl -H 'Authorization: Bearer {canary}' http://localhost/health"]
    probes = {"readinessProbe": {"exec": {"command": command}}, "livenessProbe": {"exec": {"command": command}}}
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=probes), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert [f["id"] for f in data["findings"]] == ["liveness-equals-readiness"]
    assert canary not in json.dumps(data) and "Sup3rS3cret" not in json.dumps(data)


# the first literal is split so no source line has a provider-key shape; the test checks that its first 12 characters are never echoed
@pytest.mark.parametrize("secret", ["sk" "_live_" "4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a", "x" * 5000])
def test_rejected_manifest_values_are_never_echoed(tmp_path, secret):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, grace=secret,
                                         probes={"readinessProbe": {"httpGet": {"path": "/r", "port": 80}, "periodSeconds": secret}}),
          pdb(minAvailable=2))
    data = run_cli(tmp_path)[1]
    assert {f["id"] for f in data["findings"]} == {"shutdown-invalid", "probe-invalid"}
    assert secret[:12] not in json.dumps(data) and all(len(f["summary"]) <= 200 for f in data["findings"])


@pytest.mark.parametrize("probe", [
    {"livenessProbe": {"httpGet": {"path": "/l", "port": 80}, "successThreshold": 2}},      # API: must be 1
    {"readinessProbe": {"httpGet": {"path": "/r", "port": 80}, "periodSeconds": 0}},          # API: >= 1
    {"readinessProbe": {"httpGet": {"path": "/r", "port": 80}, "failureThreshold": "3"}},
    {"readinessProbe": {"periodSeconds": 5}},                                                  # no handler
])
def test_probe_values_the_api_would_refuse_are_findings_not_crashes(tmp_path, probe):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=probe), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and by_rule(data)["probe-invalid"]["severity"] == "major"


@pytest.mark.parametrize(("grace", "lifecycle", "expected"), [
    (10, {"preStop": {"sleep": {"seconds": 20}}}, {"sigkill-before-drain": "blocker"}),
    (10, {"preStop": {"exec": {"command": ["/bin/sh", "-c", "sleep 20 && true"]}}}, {"sigkill-before-drain": "blocker"}),
    (10, {"preStop": {"exec": {"command": ["sleep", "10"]}}}, {"thin-margin": "minor"}),
    (12, {"preStop": {"sleep": {"seconds": 10}}}, {"thin-margin": "minor"}),
    (15, {"preStop": {"sleep": {"seconds": 10}}}, {}),
    (0, PRESTOP, {"grace-zero": "blocker"}),
    (None, PRESTOP, {}),                                        # default 30 s
    (30, None, {"no-prestop": "minor"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 30s"]}}}, {"sigkill-before-drain": "blocker"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 1m"]}}}, {"sigkill-before-drain": "blocker"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 19.5"]}}}, {"thin-margin": "minor"}),
    (100, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 1m 30s"]}}}, {}),
])
def test_graceful_shutdown_rules(tmp_path, grace, lifecycle, expected):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=lifecycle, grace=grace), pdb(minAvailable=2))
    assert {f["id"]: f["severity"] for f in run_cli(tmp_path)[1]["findings"]} == expected


def test_invalid_grace_period_is_reported(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, grace="soon"), pdb(minAvailable=2))
    assert by_rule(run_cli(tmp_path)[1])["shutdown-invalid"]["severity"] == "major"


def test_findings_are_sorted_by_severity_then_location_and_deterministic(tmp_path):
    write(tmp_path, "b.json", workload("b", probes={}, labels={"app": "b"}))
    write(tmp_path, "a.json", workload("a", probes={}, labels={"app": "a"}))
    first = run_cli(tmp_path)[1]
    assert first["findings"] == run_cli(tmp_path)[1]["findings"]
    keys = [(f["severity"], f["location"]) for f in first["findings"]]
    order = ["blocker", "major", "minor", "info"]
    assert keys == sorted(keys, key=lambda k: (order.index(k[0]), k[1]))
    assert first["files_scanned"] == 2 and first["workloads_checked"] == 2


def test_list_documents_and_nested_directories(tmp_path):
    nested = tmp_path / "deploy" / "prod"
    nested.mkdir(parents=True)
    write(nested, "all.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 0 and (data["workloads_checked"], data["pdbs_found"]) == (1, 1)
    code, data = run_cli(tmp_path, "--path", "deploy")
    assert code == 0 and data["files_scanned"] == 1
    code, data = run_cli(tmp_path, "--path", "deploy/prod/all.json")
    assert code == 0 and data["files_scanned"] == 1


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
def test_yaml_multi_document_manifests_are_read_when_pyyaml_imports(tmp_path):
    import yaml

    docs = [workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2), {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "s"}}]
    (tmp_path / "all.yaml").write_text("---\n".join(yaml.safe_dump(d) for d in docs), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 0 and (data["workloads_checked"], data["pdbs_found"], data["skipped"]) == (1, 1, [])


def test_yaml_is_reported_as_skipped_when_pyyaml_is_missing_and_json_still_works(tmp_path):
    (tmp_path / "bare.yaml").write_text(json.dumps(workload("yamlonly", probes={})), encoding="utf-8")   # JSON is valid YAML
    write(tmp_path, "bare.json", workload("jsononly", probes={}, labels={"app": "j"}))
    module = load_module()
    module.yaml = None
    result = module._scan(tmp_path, tmp_path)
    assert result["skipped"] == ["yaml:skipped:tool-missing (1 files)"]
    assert {f["workload"] for f in result["findings"] if f["id"] != "scan/incomplete"} == {"shop/Deployment/jsononly"} and result["status"] == "fail"
    assert result["files_scanned"] == 2


def test_template_syntax_and_non_manifests_are_skipped_without_crashing(tmp_path):
    (tmp_path / "chart.yaml").write_text("kind: {{ .Values.kind }}\nname: [unclosed\n", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"name": "not a manifest"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text('{"kind": ', encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"] and "unparseable:chart.yaml" in data["findings"][0]["summary"]
    assert sorted(s.split(":")[0] + ":" + s.split(":")[1] for s in data["skipped"]) == ["unparseable:broken.json", "unparseable:chart.yaml"]


# --- bounds -------------------------------------------------------------------------------------------------------


def test_large_binary_and_symlinked_files_are_skipped(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside, "evil.json", workload("outside", probes={}))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.json").write_text(" " * (1024 * 1024 + 1) + '{"kind": "Deployment"}', encoding="utf-8")
    (root / "bin.yaml").write_bytes(b"kind: Deployment\x00\x01\x02")
    (root / "linked.json").symlink_to(outside / "evil.json")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    write(root, "ok.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    code, data = run_cli(root)
    assert code == 1 and data["workloads_checked"] == 1 and [f["id"] for f in data["findings"]] == ["scan/incomplete"]
    assert data["files_scanned"] == 3
    assert data["skipped"] == ["binary:bin.yaml", "symlink:linkdir", "symlink:linked.json", "too-large:big.json"]


def test_a_file_of_exactly_one_mebibyte_is_still_read(tmp_path):
    text = json.dumps(workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    (tmp_path / "exact.json").write_text(text + " " * (1024 * 1024 - len(text)), encoding="utf-8")
    assert (tmp_path / "exact.json").stat().st_size == 1024 * 1024
    code, data = run_cli(tmp_path)
    assert data["workloads_checked"] == 1 and data["skipped"] == [] and code == 1      # no PDB: major


def test_scan_stops_at_two_thousand_files(tmp_path):
    for index in range(2001):
        (tmp_path / f"f{index:04d}.json").write_text("{}", encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["files_scanned"] == 2000 and data["skipped"] == ["limit:2000-files"]
    assert data["status"] == "fail" and [f["id"] for f in data["findings"]] == ["scan/incomplete"]


def test_exactly_two_thousand_files_are_not_a_truncation(tmp_path):
    for index in range(2000):
        (tmp_path / f"f{index:04d}.json").write_text("{}", encoding="utf-8")
    data = run_cli(tmp_path)[1]
    assert data["files_scanned"] == 2000 and data["skipped"] == []


def test_vendor_and_git_directories_are_not_scanned(tmp_path):
    for skipped in (".git", "node_modules", "vendor"):
        (tmp_path / skipped).mkdir()
        write(tmp_path / skipped, "x.json", workload("hidden", probes={}))
    data = run_cli(tmp_path)[1]
    assert data["files_scanned"] == 0 and data["workloads_checked"] == 0


# --- path handling --------------------------------------------------------------------------------------------------


def test_path_outside_the_root_or_missing_is_an_input_error(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    code, data = run_cli(root, "--path", "..")
    assert code == 2 and data["status"] == "error" and "leaves the repository root" in data["error"]["message"]
    code, data = run_cli(root, "--path", "nope")
    assert code == 2 and data["status"] == "error" and "does not exist" in data["error"]["message"]


def test_path_that_is_a_symlink_to_outside_the_root_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    code, data = run_cli(root, "--path", "escape")
    assert code == 2 and "leaves the repository root" in data["error"]["message"]


def test_empty_tree_is_incomplete(tmp_path):
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["files_scanned"] == 0
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"] and data["summary"].startswith("1 finding(s) in 0 workload(s)")


def test_a_budget_with_no_field_is_reported_once_at_the_budget_file(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdb.json", pdb())
    assert [(f["id"], f["location"]) for f in run_cli(tmp_path)[1]["findings"]] == [("pdb-invalid", "pdb.json")]


def test_http_probes_on_different_ports_are_different_targets(tmp_path):
    probes = {"readinessProbe": {"httpGet": {"path": "/healthz", "port": 8080}}, "livenessProbe": {"httpGet": {"path": "/healthz", "port": 9090}}}
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=probes), pdb(minAvailable=2))
    assert run_cli(tmp_path)[1]["findings"] == []
