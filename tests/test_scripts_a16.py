"""Tests of scripts/cloud_readiness_scan.py (A16): fixture trees in tmp_path, findings, exit codes, JSON shape, bounds and the dry-run contract.

The scan is run as a child process for the CLI contract (exit codes, stdout is one JSON document) and in-process
through the script's `run` for the finding logic, so the PyYAML-missing path can be forced by clearing the module's
`yaml` attribute.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "cloud_readiness_scan.py"
TWIN = ROOT / "scripts" / "ts" / "cloud_readiness_scan.ts"
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"

DEPLOYMENT_GOOD = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  labels:
    app: api
spec:
  replicas: 3
  template:
    spec:
      terminationGracePeriodSeconds: 45
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway
      containers:
        - name: api
          image: example/api:1.0.0
          readinessProbe:
            httpGet: {path: /ready, port: 8080}
          resources:
            requests: {cpu: 100m, memory: 128Mi}
"""
HPA = "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: api\nspec:\n  maxReplicas: 6\n"
TF_GOOD = """\
resource "aws_db_instance" "db" {
  instance_class          = "db.t4g.medium"
  multi_az                = true
  storage_encrypted       = true
  backup_retention_period = 7
  tags = {
    owner       = "payments"
    environment = "prod"
    cost-center = "cc-42"
  }
}
"""
DOCKERFILE_GOOD = "FROM python:3.12-slim\nCOPY . /app\nHEALTHCHECK CMD curl -f http://localhost:8080/health\nENTRYPOINT [\"python\", \"/app/app.py\"]\n"
APP_GOOD = "import os, signal\nPORT = int(os.environ['PORT'])\nsignal.signal(signal.SIGTERM, lambda *a: None)\n"


def write(base: Path, rel: str, text: str = "") -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def good_repo(base: Path) -> Path:
    write(base, ".github/workflows/ci.yml", "name: ci\non: push\n")
    write(base, "app.py", APP_GOOD)
    write(base, "Dockerfile", DOCKERFILE_GOOD)
    write(base, "main.tf", TF_GOOD)
    write(base, "k8s/deployment.yaml", DEPLOYMENT_GOOD)
    write(base, "k8s/hpa.yaml", HPA)
    return base


def load_script():
    spec = importlib.util.spec_from_file_location("_cloud_readiness_scan_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scan(base: Path, path: str = ".", module=None) -> dict:
    module = module or load_script()
    return module.run(argparse.Namespace(path=path), SimpleNamespace(dry_run=False, root=base))


def brief(result: dict) -> list[tuple]:
    return [(f["id"], f["severity"], f.get("location", "")) for f in result["findings"]]


def cli(*args: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "SWARM_DIR": str(tmp_path / ".swarm-state")}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT)


# --- dry run and CLI contract -----------------------------------------------------------------------------------


def test_dry_run_prints_one_json_document_without_reading_the_root(tmp_path):
    result = cli("--dry-run", "--json", "--root", str(tmp_path / "does-not-exist"), tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["agent"] == "A16" and data["script"] == "cloud_readiness_scan" and data["status"] == "ok"
    assert data["dry_run"] is True and data["findings"] == [] and data["pillars"] == [] and data["files_scanned"] == 0
    assert not (tmp_path / "does-not-exist").exists()  # the root was neither read nor created


def test_dry_run_output_is_deterministic(tmp_path):
    first = cli("--dry-run", "--json", tmp_path=tmp_path).stdout
    assert first == cli("--dry-run", "--json", tmp_path=tmp_path).stdout


def test_real_run_json_shape_and_exit_code_zero_on_a_ready_repo(tmp_path):
    repo = good_repo(tmp_path / "repo")
    result = cli("--json", "--root", str(repo), tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["agent"] == "A16" and data["script"] == "cloud_readiness_scan" and data["status"] == "ok"
    assert set(data) >= {"summary", "files_scanned", "files_skipped", "truncated", "signals", "pillars", "overall_score_pct", "skipped", "findings"}


def test_cli_exits_1_when_a_major_finding_exists_and_2_for_bad_paths(tmp_path):
    repo = tmp_path / "repo"
    write(repo, ".env", "TOKEN=abc\n")
    failing = cli("--json", "--root", str(repo), tmp_path=tmp_path)
    assert failing.returncode == 1 and json.loads(failing.stdout)["status"] == "fail"
    escaping = cli("--json", "--root", str(repo), "--path", "..", tmp_path=tmp_path)
    assert escaping.returncode == 2 and json.loads(escaping.stdout)["error"]["code"] == "E-INPUT"
    missing = cli("--json", "--root", str(repo), "--path", "nope", tmp_path=tmp_path)
    assert missing.returncode == 2 and json.loads(missing.stdout)["status"] == "error"


@pytest.mark.skipif(not Path(BUN).exists(), reason="bun not installed")
def test_bun_twin_dry_run_matches_the_python_script(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm-state")}
    twin = subprocess.run([BUN, str(TWIN), "--dry-run", "--json"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert twin.returncode == 0, twin.stderr
    assert json.loads(twin.stdout) == json.loads(cli("--dry-run", "--json", tmp_path=tmp_path).stdout)


# --- finding logic ----------------------------------------------------------------------------------------------


def test_ready_repository_has_no_findings_and_scores_100_in_every_pillar(tmp_path):
    pytest.importorskip("yaml")
    result = scan(good_repo(tmp_path))
    assert result["findings"] == [] and result["status"] == "ok" and result["skipped"] == []
    assert result["overall_score_pct"] == 100.0 and [p["score_pct"] for p in result["pillars"]] == [100.0] * 6
    assert [p["pillar"] for p in result["pillars"]] == ["operational_excellence", "security", "reliability", "performance_efficiency",
                                                       "cost_optimization", "sustainability"]
    assert result["signals"] == {"ci_files": 1, "dockerfiles": 1, "kubernetes_workloads": 1, "iac_files": 1, "source_files": 1}
    assert result["files_scanned"] == 6 and result["files_skipped"] == 0 and result["truncated"] is False


def test_empty_repository_reports_minor_gaps_only_and_scores_each_pillar_from_its_always_applicable_question(tmp_path):
    result = scan(tmp_path)
    assert result["status"] == "ok"
    assert brief(result) == [
        ("cloud.autoscaling-missing", "minor", ""), ("cloud.ci-pipeline-missing", "minor", ""), ("cloud.efficiency-signals-missing", "minor", ""),
        ("cloud.health-probe-missing", "minor", ""), ("cloud.tags-missing", "minor", "")]
    assert {p["pillar"]: p["score_pct"] for p in result["pillars"]} == {
        "operational_excellence": 50.0, "security": 100.0, "reliability": 50.0, "performance_efficiency": 50.0,
        "cost_optimization": 50.0, "sustainability": 50.0}
    assert result["overall_score_pct"] == 58.33


def test_unready_repository_findings_severities_locations_and_pillar_scores(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, ".env", "DB_PASSWORD=hunter2\n")
    write(tmp_path, ".env.example", "DB_PASSWORD=changeme\n")
    write(tmp_path, "Dockerfile", "FROM python:3.12\nCMD python app.py\n")
    write(tmp_path, "app.py", "print('hi')\n")
    write(tmp_path, "k8s/deploy.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  replicas: 1\n"
                                      "  template:\n    spec:\n      containers:\n        - name: web\n          image: web:1\n")
    write(tmp_path, "infra/main.tf", 'resource "aws_instance" "web" {}\n')
    write(tmp_path, "infra/db.tf", 'resource "aws_db_instance" "d" {\n  multi_az = false\n}\n')
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert brief(result) == [
        ("cloud.env-file-committed", "major", ".env"), ("cloud.k8s-probes-missing", "major", "k8s/deploy.yaml"),
        ("cloud.multi-az-disabled", "major", "infra/db.tf"), ("cloud.multi-az-missing", "major", "infra/db.tf"),
        ("cloud.autoscaling-missing", "minor", ""), ("cloud.backup-not-declared", "minor", "infra/db.tf"),
        ("cloud.ci-pipeline-missing", "minor", ""), ("cloud.config-not-from-environment", "minor", ""),
        ("cloud.docker-shell-form-entrypoint", "minor", "Dockerfile"), ("cloud.efficiency-signals-missing", "minor", ""),
        ("cloud.encryption-not-declared", "minor", "infra/db.tf"), ("cloud.graceful-shutdown-missing", "minor", ""),
        ("cloud.k8s-no-resource-requests", "minor", "k8s/deploy.yaml"), ("cloud.k8s-single-replica", "minor", "k8s/deploy.yaml"),
        ("cloud.tags-missing", "minor", "infra/db.tf")]
    scores = {p["pillar"]: p["score_pct"] for p in result["pillars"]}
    # security: env-file high (5) + encryption medium (3): 100 * (1 - 6.5/8); reliability: 13 of 17 weight at risk
    assert scores["security"] == 18.75 and scores["reliability"] == 23.53 and scores["operational_excellence"] == 50.0
    assert result["overall_score_pct"] == 40.38


def test_env_file_variants_only_values_count_and_templates_are_ignored(tmp_path):
    write(tmp_path, ".env", "# comment only\nEMPTY=\n")
    write(tmp_path, ".env.production", "export API_KEY=abc\n")
    write(tmp_path, ".env.sample", "API_KEY=abc\n")
    write(tmp_path, ".env.template", "API_KEY=abc\n")
    result = scan(tmp_path)
    assert [(f["id"], f.get("location")) for f in result["findings"] if f["id"] == "cloud.env-file-committed"] == [("cloud.env-file-committed", ".env.production")]


def test_sources_without_env_reads_are_flagged_and_test_files_do_not_count_as_sources(tmp_path):
    write(tmp_path, "tests/test_x.py", "import os\nos.environ['A']\n")
    write(tmp_path, "src/service.py", "print('x')\n")
    result = scan(tmp_path)
    assert "cloud.config-not-from-environment" in [f["id"] for f in result["findings"]]
    assert result["signals"]["source_files"] == 1
    write(tmp_path, "src/service.go", 'package main\nfunc main() { _ = os.Getenv("PORT") }\n')
    assert "cloud.config-not-from-environment" not in [f["id"] for f in scan(tmp_path)["findings"]]


@pytest.mark.parametrize("name,text", [
    ("app.py", "import signal\nsignal.signal(signal.SIGTERM, h)\n"),
    ("main.go", "signal.Notify(c, syscall.SIGTERM)\n"),
    ("server.js", "process.on('SIGTERM', stop)\n"),
    ("App.java", "Runtime.getRuntime().addShutdownHook(t);\n"),
])
def test_graceful_shutdown_signals_in_source_clear_the_finding(tmp_path, name, text):
    write(tmp_path, name, text)
    assert "cloud.graceful-shutdown-missing" not in [f["id"] for f in scan(tmp_path)["findings"]]
    write(tmp_path, name, "print('no handler')\n")
    assert "cloud.graceful-shutdown-missing" in [f["id"] for f in scan(tmp_path)["findings"]]


def test_dockerfile_only_the_final_stage_counts_and_none_disables_the_healthcheck(tmp_path):
    write(tmp_path, "Dockerfile", "FROM golang AS build\nHEALTHCHECK CMD true\nFROM scratch\nCOPY --from=build /app /app\nENTRYPOINT [\"/app\"]\n")
    assert "cloud.health-probe-missing" in [f["id"] for f in scan(tmp_path)["findings"]]
    write(tmp_path, "Dockerfile", "FROM alpine\nHEALTHCHECK NONE\nCMD [\"/app\"]\n")
    assert "cloud.health-probe-missing" in [f["id"] for f in scan(tmp_path)["findings"]]
    write(tmp_path, "Dockerfile", "FROM alpine\nHEALTHCHECK --interval=30s \\\n  CMD wget -q http://localhost/ || exit 1\nCMD [\"/app\"]\n")
    assert "cloud.health-probe-missing" not in [f["id"] for f in scan(tmp_path)["findings"]]


def test_dockerfile_shell_form_launcher_is_flagged_unless_it_execs(tmp_path):
    write(tmp_path, "Dockerfile", "FROM alpine\nENTRYPOINT /app --serve\n")
    flagged = [f for f in scan(tmp_path)["findings"] if f["id"] == "cloud.docker-shell-form-entrypoint"]
    assert [(f["severity"], f["location"]) for f in flagged] == [("minor", "Dockerfile")] and flagged[0]["summary"].startswith("ENTRYPOINT runs through")
    write(tmp_path, "Dockerfile", "FROM alpine\nCMD exec /app --serve\n")
    assert "cloud.docker-shell-form-entrypoint" not in [f["id"] for f in scan(tmp_path)["findings"]]
    write(tmp_path, "Dockerfile.prod", "FROM alpine\nENTRYPOINT [\"/app\"]\nCMD /bin/echo\n")  # exec-form ENTRYPOINT launches the process
    assert "cloud.docker-shell-form-entrypoint" not in [f["id"] for f in scan(tmp_path)["findings"]]


def test_compose_healthcheck_counts_as_a_health_signal(tmp_path):
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    image: web\n    healthcheck:\n      test: [CMD, curl, -f, http://localhost]\n")
    assert "cloud.health-probe-missing" not in [f["id"] for f in scan(tmp_path)["findings"]]


@pytest.mark.parametrize("text,present", [
    ('resource "aws_instance" "a" {}\nvariable "azs" { default = ["a", "b"] }\nlocals { availability_zones = ["eu-1a", "eu-1b"] }\n', True),
    ('resource "aws_instance" "a" {}\nlocals { availability_zones = ["eu-1a"] }\n', False),
    ('resource "aws_db_instance" "a" {\n  multi_az = true\n}\n', True),
    ('resource "azurerm_postgresql_flexible_server" "a" {\n  zone_redundant = true\n}\n', True),
    ('resource "aws_instance" "a" {}\n', False),
])
def test_multi_az_declarations_in_terraform(tmp_path, text, present):
    write(tmp_path, "main.tf", text)
    ids = [f["id"] for f in scan(tmp_path)["findings"]]
    assert ("cloud.multi-az-missing" not in ids) is present


def test_cloudformation_multi_az_and_backup_are_read_from_text(tmp_path):
    write(tmp_path, "db.yaml", "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Db:\n    Type: AWS::RDS::DBInstance\n"
                               "    Properties:\n      MultiAZ: true\n      BackupRetentionPeriod: 7\n      StorageEncrypted: true\n")
    ids = [f["id"] for f in scan(tmp_path)["findings"]]
    assert not {"cloud.multi-az-missing", "cloud.multi-az-disabled", "cloud.backup-not-declared", "cloud.encryption-not-declared"} & set(ids)
    write(tmp_path, "db.yaml", "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Db:\n    Type: AWS::RDS::DBInstance\n    Properties:\n      MultiAZ: false\n"
                               "      BackupRetentionPeriod: 0\n")
    result = scan(tmp_path)
    assert {("cloud.multi-az-disabled", "major"), ("cloud.backup-not-declared", "minor"), ("cloud.encryption-not-declared", "minor")} <= {
        (f["id"], f["severity"]) for f in result["findings"]}


def test_iac_without_compute_or_data_resources_is_not_judged_for_multi_az(tmp_path):
    write(tmp_path, "iam.tf", 'resource "aws_iam_role" "r" {}\n')
    assert not {"cloud.multi-az-missing", "cloud.backup-not-declared"} & {f["id"] for f in scan(tmp_path)["findings"]}


def test_tag_keys_must_cover_owner_environment_and_cost_center(tmp_path):
    write(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  multi_az = true\n  tags = {\n    Owner = "x"\n  }\n}\n')
    finding = next(f for f in scan(tmp_path)["findings"] if f["id"] == "cloud.tag-keys-missing")
    assert finding["summary"] == "allocation tag keys not found in IaC: cost-center, environment" and finding["location"] == "main.tf"
    write(tmp_path, "stack.yaml", "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  A:\n    Type: AWS::EC2::Instance\n    Properties:\n"
                                  "      Tags:\n        - Key: Environment\n          Value: prod\n        - Key: cost_center\n          Value: c1\n")
    assert "cloud.tag-keys-missing" not in [f["id"] for f in scan(tmp_path)["findings"]]


def test_kubernetes_findings_per_workload_zone_spread_and_daemonset_exemptions(tmp_path):
    pytest.importorskip("yaml")
    spec = ("apiVersion: apps/v1\nkind: {kind}\nmetadata:\n  name: {name}\nspec:\n{replicas}  template:\n    spec:\n      containers:\n"
            "        - name: c\n          image: i\n          livenessProbe: {{httpGet: {{path: /, port: 80}}}}\n          resources:\n            requests: {{cpu: 1m}}\n")
    write(tmp_path, "a.yaml", spec.format(kind="Deployment", name="three", replicas="  replicas: 3\n"))
    write(tmp_path, "b.yaml", spec.format(kind="StatefulSet", name="unset", replicas=""))
    write(tmp_path, "c.yaml", spec.format(kind="DaemonSet", name="agent", replicas=""))
    write(tmp_path, "d.yaml", spec.format(kind="StatefulSet", name="solo", replicas="  replicas: 1\n"))
    result = scan(tmp_path)
    kube = [(f["id"], f["location"]) for f in result["findings"] if "k8s" in f["id"]]
    # an unset StatefulSet replicas field defaults to one replica (the apps/v1 API default), so b.yaml is a single-replica workload
    assert kube == [("cloud.k8s-no-zone-spread", "a.yaml"), ("cloud.k8s-single-replica", "b.yaml"),
                    ("cloud.k8s-single-replica", "d.yaml")]
    assert result["signals"]["kubernetes_workloads"] == 4


def test_kubernetes_list_kind_and_multi_document_files_are_flattened(tmp_path):
    pytest.importorskip("yaml")
    body = "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: %s}\nspec:\n  template:\n    spec:\n      containers: [{name: c}]\n"
    write(tmp_path, "multi.yaml", body % "one" + "---\n" + body % "two")
    write(tmp_path, "list.json", json.dumps({"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "three"}, "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}]}))
    result = scan(tmp_path)
    assert result["signals"]["kubernetes_workloads"] == 3
    assert [f["location"] for f in result["findings"] if f["id"] == "cloud.k8s-probes-missing"] == ["list.json", "multi.yaml", "multi.yaml"]


def test_helm_templates_are_judged_from_text_without_a_yaml_parser(tmp_path):
    module = load_script()
    module.yaml = None
    write(tmp_path, "chart/templates/deployment.yaml",
          "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\nspec:\n  replicas: 1\n  template:\n    spec:\n"
          "      containers:\n        - name: app\n          image: {{ .Values.image }}\n")
    result = scan(tmp_path, module=module)
    assert ("cloud.k8s-probes-missing", "major", "chart/templates/deployment.yaml") in brief(result)
    assert "cloud.yaml-checks-skipped" not in [f["id"] for f in result["findings"]] and result["skipped"] == []
    write(tmp_path, "chart/templates/deployment.yaml",
          "apiVersion: apps/v1\nkind: Deployment\nspec:\n  replicas: {{ .Values.replicas }}\n  template:\n    spec:\n      terminationGracePeriodSeconds: 30\n"
          "      containers:\n        - name: app\n          readinessProbe: {}\n          resources:\n            requests: {cpu: 1m}\n")
    ids = [f["id"] for f in scan(tmp_path, module=module)["findings"]]
    assert not {"cloud.k8s-probes-missing", "cloud.k8s-no-resource-requests", "cloud.k8s-single-replica", "cloud.graceful-shutdown-missing"} & set(ids)


def test_missing_pyyaml_reports_skipped_tool_missing_and_keeps_json_manifests(tmp_path):
    module = load_script()
    module.yaml = None
    write(tmp_path, "k8s/deploy.yaml", DEPLOYMENT_GOOD)
    write(tmp_path, "k8s/other.yml", HPA)
    write(tmp_path, "k8s/bad.json", json.dumps({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "j"},
                                               "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}))
    result = scan(tmp_path, module=module)
    assert result["skipped"] == ["skipped:tool-missing: PyYAML (Kubernetes YAML manifests not analysed)"]
    skipped = next(f for f in result["findings"] if f["id"] == "cloud.yaml-checks-skipped")
    assert skipped["severity"] == "info" and skipped["summary"].startswith("skipped:tool-missing") and "2 Kubernetes YAML" in skipped["summary"]
    assert ("cloud.k8s-probes-missing", "major", "k8s/bad.json") in brief(result)  # JSON is parsed natively
    assert result["signals"]["kubernetes_workloads"] == 1 and result["status"] == "fail"


def test_unparseable_manifests_are_reported_as_info_and_do_not_crash(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "broken.yaml", "apiVersion: v1\nkind: Deployment\n  bad: [unclosed\n")
    write(tmp_path, "broken.json", '{"apiVersion": "v1", "kind": ')
    result = scan(tmp_path)
    assert [(f["id"], f["severity"], f["location"]) for f in result["findings"] if f["id"] == "cloud.manifest-unparseable"] == [
        ("cloud.manifest-unparseable", "info", "broken.json"), ("cloud.manifest-unparseable", "info", "broken.yaml")]


# --- bounds -----------------------------------------------------------------------------------------------------


def test_oversized_and_binary_files_are_skipped_and_counted(tmp_path):
    write(tmp_path, "big.py", "import os\nos.environ\n" + "#" * (1024 * 1024))
    (tmp_path / "blob.py").write_bytes(b"\x00\x01os.environ")
    write(tmp_path, "ok.py", "import os\nos.environ\n")
    result = scan(tmp_path)
    assert result["files_scanned"] == 1 and result["files_skipped"] == 2 and result["signals"]["source_files"] == 1


def test_a_file_of_exactly_one_mebibyte_is_still_read(tmp_path):
    text = "import os\nos.environ\n"
    write(tmp_path, "edge.py", text + "#" * (1024 * 1024 - len(text)))
    assert (tmp_path / "edge.py").stat().st_size == 1024 * 1024
    assert scan(tmp_path)["files_scanned"] == 1


def test_symlinks_are_never_followed_out_of_the_root(tmp_path):
    outside = tmp_path / "outside"
    write(outside, ".env", "SECRET=1\n")
    write(outside, "leak.py", "import os\nos.environ\n")
    repo = tmp_path / "repo"
    write(repo, "app.py", "print('x')\n")
    (repo / "linked-dir").symlink_to(outside, target_is_directory=True)
    (repo / "linked.py").symlink_to(outside / "leak.py")
    (repo / ".env").symlink_to(outside / ".env")
    result = scan(repo)
    assert "cloud.env-file-committed" not in [f["id"] for f in result["findings"]]
    assert result["signals"]["source_files"] == 1 and result["files_skipped"] >= 2


def test_scan_stops_after_2000_files_and_says_so(tmp_path):
    for i in range(2000):
        (tmp_path / f"f{i:04d}.txt").write_text("x")
    exact = scan(tmp_path)
    assert exact["truncated"] is False and "cloud.scan-truncated" not in [f["id"] for f in exact["findings"]]
    (tmp_path / "f2000.txt").write_text("x")
    result = scan(tmp_path)
    assert result["truncated"] is True and ("cloud.scan-truncated", "info", "") in brief(result)


def test_skipped_directories_are_not_scanned(tmp_path):
    write(tmp_path, "node_modules/pkg/.env", "A=1\n")
    write(tmp_path, ".git/.env", "A=1\n")
    write(tmp_path, "vendor/x/main.tf", 'resource "aws_instance" "a" {}\n')
    assert scan(tmp_path)["files_scanned"] == 0


def test_path_narrows_the_scan_to_a_subdirectory_or_one_file(tmp_path):
    write(tmp_path, "a/.env", "A=1\n")
    write(tmp_path, "b/app.py", "print(1)\n")
    assert [f["location"] for f in scan(tmp_path, "a")["findings"] if f["id"] == "cloud.env-file-committed"] == ["a/.env"]
    assert "cloud.env-file-committed" not in [f["id"] for f in scan(tmp_path, "b")["findings"]]
    assert scan(tmp_path, "b/app.py")["signals"]["source_files"] == 1


def test_run_refuses_paths_outside_or_missing_under_the_root(tmp_path):
    module = load_script()
    from swarm.errors import SwarmError
    with pytest.raises(SwarmError):
        scan(tmp_path, "../elsewhere", module)
    with pytest.raises(SwarmError):
        scan(tmp_path, "missing", module)


# --- secret safety ----------------------------------------------------------------------------------------------

SECRETS = ("AKIAIOSFODNN7EXAMPLE", "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
           "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about", "hunter2pass")


def test_no_scanned_value_or_credential_shaped_name_reaches_the_output(tmp_path):
    pytest.importorskip("yaml")
    repo = tmp_path / "repo"
    write(repo, ".env", f"AWS_ACCESS_KEY_ID={SECRETS[0]}\nGITHUB_TOKEN={SECRETS[1]}\nPRIVATE_KEY={SECRETS[2]}\nMNEMONIC={SECRETS[3]}\n")
    write(repo, "app.py", f"TOKEN = '{SECRETS[1]}'\nimport os\nos.environ\n")
    write(repo, "Dockerfile", f"FROM python:3.12\nENV DB_URL=https://user:{SECRETS[4]}@db.example/x\nCMD run.sh\n")
    write(repo, "main.tf", f'resource "aws_db_instance" "db" {{\n  password = "{SECRETS[4]}"\n  username = "{SECRETS[0]}"\n}}\n')
    write(repo, "k8s/d.yaml", f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {SECRETS[1]}\nspec:\n  template:\n    spec:\n      containers:\n"
                              f"        - name: {SECRETS[0]}\n          env:\n            - {{name: T, value: {SECRETS[2]}}}\n")
    write(repo, f".env.{SECRETS[2]}", "A=1\n")
    for text in (json.dumps(scan(repo)), cli("--json", "--root", str(repo), tmp_path=tmp_path).stdout):
        assert text and not any(secret in text for secret in SECRETS)
    names = [f["summary"] for f in scan(repo)["findings"] if f["id"] == "cloud.k8s-probes-missing"]
    assert names and names[0].startswith("Deployment [redacted:credential:40:")


def test_a_path_argument_is_never_echoed_in_the_error(tmp_path):
    module = load_script()
    from swarm.errors import SwarmError
    for bad in ("https://user:hunter2pass@host.example/x", "../hunter2pass"):
        with pytest.raises(SwarmError) as caught:
            scan(tmp_path, bad, module)
        assert "hunter2pass" not in str(caught.value) and "hunter2pass" not in json.dumps(getattr(caught.value, "details", {}))
    out = cli("--json", "--root", str(tmp_path), "--path", "../hunter2pass", tmp_path=tmp_path)
    assert out.returncode == 2 and "hunter2pass" not in out.stdout + out.stderr


def test_env_file_finding_does_not_claim_the_file_is_tracked(tmp_path):
    write(tmp_path, ".env", "TOKEN=abc\n")
    finding = next(f for f in scan(tmp_path)["findings"] if f["id"] == "cloud.env-file-committed")
    assert finding["severity"] == "major" and finding["location"] == ".env"
    assert "committed unless version control ignores it" in finding["summary"]


def test_tab_separated_healthcheck_and_exec_form_are_read_like_the_space_form():
    module = load_script()
    assert module._dockerfile_facts("FROM a\nHEALTHCHECK\tCMD true\n")["healthcheck"] is True
    assert module._dockerfile_facts("FROM a\nHEALTHCHECK\tNONE\n")["healthcheck"] is False
    assert module._dockerfile_facts("FROM a\nCMD\tnode app.js\n") == {"healthcheck": False, "shell_form": True, "launcher": "CMD"}


def test_string_typed_workload_metadata_is_skipped_and_counted_and_the_scan_completes(tmp_path):
    pytest.importorskip("yaml")
    repo = tmp_path / "repo"
    write(repo, "k8s/broken.json", json.dumps({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": "a", "spec": {"replicas": 2}}))
    write(repo, "k8s/deploy.yaml", DEPLOYMENT_GOOD)
    result = cli("--json", "--root", str(repo), tmp_path=tmp_path)
    assert result.returncode in (0, 1), result.stderr  # the scan completes; 2 would mean it aborted
    data = json.loads(result.stdout)
    assert data["signals"]["kubernetes_workloads"] == 1
    assert [f for f in brief(data) if f[0] == "cloud.workload-malformed"] == [("cloud.workload-malformed", "info", "k8s/broken.json")]


# --- QA-03 regressions ------------------------------------------------------------------------------------------


def test_unset_replicas_default_to_one_so_a_single_replica_workload_is_flagged(tmp_path):
    pytest.importorskip("yaml")
    write(tmp_path, "k8s/web.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n"
                                    "      containers:\n        - name: web\n          readinessProbe: {httpGet: {path: /, port: 80}}\n"
                                    "          resources:\n            requests: {cpu: 1m}\n")
    result = scan(tmp_path)
    assert [(f["id"], f["location"]) for f in result["findings"] if "k8s-single" in f["id"]] == [("cloud.k8s-single-replica", "k8s/web.yaml")]
    assert result["signals"]["kubernetes_workloads"] == 1


def test_unparsed_manifest_makes_absence_rules_unknown_not_missing(tmp_path):
    module = load_script()
    module.yaml = None
    write(tmp_path, "app.py", "print('no signals')\n")
    write(tmp_path, "k8s/deploy.yaml", DEPLOYMENT_GOOD)  # may hold the probes, grace period, labels and autoscaler the scan cannot read
    result = scan(tmp_path, module=module)
    ids = {f["id"] for f in result["findings"]}
    assert {"cloud.health-probe-unknown", "cloud.autoscaling-unknown", "cloud.tags-unknown", "cloud.graceful-shutdown-unknown"} <= ids
    assert not {"cloud.health-probe-missing", "cloud.autoscaling-missing", "cloud.tags-missing", "cloud.graceful-shutdown-missing"} & ids
    unknown = next(f for f in result["findings"] if f["id"] == "cloud.health-probe-unknown")
    assert unknown["severity"] == "minor" and unknown["summary"].startswith("unknown, not missing: no health check signal found")
    assert unknown["summary"].endswith("Kubernetes YAML was not parsed (PyYAML missing)")
