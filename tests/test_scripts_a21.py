"""Behaviour tests for scripts/k8s_manifest_check.py (A21): removed APIs and missing requests/limits in manifest trees.

Real-mode tests build fixture trees under tmp_path and run the script as a subprocess; the optional-dependency and bound
paths run the module in-process so PyYAML can be hidden and the file bound lowered.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT, _scrubbed_base_env, run_script

SCRIPT = ROOT / "scripts" / "k8s_manifest_check.py"
FULL_RESOURCES = {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}}


def deployment(name="web", resources=None, api_version="apps/v1", containers=None):
    container = {"name": "app", "image": "x"}
    if resources is not None:
        container["resources"] = resources
    return {"apiVersion": api_version, "kind": "Deployment", "metadata": {"name": name},
            "spec": {"template": {"spec": {"containers": containers or [container]}}}}


def put(root: Path, relative: str, document) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document) if not isinstance(document, str) else document, encoding="utf-8")
    return path


def scan(root: Path, *args: str):
    proc = run_script("k8s_manifest_check.py", "--root", str(root), "--json", *args)
    assert proc.stderr == "" or "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def rules(result: dict) -> list[tuple[str, str, str]]:
    return [(f["id"], f["severity"], f["location"]) for f in result["findings"]]


def load_module():
    spec = importlib.util.spec_from_file_location("k8s_manifest_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_in_process(module, root: Path, capsys, *args: str):
    code = module.AgentScript("A21", "k8s_manifest_check", module.run, description=module.__doc__, add_args=module.add_args).main(
        ["--root", str(root), "--json", *args])
    return code, json.loads(capsys.readouterr().out)


# --- dry run --------------------------------------------------------------------------------------------------


def test_dry_run_prints_only_canned_json_and_reads_nothing(tmp_path):
    put(tmp_path, "bad.json", deployment(api_version="extensions/v1beta1"))
    proc = run_script("k8s_manifest_check.py", "--dry-run", "--json", "--root", str(tmp_path))
    assert proc.returncode == 0 and "Traceback" not in proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "ok" and result["dry_run"] is True and result["agent"] == "A21" and result["script"] == "k8s_manifest_check"
    assert (result["files_scanned"], result["manifests_scanned"], result["findings"]) == (0, 0, [])
    assert json.loads(run_script("k8s_manifest_check.py", "--dry-run", "--json").stdout) == result | {"summary": result["summary"]}


def test_dry_run_target_version_defaults_to_the_newest_removal_in_the_table():
    result = json.loads(run_script("k8s_manifest_check.py", "--dry-run", "--json").stdout)
    assert result["target_version"] == "1.32"
    assert json.loads(run_script("k8s_manifest_check.py", "--dry-run", "--json", "--target-version", "1.25").stdout)["target_version"] == "1.25"


# --- removed APIs ---------------------------------------------------------------------------------------------


def test_removed_api_in_a_json_manifest_is_a_blocker_and_fails_the_scan(tmp_path):
    put(tmp_path, "deploy/ingress.json", {"apiVersion": "extensions/v1beta1", "kind": "Ingress", "metadata": {"name": "site"}})
    code, result = scan(tmp_path, "--target-version", "1.22")
    assert code == 1 and result["status"] == "fail" and result["target_version"] == "1.22"
    assert rules(result) == [("removed-api/extensions/v1beta1/Ingress/site", "blocker", "deploy/ingress.json")]
    assert "removed in 1.22" in result["findings"][0]["summary"] and "networking.k8s.io/v1" in result["findings"][0]["summary"]
    assert (result["files_scanned"], result["manifests_scanned"], result["truncated"], result["skipped"]) == (1, 1, False, [])
    assert result["agent"] == "A21" and result["script"] == "k8s_manifest_check" and "1 finding(s) in 1 manifest(s)" in result["summary"]


def test_api_removed_in_the_next_minor_is_only_a_minor_finding(tmp_path):
    put(tmp_path, "ing.json", {"apiVersion": "extensions/v1beta1", "kind": "Ingress", "metadata": {"name": "site"}})
    code, result = scan(tmp_path, "--target-version", "1.21")
    assert code == 0 and result["status"] == "ok" and [f["severity"] for f in result["findings"]] == ["minor"]
    code, result = scan(tmp_path, "--target-version", "1.20")
    assert code == 0 and result["findings"] == []


def test_default_target_flags_every_table_api_and_the_scan_is_deterministic(tmp_path):
    put(tmp_path, "b.json", {"apiVersion": "flowcontrol.apiserver.k8s.io/v1beta3", "kind": "FlowSchema", "metadata": {"name": "fs"}})
    put(tmp_path, "a.json", {"apiVersion": "policy/v1beta1", "kind": "PodDisruptionBudget", "metadata": {"name": "pdb"}})
    first = scan(tmp_path)
    assert first[0] == 1 and first[1]["target_version"] == "1.32"
    assert rules(first[1]) == [("removed-api/policy/v1beta1/PodDisruptionBudget/pdb", "blocker", "a.json"),
                               ("removed-api/flowcontrol.apiserver.k8s.io/v1beta3/FlowSchema/fs", "blocker", "b.json")]
    assert scan(tmp_path) == first


# --- requests and limits --------------------------------------------------------------------------------------


def test_missing_requests_are_major_and_fail_missing_limits_are_minor_and_pass(tmp_path):
    put(tmp_path, "bare.json", deployment("bare"))
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 1
    assert rules(result) == [
        ("missing-request/Deployment/bare/app/cpu", "major", "bare.json"), ("missing-request/Deployment/bare/app/memory", "major", "bare.json"),
        ("missing-limit/Deployment/bare/app/cpu", "minor", "bare.json"), ("missing-limit/Deployment/bare/app/memory", "minor", "bare.json")]
    assert result["findings"][0]["rule"] == "missing-request" and "has no cpu request" in result["findings"][0]["summary"]
    only_requests = tmp_path / "bare.json"
    only_requests.write_text(json.dumps(deployment("bare", {"requests": {"cpu": "100m", "memory": "64Mi"}})), encoding="utf-8")
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 0 and result["status"] == "ok" and [f["severity"] for f in result["findings"]] == ["minor", "minor"]
    only_requests.write_text(json.dumps(deployment("bare", FULL_RESOURCES)), encoding="utf-8")
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 0 and result["findings"] == []


def test_a_limit_without_a_request_counts_as_the_request_because_kubernetes_copies_it(tmp_path):
    put(tmp_path, "limits.json", deployment("l", {"limits": {"cpu": "500m", "memory": "1Gi"}}))
    put(tmp_path, "cpu-only.json", deployment("c", {"limits": {"cpu": "500m"}}))
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 1 and sorted(rules(result)) == [
        ("missing-limit/Deployment/c/app/memory", "minor", "cpu-only.json"),
        ("missing-request/Deployment/c/app/memory", "major", "cpu-only.json")]


def test_empty_quantities_count_as_missing_and_partial_resources_report_only_the_gap(tmp_path):
    put(tmp_path, "d.json", deployment("d", {"requests": {"cpu": "100m", "memory": ""}, "limits": {"cpu": "200m", "memory": "128Mi"}}))
    _, result = scan(tmp_path, "--target-version", "1.30")
    assert rules(result) == [("missing-request/Deployment/d/app/memory", "major", "d.json")]


def test_every_workload_kind_and_init_containers_are_checked(tmp_path):
    pod_spec = {"containers": [{"name": "c", "resources": FULL_RESOURCES}], "initContainers": [{"name": "init"}]}
    docs = {
        "pod.json": {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "p"}, "spec": pod_spec},
        "sts.json": {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "s"}, "spec": {"template": {"spec": pod_spec}}},
        "ds.json": {"apiVersion": "apps/v1", "kind": "DaemonSet", "metadata": {"name": "d"}, "spec": {"template": {"spec": pod_spec}}},
        "job.json": {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "j"}, "spec": {"template": {"spec": pod_spec}}},
        "cron.json": {"apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "c"}, "spec": {"jobTemplate": {"spec": {"template": {"spec": pod_spec}}}}},
    }
    for name, document in docs.items():
        put(tmp_path, name, document)
    _, result = scan(tmp_path, "--target-version", "1.30")
    majors = sorted(f["id"] for f in result["findings"] if f["severity"] == "major")
    assert majors == sorted(f"missing-request/{kind}/{name}/init/{resource}" for kind, name in
                            (("Pod", "p"), ("StatefulSet", "s"), ("DaemonSet", "d"), ("Job", "j"), ("CronJob", "c")) for resource in ("cpu", "memory"))
    assert "init container init has no cpu request" in next(f["summary"] for f in result["findings"] if f["id"].endswith("Pod/p/init/cpu"))


def test_kind_list_items_are_expanded_and_non_workload_objects_are_ignored(tmp_path):
    put(tmp_path, "list.json", {"apiVersion": "v1", "kind": "List", "items": [deployment("one"), {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cm"}}]})
    put(tmp_path, "package.json", {"name": "app", "version": "1.0.0"})
    put(tmp_path, "crd-instance.json", {"apiVersion": "example.com/v1", "kind": "Job", "metadata": {"name": "custom"}, "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}})
    _, result = scan(tmp_path, "--target-version", "1.30")
    assert result["manifests_scanned"] == 3 and {f["id"].split("/")[2] for f in result["findings"]} == {"one"}


# --- YAML -----------------------------------------------------------------------------------------------------


def test_yaml_multi_document_files_report_the_document_index(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "manifests/all.yaml", (
        "apiVersion: batch/v1beta1\nkind: CronJob\nmetadata:\n  name: nightly\nspec:\n  jobTemplate:\n    spec:\n      template:\n        spec:\n          containers:\n"
        "          - name: job\n            resources: {requests: {cpu: 1, memory: 1Gi}, limits: {cpu: 1, memory: 1Gi}}\n"
        "---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n      - name: app\n"))
    code, result = scan(tmp_path, "--target-version", "1.25")
    assert code == 1 and (result["files_scanned"], result["manifests_scanned"]) == (1, 2)
    assert ("removed-api/batch/v1beta1/CronJob/nightly", "blocker", "manifests/all.yaml#0") in rules(result)
    assert ("missing-request/Deployment/web/app/cpu", "major", "manifests/all.yaml#1") in rules(result)
    assert not any(loc.endswith("#0") for fid, _, loc in rules(result) if fid.startswith("missing"))


def test_unparsable_yaml_and_alias_bombs_are_counted_not_fatal(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "broken.yaml", "key: [unclosed\n  - : :\n")
    put(tmp_path, "bomb.yaml", "a: &a [x]\n" + "".join(f"k{i}: *a\n" for i in range(30)))
    put(tmp_path, "broken.json", "{not json")
    code, result = scan(tmp_path)
    assert code == 0 and result["files_skipped"]["unparsable"] == 3 and result["files_scanned"] == 0 and result["findings"] == []


def test_an_invalid_calendar_date_in_yaml_is_counted_unparsable_and_the_scan_continues(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "bad-date.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: stamped\ncreated: 2021-02-30\n")
    put(tmp_path, "ing.json", {"apiVersion": "extensions/v1beta1", "kind": "Ingress", "metadata": {"name": "site"}})
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--json", "--target-version", "1.22"],
                          capture_output=True, text=True, env=_scrubbed_base_env(), cwd=ROOT, timeout=60)
    result = json.loads(proc.stdout)
    assert "Traceback" not in proc.stderr and result["status"] == "ok"
    assert proc.returncode == 1
    assert result["files_skipped"]["unparsable"] == 1 and (result["files_scanned"], result["manifests_scanned"]) == (1, 1)
    assert rules(result) == [("removed-api/extensions/v1beta1/Ingress/site", "blocker", "ing.json")]


def test_without_pyyaml_yaml_files_are_reported_as_skipped_tool_missing_and_json_still_works(tmp_path, monkeypatch, capsys):
    put(tmp_path, "one.yaml", "apiVersion: batch/v1beta1\nkind: CronJob\nmetadata:\n  name: n\n")
    put(tmp_path, "two.yml", "kind: Pod\n")
    put(tmp_path, "ing.json", {"apiVersion": "extensions/v1beta1", "kind": "Ingress", "metadata": {"name": "site"}})
    monkeypatch.setitem(sys.modules, "yaml", None)
    code, result = run_in_process(load_module(), tmp_path, capsys, "--target-version", "1.22")
    assert code == 1 and result["skipped"] == [{"check": "yaml-manifests", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 2}]
    assert rules(result) == [("removed-api/extensions/v1beta1/Ingress/site", "blocker", "ing.json")]
    assert (result["files_scanned"], result["manifests_scanned"]) == (1, 1)


# --- bounds and filesystem safety -----------------------------------------------------------------------------


def test_large_binary_and_vcs_files_are_skipped(tmp_path):
    put(tmp_path, "big.json", json.dumps(deployment("big")) + " " * (1024 * 1024))
    (tmp_path / "bin.json").write_bytes(b"\x00\x01\x02" + b"{}" * 10)
    put(tmp_path, ".git/hooks/x.json", deployment("git"))
    put(tmp_path, "node_modules/pkg/x.json", deployment("node"))
    put(tmp_path, "ok/exact.json", json.dumps(deployment("fits", FULL_RESOURCES)) + " " * 100)
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1
    assert result["files_skipped"] == {"symlink": 0, "too_large": 1, "binary": 1, "unparsable": 0, "unreadable": 0}


def test_symlinks_are_never_followed_out_of_the_root(tmp_path):
    outside = tmp_path / "outside"
    root = tmp_path / "repo"
    put(outside, "secret.json", deployment("outside", api_version="extensions/v1beta1"))
    put(root, "real.json", deployment("inside", FULL_RESOURCES))
    os.symlink(outside / "secret.json", root / "link.json")
    os.symlink(outside, root / "linked-dir")
    code, result = scan(root, "--target-version", "1.30")
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1 and result["files_skipped"]["symlink"] == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_named_pipe_named_like_a_manifest_is_not_opened_and_the_scan_completes(tmp_path):
    put(tmp_path, "real.json", deployment("inside", FULL_RESOURCES))
    os.mkfifo(tmp_path / "pipe.json")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--json", "--target-version", "1.30"],
                          capture_output=True, text=True, env=_scrubbed_base_env(), cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "ok" and result["files_scanned"] == 1 and result["files_skipped"]["unreadable"] == 1


def test_scan_stops_at_the_file_limit_and_says_so(tmp_path, capsys):
    for index in range(5):
        put(tmp_path, f"f{index}.json", deployment(f"d{index}", FULL_RESOURCES))
    module = load_module()
    module.MAX_FILES = 3
    code, result = run_in_process(module, tmp_path, capsys, "--target-version", "1.30")
    assert code == 0 and result["truncated"] is True and result["files_scanned"] == 3
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("scan/file-limit", "info")]


def test_the_real_file_limit_is_2000():
    assert load_module().MAX_FILES == 2000 and load_module().MAX_FILE_BYTES == 1024 * 1024


# --- arguments ------------------------------------------------------------------------------------------------


def test_path_narrows_the_scan_and_escaping_or_missing_paths_are_input_errors(tmp_path):
    put(tmp_path, "a/ok.json", deployment("ok", FULL_RESOURCES))
    put(tmp_path, "b/bad.json", deployment("bad"))
    code, result = scan(tmp_path, "--path", "a", "--target-version", "1.30")
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1
    code, result = scan(tmp_path, "--path", "b/bad.json", "--target-version", "1.30")
    assert code == 1 and {f["location"] for f in result["findings"]} == {"b/bad.json"}
    for bad in ("../outside", "missing"):
        code, result = scan(tmp_path, "--path", bad)
        assert code == 2 and result["status"] == "error" and result["error"]["code"] == "E-INPUT"
    code, result = scan(tmp_path, "--target-version", "banana")
    assert code == 2 and result["error"]["code"] == "E-INPUT"


# --- independent review regressions ---------------------------------------------------------------------------


def test_pathologically_nested_files_are_counted_unparsable_and_do_not_abort_the_scan(tmp_path):
    put(tmp_path, "deep.json", "[" * 200_000 + "]" * 200_000)
    put(tmp_path, "deep.yaml", "[" * 100_000 + "]" * 100_000)
    put(tmp_path, "good.json", deployment("ok", FULL_RESOURCES))
    code, result = scan(tmp_path, "--target-version", "1.30")
    assert code == 0 and result["status"] == "ok" and result["files_scanned"] == 1
    assert result["files_skipped"]["unparsable"] == 2 and result["findings"] == []


CANARIES = ("ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "AKIAIOSFODNN7EXAMPLE", "https://user:s3cr3t@host.example/x",
            "ab" * 32, "abandon ability able about above absent absorb abstract absurd abuse access accident")


def test_secret_values_in_manifests_never_reach_the_output(tmp_path):
    for index, canary in enumerate(CANARIES):
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": f"s{index}", "annotations": {"note": canary}}, "stringData": {"token": canary}, "data": {"k": canary}}
        env = deployment(f"d{index}", containers=[{"name": "app", "image": canary, "env": [{"name": "TOKEN", "value": canary}, {"name": "C", "valueFrom": {"secretKeyRef": {"name": canary, "key": canary}}}], "args": [canary]}])
        put(tmp_path, f"s{index}.json", secret)
        put(tmp_path, f"d{index}.json", env)
        put(tmp_path, f"y{index}.yaml", f"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c{index}\ndata:\n  password: \"{canary}\"\n")
        put(tmp_path, f"bad{index}.json", f'{{"apiVersion": "{canary}", "kind": "{canary}", "metadata": {{"name": "x"}}')
    code, result = scan(tmp_path, "--target-version", "1.30")
    text = json.dumps(result)
    assert code == 1 and result["findings"]
    for canary in CANARIES:
        assert canary not in text


def test_input_errors_do_not_echo_the_rejected_option_values(tmp_path):
    for canary in CANARIES:
        for args in (("--target-version", canary), ("--path", canary)):
            code, result = scan(tmp_path, *args)
            assert code == 2 and result["error"]["code"] == "E-INPUT" and canary not in json.dumps(result)
