"""Behaviour tests for scripts/finops_cost_scan.py: fixture trees in tmp_path, findings, exit codes and JSON shape.

Rule checks call `scan()` in-process (fast, can patch the optional PyYAML import); the CLI contract (exit codes, --json,
--dry-run, --path) runs the script as a subprocess.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from conftest import run_script

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "finops_cost_scan.py"
NO_TAGS: list[str] = []


def module():
    """A fresh copy of the script module (tests/conftest.py evicts swarm* modules between tests)."""
    spec = importlib.util.spec_from_file_location("finops_cost_scan_under_test", ROOT / "scripts" / SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def put(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def scan(root: Path, required=NO_TAGS, max_xlarge: int = 8, mod=None) -> dict:
    return (mod or module()).scan(root, root, list(required), max_xlarge)


def hits(result: dict, rule: str) -> list[tuple[str, str]]:
    """(resource reference, severity) of every finding of a rule, in report order."""
    return [(f["summary"].split(" ")[0].rstrip(":"), f["severity"]) for f in result["findings"] if f["rule"] == rule]


def cli(root: Path, *args: str):
    return run_script(SCRIPT, "--root", str(root), "--json", *args, env={"SWARM_DIR": str(root / ".swarm-state")})


# --- CLI contract ---------------------------------------------------------------------------------------------------


def test_dry_run_prints_one_canned_json_document_and_reads_nothing(tmp_path):
    put(tmp_path, "bad.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }')
    result = run_script(SCRIPT, "--dry-run", "--json", "--root", str(tmp_path), env={"SWARM_DIR": str(tmp_path / ".s")})
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out == {
        "status": "ok", "dry_run": True, "files_scanned": 0,
        "files_skipped": {"too_large": 0, "binary": 0, "parse_error": 0, "symlink": 0, "yaml_tool_missing": 0},
        "file_limit_reached": False, "counts": {"info": 0, "minor": 0, "major": 0, "blocker": 0}, "by_rule": {},
        "findings_truncated": False, "findings": [], "skipped": [],
        "summary": "dry-run: finops_cost_scan would scan IaC and Kubernetes manifests under --root",
        "agent": "A20", "script": "finops_cost_scan",
    }
    bare = run_script(SCRIPT, "--dry-run", "--json", env={"SWARM_DIR": str(tmp_path / ".s2")})
    assert bare.returncode == 0 and json.loads(bare.stdout)["status"] == "ok"


def test_real_mode_reports_shape_and_exits_one_on_a_major_finding(tmp_path):
    put(tmp_path, "deploy.json", json.dumps({"kind": "Deployment", "metadata": {"name": "api"}, "spec": {"template": {"spec": {"containers": [{"name": "web", "image": "x"}]}}}}))
    result = cli(tmp_path)
    assert result.returncode == 1, result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "fail" and out["agent"] == "A20" and out["script"] == "finops_cost_scan"
    assert out["files_scanned"] == 1 and out["counts"] == {"info": 0, "minor": 1, "major": 1, "blocker": 0}
    assert out["by_rule"] == {"k8s-missing-limits": 1, "k8s-missing-requests": 1}
    assert [f["severity"] for f in out["findings"]] == ["major", "minor"]  # worst first
    first = out["findings"][0]
    assert set(first) == {"id", "severity", "rule", "summary", "file", "location"} and first["file"] == "deploy.json"
    assert out["skipped"] == [] and out["file_limit_reached"] is False and out["findings_truncated"] is False


def test_only_minor_findings_exit_zero(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_ebs_volume" "v" { type = "gp2" }')
    result = cli(tmp_path, "--required-tags", "")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "ok" and out["counts"]["minor"] == 1 and out["counts"]["major"] == 0


def test_a_tree_without_iac_inputs_is_incomplete_not_ok(tmp_path):
    put(tmp_path, "README.md", "nothing to scan")
    out = json.loads(cli(tmp_path).stdout)
    assert (out["status"], out["files_scanned"]) == ("fail", 0)
    assert [(f["rule"], f["severity"]) for f in out["findings"]] == [("scan-incomplete", "major")]
    assert out["findings"][0]["summary"].endswith("no in-scope input was analysed")


def test_path_must_stay_inside_root_and_exist(tmp_path):
    put(tmp_path / "repo", "a.tf", "")
    outside = cli(tmp_path / "repo", "--path", "..")
    assert outside.returncode == 2 and json.loads(outside.stdout)["status"] == "error"
    missing = cli(tmp_path / "repo", "--path", "nope")
    assert missing.returncode == 2 and "not a directory" in json.loads(missing.stdout)["error"]["message"]
    inside = cli(tmp_path / "repo", "--path", ".")
    assert inside.returncode == 0


def test_path_limits_the_scan_to_a_subdirectory(tmp_path):
    put(tmp_path, "a/main.tf", 'resource "aws_ebs_volume" "v" { type = "gp2" }')
    put(tmp_path, "b/main.tf", 'resource "aws_ebs_volume" "w" { type = "standard" }')
    out = json.loads(cli(tmp_path, "--path", "b", "--required-tags", "").stdout)
    assert [f["file"] for f in out["findings"]] == ["b/main.tf"]


def test_max_xlarge_and_required_tags_flags(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.4xlarge"\n  tags = { team = "x" }\n}\n')
    default = json.loads(cli(tmp_path).stdout)
    assert [f["rule"] for f in default["findings"]] == ["iac-untagged-resource"]
    tuned = json.loads(cli(tmp_path, "--max-xlarge", "4", "--required-tags", "team").stdout)
    assert [(f["rule"], f["severity"]) for f in tuned["findings"]] == [("iac-oversized-instance", "minor")]
    assert cli(tmp_path, "--max-xlarge", "0").returncode == 2


# --- Kubernetes -----------------------------------------------------------------------------------------------------

DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: api}
spec:
  template:
    spec:
      containers:
        - name: web
          image: x
          resources:
            requests: {cpu: 100m}
        - name: side
          image: y
          resources:
            requests: {cpu: 100m, memory: 64Mi}
            limits: {memory: 128Mi}
      initContainers:
        - name: init
          image: z
---
apiVersion: v1
kind: ConfigMap
metadata: {name: ignored}
"""


def test_kubernetes_yaml_requests_and_limits_per_container(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "k8s/dep.yaml", DEPLOYMENT)
    result = scan(tmp_path)
    assert result["files_scanned"] == 1
    assert hits(result, "k8s-missing-requests") == [("Deployment/api", "major"), ("Deployment/api", "major")]
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "k8s-missing-requests"]
    assert any("container web has no memory request" in s for s in summaries)
    assert any("container init has no cpu or memory request" in s for s in summaries)
    limits = [f["summary"] for f in result["findings"] if f["rule"] == "k8s-missing-limits"]
    assert len(limits) == 2 and all(s.startswith("Deployment/api container") for s in limits)
    assert not any("side" in f["summary"] for f in result["findings"])  # fully specified container
    assert result["status"] == "fail"


@pytest.mark.parametrize("kind, wrap", [
    ("Deployment", lambda c: {"spec": {"template": {"spec": {"containers": c}}}}),
    ("StatefulSet", lambda c: {"spec": {"template": {"spec": {"containers": c}}}}),
    ("DaemonSet", lambda c: {"spec": {"template": {"spec": {"containers": c}}}}),
    ("Job", lambda c: {"spec": {"template": {"spec": {"containers": c}}}}),
    ("CronJob", lambda c: {"spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": c}}}}}}),
    ("Pod", lambda c: {"spec": {"containers": c}}),
])
def test_kubernetes_json_workload_kinds_are_all_checked(tmp_path, kind, wrap):
    put(tmp_path, "w.json", json.dumps({"kind": kind, "metadata": {"name": "w"}, **wrap([{"name": "c", "image": "i"}])}))
    result = scan(tmp_path)
    assert hits(result, "k8s-missing-requests") == [(f"{kind}/w", "major")]
    assert hits(result, "k8s-missing-limits") == [(f"{kind}/w", "minor")]


def test_kubernetes_fully_specified_container_has_no_findings(tmp_path):
    container = {"name": "c", "image": "i", "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}}}
    put(tmp_path, "ok.json", json.dumps({"kind": "Deployment", "metadata": {"name": "ok"}, "spec": {"template": {"spec": {"containers": [container]}}}}))
    result = scan(tmp_path)
    assert (result["status"], result["findings"]) == ("ok", [])


def test_kubernetes_cpu_limit_is_not_required_but_memory_limit_is(tmp_path):
    container = {"name": "c", "image": "i", "resources": {"requests": {"cpu": "1", "memory": "1Gi"}, "limits": {"cpu": "2"}}}
    put(tmp_path, "w.json", json.dumps({"kind": "Deployment", "metadata": {"name": "w"}, "spec": {"template": {"spec": {"containers": [container]}}}}))
    result = scan(tmp_path)
    assert [(f["rule"], f["severity"]) for f in result["findings"]] == [("k8s-missing-limits", "minor")]


def test_kubernetes_list_kind_is_flattened(tmp_path):
    item = {"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c", "image": "i"}]}}
    put(tmp_path, "list.json", json.dumps({"kind": "List", "items": [item]}))
    assert hits(scan(tmp_path), "k8s-missing-requests") == [("Pod/p", "major")]


@pytest.mark.parametrize("provisioner, volume, expected", [
    ("ebs.csi.aws.com", "gp2", True), ("ebs.csi.aws.com", "io1", True), ("ebs.csi.aws.com", "standard", True),
    ("kubernetes.io/aws-ebs", "gp2", True), ("ebs.csi.aws.com", "gp3", False), ("ebs.csi.aws.com", "io2", False),
    # `standard` or `gp2` under another provisioner is not an EBS type: no advice about EBS
    ("kubernetes.io/gce-pd", "standard", False), ("cinder.csi.openstack.org", "gp2", False), (None, "gp2", False),
])
def test_kubernetes_storage_class_volumes_are_judged_only_for_aws_ebs_provisioners(tmp_path, provisioner, volume, expected):
    doc = {"kind": "StorageClass", "metadata": {"name": "fast"}, "parameters": {"type": volume}}
    if provisioner:
        doc["provisioner"] = provisioner
    put(tmp_path, "sc.json", json.dumps(doc))
    assert bool(hits(scan(tmp_path), "iac-previous-generation-volume")) is expected


# --- Terraform ------------------------------------------------------------------------------------------------------


def test_instance_size_review_and_major_bands(tmp_path):
    put(tmp_path, "main.tf", "\n".join(
        f'resource "aws_instance" "{name}" {{\n  instance_type = "{itype}"\n}}' for name, itype in
        [("a", "m5.4xlarge"), ("b", "m5.8xlarge"), ("c", "m5.16xlarge"), ("d", "m5.metal"), ("e", "m5.xlarge"), ("f", "m5.large"), ("g", "m5.24xlarge")]))
    result = scan(tmp_path)
    assert hits(result, "iac-oversized-instance") == [("aws_instance.c", "major"), ("aws_instance.g", "major"), ("aws_instance.b", "minor"), ("aws_instance.d", "minor")]
    assert hits(result, "iac-previous-generation-instance") == []


def test_instance_size_threshold_is_configurable_and_inclusive(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.4xlarge"\n}\nresource "aws_instance" "b" {\n  instance_type = "m5.3xlarge"\n}\n')
    assert hits(scan(tmp_path, max_xlarge=4), "iac-oversized-instance") == [("aws_instance.a", "minor")]
    assert hits(scan(tmp_path, max_xlarge=2), "iac-oversized-instance") == [("aws_instance.a", "major"), ("aws_instance.b", "minor")]


@pytest.mark.parametrize("attr, value, flagged", [
    ("instance_type", "m4.large", True), ("instance_type", "c3.large", True), ("instance_type", "a1.large", True),
    ("instance_type", "p3.2xlarge", True), ("instance_type", "g3.4xlarge", True), ("instance_type", "t1.micro", True),
    # t2 and g2 are not on the AWS previous-generation instance list (docs.aws.amazon.com/ec2/latest/instancetypes/pg.html)
    ("instance_type", "t2.micro", False), ("instance_type", "g2.2xlarge", False),
    ("instance_type", "m5.large", False), ("instance_type", "t3.micro", False),
    # databases: the RDS instance-class page carries no previous-generation label; ElastiCache labels only cache.t2
    ("instance_class", "db.m3.large", False), ("instance_class", "db.r6g.large", False),
    ("node_type", "cache.m4.large", False), ("node_type", "cache.r4.large", False), ("node_type", "cache.t2.micro", True),
])
def test_previous_generation_families(tmp_path, attr, value, flagged):
    put(tmp_path, "main.tf", f'resource "aws_x" "a" {{\n  {attr} = "{value}"\n}}\n')
    assert bool(hits(scan(tmp_path), "iac-previous-generation-instance")) is flagged


def test_instance_types_list_and_launch_template_volumes(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_eks_node_group" "ng" {\n  instance_types = ["m4.large", "m5.large"]\n}\n'
                             'resource "aws_launch_template" "lt" {\n  block_device_mappings {\n    ebs {\n      volume_type = "gp2"\n    }\n  }\n}\n')
    result = scan(tmp_path)
    assert hits(result, "iac-previous-generation-instance") == [("aws_eks_node_group.ng", "minor")]
    assert hits(result, "iac-previous-generation-volume") == [("aws_launch_template.lt", "minor")]


def test_interpolated_instance_and_volume_types_are_reported_not_skipped(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "${var.size}"\n}\n'
                             'resource "aws_launch_template" "lt" {\n  block_device_mappings {\n    ebs {\n      volume_type = "${var.vt}"\n    }\n  }\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "main.tf::aws_instance.a: instance_type is an interpolation and was not evaluated" in summary
    assert "main.tf::aws_launch_template.lt: volume_type is an interpolation and was not evaluated" in summary
    assert "var.size" not in summary and "var.vt" not in summary  # the value is never echoed


def test_variable_and_local_references_in_type_values_are_reported_not_skipped(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "b" {\n  instance_type = var.size\n}\n'
                             'resource "aws_eks_node_group" "ng" {\n  instance_types = local.types\n}\n'
                             'resource "aws_ebs_volume" "v" {\n  type = var.vt\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "main.tf::aws_instance.b: instance_type is a variable reference and was not evaluated" in summary
    assert "main.tf::aws_eks_node_group.ng: instance_types is a variable reference and was not evaluated" in summary
    assert "main.tf::aws_ebs_volume.v: type is a variable reference and was not evaluated" in summary


def test_count_and_for_each_are_reported_not_skipped(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "web" {\n  count = var.n\n  instance_type = "m5.large"\n}\n'
                             'resource "aws_instance" "pool" {\n  for_each = var.pools\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "main.tf::aws_instance.web: count is not evaluated: the instance count is unknown" in summary
    assert "main.tf::aws_instance.pool: for_each is not evaluated: the instance count is unknown" in summary


def test_dynamic_blocks_are_reported_not_skipped(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_launch_template" "lt" {\n  dynamic "block_device_mappings" {\n    for_each = var.disks\n    content {\n      ebs {\n        volume_type = "gp2"\n      }\n    }\n  }\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete", "iac-previous-generation-volume"]
    assert "main.tf::aws_launch_template.lt: dynamic block is not evaluated" in result["findings"][0]["summary"]


def test_terraform_json_and_cloudformation_type_values_and_counts_are_reported_not_skipped(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"resource": {"aws_instance": {"a": {"instance_type": "${var.size}", "count": 2}}}}))
    put(tmp_path, "stack.json", json.dumps({"Resources": {"Ref": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": {"Ref": "Size"}}}}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "main.tf.json::aws_instance.a: instance_type is an interpolation and was not evaluated" in summary
    assert "main.tf.json::aws_instance.a: count is not evaluated: the instance count is unknown" in summary
    assert "stack.json::AWS::EC2::Instance:Ref: InstanceType is an intrinsic function and was not evaluated" in summary


def test_module_block_with_a_remote_source_is_an_incomplete_input(tmp_path):
    put(tmp_path, "main.tf", 'module "vpc" {\n  source = "terraform-aws-modules/vpc/aws"\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "main.tf::module.vpc: module block is not evaluated" in result["findings"][0]["summary"]


def test_module_with_a_local_source_is_covered_by_its_analysed_directory(tmp_path):
    put(tmp_path, "main.tf", 'module "vpc" {\n  source = "./vpc"\n}\n')
    put(tmp_path, "vpc/main.tf", 'resource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["iac-previous-generation-volume"] and result["status"] == "ok"


def test_module_count_is_reported_on_the_module_block_even_with_an_analysed_source(tmp_path):
    put(tmp_path, "main.tf", 'module "pool" {\n  source = "./pool"\n  count = var.n\n}\n')
    put(tmp_path, "pool/main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "main.tf::module.pool: count is not evaluated: the instance count is unknown" in result["findings"][0]["summary"]


def test_terraform_json_module_blocks_are_reported_like_hcl(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"module": {"db": {"source": "git::https://example.com/db.git"}}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "main.tf.json::module.db: module block is not evaluated" in result["findings"][0]["summary"]


def test_a_commented_out_module_block_is_not_reported(tmp_path):
    put(tmp_path, "main.tf", '# module "vpc" {\n#   source = "terraform-aws-modules/vpc/aws"\n# }\n')
    result = scan(tmp_path)
    assert (result["findings"], result["status"]) == ([], "ok")



def test_ebs_volume_generations(tmp_path):
    put(tmp_path, "main.tf", "\n".join(f'resource "aws_ebs_volume" "{t}" {{\n  type = "{t}"\n}}' for t in ["gp2", "gp3", "io1", "io2", "standard", "st1"]))
    assert hits(scan(tmp_path), "iac-previous-generation-volume") == [("aws_ebs_volume.gp2", "minor"), ("aws_ebs_volume.io1", "minor"), ("aws_ebs_volume.standard", "minor")]


def test_lifecycle_rules_for_buckets_log_groups_and_repositories(tmp_path):
    put(tmp_path, "main.tf", """
resource "aws_s3_bucket" "bare" { bucket = "a" }
resource "aws_s3_bucket" "inline" {
  bucket = "b"
  lifecycle_rule { enabled = true }
}
resource "aws_s3_bucket" "split" { bucket = "c" }
resource "aws_s3_bucket_lifecycle_configuration" "split" {
  bucket = aws_s3_bucket.split.id
  rule { id = "r" }
}
resource "aws_cloudwatch_log_group" "forever" { name = "a" }
resource "aws_cloudwatch_log_group" "zero" {
  name = "b"
  retention_in_days = 0
}
resource "aws_cloudwatch_log_group" "kept" {
  name = "c"
  retention_in_days = 30
}
resource "aws_ecr_repository" "bare" { name = "a" }
resource "aws_ecr_repository" "managed" { name = "b" }
resource "aws_ecr_lifecycle_policy" "managed" {
  repository = aws_ecr_repository.managed.name
  policy = "{}"
}
""")
    assert hits(scan(tmp_path), "iac-missing-lifecycle") == [
        ("aws_cloudwatch_log_group.forever", "minor"), ("aws_cloudwatch_log_group.zero", "minor"),
        ("aws_ecr_repository.bare", "minor"), ("aws_s3_bucket.bare", "minor"),
    ]


def test_lifecycle_resource_only_covers_buckets_of_its_own_directory(tmp_path):
    put(tmp_path, "a/main.tf", 'resource "aws_s3_bucket" "logs" { bucket = "a" }\n')
    put(tmp_path, "b/main.tf", 'resource "aws_s3_bucket" "logs" { bucket = "b" }\nresource "aws_s3_bucket_lifecycle_configuration" "x" {\n  bucket = aws_s3_bucket.logs.id\n}\n')
    result = scan(tmp_path)
    assert [f["file"] for f in result["findings"] if f["rule"] == "iac-missing-lifecycle"] == ["a/main.tf"]


TAGGED = 'resource "aws_instance" "web" {\n  instance_type = "m5.large"\n  tags = {\n    owner       = "team-a"\n    environment = "prod"\n  }\n}\n'


def test_untagged_resources_use_the_tag_coverage_rules(tmp_path):
    put(tmp_path, "good.tf", TAGGED)
    put(tmp_path, "none.tf", 'resource "aws_instance" "bare" {\n  instance_type = "m5.large"\n}\n')
    put(tmp_path, "case.tf", 'resource "aws_instance" "cased" {\n  tags = { Owner = "x", environment = "prod" }\n}\n')
    put(tmp_path, "empty.tf", 'resource "aws_instance" "blank" {\n  tags = { owner = "", environment = "prod" }\n}\n')
    result = scan(tmp_path, required=["owner", "environment"])
    by_ref = {f["summary"].split(":")[0]: f for f in result["findings"] if f["rule"] == "iac-untagged-resource"}
    assert sorted(by_ref) == ["aws_instance.bare", "aws_instance.blank", "aws_instance.cased"]
    assert by_ref["aws_instance.bare"]["summary"] == "aws_instance.bare: missing: environment; missing: owner"
    assert by_ref["aws_instance.cased"]["summary"] == "aws_instance.cased: case_mismatch: owner"
    assert by_ref["aws_instance.blank"]["summary"] == "aws_instance.blank: empty_value: owner"
    assert all(f["severity"] == "major" for f in by_ref.values())


def test_tag_expressions_are_not_judged_and_never_count_as_present(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  tags = merge(local.common, { Name = "a" })\n}\nresource "aws_instance" "b" {\n  tags = local.tags\n}\n'
                             'resource "aws_instance" "c" {\n  tags = { owner = var.owner, environment = "${var.env}" }\n}\n')
    result = scan(tmp_path, required=["owner", "environment"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    detail = result["findings"][0]["summary"]
    assert all(f"main.tf::aws_instance.{ref}:" in detail for ref in "abc")


def test_an_intrinsic_tag_value_is_not_counted_as_present(tmp_path):
    put(tmp_path, "stack.json", json.dumps({"Resources": {
        "Queue": {"Type": "AWS::SQS::Queue", "Properties": {"Tags": [{"Key": "owner", "Value": {"Ref": "OwnerParam"}}, {"Key": "environment", "Value": "prod"}]}},
        "Plain": {"Type": "AWS::SQS::Queue", "Properties": {"Tags": [{"Key": "owner", "Value": "x"}, {"Key": "environment", "Value": "prod"}]}},
    }}))
    result = scan(tmp_path, required=["owner", "environment"])
    assert [f for f in result["findings"] if f["rule"] == "iac-untagged-resource"] == []
    assert "stack.json::AWS::SQS::Queue:Queue: a required tag value is an expression" in result["findings"][0]["summary"]
    assert result["status"] == "fail"


def test_provider_default_tags_skip_the_terraform_tag_check_and_say_so(tmp_path):
    put(tmp_path, "main.tf", 'provider "aws" {\n  default_tags {\n    tags = { owner = "x" }\n  }\n}\nresource "aws_instance" "a" {\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path, required=["owner"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"]  # inherited tags are not judged: the scan says so
    assert [(s["check"], s["status"]) for s in result["skipped"]] == [("iac-untagged-resource", "skipped:provider-default-tags")]


def test_computed_tag_maps_are_not_judged_and_never_count_as_missing_tags(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  tags = { for k, v in local.common : k => v }\n}\n')
    result = scan(tmp_path, required=["owner", "environment"])
    assert hits(result, "iac-untagged-resource") == [] and [f["rule"] for f in result["findings"]] == ["scan-incomplete"]


def test_a_data_source_reference_does_not_cover_a_managed_bucket_or_repository(tmp_path):
    put(tmp_path, "s3/main.tf", 'data "aws_s3_bucket" "logs" { bucket = "shared" }\nresource "aws_s3_bucket" "logs" { bucket = "mine" }\n'
                                'resource "aws_s3_bucket_lifecycle_configuration" "x" {\n  bucket = data.aws_s3_bucket.logs.id\n  rule { id = "r" }\n}\n')
    put(tmp_path, "ecr/main.tf", 'resource "aws_ecr_repository" "api" { name = "api" }\n'
                                 'resource "aws_ecr_lifecycle_policy" "p" {\n  repository = data.aws_ecr_repository.api.name\n  policy = "{}"\n}\n')
    result = scan(tmp_path)
    assert sorted(hits(result, "iac-missing-lifecycle")) == [("aws_ecr_repository.api", "minor"), ("aws_s3_bucket.logs", "minor")]


def test_default_tags_exempt_only_through_an_unaliased_provider(tmp_path):
    put(tmp_path, "vars/main.tf", 'variable "default_tags" { default = {} }\nresource "aws_instance" "a" { instance_type = "m5.large" }\n')
    put(tmp_path, "west/main.tf", 'provider "aws" {\n  alias = "west"\n  default_tags {\n    tags = { owner = "x" }\n  }\n}\n'
                                  'resource "aws_instance" "b" { instance_type = "m5.large" }\n')
    result = scan(tmp_path, required=["owner"])
    assert sorted(hits(result, "iac-untagged-resource")) == [("aws_instance.a", "major"), ("aws_instance.b", "major")]
    assert not any(s["status"] == "skipped:provider-default-tags" for s in result["skipped"])


def test_provider_default_tags_in_one_terraform_root_do_not_hide_untagged_resources_in_another(tmp_path):
    put(tmp_path, "dev/main.tf", 'provider "aws" {\n  default_tags {\n    tags = { owner = "x" }\n  }\n}\nresource "aws_instance" "a" {\n  instance_type = "m5.large"\n}\n')
    put(tmp_path, "prod/main.tf", 'resource "aws_instance" "b" {\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path, required=["owner"])
    untagged = [(f["file"], f["summary"].split(":")[0]) for f in result["findings"] if f["rule"] == "iac-untagged-resource"]
    assert untagged == [("prod/main.tf", "aws_instance.b")]


def test_empty_required_tags_disable_the_tag_check_and_say_so(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path, required=NO_TAGS)
    assert (result["findings"], result["skipped"]) == ([], [{"check": "iac-untagged-resource", "status": "skipped:no-required-tags"}])


def test_non_taggable_resources_are_not_tag_checked(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_security_group" "sg" {\n  name = "x"\n}\n')
    assert scan(tmp_path, required=["owner"])["findings"] == []


def test_hcl_comments_heredocs_and_strings_do_not_confuse_the_block_parser(tmp_path):
    put(tmp_path, "main.tf", '''
# resource "aws_instance" "commented" { instance_type = "m4.16xlarge" }
/* resource "aws_instance" "blocked" {
  instance_type = "m4.16xlarge"
} */
resource "aws_iam_policy" "p" {
  policy = <<EOT
{ "unbalanced": "{ {" }
EOT
}
resource "aws_instance" "real" {
  user_data = "echo } {"
  instance_type = "m4.xlarge" # trailing } comment
}
''')
    result = scan(tmp_path)
    assert hits(result, "iac-previous-generation-instance") == [("aws_instance.real", "minor")]
    assert hits(result, "iac-oversized-instance") == []


def test_findings_carry_the_resource_line_in_terraform_files(tmp_path):
    put(tmp_path, "main.tf", '\n\nresource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    (finding,) = scan(tmp_path)["findings"]
    assert (finding["file"], finding["location"]) == ("main.tf", "main.tf:3")


# --- Terraform JSON and CloudFormation ------------------------------------------------------------------------------


def test_terraform_json_resources(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"resource": {
        "aws_instance": {"big": {"instance_type": "c4.8xlarge", "tags": {"owner": "x"}}},
        "aws_s3_bucket": {"b": {"bucket": "x"}, "kept": {"lifecycle_rule": [{"enabled": True}]}},
        "aws_ebs_volume": {"v": {"type": "gp2"}},
    }}))
    result = scan(tmp_path, required=["owner"])
    assert hits(result, "iac-oversized-instance") == [("aws_instance.big", "minor")]
    assert hits(result, "iac-previous-generation-instance") == [("aws_instance.big", "minor")]
    assert hits(result, "iac-missing-lifecycle") == [("aws_s3_bucket.b", "minor")]
    assert hits(result, "iac-previous-generation-volume") == [("aws_ebs_volume.v", "minor")]
    assert sorted(ref for ref, _ in hits(result, "iac-untagged-resource")) == ["aws_ebs_volume.v", "aws_s3_bucket.b", "aws_s3_bucket.kept"]


CFN = {"Resources": {
    "Web": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m4.16xlarge", "Tags": [{"Key": "owner", "Value": "x"}]}},
    "Vol": {"Type": "AWS::EC2::Volume", "Properties": {"VolumeType": "gp2", "Size": 10}},
    "Logs": {"Type": "AWS::S3::Bucket", "Properties": {"BucketName": "l"}},
    "Kept": {"Type": "AWS::S3::Bucket", "Properties": {"LifecycleConfiguration": {"Rules": []}, "Tags": [{"Key": "owner", "Value": "y"}]}},
    "Group": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": 14, "Tags": [{"Key": "owner", "Value": "y"}]}},
    "Ref": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": {"Ref": "Size"}}},
}}


def test_cloudformation_json_resources(tmp_path):
    put(tmp_path, "stack.json", json.dumps(CFN))
    result = scan(tmp_path, required=["owner"])
    assert hits(result, "iac-oversized-instance") == [("AWS::EC2::Instance:Web", "major")]
    assert hits(result, "iac-previous-generation-instance") == [("AWS::EC2::Instance:Web", "minor")]
    assert hits(result, "iac-previous-generation-volume") == [("AWS::EC2::Volume:Vol", "minor")]
    assert hits(result, "iac-missing-lifecycle") == [("AWS::S3::Bucket:Logs", "minor")]
    assert sorted(ref for ref, _ in hits(result, "iac-untagged-resource")) == ["AWS::EC2::Instance:Ref", "AWS::EC2::Volume:Vol", "AWS::S3::Bucket:Logs"]


def test_cloudformation_yaml_short_form_intrinsics_are_read_as_unknown(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.yaml", """\
Resources:
  Web:
    Type: AWS::EC2::Instance
    Properties:
      InstanceType: !Ref InstanceSizeParam
      Tags:
        - Key: owner
          Value: !Sub "${Team}"
  Old:
    Type: AWS::EC2::Instance
    Properties:
      InstanceType: m3.medium
      Tags:
        - {Key: owner, Value: x}
""")
    result = scan(tmp_path, required=["owner"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete", "iac-previous-generation-instance"]
    assert "stack.yaml::AWS::EC2::Instance:Web" in result["findings"][0]["summary"]  # the !Sub owner value is not counted as present
    assert [f["summary"].split(" ")[0] for f in result["findings"] if f["rule"] == "iac-previous-generation-instance"] == ["AWS::EC2::Instance:Old"]


def test_a_whole_tags_property_that_is_an_intrinsic_is_unknown_not_missing(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.yaml", """\
Resources:
  A:
    Type: AWS::SQS::Queue
    Properties:
      Tags: !If [UseTags, [{Key: owner, Value: x}, {Key: environment, Value: p}], !Ref AWS::NoValue]
  B:
    Type: AWS::SQS::Queue
    Properties:
      Tags: !Ref TagList
  C:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: bare
""")
    put(tmp_path, "stack.json", json.dumps({"Resources": {
        "D": {"Type": "AWS::SQS::Queue", "Properties": {"Tags": {"Fn::If": ["UseTags", [{"Key": "owner", "Value": "x"}], {"Ref": "AWS::NoValue"}]}}},
        "E": {"Type": "AWS::SQS::Queue", "Properties": {"Tags": [{"Fn::If": ["UseTags", {"Key": "owner", "Value": "x"}, {"Ref": "AWS::NoValue"}]}]}},
        "F": {"Type": "AWS::SQS::Queue", "Properties": {"Tags": [{"Key": "owner", "Value": "x"}]}},
    }}))
    result = scan(tmp_path, required=["owner", "environment"])
    # only the resources whose tags are literal (or absent) are judged: C has none, F lacks environment
    assert sorted(f["summary"].split(" ")[0].rstrip(":") for f in result["findings"] if f["rule"] == "iac-untagged-resource") == ["AWS::SQS::Queue:C", "AWS::SQS::Queue:F"]


# --- optional PyYAML ------------------------------------------------------------------------------------------------


def test_yaml_checks_are_reported_as_incomplete_when_pyyaml_is_missing(tmp_path):
    put(tmp_path, "dep.yaml", DEPLOYMENT)
    put(tmp_path, "dep.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c", "image": "i"}]}}))
    mod = module()
    mod.yaml = None
    result = scan(tmp_path, required=["owner"], mod=mod)
    assert result["skipped"] == [{"check": "yaml-manifests", "status": "skipped:tool-missing", "detail": "PyYAML is not importable: 1 YAML file(s) not read"}]
    assert result["files_skipped"]["yaml_tool_missing"] == 1 and result["files_scanned"] == 1
    assert [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"] == [
        "scan incomplete, status is fail until every in-scope input is analysed: dep.yaml: YAML not read, PyYAML is not importable"]
    assert hits(result, "k8s-missing-requests") == [("Pod/p", "major")]  # JSON handling stays native


def test_pyyaml_present_reads_yaml_and_reports_no_skip(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "dep.yml", DEPLOYMENT)
    result = scan(tmp_path, required=["owner"])
    assert result["skipped"] == [] and result["files_scanned"] == 1 and result["findings"]


# --- bounds ---------------------------------------------------------------------------------------------------------


def test_large_binary_and_malformed_files_are_skipped_and_counted(tmp_path):
    put(tmp_path, "big.json", b" " * (1024 * 1024) + b"{}")
    put(tmp_path, "edge.json", b" " * (1024 * 1024 - 2) + b"{}")  # exactly 1 MiB is still read
    put(tmp_path, "blob.json", b'{"kind": "Pod"}\x00\x01')
    put(tmp_path, "latin.tf", b'resource "aws_instance" "a" { instance_type = "m4.large" }\xff')
    put(tmp_path, "broken.json", "{not json")
    result = scan(tmp_path)
    assert result["files_skipped"] == {"too_large": 1, "binary": 2, "parse_error": 1, "symlink": 0, "yaml_tool_missing": 0}
    assert result["files_scanned"] == 1  # edge.json
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"


def test_scan_stops_at_two_thousand_files(tmp_path):
    for index in range(2005):
        put(tmp_path, f"f{index:04d}.json", "{}")
    result = scan(tmp_path)
    assert result["files_scanned"] == 2000 and result["file_limit_reached"] is True
    exact = tmp_path / "exact"
    for index in range(2000):
        put(exact, f"g{index:04d}.json", "{}")
    assert scan(exact)["file_limit_reached"] is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlinks_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    put(outside, "evil.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }')
    root = tmp_path / "repo"
    put(root, "ok.tf", "")
    (root / "linked.tf").symlink_to(outside / "evil.tf")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    result = scan(root)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["files_skipped"]["symlink"] == 1
    assert "linked.tf: symlink not followed" in result["findings"][0]["summary"] and "linkdir: symlinked directory not followed" in result["findings"][0]["summary"]
    assert result["files_scanned"] == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_path_symlink_leaving_the_root_is_refused(tmp_path):
    outside = tmp_path / "outside"
    put(outside, "evil.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }')
    root = tmp_path / "repo"
    root.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    result = cli(root, "--path", "escape")
    assert result.returncode == 2 and "leaves the repository root" in json.loads(result.stdout)["error"]["message"]


def test_silent_directories_are_never_read_and_add_no_reason(tmp_path):
    silent = (".git/hooks", ".hg/store", ".svn/pristine", "node_modules/pkg", ".venv/lib", "venv/lib", "__pycache__", ".tox/py", ".mypy_cache/x", ".swarm/state", ".terraform/modules")
    for folder in silent:
        put(tmp_path, f"{folder}/main.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }')
    put(tmp_path, "src/main.tf", 'resource "aws_ebs_volume" "v" { type = "gp2" }')
    result = scan(tmp_path)
    assert [f["file"] for f in result["findings"]] == ["src/main.tf"] and result["files_scanned"] == 1
    assert result["status"] == "ok" and not [f for f in result["findings"] if f["rule"] == "scan-incomplete"]


def test_build_dist_and_vendor_directories_are_read_like_any_other(tmp_path):
    put(tmp_path, "build/main.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }\n')
    put(tmp_path, "dist/main.tf", 'resource "aws_ebs_volume" "d" {\n  type = "gp2"\n}\n')
    put(tmp_path, "vendor/main.tf", 'resource "aws_ebs_volume" "w" {\n  type = "gp2"\n}\n')
    result = scan(tmp_path)
    assert sorted({f["file"] for f in result["findings"]}) == ["build/main.tf", "dist/main.tf", "vendor/main.tf"]
    assert result["files_scanned"] == 3 and not [f for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert result["status"] == "fail"  # build's m4.16xlarge is a major finding, judged like any other directory


def test_a_node_modules_directory_gives_status_ok(tmp_path):
    put(tmp_path, "package.json", '{"name": "app"}')
    put(tmp_path, "node_modules/left-pad/main.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }\n')
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 1)


def test_findings_are_sorted_worst_first_then_by_file_and_capped(tmp_path):
    for index in range(1100):
        put(tmp_path, f"v{index:04d}.tf", 'resource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    put(tmp_path, "z.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}))
    result = scan(tmp_path)
    assert result["findings_truncated"] is True and len(result["findings"]) == 1000
    assert result["counts"] == {"info": 0, "minor": 1101, "major": 1, "blocker": 0}  # counts cover every finding
    assert result["findings"][0]["severity"] == "major" and result["status"] == "fail"
    files = [f["file"] for f in result["findings"][1:]]
    assert files == sorted(files)


def test_untagged_check_handles_more_resources_than_one_tag_chunk(tmp_path):
    body = "\n".join(f'resource "aws_instance" "i{index:03d}" {{\n  instance_type = "m5.large"\n}}' for index in range(130))
    put(tmp_path, "many.tf", body)
    result = scan(tmp_path, required=["owner"])
    assert len(hits(result, "iac-untagged-resource")) == 130
    assert result["by_rule"] == {"iac-untagged-resource": 130}


# --- reference tables -----------------------------------------------------------------------------------------------

# The AWS "Specifications for Amazon EC2 previous generation instances" page lists exactly these families
# (https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html, checked 2026-10-20).
PREVIOUS_GEN = ["a1", "c1", "c3", "c4", "g3", "i2", "m1", "m2", "m3", "m4", "p3", "p3dn", "r3", "r4", "t1"]
CURRENT_GEN = ["t2", "t3", "t3a", "t4g", "m5", "m6i", "m7g", "c5", "c6i", "c7g", "r5", "r6i", "r7g", "i3", "i4i", "g2", "g4dn", "g5", "p4d", "x2idn"]


def test_previous_generation_family_table_is_pinned():
    assert module().PREVIOUS_GEN_FAMILIES == set(PREVIOUS_GEN)
    assert module().PREVIOUS_GEN_CACHE_FAMILIES == {"t2"}
    assert module().PREVIOUS_GEN_VOLUMES == {"gp2": "gp3", "io1": "io2", "standard": "gp3"}


@pytest.mark.parametrize("family", PREVIOUS_GEN)
def test_every_previous_generation_family_is_flagged_for_ec2_only(tmp_path, family):
    put(tmp_path, "main.tf", f'resource "aws_instance" "i" {{\n  instance_type = "{family}.large"\n}}\n'
                             f'resource "aws_db_instance" "d" {{\n  instance_class = "db.{family}.large"\n}}\n'
                             f'resource "aws_elasticache_cluster" "c" {{\n  node_type = "cache.{family}.large"\n}}\n')
    assert [ref for ref, _ in hits(scan(tmp_path), "iac-previous-generation-instance")] == ["aws_instance.i"]


@pytest.mark.parametrize("family", CURRENT_GEN)
def test_current_generation_families_are_not_flagged(tmp_path, family):
    put(tmp_path, "main.tf", f'resource "aws_instance" "i" {{\n  instance_type = "{family}.large"\n}}\n')
    assert hits(scan(tmp_path), "iac-previous-generation-instance") == []


@pytest.mark.parametrize("volume, expected", [
    ("gp2", "gp3 is the newer general purpose SSD type and usually costs less per GB-month: compare the regional price"),
    ("io1", "io2 is the newer provisioned IOPS SSD type (same list price per GB and per IOPS up to 32,000 IOPS in us-east-1: check your region)"),
    ("standard", "AWS lists Magnetic (standard) as a previous generation volume type: gp3 is the replacement"),
])
def test_volume_advice_states_what_aws_documents_and_no_more(tmp_path, volume, expected):
    put(tmp_path, "main.tf", f'resource "aws_ebs_volume" "v" {{\n  type = "{volume}"\n}}\n')
    (finding,) = scan(tmp_path)["findings"]
    assert finding["summary"] == f"aws_ebs_volume.v uses {volume} volumes: {expected}"


def test_scan_never_writes_into_the_scanned_tree(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    put(tmp_path, "k.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}))
    before = sorted((p.relative_to(tmp_path).as_posix(), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())
    assert scan(tmp_path, required=["owner"])["findings"]
    assert sorted((p.relative_to(tmp_path).as_posix(), p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == before


# --- review regressions: HCL lexing, Kubernetes semantics, hostile files, secrets ----------------------------------------


def test_hcl_interpolation_with_nested_quotes_and_slashes_does_not_swallow_the_rest_of_the_line(tmp_path):
    put(tmp_path, "nested.tf", 'resource "aws_instance" "first" {\n'
                               '  tags = { Name = "${replace(var.u, "//", "/")}", owner = "a" }\n'
                               '  instance_type = "m5.24xlarge"\n}\n'
                               'resource "aws_instance" "second" {\n  instance_type = "m4.large"\n  tags = { owner = "a", environment = "p" }\n}\n')
    result = scan(tmp_path, required=["owner", "environment"])
    assert hits(result, "iac-oversized-instance") == [("aws_instance.first", "major")]
    assert hits(result, "iac-previous-generation-instance") == [("aws_instance.second", "minor")]
    assert hits(result, "iac-untagged-resource") == [("aws_instance.first", "major")]
    assert result["skipped"] == []


def test_hcl_crlf_files_mask_heredoc_bodies(tmp_path):
    text = ('resource "aws_db_instance" "db" {\n  user_data = <<-EOT\n    {\n    instance_type = "m5.24xlarge"\n  EOT\n'
            '  instance_class = "db.r5.12xlarge"\n}\n').replace("\n", "\r\n")
    put(tmp_path, "crlf.tf", text)
    # the 24xlarge inside the heredoc is text, the real class is 12xlarge (>= 8, < 16)
    assert hits(scan(tmp_path), "iac-oversized-instance") == [("aws_db_instance.db", "minor")]


def test_a_terraform_resource_that_cannot_be_delimited_is_reported_not_dropped_silently(tmp_path):
    put(tmp_path, "broken.tf", 'resource "aws_instance" "x" {\n  instance_type = "m5.24xlarge"\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and "broken.tf: 1 resource block(s) could not be delimited" in result["findings"][0]["summary"]
    (entry,) = [e for e in result["skipped"] if e["check"] == "terraform-resources"]
    assert entry["check"] == "terraform-resources" and entry["status"] == "skipped:unbalanced-braces"
    assert "1 resource block(s)" in entry["detail"] and "broken.tf" in entry["detail"]


def test_kubernetes_limit_without_request_is_the_request(tmp_path):
    # https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/: a limit with no request is copied
    # into the request, so the scheduler can place the container and the cost is attributable
    only_limits = {"name": "a", "image": "i", "resources": {"limits": {"cpu": "1", "memory": "1Gi"}}}
    memory_missing = {"name": "b", "image": "i", "resources": {"limits": {"cpu": "1"}, "requests": {"cpu": "1"}}}
    put(tmp_path, "w.json", json.dumps({"kind": "Deployment", "metadata": {"name": "w"}, "spec": {"template": {"spec": {"containers": [only_limits, memory_missing]}}}}))
    result = scan(tmp_path)
    assert [(f["id"], f["severity"]) for f in result["findings"] if f["rule"] == "k8s-missing-requests"] == [("k8s-missing-requests:w.json:Deployment/w:b", "major")]
    assert "has no memory request" in next(f["summary"] for f in result["findings"] if f["rule"] == "k8s-missing-requests")


@pytest.mark.parametrize("doc", [
    {"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": 5}},
    {"kind": "Deployment", "metadata": {"name": "d"}, "spec": {"template": "bad"}},
    {"kind": "CronJob", "metadata": {"name": "c"}, "spec": {"jobTemplate": []}},
    {"kind": "Pod", "metadata": "x", "spec": {"containers": [7, {"name": "c", "resources": {"requests": 3, "limits": []}}]}},
])
def test_malformed_manifests_do_not_abort_the_scan(tmp_path, doc):
    put(tmp_path, "a-bad.json", json.dumps(doc))
    put(tmp_path, "b-good.json", json.dumps({"kind": "Pod", "metadata": {"name": "good"}, "spec": {"containers": [{"name": "c"}]}}))
    result = scan(tmp_path)
    assert ("Pod/good", "major") in hits(result, "k8s-missing-requests")
    assert result["files_scanned"] >= 1 and result["status"] == "fail"


@pytest.mark.skipif(not hasattr(os, "scandir"), reason="needs scandir")
def test_an_unreadable_subtree_is_reported_as_incomplete_not_dropped(tmp_path, monkeypatch):
    put(tmp_path, "ok/main.tf", 'resource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    put(tmp_path, "locked/main.tf", 'resource "aws_ebs_volume" "w" {\n  type = "gp2"\n}\n')
    real_scandir = os.scandir

    def scandir(path="."):
        if os.path.basename(str(path)) == "locked":
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert [f["rule"] for f in result["findings"] if f["rule"] == "scan-incomplete"] == ["scan-incomplete"]
    assert "locked: directory could not be read" in result["findings"][0]["summary"]


def test_a_cloudformation_resource_with_a_non_literal_type_is_reported_not_skipped(tmp_path):
    put(tmp_path, "stack.json", json.dumps({"Resources": {"Q": {"Type": {"Ref": "T"}, "Properties": {"InstanceType": "m4.16xlarge"}}}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "stack.json::Q: resource Type is not a literal string and the resource was not evaluated" in result["findings"][0]["summary"]


def test_a_quoted_tag_key_with_a_space_is_one_key_not_its_last_word(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.large"\n  tags = {\n    "app environment" = "prod"\n    "cost center" = "x"\n  }\n}\n')
    result = scan(tmp_path, required=["environment", "center"])
    assert [f["rule"] for f in result["findings"]] == ["iac-untagged-resource"] and result["status"] == "fail"
    assert result["findings"][0]["summary"] == "aws_instance.a: missing: center; missing: environment"


def test_a_non_literal_hcl_log_group_retention_is_reported_not_read_as_absent(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_cloudwatch_log_group" "g" {\n  retention_in_days = var.days\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "main.tf::aws_cloudwatch_log_group.g: retention_in_days is not a literal and was not evaluated" in result["findings"][0]["summary"]


def test_a_non_literal_json_log_group_retention_is_reported_not_read_as_present(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"resource": {"aws_cloudwatch_log_group": {"g": {"retention_in_days": {"Ref": "D"}}}}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "main.tf.json::aws_cloudwatch_log_group.g: retention_in_days is not a literal and was not evaluated" in result["findings"][0]["summary"]


def test_a_cloudformation_log_group_retention_intrinsic_is_reported_not_read_as_present(tmp_path):
    put(tmp_path, "stack.json", json.dumps({"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": {"Ref": "D"}}}}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "stack.json::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in result["findings"][0]["summary"]


def test_a_yaml_short_form_log_group_retention_is_reported_not_read_as_absent(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.yaml", "Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: !Ref Days\n")
    result = scan(tmp_path)
    assert "iac-missing-lifecycle" not in [f["rule"] for f in result["findings"]]  # the !Ref is not an absent retention
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "stack.yaml::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in result["findings"][0]["summary"]


def test_a_deployment_without_a_pod_template_is_reported_not_counted_silently(tmp_path):
    put(tmp_path, "d.json", json.dumps({"kind": "Deployment", "metadata": {"name": "d"}, "spec": {}}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "d.json::Deployment/d: pod template or spec is not a mapping and the workload was not evaluated" in result["findings"][0]["summary"]


def test_a_list_item_whose_spec_is_a_string_is_reported_not_counted_silently(tmp_path):
    put(tmp_path, "l.json", json.dumps({"kind": "List", "items": [{"kind": "Pod", "metadata": {"name": "p"}, "spec": "bad"}]}))
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "l.json::Pod/p: pod template or spec is not a mapping and the workload was not evaluated" in result["findings"][0]["summary"]


def test_a_terraform_file_under_build_is_read_and_judged(tmp_path):
    put(tmp_path, "src/main.tf", 'resource "aws_ebs_volume" "v" {\n  type = "gp2"\n}\n')
    put(tmp_path, "build/main.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }\n')
    result = scan(tmp_path)
    assert sorted(f["file"] for f in result["findings"] if f["rule"] == "iac-oversized-instance") == ["build/main.tf"]
    assert not [f for f in result["findings"] if f["rule"] == "scan-incomplete"] and result["status"] == "fail"


def test_a_cloudformation_resource_whose_body_is_not_a_mapping_is_reported(tmp_path):
    put(tmp_path, "stack.json", json.dumps({"Resources": {"X": "oops", "Y": None}}))
    result = scan(tmp_path, required=["owner"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "stack.json::X: resource body is not a mapping and was not evaluated" in summary
    assert "stack.json::Y: resource body is not a mapping and was not evaluated" in summary


def test_an_intrinsic_properties_value_is_reported_and_judges_no_tags_or_type(tmp_path):
    body = {"Fn::If": ["C", {"InstanceType": "m4.16xlarge", "Tags": [{"Key": "owner", "Value": "x"}]}, {"Ref": "AWS::NoValue"}]}
    put(tmp_path, "stack.json", json.dumps({"Resources": {"I": {"Type": "AWS::EC2::Instance", "Properties": body}}}))
    result = scan(tmp_path, required=["owner"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"  # no missing-tag or type finding
    assert "stack.json::AWS::EC2::Instance:I: Properties is an intrinsic function and was not evaluated" in result["findings"][0]["summary"]


def test_a_list_item_that_is_not_a_mapping_is_reported_and_the_rest_is_scanned(tmp_path):
    item = {"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}
    put(tmp_path, "list.json", json.dumps({"kind": "List", "items": ["oops", item]}))
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "list.json::List[0]: List item is not a mapping and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert ("Pod/p", "major") in hits(result, "k8s-missing-requests")


def test_a_retention_too_large_for_a_float_fails_only_its_document_and_the_scan_continues(tmp_path):
    put(tmp_path, "a-huge.json", json.dumps({"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": 10 ** 400}}}}))
    put(tmp_path, "b-good.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}))
    result = scan(tmp_path)  # must return, not raise OverflowError out of the whole tree
    assert result["status"] == "fail" and result["files_scanned"] == 2  # both are recognised IaC files; the huge retention is reported, not evaluated
    assert "a-huge.json::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert ("Pod/p", "major") in hits(result, "k8s-missing-requests")


def test_an_interpolated_tag_key_is_reported_by_key_with_no_missing_tag_and_no_value(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "m5.large"\n  tags = {\n    "${var.env}-owner" = "team-a"\n    environment = "prod"\n  }\n}\n')
    result = scan(tmp_path, required=["owner", "environment"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"  # no false missing owner
    summary = result["findings"][0]["summary"]
    assert 'main.tf::aws_instance.a: tag key "${var.env}-owner" is an interpolation and was not evaluated' in summary
    assert "team-a" not in summary



def test_an_interpolated_terraform_json_tag_key_is_reported_by_key_with_no_missing_tag_and_no_value(tmp_path):
    tags = {"${var.env}-owner": "team-a", "environment": "prod"}
    put(tmp_path, "main.tf.json", json.dumps({"resource": {"aws_instance": {"a": {"instance_type": "m5.large", "tags": tags}}}}))
    result = scan(tmp_path, required=["owner", "environment"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"  # no false missing owner
    summary = result["findings"][0]["summary"]
    assert 'main.tf.json::aws_instance.a: tag key "${var.env}-owner" is an interpolation and was not evaluated' in summary
    assert "team-a" not in summary


def test_a_top_level_json_array_is_passed_over_silently_as_not_an_iac_document(tmp_path):
    put(tmp_path, "list.json", json.dumps([{"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}]))
    result = scan(tmp_path)
    assert (result["status"], result["files_scanned"], result["findings"]) == ("ok", 0, [])  # no scan-incomplete, no Pod finding


def test_a_top_level_json_scalar_is_passed_over_silently_as_not_an_iac_document(tmp_path):
    put(tmp_path, "scalar.json", "42")
    result = scan(tmp_path)
    assert (result["status"], result["files_scanned"], result["findings"]) == ("ok", 0, [])


CLEAN_MAIN = 'resource "aws_instance" "ok" {\n  instance_type = "m5.large"\n}\n'


@pytest.mark.parametrize("value", ["${var.owner}", "%{if true}p%{endif}"])
def test_an_interpolated_terraform_json_required_tag_value_is_not_counted_present(tmp_path, value):
    put(tmp_path, "main.tf.json", json.dumps({"resource": {"aws_instance": {"a": {"instance_type": "m5.large", "tags": {"owner": value, "environment": "prod"}}}}}))
    result = scan(tmp_path, required=["owner", "environment"])
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    summary = result["findings"][0]["summary"]
    assert "main.tf.json::aws_instance.a: a required tag value is an expression and was not judged" in summary
    assert value not in summary and "var.owner" not in summary and "endif" not in summary  # the value never appears


def test_a_cloudformation_template_file_is_a_candidate_and_its_finding_is_reported(tmp_path):
    put(tmp_path, "stack.template", json.dumps({"Resources": {"I": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["files_scanned"] == 2 and result["status"] == "fail"
    assert [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "stack.template")]


def test_a_yaml_cloudformation_template_file_is_read_too(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.template", "Resources:\n  I:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: c5.18xlarge\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["files_scanned"] == 2 and [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "stack.template")]


def test_a_deployment_list_item_is_judged_by_its_own_kind(tmp_path):
    item = {"kind": "Deployment", "metadata": {"name": "d"}, "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}
    put(tmp_path, "list.json", json.dumps({"kind": "DeploymentList", "items": [item]}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert ("Deployment/d", "major") in hits(result, "k8s-missing-requests") and result["status"] == "fail"
    assert result["files_scanned"] == 2


def test_a_configmap_list_stays_silent(tmp_path):
    item = {"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {}}
    put(tmp_path, "cm.json", json.dumps({"kind": "ConfigMapList", "items": [item]}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)


@pytest.mark.parametrize("text", ["# nothing here\n", "---\n"])
def test_a_yaml_file_with_no_document_is_passed_over_and_not_counted(tmp_path, text):
    pytest.importorskip("yaml")
    put(tmp_path, "notes.yaml", text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["files_scanned"], result["findings"]) == ("ok", 1, [])


def test_a_template_holding_a_yaml_sequence_is_passed_over_and_not_misread_as_json(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.template", "[a, b]\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 1)  # no could-not-be-parsed reason


def test_a_template_holding_a_yaml_flow_mapping_is_read_as_yaml(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.template", "{Resources: {I: {Type: AWS::EC2::Instance, Properties: {InstanceType: c5.18xlarge}}}}\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["files_scanned"] == 2 and [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "stack.template")]


def test_a_json_template_is_still_read_as_json(tmp_path):
    put(tmp_path, "stack.template", json.dumps({"Resources": {"I": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["files_scanned"] == 2 and [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "stack.template")]


@pytest.mark.parametrize("text", ["{ not json : [\n", "Resources: [unclosed\n"])
def test_a_template_that_is_neither_json_nor_yaml_is_reported_as_could_not_be_parsed(tmp_path, text):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.template", text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "stack.template: could not be parsed" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_deployment_list_item_with_no_kind_is_reported_and_a_valid_item_is_still_analysed(tmp_path):
    no_kind = {"spec": {"containers": [{"name": "c"}]}}
    valid = {"kind": "Deployment", "metadata": {"name": "d"}, "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}
    put(tmp_path, "list.json", json.dumps({"kind": "DeploymentList", "items": [no_kind, valid]}))
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "list.json::DeploymentList[0]: DeploymentList item has no kind and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert ("Deployment/d", "major") in hits(result, "k8s-missing-requests")


@pytest.mark.parametrize("name", ["stack.template", "stack.json"])
def test_a_non_finite_json_retention_is_not_a_literal_and_fails_the_scan(tmp_path, name):
    put(tmp_path, name, '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": 1e400}}}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert f"{name}::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_non_finite_yaml_retention_fails_the_scan_like_json(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "stack.yaml", "Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: .inf\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "stack.yaml::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("name", ["dep.yaml", "dep.yml"])
def test_a_tab_indented_json_deployment_in_a_yaml_file_is_read_as_json(tmp_path, name):
    pytest.importorskip("yaml")
    tab_json = '{\n\t"kind": "Deployment",\n\t"metadata": {"name": "d"},\n\t"spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}\n}\n'
    put(tmp_path, name, tab_json)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert ("Deployment/d", "major") in hits(result, "k8s-missing-requests") and result["files_scanned"] == 2


@pytest.mark.parametrize("name", ["bad.yaml", "bad.yml"])
def test_a_yaml_file_with_neither_yaml_nor_json_syntax_is_reported_as_could_not_be_parsed(tmp_path, name):
    pytest.importorskip("yaml")
    put(tmp_path, name, "key: [unclosed\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert f"{name}: could not be parsed" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def _cfn_log_group_json(value: str) -> str:
    return '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": %s}}}}' % value


def _cfn_log_group_yaml(value: str) -> str:
    return "Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: %s\n" % value


@pytest.mark.parametrize("name", ["s.template", "s.json", "s.yaml"])
def test_a_repeated_key_is_reported_as_a_duplicate_and_fails_the_scan(tmp_path, name):
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
        text = 'Resources:\n  A:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: c5.18xlarge\nResources:\n  B:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m5.large\n'
    else:
        text = '{"Resources": {"A": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}, "Resources": {"B": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "m5.large"}}}}'
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert f'{name}: duplicate key "Resources" in the document: the earlier value was not evaluated' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("name", ["s.template", "s.json", "s.yaml"])
def test_a_document_with_no_repeated_key_has_no_duplicate_reason(tmp_path, name):
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
        text = "Resources:\n  A:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: c5.18xlarge\n"
    else:
        text = '{"Resources": {"A": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}}'
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert not [f for f in result["findings"] if "duplicate key" in f["summary"]]
    assert [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", name)]


@pytest.mark.parametrize("name, text", [
    ("s.json", _cfn_log_group_json("-30")),
    ("s.json", _cfn_log_group_json("5e-400")),
    ("s.yaml", _cfn_log_group_yaml("-30")),
    ("s.yaml", _cfn_log_group_yaml("5.0e-400")),
])
def test_a_non_positive_retention_is_not_a_literal_and_fails_the_scan(tmp_path, name, text):
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert f"{name}::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert not [f for f in result["findings"] if f["rule"] == "iac-missing-lifecycle"]  # not the old minor set-retention finding


@pytest.mark.parametrize("name, text", [("s.json", _cfn_log_group_json("30")), ("s.yaml", _cfn_log_group_yaml("30"))])
def test_a_positive_retention_still_passes(tmp_path, name, text):
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)


@pytest.mark.parametrize("name", ["s.json", "s.yaml"])
def test_a_retention_too_large_for_a_float_fails_only_its_value_and_the_rest_is_judged(tmp_path, name):
    big = "1" * 400
    other = {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
        text = f"Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: {big}\n  I:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: c5.18xlarge\n"
    else:
        text = '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": %s}}, "I": %s}}' % (big, json.dumps(other))
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert result["status"] == "fail" and len(summaries) == 1
    assert f"{name}::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in summaries[0]
    assert "could not be evaluated" not in summaries[0]  # the document was not lost as a whole
    assert ("iac-oversized-instance", name) in [(f["rule"], f["file"]) for f in result["findings"]]  # the other resource is still judged


@pytest.mark.parametrize("name", ["s.template", "s.json", "s.yaml"])
def test_a_data_object_with_a_repeated_key_is_passed_over_silently(tmp_path, name):
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
        text = "name: a\nname: b\n"
    else:
        text = '{"name": "a", "name": "b"}'
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # no duplicate-key reason for a non-IaC document


def test_a_repeated_key_in_a_data_document_beside_an_iac_document_stays_silent(tmp_path):
    pytest.importorskip("yaml")
    text = "Resources:\n  A:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m5.large\n---\nname: a\nname: b\n"
    put(tmp_path, "stack.yaml", text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)


def test_a_json_object_with_a_repeated_data_key_is_passed_over_silently(tmp_path):
    put(tmp_path, "data.json", '{"data": {"a": 1}, "data": {"b": 2}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # data is not an IaC marker


def test_a_terraform_json_document_with_a_repeated_resource_key_is_reported_and_fails(tmp_path):
    resource = {"aws_instance": {"a": {"instance_type": "m5.large"}}}
    put(tmp_path, "main.tf.json", '{"resource": %s, "resource": {"aws_instance": {"b": {"instance_type": "m5.large"}}}}' % json.dumps(resource))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert 'main.tf.json: duplicate key "resource" in the document: the earlier value was not evaluated' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("module", [{"a": 1}, {"net": {"version": "1"}}, {}])
def test_a_json_module_key_that_is_not_a_terraform_module_block_is_passed_over(tmp_path, module):
    put(tmp_path, "mod.json", json.dumps({"module": module}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # an ordinary JSON object, not a module


def test_a_terraform_shaped_module_block_in_a_json_object_is_reported_not_evaluated(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"module": {"net": {"source": "./net"}}}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "main.tf.json::module.net: module block is not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_repeated_resource_key_nested_in_a_data_document_is_passed_over(tmp_path):
    put(tmp_path, "d.json", '{"items": [{"resource": "x", "resource": "y"}]}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # the root is not IaC


def test_a_top_level_array_with_a_repeated_resource_key_is_passed_over(tmp_path):
    put(tmp_path, "d.json", '[{"resource": "x", "resource": "y"}]')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 1)  # the array is out of scope


def test_a_repeated_marker_name_nested_in_a_data_object_is_passed_over(tmp_path):
    put(tmp_path, "d.json", '{"meta": {"Resources": 1, "Resources": 2}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)


def test_a_repeated_key_inside_a_terraform_json_module_block_is_reported_and_fails(tmp_path):
    put(tmp_path, "main.tf.json", '{"module": {"net": {"source": "./net", "count": 3}, "net": {"source": "./net"}}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    summary = next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert 'main.tf.json: duplicate key "net" in the document: the earlier value was not evaluated' in summary


def test_a_terraform_json_module_mapping_with_a_non_block_sibling_fails_naming_both(tmp_path):
    put(tmp_path, "main.tf.json", '{"module": {"net": {"source": "./net"}, "a": 1}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    summary = next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert "main.tf.json::module.net: module block is not evaluated" in summary
    assert "main.tf.json::module.a: module entry is not a Terraform module block and was not evaluated" in summary


def test_a_null_log_group_retention_gives_the_retention_reason_and_fails(tmp_path):
    put(tmp_path, "s.json", '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": null}}}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "s.json::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert not [f for f in result["findings"] if f["rule"] == "iac-missing-lifecycle"]  # null is not the never-set minor finding


def test_zero_retention_is_the_same_minor_finding_in_terraform_and_terraform_json(tmp_path):
    put(tmp_path, "a/main.tf", 'resource "aws_cloudwatch_log_group" "g" {\n  retention_in_days = 0\n}\n')
    put(tmp_path, "b/main.tf.json", '{"resource": {"aws_cloudwatch_log_group": {"g": {"retention_in_days": 0}}}}')
    result = scan(tmp_path)
    assert result["status"] == "ok"
    minors = [f for f in result["findings"] if f["rule"] == "iac-missing-lifecycle"]
    assert len(minors) == 2 and {f["severity"] for f in minors} == {"minor"}
    by_file = {f["file"]: f for f in minors}
    hcl, json_form = by_file["a/main.tf"], by_file["b/main.tf.json"]
    assert hcl["summary"] == json_form["summary"]
    # the finding id is rule:file:ref, so the two differ only by the file path; rule and ref are the same
    assert hcl["id"].split(":")[0] == json_form["id"].split(":")[0] and hcl["id"].split(":")[-1] == json_form["id"].split(":")[-1] == "aws_cloudwatch_log_group.g"


@pytest.mark.parametrize("pair, duplicate", [(("1", '"1"'), False), (("0x1", "1"), True)])
def test_yaml_keys_are_compared_by_their_constructed_value(tmp_path, pair, duplicate):
    pytest.importorskip("yaml")
    first, second = pair
    put(tmp_path, "s.yaml", f"Resources:\n  G:\n    Type: AWS::EC2::Instance\n    Properties:\n      {first}: a\n      {second}: b\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    if duplicate:
        assert result["status"] == "fail"
        assert 's.yaml: duplicate key "1" in the document: the earlier value was not evaluated' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    else:
        assert (result["status"], result["findings"]) == ("ok", [])


@pytest.mark.parametrize("literal, status", [("1.0e+2", "ok"), ("1e2", "fail")])
def test_a_yaml_float_retention_with_a_dot_and_signed_exponent_is_read_and_one_without_is_a_string(tmp_path, literal, status):
    pytest.importorskip("yaml")
    put(tmp_path, "s.yaml", f"Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: {literal}\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == status
    if status == "fail":
        assert "s.yaml::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("text", ["time: 1:30.5\n", "time: 190:20:30.15\n", "time: ._.\n"])
def test_a_base_60_or_dotted_yaml_value_does_not_abort_the_scan(tmp_path, text):
    pytest.importorskip("yaml")
    put(tmp_path, "t.yaml", text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # the document is read, main.tf stays ok


def test_a_base_60_retention_is_a_non_literal_and_gives_the_retention_reason(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "s.yaml", "Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: 1:30.5\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "s.yaml::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("name, expected", [("1.0e+2", "100.0"), ("1.50", "1.5")])
def test_a_yaml_float_name_prints_as_a_float(tmp_path, name, expected):
    pytest.importorskip("yaml")
    put(tmp_path, "d.yaml", f"kind: Deployment\nmetadata: {{name: {name}}}\nspec:\n  template:\n    spec:\n      containers:\n      - name: c\n")
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert any(f["summary"].startswith(f"Deployment/{expected} container c has no cpu or memory request") for f in result["findings"])


def test_a_json_float_name_prints_as_a_float(tmp_path):
    put(tmp_path, "d.json", '{"kind": "Deployment", "metadata": {"name": 1E2}, "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert any(f["summary"].startswith("Deployment/100.0 container c has no cpu or memory request") for f in result["findings"])


def test_float_text_decides_zero_underflow_and_a_retention_of_one_hundred(tmp_path):
    mod = module()
    assert mod._doc_retention(mod._NumText("1.0e+2")) is True and float(mod._NumText("1.0e+2")) == 100.0  # reads as 100
    assert mod._doc_retention(mod._NumText("5e-400")) is None  # an underflow, read from the text, is not zero
    assert mod._doc_retention(mod._NumText("0.0")) is False  # a zero literal
    put(tmp_path, "s.json", '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": 5e-400}}}}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail" and "retention_in_days is not a literal" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_module_key_in_a_data_json_file_is_an_ordinary_object(tmp_path):
    put(tmp_path, "data.json", json.dumps({"name": "app", "module": {"widget": {"source": "docs/widget.md", "title": "Widget"}}}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # Terraform reads only *.tf.json


def test_the_same_module_block_in_a_terraform_json_file_is_still_reported(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps({"module": {"widget": {"source": "docs/widget.md", "title": "Widget"}}}))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert "main.tf.json::module.widget: module block is not evaluated" in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("name", ["s.json", "s.yaml"])
def test_a_retention_longer_than_4000_digits_gives_the_retention_reason_and_judges_the_rest(tmp_path, name):
    big = "1" * 5000
    if name.endswith(".yaml"):
        pytest.importorskip("yaml")
        text = f"Resources:\n  G:\n    Type: AWS::Logs::LogGroup\n    Properties:\n      RetentionInDays: {big}\n  I:\n    Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: c5.18xlarge\n"
    else:
        text = '{"Resources": {"G": {"Type": "AWS::Logs::LogGroup", "Properties": {"RetentionInDays": %s}}, "I": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}}' % big
    put(tmp_path, name, text)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    summary = next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert result["status"] == "fail"
    assert f"{name}::AWS::Logs::LogGroup:G: retention_in_days is not a literal and was not evaluated" in summary
    assert "could not be parsed" not in summary  # the document is not lost as a whole
    assert ("iac-oversized-instance", name) in [(f["rule"], f["file"]) for f in result["findings"]]  # the other resource is still judged


def test_a_long_integer_marker_renders_as_its_text(tmp_path):
    mod = module()
    text = "7" * 5000
    marker = mod._TooLongInt(text)
    assert str(marker) == text and f"{marker}" == text and repr(marker) == text


@pytest.mark.parametrize("digits", [40, 4001])
def test_a_repeated_explicit_key_of_any_length_in_a_resources_mapping_is_reported(tmp_path, digits):
    pytest.importorskip("yaml")
    key = "1" * digits
    block = f"  ? {key}\n  : Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m5.large\n"
    put(tmp_path, "s.yaml", "Resources:\n" + block + block)
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"  # the 40-digit and the 4001-digit key are treated alike
    assert f's.yaml: duplicate key "{key}" in the document: the earlier value was not evaluated' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_long_integer_marker_equals_only_a_marker_with_the_same_text(tmp_path):
    mod = module()
    first, second = mod._TooLongInt("7" * 4001), mod._TooLongInt("7" * 4001)
    assert first == second and hash(first) == hash(second) and len({first, second}) == 1
    assert first != mod._TooLongInt("8" * 4001)
    assert first != 7 and first != 7.0 and first != "7" * 4001


RESOURCE_DOCUMENT = {"resource": {"aws_instance": {"web": {"instance_type": "c5.18xlarge"}}}}


def test_a_terraform_resource_document_in_a_data_json_file_is_an_ordinary_object(tmp_path):
    put(tmp_path, "data.json", json.dumps(RESOURCE_DOCUMENT))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)  # Terraform reads only *.tf.json


def test_the_same_terraform_resource_document_in_a_terraform_json_file_is_still_judged(tmp_path):
    put(tmp_path, "main.tf.json", json.dumps(RESOURCE_DOCUMENT))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "main.tf.json")]


def test_a_cloudformation_resources_document_in_a_json_file_is_still_judged(tmp_path):
    put(tmp_path, "stack.json", json.dumps({"Resources": {"I": {"Type": "AWS::EC2::Instance", "Properties": {"InstanceType": "c5.18xlarge"}}}}))
    result = scan(tmp_path)
    assert [(f["rule"], f["file"]) for f in result["findings"]] == [("iac-oversized-instance", "stack.json")]


def test_a_repeated_resource_key_at_the_root_of_a_data_json_file_is_passed_over(tmp_path):
    put(tmp_path, "data.json", '{"resource": 1, "resource": 2}')
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], result["findings"], result["files_scanned"]) == ("ok", [], 2)


def _resources_with_keys(*keys: str) -> str:
    return "Resources:\n" + "".join(f"  ? {key}\n  : Type: AWS::EC2::Instance\n    Properties:\n      InstanceType: m5.large\n" for key in keys)


@pytest.mark.parametrize("first, second", [("+" + "1" * 4001, "1" * 4001), ("1" * 4001, "+" + "1" * 4001)])
def test_a_plus_prefixed_long_key_and_a_plain_long_key_of_the_same_value_are_a_duplicate(tmp_path, first, second):
    pytest.importorskip("yaml")
    put(tmp_path, "s.yaml", _resources_with_keys(first, second))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert f's.yaml: duplicate key "{second}" in the document: the earlier value was not evaluated' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


def test_a_long_leading_zero_key_is_octal_as_pyyaml_reads_it_so_it_is_not_its_decimal_twin(tmp_path):
    pytest.importorskip("yaml")
    # PyYAML (YAML 1.1) reads a leading zero as octal: 0000000000 followed by 4000 ones is a different integer from 4000 ones in decimal
    put(tmp_path, "s.yaml", _resources_with_keys("0" * 10 + "1" * 4000, "1" * 4000))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], [f for f in result["findings"] if "duplicate key" in f["summary"]]) == ("ok", [])


def test_a_leading_zero_key_equal_to_a_decimal_key_by_pyyaml_octal_reading_is_a_duplicate(tmp_path):
    pytest.importorskip("yaml")
    put(tmp_path, "s.yaml", _resources_with_keys("0111", "73"))  # 0111 is octal 73, the same integer as 73
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert result["status"] == "fail"
    assert 's.yaml: duplicate key "73" in the document' in next(f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete")


@pytest.mark.parametrize("first, second", [("1" * 4001, "2" * 4001), ("+" + "1" * 4001, "-" + "1" * 4001)])
def test_long_keys_of_different_value_are_not_duplicates(tmp_path, first, second):
    pytest.importorskip("yaml")
    put(tmp_path, "s.yaml", _resources_with_keys(first, second))
    put(tmp_path, "main.tf", CLEAN_MAIN)
    result = scan(tmp_path)
    assert (result["status"], [f for f in result["findings"] if "duplicate key" in f["summary"]]) == ("ok", [])


def test_a_long_integer_marker_compares_by_its_value_with_a_plain_int(tmp_path):
    mod = module()
    plus = mod._TooLongInt("+" + "1" * 4001)
    assert plus == mod._TooLongInt("1" * 4001) and hash(plus) == hash(mod._TooLongInt("1" * 4001))
    leading = mod._TooLongInt("0000" + "1" * 4000)
    assert leading == int("1" * 4000) and hash(leading) == hash(int("1" * 4000))
    assert mod._TooLongInt("-" + "1" * 4001) != plus
    assert plus != 7.0 and plus != "1" * 4001


def test_interpolated_type_values_still_record_a_reason_after_the_scan_completeness_fixes(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "${var.size}"\n}\n')
    result = scan(tmp_path)
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"] and result["status"] == "fail"
    assert "instance_type is an interpolation and was not evaluated" in result["findings"][0]["summary"]


def test_a_named_pipe_with_a_manifest_name_is_skipped_instead_of_blocking_the_scan(tmp_path):
    import signal

    os.mkfifo(tmp_path / "pipe.json")
    put(tmp_path, "ok.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(AssertionError("scan blocked on a FIFO")))  # not an OSError: the scan must not swallow it
    signal.alarm(10)
    try:
        result = scan(tmp_path)
    finally:
        signal.alarm(0)
    assert result["files_skipped"]["parse_error"] == 1 and ("Pod/p", "major") in hits(result, "k8s-missing-requests")


CANARIES = [
    "AKIAIOSFODNN7EXAMPLE", "ghp_1234567890abcdefghijklmnopqrstuvwxyz", "ab" * 32,
    "abandon ability able about above absent absorb abstract absurd abuse access accident", "https://user:pa55w0rd@host.example/x",
]


@pytest.mark.parametrize("canary", CANARIES)
def test_secrets_found_in_scanned_files_never_reach_the_report(tmp_path, canary):
    """The scanner reads files that may carry credentials; it reports paths, resource references and sizes only."""
    put(tmp_path, "main.tf", f'''
resource "aws_instance" "web" {{
  instance_type = "m5.24xlarge"
  user_data     = "export TOKEN={canary}"
  tags          = {{ owner = "{canary}", environment = "" }}
}}
resource "aws_ebs_volume" "v" {{
  type = "gp2"
  tags = {{ owner = "{canary}" }}
}}
''')
    put(tmp_path, "k.yaml", f'''apiVersion: v1
kind: Pod
metadata: {{name: p, annotations: {{secret: "{canary}"}}}}
spec:
  containers:
  - name: c
    image: "{canary}"
    env: [{{name: TOKEN, value: "{canary}"}}]
---
apiVersion: v1
kind: Secret
metadata: {{name: s}}
stringData: {{password: "{canary}"}}
''')
    put(tmp_path, "cfn.json", json.dumps({"Resources": {"I": {"Type": "AWS::EC2::Instance", "Properties": {
        "InstanceType": "m5.24xlarge", "UserData": canary, "Tags": [{"Key": "owner", "Value": canary}]}}}}))
    result = scan(tmp_path, required=["owner", "environment"])
    assert result["findings"], "the fixture must produce findings for the assertion to mean anything"
    assert canary not in json.dumps(result)


def test_a_byte_order_mark_does_not_hide_the_first_resource_or_a_json_manifest(tmp_path):
    (tmp_path / "bom.tf").write_bytes(b'\xef\xbb\xbfresource "aws_instance" "first" {\n  instance_type = "m5.24xlarge"\n}\n')
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c"}]}}).encode())
    result = scan(tmp_path)
    assert hits(result, "iac-oversized-instance") == [("aws_instance.first", "major")]
    assert hits(result, "k8s-missing-requests") == [("Pod/p", "major")]
    assert result["files_skipped"]["parse_error"] == 0 and result["files_scanned"] == 2


def test_yaml_alias_bomb_under_a_terraform_shaped_key_stays_bounded(tmp_path):
    # nine levels of nine aliases would be 9**9 strings if the shared nodes were expanded
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x]"] + [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]" for i in range(1, 9)]
    body = "\n".join(f"      {line}" for line in levels)
    put(tmp_path, "bomb.yaml", f"resource:\n  aws_s3_bucket_lifecycle_configuration:\n    b:\n{body}\n")
    assert scan(tmp_path)["files_scanned"] == 1


def test_yaml_mapping_with_mixed_key_types_is_not_a_crash(tmp_path):
    put(tmp_path, "mixed.yaml", "kind: Pod\nmetadata: {name: m}\nspec:\n  containers:\n  - name: c\n    resources:\n      requests: {1: a, cpu: 1, memory: 1Gi}\n      limits: {memory: 2Gi}\n")
    assert scan(tmp_path)["findings"] == []
