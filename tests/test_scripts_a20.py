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


def test_clean_tree_is_ok_with_no_findings(tmp_path):
    put(tmp_path, "README.md", "nothing to scan")
    out = json.loads(cli(tmp_path).stdout)
    assert (out["status"], out["files_scanned"], out["findings"]) == ("ok", 0, [])


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


def test_interpolated_instance_types_are_not_evaluated(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  instance_type = "${var.size}"\n}\nresource "aws_instance" "b" {\n  instance_type = var.size\n}\n')
    assert scan(tmp_path)["findings"] == []


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


def test_tag_expressions_are_unknown_and_not_flagged(tmp_path):
    put(tmp_path, "main.tf", 'resource "aws_instance" "a" {\n  tags = merge(local.common, { Name = "a" })\n}\nresource "aws_instance" "b" {\n  tags = local.tags\n}\n'
                             'resource "aws_instance" "c" {\n  tags = { owner = var.owner, environment = "${var.env}" }\n}\n')
    assert scan(tmp_path, required=["owner", "environment"])["findings"] == []


def test_provider_default_tags_skip_the_terraform_tag_check_and_say_so(tmp_path):
    put(tmp_path, "main.tf", 'provider "aws" {\n  default_tags {\n    tags = { owner = "x" }\n  }\n}\nresource "aws_instance" "a" {\n  instance_type = "m5.large"\n}\n')
    result = scan(tmp_path, required=["owner"])
    assert result["findings"] == []
    assert [(s["check"], s["status"]) for s in result["skipped"]] == [("iac-untagged-resource", "skipped:provider-default-tags")]


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
    assert [(f["rule"], f["summary"].split(" ")[0]) for f in result["findings"]] == [("iac-previous-generation-instance", "AWS::EC2::Instance:Old")]


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


def test_yaml_checks_are_skipped_not_failed_when_pyyaml_is_missing(tmp_path):
    put(tmp_path, "dep.yaml", DEPLOYMENT)
    put(tmp_path, "dep.json", json.dumps({"kind": "Pod", "metadata": {"name": "p"}, "spec": {"containers": [{"name": "c", "image": "i"}]}}))
    mod = module()
    mod.yaml = None
    result = scan(tmp_path, required=["owner"], mod=mod)
    assert result["skipped"] == [{"check": "yaml-manifests", "status": "skipped:tool-missing", "detail": "PyYAML is not importable: 1 YAML file(s) not read"}]
    assert result["files_skipped"]["yaml_tool_missing"] == 1 and result["files_scanned"] == 1
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
    assert result["findings"] == [] and result["status"] == "ok"


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
    assert result["findings"] == [] and result["files_skipped"]["symlink"] == 1
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


def test_vendored_and_state_directories_are_skipped(tmp_path):
    for folder in ("node_modules/pkg", ".git/hooks", ".terraform/modules", ".venv/lib", "dist", "build"):
        put(tmp_path, f"{folder}/main.tf", 'resource "aws_instance" "x" { instance_type = "m4.16xlarge" }')
    put(tmp_path, "src/main.tf", 'resource "aws_ebs_volume" "v" { type = "gp2" }')
    result = scan(tmp_path)
    assert [f["file"] for f in result["findings"]] == ["src/main.tf"] and result["files_scanned"] == 1


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
    assert result["findings"] == []
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
