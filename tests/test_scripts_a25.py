"""scripts/resil_manifest_check.py: fixture trees in tmp_path, findings, exit codes, JSON shape and the dry-run contract."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
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
    (10, {"preStop": {"exec": {"command": ["/bin/sh", "-c", "sleep 20 && true"]}}}, {"scan/incomplete": "major"}),
    (10, {"preStop": {"exec": {"command": ["sleep", "10"]}}}, {"thin-margin": "minor"}),
    (12, {"preStop": {"sleep": {"seconds": 10}}}, {"thin-margin": "minor"}),
    (15, {"preStop": {"sleep": {"seconds": 10}}}, {}),
    (0, PRESTOP, {"grace-zero": "blocker"}),
    (None, PRESTOP, {}),                                        # default 30 s
    (30, None, {"no-prestop": "minor"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 30s"]}}}, {"sigkill-before-drain": "blocker"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 1m"]}}}, {"sigkill-before-drain": "blocker"}),
    (20, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 19.5"]}}}, {"scan/incomplete": "major"}),
    (100, {"preStop": {"exec": {"command": ["sh", "-c", "sleep 1m 30s"]}}}, {"scan/incomplete": "major"}),
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
    assert data["files_scanned"] == 2  # bin.yaml and ok.json were read; big.json was over the per-file cap and was never opened
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


# --- incomplete analysis: every input or part not read is a reason and fails the scan --------------------------------


def test_an_unlistable_directory_fails_the_scan_and_is_named(tmp_path, monkeypatch):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    locked = tmp_path / "locked"
    locked.mkdir()
    write(locked, "hidden.json", workload("hidden", spread=OK_SPREAD, lifecycle=PRESTOP))
    listed = os.scandir

    def refuse(path="."):
        if Path(path) == locked:
            raise PermissionError(13, "Permission denied", str(path))
        return listed(path)

    monkeypatch.setattr(os, "scandir", refuse)
    result = load_module()._scan(tmp_path, tmp_path)
    assert result["status"] == "fail" and result["workloads_checked"] == 1
    assert result["skipped"] == ["unreadable-directory:locked:PermissionError"]
    assert [f["id"] for f in result["findings"]] == ["scan/incomplete"]


@pytest.mark.parametrize(("lifecycle", "reason"), [
    ({"preStop": {"exec": {"command": ["sh", "-c", "sleep $DRAIN_SECONDS"]}}}, "preStop exec command is not a recognised sleep form"),
    ({"preStop": {"exec": {"command": "sleep 30"}}}, "exec.command is a string, not an argument list"),
    ({"preStop": {"exec": {"command": ["sh", "-c", {"script": "sleep 30"}]}}}, "exec.command has an argument that is not a string or number"),
    ({"preStop": {"sleep": {"seconds": "soon"}}}, "preStop sleep.seconds is not an integer"),
])
def test_preStop_input_that_cannot_be_read_fails_the_scan_with_a_reason(tmp_path, lifecycle, reason):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=lifecycle, grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and "scan/incomplete" in [f["id"] for f in data["findings"]]
    assert any(reason in skip for skip in data["skipped"])


def test_a_scalar_exec_probe_command_is_a_skip_reason(tmp_path):
    probes = {"readinessProbe": {"exec": {"command": "curl localhost/ready"}}, "livenessProbe": {"httpGet": {"path": "/l", "port": 80}}}
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP, probes=probes), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and "scan/incomplete" in [f["id"] for f in data["findings"]]
    assert any("exec.command is a string" in skip for skip in data["skipped"])


@pytest.mark.parametrize("replicas", ["3", -1, 2.5])
def test_invalid_replicas_are_a_major_finding_and_skip_the_budget_rules(tmp_path, replicas):
    write(tmp_path, "web.json", workload(replicas=replicas, spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail"
    assert [(f["id"], f["severity"], f["summary"]) for f in data["findings"]] == [
        ("replicas-invalid", "major", "replicas must be a non-negative integer")]


def test_an_invalid_hpa_minimum_is_a_major_finding(tmp_path):
    write(tmp_path, "web.json", workload(hpa_style=True, spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "hpa.json", hpa(minimum="2"))
    code, data = run_cli(tmp_path)
    assert code == 1 and [(f["id"], f["summary"]) for f in data["findings"]] == [
        ("replicas-invalid", "HorizontalPodAutoscaler minReplicas must be a non-negative integer")]


def test_a_symlinked_path_is_reported_like_a_symlink_met_during_the_walk(tmp_path):
    write(tmp_path, "real.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    (tmp_path / "link.json").symlink_to(tmp_path / "real.json")
    code, data = run_cli(tmp_path, "--path", "link.json")
    assert code == 1 and data["status"] == "fail" and data["files_scanned"] == 0
    assert data["skipped"] == ["symlink:link.json"] and [f["id"] for f in data["findings"]] == ["scan/incomplete"]


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
def test_an_alias_bomb_is_an_unparseable_skip_and_is_never_expanded(tmp_path):
    lines = ["l0: &l0 [lol, lol, lol, lol, lol, lol, lol, lol, lol]"]
    lines += [f"l{level}: &l{level} [" + ", ".join([f"*l{level - 1}"] * 9) + "]" for level in range(1, 8)]
    lines += ["apiVersion: apps/v1", "kind: Deployment", "metadata: {name: web, namespace: ns}", "spec:", "  replicas: 3",
              "  template:", "    metadata: {labels: {app: web}}", "    spec:", "      containers:", "      - name: app",
              "        image: x", "        lifecycle:", "          preStop:", "            exec:", "              command: [sh, -c, *l7]"]
    (tmp_path / "bomb.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    started = time.monotonic()
    result = load_module()._scan(tmp_path, tmp_path)
    assert time.monotonic() - started < 5
    assert result["status"] == "fail" and result["workloads_checked"] == 0
    assert result["skipped"] == ["unparseable:bomb.yaml:more-than-200000-nodes"]


def test_a_non_scalar_topology_key_is_a_zone_spread_finding_not_a_crash(tmp_path):
    spread = [{"maxSkew": 1, "topologyKey": {"a": 1}, "whenUnsatisfiable": "DoNotSchedule"}]
    write(tmp_path, "web.json", workload(spread=spread, lifecycle=PRESTOP), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and by_rule(data)["zone-spread-missing"]["severity"] == "major"


def test_a_non_scalar_selector_key_leaves_the_budget_unanalysed_and_does_not_crash(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    selector = {"matchExpressions": [{"key": {"a": 1}, "operator": "In", "values": ["web"]}]}
    write(tmp_path, "pdb.json", {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": "p", "namespace": "shop"},
                                 "spec": {"minAvailable": 2, "selector": selector}})
    data = run_cli(tmp_path)[1]
    assert "pdb-missing" not in by_rule(data)
    assert data["skipped"] == ["malformed-workload:pdb.json:spec.selector.matchExpressions.key",
                               "web.json:shop/Deployment/web: a malformed PodDisruptionBudget may select this workload: its budget is not analysed"]


# --- malformed workloads: a value that is not a string or number is never defaulted, it is a skip reason ---------------

# A synthetic canary, assembled from parts so the source holds no credential-shaped literal (secret-scanning push protection).
MALFORMED_VALUE = "sk_" + "live_" + "4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a"


def test_a_namespace_given_as_a_mapping_is_a_malformed_workload_not_a_default(tmp_path):
    write(tmp_path, "web.json", workload(ns={"team": MALFORMED_VALUE}, spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:metadata.namespace"]
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"] and MALFORMED_VALUE not in json.dumps(data)


def test_a_label_value_given_as_a_list_is_a_malformed_workload_not_an_empty_label(tmp_path):
    write(tmp_path, "web.json", workload(labels={"app": ["web", MALFORMED_VALUE]}, spread=OK_SPREAD, lifecycle=PRESTOP),
          pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:spec.template.metadata.labels"]
    assert MALFORMED_VALUE not in json.dumps(data)


def test_a_workload_without_a_name_is_a_malformed_workload_not_an_empty_name(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    del doc["metadata"]["name"]
    write(tmp_path, "web.json", doc, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:metadata.name"]


def test_a_budget_selector_value_given_as_a_list_is_a_malformed_budget(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdb.json", pdb(selector={"matchLabels": {"app": ["web"]}}, minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["workloads_checked"] == 1
    assert data["skipped"] == ["malformed-workload:pdb.json:spec.selector.matchLabels",
                               "web.json:shop/Deployment/web: a malformed PodDisruptionBudget may select this workload: its budget is not analysed"]
    assert "scan/incomplete" in [f["id"] for f in data["findings"]]


def test_a_container_name_given_as_a_list_is_a_malformed_workload_not_a_container_default(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    doc["spec"]["template"]["spec"]["containers"][0]["name"] = [MALFORMED_VALUE]
    write(tmp_path, "web.json", doc, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:spec.template.spec.containers.name"]
    assert MALFORMED_VALUE not in json.dumps(data)


# --- preStop hooks and container shapes that cannot be read are skip reasons, never zero, never coerced to empty ------

EXEC_CANARY = "Sup3rS3cret-canary-9f2a"


@pytest.mark.parametrize(("command", "analysed"), [
    (["/bin/sleep", "30"], True),
    (["sh", "-c", "/bin/sleep 30"], True),
    (["sh", "-c", "`sleep 30`"], False),
    (["sleep", "30s"], True),
    (["sleep", "30", "extra"], False),
    (["sh", "-c", f"sleep 30 && curl https://svc-user:{EXEC_CANARY}@db.internal/x"], False),
])
def test_preStop_exec_forms_are_read_only_as_sleep_n_and_otherwise_not_analysed(tmp_path, command, analysed):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {"exec": {"command": command}}}, grace=20),
          pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    ids = [f["id"] for f in data["findings"]]
    if analysed:
        assert code == 1 and ids == ["sigkill-before-drain"] and data["skipped"] == []
    else:
        assert code == 1 and data["status"] == "fail" and "scan/incomplete" in ids and "sigkill-before-drain" not in ids
        assert data["skipped"] == ["web.json:shop/Deployment/web container app: preStop exec command is not a recognised sleep form: "
                                   "not analysed"]
    assert EXEC_CANARY not in json.dumps(data)


@pytest.mark.parametrize(("hook", "label"), [
    ({"httpGet": {"path": "/drain", "port": 80}}, "httpGet"),
    ({"tcpSocket": {"port": 80}}, "tcpSocket"),
    ({"grpc": {"port": 80}}, "unrecognised"),
])
def test_a_preStop_hook_without_a_computable_duration_fails_the_scan_and_is_not_counted_as_zero(tmp_path, hook, label):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": hook}, grace=10), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and [f["id"] for f in data["findings"]] == ["scan/incomplete"]
    assert data["skipped"] == [f"web.json:shop/Deployment/web container app: preStop {label} hook has no computable duration: "
                               "not analysed"]


CONTAINER_CANARY = "sk_" + "live_" + "4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a"


@pytest.mark.parametrize(("containers", "path"), [
    ({"name": CONTAINER_CANARY, "image": "x"}, "spec.template.spec.containers:mapping"),
    (["just-" + CONTAINER_CANARY, {"name": "app", "image": "x", "ports": [{"containerPort": 80}]}],
     "spec.template.spec.containers[0]:string"),
])
def test_containers_that_are_not_a_sequence_of_mappings_are_malformed_not_coerced_to_empty(tmp_path, containers, path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    doc["spec"]["template"]["spec"]["containers"] = containers
    write(tmp_path, "web.json", doc, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == [f"malformed-workload:web.json:{path}"]
    assert CONTAINER_CANARY not in json.dumps(data)


@pytest.mark.parametrize(("selector", "path"), [
    ({"matchLabels": ["web"]}, "spec.selector.matchLabels"),
    ({"matchExpressions": {"key": "app"}}, "spec.selector.matchExpressions"),
    ({"matchExpressions": [{"key": "app", "operator": "In", "values": "web"}]}, "spec.selector.matchExpressions.values"),
    ({"matchExpressions": [{"key": "app", "operator": "In", "values": [1]}]}, "spec.selector.matchExpressions.values"),
    ({"matchExpressions": [{"key": 1, "operator": "In", "values": ["web"]}]}, "spec.selector.matchExpressions.key"),
    ("web", "spec.selector"),
])
def test_a_selector_shape_that_would_be_coerced_to_empty_is_a_malformed_budget(tmp_path, selector, path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdb.json", pdb(selector=selector, minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail"
    assert f"malformed-workload:pdb.json:{path}" in data["skipped"]
    assert any("its budget is not analysed" in skip for skip in data["skipped"])
    assert "pdb-missing" not in by_rule(data) and "pdb-allows-quorum-loss" not in by_rule(data)


def _expanded_alias_document(index: int, pad: int) -> str:
    """One document of about 141k expanded nodes from about 300 bytes of YAML aliases: each fits the per-document cap alone."""
    lines = ["apiVersion: v1", "kind: ConfigMap", f"metadata: {{name: bomb{index}}}", "l0: &l0 [a, a, a, a, a, a, a, a, a]"]
    lines += [f"l{level}: &l{level} [" + ", ".join([f"*l{level - 1}"] * 9) + "]" for level in range(1, 5)]
    lines += ["x: [*l4]", "# " + "p" * pad]
    return "---\n" + "\n".join(lines) + "\n"


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
def test_a_file_whose_documents_together_exceed_the_file_budget_fails_and_an_ordinary_multi_document_file_passes(tmp_path):
    import yaml
    per_document = 1_048_000 // 2264
    pad = per_document - len(_expanded_alias_document(0, 0)) - 10
    text = "".join(_expanded_alias_document(index, pad) for index in range(2264))
    assert len(text.encode()) <= 1 << 20
    (tmp_path / "bomb.yaml").write_text(text, encoding="utf-8")
    started = time.monotonic()
    data = run_cli(tmp_path)[1]
    assert time.monotonic() - started < 60
    assert data["status"] == "fail" and data["skipped"] == ["unparseable:bomb.yaml:more-than-1000000-nodes-in-file"]
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    docs = []
    for index in range(150):
        docs.append(workload(name=f"app{index}", spread=OK_SPREAD, lifecycle=PRESTOP))
        docs.append(pdb(name=f"pdb{index}", selector={"matchLabels": {"app": f"app{index}"}}, minAvailable=2))
    (ordinary / "all.yaml").write_text("".join("---\n" + yaml.safe_dump(doc, sort_keys=False) for doc in docs), encoding="utf-8")
    code, data = run_cli(ordinary)
    assert code == 0 and data["status"] == "ok" and data["skipped"] == [] and data["workloads_checked"] == 150 and data["findings"] == []


def test_a_generated_name_stands_in_for_a_missing_name_on_a_workload_and_a_budget(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    del doc["metadata"]["name"]
    doc["metadata"]["generateName"] = "web-"
    budget = pdb(minAvailable=2)
    del budget["metadata"]["name"]
    budget["metadata"]["generateName"] = "web-pdb-"
    write(tmp_path, "web.json", doc, budget)
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok" and data["skipped"] == []
    assert data["workloads_checked"] == 1 and data["pdbs_found"] == 1 and data["findings"] == []


@pytest.mark.parametrize("command", [["sleep", "9" * 5000], ["sh", "-c", "sleep " + "9" * 5000]])
def test_a_digit_run_longer_than_the_bound_is_not_a_recognised_sleep_form_and_never_raises(tmp_path, command):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {"exec": {"command": command}}}, grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail"
    assert data["skipped"] == ["web.json:shop/Deployment/web container app: preStop exec command is not a recognised sleep form: "
                               "not analysed"]
    assert "9" * 50 not in json.dumps(data)


@pytest.mark.parametrize(("command", "seconds"), [
    (["sh", "-c", "sleep 30s"], 30),
    (["sh", "-c", "sleep 1m"], 60),
    (["sh", "-c", "sleep 1h"], 3600),
    (["sleep", "1h"], 3600),
])
def test_sleep_durations_are_an_integer_with_one_optional_suffix(command, seconds):
    skipped: list[str] = []
    assert load_module()._exec_seconds({"command": command}, "here", skipped) == seconds and skipped == []


def test_a_float_sleep_duration_is_still_a_not_recognised_sleep_form():
    skipped: list[str] = []
    assert load_module()._exec_seconds({"command": ["sh", "-c", "sleep 1.5"]}, "here", skipped) == 0
    assert skipped == ["here: preStop exec command is not a recognised sleep form: not analysed"]


@pytest.mark.parametrize(("target", "path"), [("web", "spec.scaleTargetRef"), ({"kind": "Deployment"}, "spec.scaleTargetRef.name")])
def test_an_hpa_whose_scale_target_is_malformed_fails_the_scan_and_is_not_ignored(tmp_path, target, path):
    write(tmp_path, "web.json", workload(replicas=1, spread=OK_SPREAD, lifecycle=PRESTOP))
    scaler = hpa(minimum=3)
    scaler["spec"]["scaleTargetRef"] = target
    write(tmp_path, "hpa.json", scaler)
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 1
    assert data["skipped"] == [f"malformed-workload:hpa.json:HorizontalPodAutoscaler:{path}"]


def test_a_top_level_array_of_workloads_fails_the_scan_and_its_workloads_are_not_counted(tmp_path):
    doc = workload(grace=20, lifecycle={"preStop": {"exec": {"command": ["sh", "-c", "sleep 30"]}}})
    (tmp_path / "web.json").write_text(json.dumps([doc]), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["unparseable:web.json:top-level-sequence"]
    assert [f["id"] for f in data["findings"]] == ["scan/incomplete"]


@pytest.mark.parametrize(("items", "reason"), [
    (workload(spread=OK_SPREAD, lifecycle=PRESTOP), "list-items-not-a-sequence:web.json"),
    (["just-text"], "list-item-not-a-mapping:web.json:items[0]"),
])
def test_a_list_whose_items_is_not_a_sequence_or_holds_a_non_mapping_fails_the_scan(tmp_path, items, reason):
    write(tmp_path, "web.json", {"apiVersion": "v1", "kind": "List", "items": items})
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == [reason]


def test_a_workload_without_an_apiversion_is_a_skip_reason_and_fails_the_scan(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    del doc["apiVersion"]
    write(tmp_path, "web.json", doc, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:Deployment:apiVersion"]


def test_a_metadata_name_given_as_a_mapping_is_malformed_even_with_a_generate_name(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    doc["metadata"]["name"] = {"team": MALFORMED_VALUE}
    doc["metadata"]["generateName"] = "web-"
    write(tmp_path, "web.json", doc, pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["malformed-workload:web.json:metadata.name"]
    assert MALFORMED_VALUE not in json.dumps(data)


@pytest.mark.parametrize("seconds", [-5, 0])
def test_a_native_prestop_sleep_that_is_not_positive_is_a_skip_reason_and_fails(tmp_path, seconds):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {"sleep": {"seconds": seconds}}}, grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 1
    assert data["skipped"] == ["web.json:shop/Deployment/web container app: preStop sleep.seconds is not a positive integer of at most "
                               "9 digits: not analysed"]


def test_an_empty_prestop_hook_names_no_hook_kind_and_fails_the_scan(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {}}, grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail"
    assert data["skipped"] == ["web.json:shop/Deployment/web container app: preStop hook names no hook kind (exec, sleep, httpGet "
                               "or tcpSocket): not analysed"]


# --- round three: shell syntax never hides in a sleep path, every List entry and top-level document is accounted for ---

NOT_A_SLEEP_FORM = "web.json:shop/Deployment/web container app: preStop exec command is not a recognised sleep form: not analysed"


@pytest.mark.parametrize("script", ["/app/drain.sh;/bin/sleep 5", "/app/drain.sh&&/bin/sleep 5", "$(/app/drain.sh)/sleep 5",
                                    "/app/drain.sh /bin/sleep 5"])
def test_shell_syntax_before_a_sleep_path_is_not_a_sleep_form_and_fails_the_scan(tmp_path, script):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {"exec": {"command": ["sh", "-c", script]}}}, grace=20),
          pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and "sigkill-before-drain" not in by_rule(data)
    assert data["skipped"] == [NOT_A_SLEEP_FORM]


def test_shell_syntax_in_an_argv_program_path_is_not_a_sleep_form(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle={"preStop": {"exec": {"command": ["/app/drain.sh;/bin/sleep", "5"]}}},
                                         grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["skipped"] == [NOT_A_SLEEP_FORM]


@pytest.mark.parametrize(("command", "seconds"), [
    (["/bin/sleep", "5"], 5),
    (["sleep", "5"], 5),
    (["/usr/bin/sleep", "30s"], 30),
    (["sh", "-c", "/bin/sleep 5"], 5),
])
def test_plain_sleep_paths_and_suffixes_still_read(command, seconds):
    skipped: list[str] = []
    assert load_module()._exec_seconds({"command": command}, "here", skipped) == seconds and skipped == []


@pytest.mark.parametrize("kind_case", ["absent", "list"])
def test_a_list_entry_without_a_string_kind_is_a_reason_and_fails(tmp_path, kind_case):
    entry = workload(grace=20, lifecycle={"preStop": {"exec": {"command": ["sh", "-c", "sleep 30"]}}})
    if kind_case == "absent":
        del entry["kind"]
    else:
        entry["kind"] = ["Deployment"]
    (tmp_path / "web.json").write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": [entry]}), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["list-item-without-kind:web.json:items[0]"]


def test_a_top_level_document_with_apiversion_and_no_string_kind_is_a_reason_and_fails(tmp_path):
    doc = workload(grace=20, lifecycle={"preStop": {"exec": {"command": ["sh", "-c", "sleep 30"]}}})
    doc["kind"] = ["Deployment"]
    write(tmp_path, "web.json", doc)
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["document-without-kind:web.json"]


def test_a_valid_list_entry_is_still_analysed_beside_a_kindless_one(tmp_path):
    entry = workload(grace=20, lifecycle={"preStop": {"exec": {"command": ["sh", "-c", "sleep 30"]}}})
    kindless = {"apiVersion": "apps/v1", "metadata": {"name": "other", "namespace": "shop"}}
    write(tmp_path, "web.json", entry, pdb(minAvailable=2), kindless)
    code, data = run_cli(tmp_path)
    assert code == 1 and data["workloads_checked"] == 1 and "sigkill-before-drain" in by_rule(data)
    assert data["skipped"] == ["list-item-without-kind:web.json:items[2]"]


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
def test_a_playbook_that_mentions_kind_but_has_no_apiversion_is_out_of_scope(tmp_path):
    (tmp_path / "play.yaml").write_text("- name: provision\n  hosts: all\n  tasks:\n    - name: create a kind cluster\n"
                                        "      debug: msg=\"a kind cluster\"\n", encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok" and data["skipped"] == [] and data["findings"] == []


def test_a_scan_whose_files_exceed_the_scan_wide_budget_fails_and_names_the_file_where_it_runs_out(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_SCAN_NODES", 1000)  # each ConfigMap below is about 607 nodes: a and b cannot both fit
    for name in ("a.json", "b.json", "c.json"):
        config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"k": ["x"] * 600}}
        (tmp_path / name).write_text(json.dumps(config), encoding="utf-8")
    result = module._scan(tmp_path, tmp_path)
    assert result["status"] == "fail"
    assert result["skipped"] == ["not-read-after-scan-node-budget:1-files", "scan-node-budget-spent:b.json"]  # the scan sorts its skipped list
    assert [f["id"] for f in result["findings"]] == ["scan/incomplete"]


@pytest.mark.parametrize("lifecycle", [
    {"preStop": {"sleep": {"seconds": 999999999}}},
    {"preStop": {"exec": {"command": ["sleep", "999999999"]}}},
])
def test_a_prestop_sleep_above_the_shutdown_bound_is_a_reason_and_fails(tmp_path, lifecycle):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=lifecycle, grace=20), pdb(minAvailable=2))
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and "sigkill-before-drain" not in by_rule(data)
    assert data["skipped"] == ["web.json:shop/Deployment/web container app: preStop hook is longer than 86400 s, the bound of the "
                               "shutdown check: not analysed"]


def test_a_scan_wide_byte_budget_analyses_the_first_file_spends_on_the_second_and_does_not_read_the_third(tmp_path, monkeypatch):
    module = load_module()
    first = json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "a"}})
    second = json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "b", "labels": {"pad": "x" * 60}}})
    third = json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}})
    (tmp_path / "a.json").write_text(first, encoding="utf-8")
    (tmp_path / "b.json").write_text(second, encoding="utf-8")
    (tmp_path / "c.json").write_text(third, encoding="utf-8")
    # the first file fits and leaves len(second) - 1 bytes: the second does not fit, so the third is never read
    monkeypatch.setattr(module, "MAX_SCAN_BYTES", len(first) + len(second) - 1)
    result = module._scan(tmp_path, tmp_path)
    assert result["status"] == "fail" and result["files_scanned"] == 1
    assert result["skipped"] == ["not-read-after-scan-byte-budget:1-files", "scan-byte-budget-spent:b.json"]
    assert [f["id"] for f in result["findings"]] == ["scan/incomplete"]


def test_files_scanned_counts_the_files_read_not_the_candidates(tmp_path):
    write(tmp_path, "web.json", workload(spread=OK_SPREAD, lifecycle=PRESTOP), pdb(minAvailable=2))
    (tmp_path / "broken.json").write_text('{"apiVersion": "v1", "kind": ', encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["workloads_checked"] == 1
    assert data["files_scanned"] == 2
    assert data["skipped"] == ["unparseable:broken.json:JSONDecodeError"]


def test_indexed_selector_verdicts_equal_the_brute_force_comparison_on_random_sets():
    import random
    module = load_module()
    rng = random.Random(20261021)  # a fixed seed: the same 200 workloads and 60 budgets on every run
    keys, values = ["app", "tier", "zone"], ["a", "b", "c"]
    budgets = []
    for index in range(60):
        namespace = rng.choice(["ns1", "ns2"])
        roll = rng.random()
        if roll < 0.6:
            selector = {"matchLabels": {key: rng.choice(values) for key in rng.sample(keys, rng.randint(0, 2))}}
        elif roll < 0.8:
            selector = {"matchExpressions": [{"key": rng.choice(keys), "operator": "In", "values": [rng.choice(values)]}]}
        elif roll < 0.9:
            selector = {}
        else:
            selector = None
        doc = {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": f"b{index}", "namespace": namespace},
               "spec": {"minAvailable": 1, "selector": selector}}
        budgets.append((f"b{index}.json", doc, namespace))
    indexed = module._PdbIndex(budgets)
    for workload_index in range(200):
        namespace = rng.choice(["ns1", "ns2"])
        labels = {key: rng.choice(values) for key in rng.sample(keys, rng.randint(0, 3))}
        brute = [(rel, doc) for rel, doc, ns in budgets
                 if ns == namespace and module._selector_matches(module._d(doc.get("spec")).get("selector"), labels)]
        verdict = indexed.matching(namespace, labels)
        assert verdict is not None
        assert [id(doc) for _, doc in verdict] == [id(doc) for _, doc in brute], f"workload {workload_index} {namespace} {labels}"
    assert indexed.comparisons < 200 * len(budgets)


def test_the_selector_comparison_allowance_leaves_the_workloads_not_yet_compared_unanalysed_and_fails_the_scan(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_SCAN_SELECTOR_COMPARISONS", 5)
    for name in ("w1", "w2", "w3"):
        write(tmp_path, f"{name}.json", workload(name, spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "pdbs.json", *[pdb(name=f"p{index}", selector={"matchExpressions": [{"key": "tier", "operator": "Exists"}]},
                                      minAvailable=1) for index in range(3)])
    result = module._scan(tmp_path, tmp_path)
    assert result["status"] == "fail"
    assert result["skipped"] == ["not-compared-after-scan-selector-budget:2-workloads", "scan-selector-budget-spent:w1.json"]
    assert "scan/incomplete" in [f["id"] for f in result["findings"]]


# --- round four: rejected files are charged to the node allowance; the text gate reads apiVersion or any-case kind; the
# --- matchLabels keys are text, as the pod labels are --------------------------------------------------------------------


def _configmap(name):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"k": ["x"] * 600}}


def test_rejected_files_are_charged_to_the_scan_node_allowance_and_the_scan_fails(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_FILE_NODES", 1000)  # each file is two ConfigMaps of about 607 nodes: rejected at the file cap
    monkeypatch.setattr(module, "MAX_SCAN_NODES", 1500)
    for index in range(1, 5):
        listing = {"apiVersion": "v1", "kind": "List", "items": [_configmap(f"f{index}a"), _configmap(f"f{index}b")]}
        (tmp_path / f"f{index}.json").write_text(json.dumps(listing), encoding="utf-8")
    result = module._scan(tmp_path, tmp_path)
    assert result["status"] == "fail" and result["files_scanned"] == 2
    assert result["skipped"] == ["not-read-after-scan-node-budget:2-files", "scan-node-budget-spent:f2.json",
                                 "unparseable:f1.json:more-than-1000-nodes-in-file"]


def test_a_json_object_with_apiversion_and_no_kind_is_a_reason_and_fails(tmp_path):
    (tmp_path / "web.json").write_text(json.dumps({"apiVersion": "v1", "metadata": {"name": "x"}}), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["skipped"] == ["document-without-kind:web.json"]


def test_a_deployment_with_a_capitalised_kind_key_is_a_reason_and_is_not_analysed(tmp_path):
    doc = workload(spread=OK_SPREAD, lifecycle=PRESTOP)
    doc["Kind"] = doc.pop("kind")
    write(tmp_path, "web.json", doc)
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["workloads_checked"] == 0
    assert data["skipped"] == ["document-without-kind:web.json"]


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
@pytest.mark.parametrize(("name", "text"), [
    ("Chart.yaml", "apiVersion: v2\nname: app\nversion: 1.0.0\n"),
    ("kustomization.yaml", "apiVersion: kustomize.config.k8s.io/v1beta1\nresources:\n- deployment.yaml\n"),
])
def test_helm_chart_and_kustomization_files_without_a_kind_stay_silent(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok" and data["skipped"] == [] and data["findings"] == []


def test_a_file_with_neither_apiversion_nor_kind_stays_out_of_scope(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"metadata": {"name": "x"}}), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 0 and data["status"] == "ok" and data["skipped"] == [] and data["findings"] == []


@pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")
def test_an_integer_matchlabels_key_matches_the_quoted_pod_label_and_is_not_pdb_missing(tmp_path):
    write(tmp_path, "web.json", workload(labels={"1": "x"}, spread=OK_SPREAD, lifecycle=PRESTOP))
    (tmp_path / "pdb.yaml").write_text("apiVersion: policy/v1\nkind: PodDisruptionBudget\nmetadata: {name: p, namespace: shop}\n"
                                       "spec:\n  minAvailable: 2\n  selector:\n    matchLabels:\n      1: x\n", encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 0 and data["pdbs_found"] == 1 and "pdb-missing" not in by_rule(data) and data["findings"] == []


# --- round five: the selector allowance pays for the work inside each comparison; a capitalised Kind is a top-level sequence too


def test_a_budget_of_many_expressions_spends_the_selector_work_allowance_and_the_scan_fails_quickly(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_SCAN_SELECTOR_COMPARISONS", 5000)  # units: label pairs, candidates, expressions and their values
    budget = {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": "many", "namespace": "shop"},
              "spec": {"minAvailable": 1, "selector": {"matchExpressions": [{"key": "app", "operator": "Exists"}] * 25000}}}
    (tmp_path / "many.json").write_text(json.dumps(budget, separators=(",", ":")), encoding="utf-8")
    for name in ("w1", "w2", "w3"):
        write(tmp_path, f"{name}.json", workload(name, spread=OK_SPREAD, lifecycle=PRESTOP))
    started = time.monotonic()
    result = module._scan(tmp_path, tmp_path)
    assert time.monotonic() - started < 2
    assert result["status"] == "fail"
    assert result["skipped"] == ["not-compared-after-scan-selector-budget:2-workloads", "scan-selector-budget-spent:w1.json"]


@pytest.mark.parametrize("key", ["kind", "Kind"])
def test_a_top_level_sequence_of_objects_is_reported_whatever_the_case_of_kind(tmp_path, key):
    items = [{"apiVersion": "apps/v1", key: "Deployment", "metadata": {"name": "web"}}]
    (tmp_path / "web.json").write_text(json.dumps(items), encoding="utf-8")
    code, data = run_cli(tmp_path)
    assert code == 1 and data["status"] == "fail" and data["skipped"] == ["unparseable:web.json:top-level-sequence"]


# --- round six: a label pair with no filed budget costs nothing, so a workload with no candidate keeps its verdict ----------


def _two_expression_budget():
    expressions = [{"key": "tier", "operator": "DoesNotExist"}, {"key": "tier", "operator": "DoesNotExist"}]
    return {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": "tiers", "namespace": "ns"},
            "spec": {"minAvailable": 1, "selector": {"matchExpressions": expressions}}}


def test_a_workload_with_no_budget_to_compare_is_not_withheld_once_the_selector_allowance_is_spent(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_SCAN_SELECTOR_COMPARISONS", 2)
    write(tmp_path, "pdbs.json", _two_expression_budget())
    write(tmp_path, "w1.json", workload("w1", ns="ns", labels={"app": "web"}, spread=OK_SPREAD, lifecycle=PRESTOP))
    write(tmp_path, "w2.json", workload("w2", ns="other", labels={"team": "x"}, spread=OK_SPREAD, lifecycle=PRESTOP))
    result = module._scan(tmp_path, tmp_path)
    assert result["status"] == "fail"
    assert result["skipped"] == ["scan-selector-budget-spent:w1.json"]
    assert ("pdb-missing", "w2.json") in {(f["id"], f["location"]) for f in result["findings"]}


def test_the_index_pays_nothing_for_a_label_pair_without_a_filed_budget(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "MAX_SCAN_SELECTOR_COMPARISONS", 2)
    index = module._PdbIndex([("pdbs.json", _two_expression_budget(), "ns")])
    assert index.matching("ns", {"app": "web"}) is None and index.comparisons == 2  # the allowance is spent by the budget itself
    assert index.matching("other", {"team": "x"}) == [] and index.comparisons == 2  # no candidate: exact, nothing charged
