"""Behaviour tests for scripts/mesh_policy_check.py (A23): fixture trees under tmp_path, real-mode findings, bounds and the
dry-run contract. Exit codes: 0 ok, 1 finding-fail (major or blocker), 2 taxonomy error."""
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
SCRIPT = ROOT / "scripts" / "mesh_policy_check.py"
TWIN = ROOT / "scripts" / "ts" / "mesh_policy_check.ts"
BUN = shutil.which("bun")
SEVERITIES = {"info", "minor", "major", "blocker"}


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return path


def run(repo: Path, *args: str, tmp: Path | None = None) -> tuple[int, dict]:
    env = {**os.environ, "SWARM_DIR": str((tmp or repo.parent) / "swarm-state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(repo), *args], capture_output=True, text=True, env=env, cwd=repo)
    assert not proc.stderr.strip().startswith("Traceback"), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def put(repo: Path, rel: str, content) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def deployment(name="web", namespace="shop", labels=None) -> dict:
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace},
            "spec": {"template": {"metadata": {"labels": labels or {"app": name}}}}}


def netpol(name, namespace, types=("Ingress", "Egress")) -> dict:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": name, "namespace": namespace},
            "spec": {"podSelector": {}, "policyTypes": list(types)}}


def peer_auth(name, namespace, mode, labels=None) -> dict:
    spec = {"mtls": {"mode": mode}, **({"selector": {"matchLabels": labels}} if labels else {})}
    return {"apiVersion": "security.istio.io/v1", "kind": "PeerAuthentication", "metadata": {"name": name, "namespace": namespace}, "spec": spec}


def authz(name, namespace, spec=None) -> dict:
    return {"apiVersion": "security.istio.io/v1", "kind": "AuthorizationPolicy", "metadata": {"name": name, "namespace": namespace}, "spec": spec or {}}


def ids(data: dict) -> list[str]:
    return sorted(f["id"] for f in data["findings"])


def check_status(data: dict, name: str) -> str:
    return next(c["status"] for c in data["checks"] if c["check"] == name)


def load_script():
    spec = importlib.util.spec_from_file_location("_mesh_policy_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


# --- dry-run contract -----------------------------------------------------------------------------------------


def test_dry_run_is_deterministic_json_and_reads_nothing(repo):
    put(repo, "netpol.json", netpol("allow-all", "shop", ("Ingress",)))
    put(repo, "deploy.json", deployment())
    first_rc, first = run(repo, "--dry-run")
    second_rc, second = run(repo, "--dry-run")
    assert (first_rc, second_rc) == (0, 0) and first == second
    assert first["status"] == "ok" and first["dry_run"] is True and first["agent"] == "A23" and first["script"] == "mesh_policy_check"
    assert first["findings"] == [] and first["finding_count"] == 0 and first["truncated"] is False and first["skipped"] == []
    assert set(first["counts"].values()) == {0}
    assert [c["check"] for c in first["checks"]] == ["netpol-default-deny", "authz-default-deny", "mtls-coverage", "gateway-references"]
    assert {c["status"] for c in first["checks"]} == {"dry-run"}


def test_dry_run_with_no_other_arguments_succeeds_in_an_empty_directory(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--json"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0 and json.loads(proc.stdout)["status"] == "ok"


@pytest.mark.skipif(BUN is None, reason="bun not installed")
def test_bun_twin_matches_the_python_dry_run(repo):
    env = {**os.environ, "SWARM_DIR": str(repo.parent / "swarm-state")}
    proc = subprocess.run([BUN, str(TWIN), "--dry-run", "--json", "--root", str(repo)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr[-400:]
    assert json.loads(proc.stdout) == run(repo, "--dry-run")[1]


# --- real mode: findings and exit codes -----------------------------------------------------------------------


def test_empty_tree_runs_no_check_and_fails_as_incomplete(repo):
    rc, data = run(repo)
    assert rc == 1 and data["status"] == "fail" and data["finding_count"] == 1
    assert [(f["id"], f["severity"], f["location"]) for f in data["findings"]] == [("scan/incomplete", "major", ".")]
    assert "no in-scope input was analysed" in data["findings"][0]["summary"]
    assert {c["status"] for c in data["checks"]} == {"skipped:no-input"}
    assert data["counts"]["files_scanned"] == 0 and data["skipped"] == [] and data["truncated"] is False


def test_a_single_valid_clean_manifest_is_ok(repo):
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    rc, data = run(repo)
    assert rc == 0 and data["status"] == "ok" and data["findings"] == [] and data["finding_count"] == 0
    assert data["counts"]["files_scanned"] == 1 and data["counts"]["documents"] == 1
    assert [c["status"] for c in data["checks"]] == ["ran", "skipped:no-input", "skipped:no-input", "skipped:no-input"]


def test_workloads_without_a_network_policy_fail_with_a_default_deny_finding(repo):
    put(repo, "k8s/deploy.json", deployment())
    rc, data = run(repo)
    assert rc == 1 and data["status"] == "fail"
    assert ids(data) == ["NETPOL-NO-DEFAULT-DENY-EGRESS", "NETPOL-NO-DEFAULT-DENY-INGRESS"]
    top = data["findings"][0]
    assert (top["id"], top["severity"]) == ("NETPOL-NO-DEFAULT-DENY-INGRESS", "major") and top["summary"].startswith("shop: no default-deny ingress policy")
    assert data["counts"]["workloads"] == 1 and data["counts"]["NetworkPolicy"] == 0
    assert check_status(data, "netpol-default-deny") == "ran" and check_status(data, "authz-default-deny") == "skipped:no-input"
    assert all(set(f) >= {"id", "severity", "summary"} and f["severity"] in SEVERITIES for f in data["findings"])


def test_default_deny_policies_make_the_scan_clean(repo):
    put(repo, "k8s/deploy.json", deployment())
    put(repo, "k8s/netpol.json", netpol("default-deny", "shop"))
    rc, data = run(repo)
    assert rc == 0 and data["status"] == "ok" and data["findings"] == [] and data["counts"]["NetworkPolicy"] == 1
    assert data["summary"].startswith("0 finding(s) in 2 file(s)")


def test_ingress_only_default_deny_leaves_a_minor_egress_finding_and_still_passes(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("deny-in", "shop", ("Ingress",)))
    rc, data = run(repo)
    assert rc == 0 and data["status"] == "ok"
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("NETPOL-NO-DEFAULT-DENY-EGRESS", "minor")]


def test_permissive_mtls_and_missing_authz_default_deny_fail_in_a_mesh_tree(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    put(repo, "mesh/pa.json", peer_auth("default", "shop", "PERMISSIVE"))
    rc, data = run(repo)
    assert rc == 1
    assert ids(data) == ["AUTHZ-NO-DEFAULT-DENY", "MTLS-COVERAGE-BELOW-TARGET", "MTLS-PERMISSIVE"]
    assert check_status(data, "mtls-coverage") == "ran" and check_status(data, "authz-default-deny") == "ran"
    assert data["counts"]["PeerAuthentication"] == 1
    relaxed_rc, relaxed = run(repo, "--min-strict-pct", "0")
    assert "MTLS-COVERAGE-BELOW-TARGET" not in ids(relaxed) and relaxed_rc == 1  # PERMISSIVE itself is minor; authz default-deny is still major


def test_strict_mtls_and_authz_default_deny_pass(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    put(repo, "pa.json", peer_auth("default", "shop", "STRICT"))
    put(repo, "authz.json", authz("deny-all", "shop"))
    rc, data = run(repo)
    assert rc == 0 and data["findings"] == [] and [c["status"] for c in data["checks"]] == ["ran", "ran", "ran", "skipped:no-input"]


def test_findings_name_the_file_of_the_resource(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    put(repo, "policies/open.json", authz("open", "shop", {"rules": [{}]}))
    rc, data = run(repo)
    assert rc == 1
    finding = next(f for f in data["findings"] if f["id"] == "AUTHZ-ALLOW-ALL")
    assert finding["severity"] == "blocker" and finding["location"] == "policies/open.json" and finding["summary"].startswith("shop/open:")


def test_mesh_checks_are_skipped_without_istio_security_resources(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    _, data = run(repo)
    assert [(c["check"], c["status"]) for c in data["checks"]] == [("netpol-default-deny", "ran"), ("authz-default-deny", "skipped:no-input"),
                                                                    ("mtls-coverage", "skipped:no-input"), ("gateway-references", "skipped:no-input")]


def gateway(name, namespace, cert_namespace=None) -> dict:
    ref = {"name": "cert", **({"namespace": cert_namespace} if cert_namespace else {})}
    return {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "Gateway", "metadata": {"name": name, "namespace": namespace},
            "spec": {"gatewayClassName": "istio", "listeners": [{"name": "https", "port": 443, "protocol": "HTTPS", "tls": {"certificateRefs": [ref]},
                                                                  "allowedRoutes": {"namespaces": {"from": "All"}}}]}}


def http_route(name, namespace, parent_namespace, backend_namespace=None) -> dict:
    backend = {"name": "web", "port": 80, **({"namespace": backend_namespace} if backend_namespace else {})}
    return {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute", "metadata": {"name": name, "namespace": namespace},
            "spec": {"parentRefs": [{"name": "edge", "namespace": parent_namespace}], "rules": [{"backendRefs": [backend]}]}}


def reference_grant(namespace, sender_kind, sender_namespace, target_kind) -> dict:
    return {"apiVersion": "gateway.networking.k8s.io/v1beta1", "kind": "ReferenceGrant", "metadata": {"name": "grant", "namespace": namespace},
            "spec": {"from": [{"group": "gateway.networking.k8s.io", "kind": sender_kind, "namespace": sender_namespace}],
                     "to": [{"group": "", "kind": target_kind}]}}


def test_cross_namespace_references_need_reference_grants(repo):
    put(repo, "gw.json", gateway("edge", "infra", "certs"))
    put(repo, "route.json", http_route("web", "shop", "infra", "data"))
    rc, data = run(repo)
    assert rc == 1 and ids(data) == ["GW-CERT-NO-GRANT", "GW-ROUTES-ALL-NAMESPACES", "ROUTE-BACKEND-NO-GRANT"]
    assert {f["location"] for f in data["findings"]} == {"gw.json", "route.json"}
    assert check_status(data, "gateway-references") == "ran" and data["counts"]["Gateway"] == 1 and data["counts"]["HTTPRoute"] == 1
    put(repo, "grants/cert.json", reference_grant("certs", "Gateway", "infra", "Secret"))
    put(repo, "grants/backend.json", reference_grant("data", "HTTPRoute", "shop", "Service"))
    rc_fixed, fixed = run(repo)
    assert rc_fixed == 0 and ids(fixed) == ["GW-ROUTES-ALL-NAMESPACES"] and fixed["counts"]["ReferenceGrant"] == 2


def test_route_whose_gateway_is_outside_the_scanned_files_is_only_minor(repo):
    put(repo, "route.json", http_route("web", "shop", "infra"))
    rc, data = run(repo)
    assert rc == 0 and data["status"] == "ok"
    finding = data["findings"][0]
    assert (finding["id"], finding["severity"]) == ("ROUTE-PARENT-MISSING", "minor") and finding["summary"].endswith("(scanned files only)")


def test_istio_gateways_and_unrelated_documents_are_ignored(repo):
    istio_gateway = {"apiVersion": "networking.istio.io/v1", "kind": "Gateway", "metadata": {"name": "g", "namespace": "n"}, "spec": {"servers": []}}
    put(repo, "istio.json", istio_gateway)
    put(repo, "config.json", {"kind": "ConfigMap", "apiVersion": "v1", "metadata": {"name": "c"}})
    put(repo, "list.json", ["not", "a", "manifest", 3])
    rc, data = run(repo)
    assert rc == 0 and data["counts"]["Gateway"] == 0 and data["counts"]["files_scanned"] == 3 and data["findings"] == []


def test_list_kind_and_json_arrays_are_expanded(repo):
    put(repo, "all.json", {"apiVersion": "v1", "kind": "List", "items": [deployment("a"), deployment("b", "other")]})
    put(repo, "array.json", [netpol("default-deny", "shop"), netpol("default-deny", "other")])
    rc, data = run(repo)
    assert rc == 0 and data["counts"]["workloads"] == 2 and data["counts"]["NetworkPolicy"] == 2 and data["counts"]["documents"] == 4


def test_namespace_objects_and_missing_namespaces_default_sensibly(repo):
    put(repo, "ns.json", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "payments"}})
    rc, data = run(repo)
    assert rc == 1 and any(f["summary"].startswith("payments: no default-deny ingress policy") for f in data["findings"])
    put(repo, "deploy.json", {**deployment(), "metadata": {"name": "web"}})
    _, with_default = run(repo)
    assert any(f["summary"].startswith("default: no default-deny ingress policy") for f in with_default["findings"])


# --- YAML ----------------------------------------------------------------------------------------------------


YAML_DOCS = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: shop
spec:
  template:
    metadata:
      labels:
        app: web
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: shop
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
"""


def test_multi_document_yaml_is_scanned_when_pyyaml_imports(repo):
    pytest.importorskip("yaml")
    put(repo, "k8s/app.yaml", YAML_DOCS)
    put(repo, "k8s/other.yml", YAML_DOCS.replace("shop", "other"))
    rc, data = run(repo)
    assert rc == 0 and data["findings"] == [] and data["counts"]["documents"] == 4 and data["counts"]["workloads"] == 2
    assert data["skipped"] == []


def test_yaml_without_pyyaml_reports_tool_missing_and_keeps_json_native(repo):
    module = load_script()
    put(repo, "k8s/app.yaml", YAML_DOCS)
    only_yaml = module.scan(repo, repo, yaml_module=None)
    assert only_yaml["status"] == "fail" and only_yaml["counts"]["documents"] == 0
    assert [f["id"] for f in only_yaml["findings"]] == ["scan/incomplete"]
    assert [c["status"] for c in only_yaml["checks"]] == ["skipped:tool-missing"] * 4
    assert only_yaml["skipped"] == [{"what": "yaml-manifests", "status": "skipped:tool-missing", "files": 1}]
    put(repo, "k8s/deploy.json", deployment())
    mixed = module.scan(repo, repo, yaml_module=None)
    assert mixed["status"] == "fail" and check_status(mixed, "netpol-default-deny") == "ran"
    assert check_status(mixed, "mtls-coverage") == "skipped:tool-missing" and mixed["counts"]["workloads"] == 1
    assert mixed["skipped"][0]["what"] == "yaml-manifests"


def test_malformed_yaml_and_json_are_info_findings_and_make_the_scan_incomplete(repo):
    pytest.importorskip("yaml")
    put(repo, "bad.yaml", "key: [unclosed\n  - x: : :")
    put(repo, "bad.json", "{not json")
    rc, data = run(repo)
    assert rc == 1 and data["status"] == "fail"
    assert sorted((f["id"], f["severity"], f["location"]) for f in data["findings"]) == [("MESH-SCAN-UNPARSABLE", "info", "bad.json"),
                                                                                       ("MESH-SCAN-UNPARSABLE", "info", "bad.yaml"),
                                                                                       ("scan/incomplete", "major", ".")]


def test_a_malformed_manifest_is_reported_without_hiding_other_checks(repo):
    put(repo, "bad-netpol.json", {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "bad", "namespace": "shop"}, "spec": {}})
    put(repo, "authz.json", authz("deny-all", "shop"))
    rc, data = run(repo)
    assert rc == 1 and data["status"] == "fail"
    assert [(f["id"], f["severity"]) for f in data["findings"]] == [("scan/incomplete", "major"), ("MESH-SCAN-INVALID-INPUT", "minor")]
    summary = next(f["summary"] for f in data["findings"] if f["id"] == "MESH-SCAN-INVALID-INPUT")
    assert "network_policies" in summary and "spec.podSelector" not in summary
    assert check_status(data, "netpol-default-deny") == "error" and check_status(data, "authz-default-deny") == "ran"


# --- bounds ----------------------------------------------------------------------------------------------------


def test_oversized_and_binary_files_are_skipped_and_reported(repo):
    big = repo / "big.json"
    big.write_text(json.dumps(deployment()) + " " * (1024 * 1024), encoding="utf-8")
    exact = repo / "exact.json"
    padding = 1024 * 1024 - len(json.dumps(deployment("exact")))
    exact.write_text(json.dumps(deployment("exact")) + " " * padding, encoding="utf-8")
    assert exact.stat().st_size == 1024 * 1024
    (repo / "binary.json").write_bytes(b"\x00\x01\x02" + json.dumps(deployment("bin")).encode())
    (repo / "latin.json").write_bytes(b"\xff\xfe" + b"x" * 10)
    _, data = run(repo)
    assert data["counts"]["workloads"] == 1  # only the file of exactly 1 MiB is read
    assert {s["what"]: s["files"] for s in data["skipped"]} == {"files-over-1MiB": 1, "binary-files": 2}
    assert {s["status"] for s in data["skipped"]} == {"skipped:bounded"}


def test_symlinks_leaving_the_root_are_never_followed(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text(json.dumps(deployment("leak", "leaked")), encoding="utf-8")
    (outside / "dir").mkdir()
    (outside / "dir" / "inner.json").write_text(json.dumps(deployment("leak2", "leaked")), encoding="utf-8")
    (repo / "link.json").symlink_to(outside / "secret.json")
    (repo / "linkdir").symlink_to(outside / "dir", target_is_directory=True)
    (repo / "dangling.json").symlink_to(outside / "missing.json")
    put(repo, "real.json", deployment("web", "shop"))
    (repo / "inside-link.json").symlink_to(repo / "real.json")
    _, data = run(repo)
    assert data["counts"]["workloads"] == 2  # real.json and the in-root symlink to it
    assert not any("leaked" in f["summary"] for f in data["findings"])
    assert {s["what"]: s["files"] for s in data["skipped"]} == {"symlinks-outside-root": 2}
    assert data["status"] == "fail" and "symlink leaves the root" in next(f["summary"] for f in data["findings"] if f["id"] == "scan/incomplete")


def test_skip_directories_are_not_scanned(repo):
    for directory in (".git", "node_modules", ".venv", "dist", "build", "__pycache__"):
        put(repo, f"{directory}/deploy.json", deployment("hidden", "hidden"))
    put(repo, "src/deploy.json", deployment())
    _, data = run(repo)
    assert data["counts"]["files_scanned"] == 1 and data["counts"]["workloads"] == 1


def test_file_cap_is_2000_and_exactly_2000_is_not_truncated(repo):
    for index in range(2000):
        put(repo, f"m/f{index:04d}.json", {})
    _, exact = run(repo)
    assert exact["counts"]["files_scanned"] == 2000 and exact["truncated"] is False
    put(repo, "m/f2000.json", {})
    _, over = run(repo)
    assert over["counts"]["files_scanned"] == 2000 and over["truncated"] is True and over["summary"].endswith("truncated")


def test_non_manifest_suffixes_are_not_candidates(repo):
    put(repo, "notes.txt", json.dumps(deployment()))
    put(repo, "deploy.JSON", deployment())
    _, data = run(repo)
    assert data["counts"]["files_scanned"] == 1


# --- paths, arguments and determinism ---------------------------------------------------------------------------


def test_path_limits_the_scan_to_a_subtree_or_file(repo):
    put(repo, "a/deploy.json", deployment())
    put(repo, "b/deploy.json", deployment("web", "other"))
    _, sub = run(repo, "--path", "a")
    assert sub["counts"]["files_scanned"] == 1 and all(f["summary"].startswith("shop:") for f in sub["findings"])
    _, single = run(repo, "--path", "b/deploy.json")
    assert single["counts"]["workloads"] == 1 and all(f["summary"].startswith("other:") for f in single["findings"])


def test_invalid_path_and_arguments_exit_with_a_taxonomy_error(repo, tmp_path):
    for args in (("--path", "../outside"), ("--path", "missing"), ("--min-strict-pct", "101"), ("--min-strict-pct", "-1")):
        rc, data = run(repo, *args)
        assert rc == 2 and data["status"] == "error" and data["error"]["code"] == "E-INPUT", args


def test_real_runs_are_deterministic_and_findings_sorted_worst_first(repo):
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("deny-in", "shop", ("Ingress",)))
    put(repo, "pa.json", peer_auth("default", "shop", "PERMISSIVE"))
    put(repo, "authz.json", authz("open", "shop", {"rules": [{}]}))
    first = run(repo)
    second = run(repo)
    assert first == second and first[0] == 1
    ranks = [{"info": 0, "minor": 1, "major": 2, "blocker": 3}[f["severity"]] for f in first[1]["findings"]]
    assert ranks == sorted(ranks, reverse=True) and first[1]["finding_count"] == len(first[1]["findings"])


# --- secret safety -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("canary", [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "0123456789abcdef" * 4,
    "abandon ability able about above absent absorb abstract absurd abuse access accident",
    "https://admin:s3cr3tpassw0rd@evil.example/x",
])
def test_scan_never_echoes_credential_shaped_names(repo, canary):
    secret = "s3cr3tpassw0rd" if "s3cr3t" in canary else canary
    put(repo, "deploy.json", deployment(canary, canary))
    put(repo, "authz.json", authz(canary, canary, {"rules": [{}]}))
    put(repo, "pa.json", peer_auth(canary, canary, "PERMISSIVE"))
    put(repo, "np.json", netpol(canary, canary, ("Ingress",)))
    put(repo, "bad-pa.json", peer_auth("bad", canary, canary))
    rc, data = run(repo)
    assert rc == 1 and data["finding_count"] > 0
    assert secret not in json.dumps(data), json.dumps(data)[:600]
    assert any(c["status"] == "error" for c in data["checks"])  # the malformed PeerAuthentication mode went through the error path too


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need os.mkfifo")
def test_named_pipe_manifest_is_never_opened_and_the_scan_completes(repo):
    os.mkfifo(repo / "gateway.yaml")
    put(repo, "deploy.json", deployment())
    env = {**os.environ, "SWARM_DIR": str(repo.parent / "swarm-state")}
    # A read of the FIFO would block forever; the timeout turns that regression into a failure instead of a hang.
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(repo)], capture_output=True, text=True, env=env,
                          cwd=repo, timeout=60)
    assert not proc.stderr.strip().startswith("Traceback"), proc.stderr
    report = json.loads(proc.stdout)
    assert report["counts"]["documents"] == 1 and report["counts"]["workloads"] == 1


def test_a_check_that_cannot_be_evaluated_makes_the_scan_fail(repo, monkeypatch):
    module = load_script()
    put(repo, "deploy.json", deployment())
    put(repo, "netpol.json", netpol("default-deny", "shop"))

    def broken(payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(module.a23_tools, "netpol_coverage", broken)
    data = module.scan(repo, repo)
    assert data["status"] == "fail" and check_status(data, "netpol-default-deny") == "error"
    incomplete = [f for f in data["findings"] if f["id"] == "scan/incomplete"]
    assert len(incomplete) == 1 and incomplete[0]["severity"] == "major" and "RuntimeError" in incomplete[0]["summary"]


def test_a_rejected_manifest_fails_the_gate_and_its_value_is_never_echoed(repo):
    put(repo, "netpol.json", netpol("default-deny", "shop"))
    put(repo, "deploy.json", deployment())
    put(repo, "authz.json", authz("deny-all", "shop"))
    put(repo, "pa.json", peer_auth("default", "shop", "STRCT"))
    rc, data = run(repo)
    assert rc == 1 and data["status"] == "fail"
    assert check_status(data, "mtls-coverage") == "error" and check_status(data, "authz-default-deny") == "ran"
    assert {f["id"] for f in data["findings"]} == {"MESH-SCAN-INVALID-INPUT", "scan/incomplete"}
    assert "STRCT" not in json.dumps(data)
