"""Behaviour tests for scripts/plat_catalog_lint.py (A17): real mode over fixture trees, bounds, and the dry-run contract."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "plat_catalog_lint.py"

try:
    import yaml  # noqa: F401

    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False
needs_yaml = pytest.mark.skipif(not HAVE_YAML, reason="PyYAML is not installed")


def descriptor(name="payments-api", **over) -> dict:
    """A clean production Component descriptor; keyword overrides replace top-level spec/metadata parts."""
    doc = {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {
            "name": name,
            "description": "Payments API",
            "labels": {"tier": "tier-1"},
            "annotations": {"backstage.io/techdocs-ref": "dir:.", "backstage.io/source-location": "url:https://example.com/repo", "example.com/runbook": "https://example.com/rb"},
        },
        "spec": {"type": "service", "lifecycle": "production", "owner": "group:default/payments", "system": "checkout"},
    }
    for key, value in over.items():
        part, _, leaf = key.partition("__")
        if leaf:
            if value is None:
                doc[part].pop(leaf, None)
            else:
                doc[part][leaf] = value
        else:
            doc[key] = value
    return doc


def write_json(root: Path, rel: str, payload) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_yaml(root: Path, rel: str, *docs) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")
    return path


def run_script(tmp_path: Path, *extra: str, root: Path | None = None, dry: bool = False) -> tuple[int, dict]:
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "swarm-state"), "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, str(SCRIPT), "--root", str(root or tmp_path / "repo"), "--json", *(["--dry-run"] if dry else []), *extra]
    done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    assert "Traceback" not in done.stderr, done.stderr
    return done.returncode, json.loads(done.stdout)


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def ids(report: dict) -> list[str]:
    return [f["id"] for f in report["findings"]]


def by_rule(report: dict, rule: str) -> list[dict]:
    return [f for f in report["findings"] if f["id"].split("@")[0] == rule]


# --- dry run ----------------------------------------------------------------------------------------------------


def test_dry_run_is_deterministic_json_and_reads_nothing(tmp_path, repo):
    write_json(repo, "catalog-info.json", descriptor(spec__owner=None))
    code, first = run_script(tmp_path, dry=True)
    assert code == 0
    assert first == {
        "status": "ok", "dry_run": True, "scanned_files": 0, "entity_count": 0, "finding_count": 0, "findings": [], "skipped": [], "truncated": False,
        "summary": "dry-run: plat_catalog_lint would lint catalog-info descriptors under --root/--path", "agent": "A17", "script": "plat_catalog_lint",
    }
    assert run_script(tmp_path, dry=True)[1] == first
    code, missing = run_script(tmp_path, root=tmp_path / "does-not-exist", dry=True)
    assert code == 0 and missing == first


def test_dry_run_with_no_other_arguments_exits_zero(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "swarm-state")}
    done = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--json"], capture_output=True, text=True, env=env, cwd=tmp_path, check=False)
    assert done.returncode == 0 and json.loads(done.stdout)["status"] == "ok"


# --- real mode: findings and exit codes -------------------------------------------------------------------------


def test_clean_descriptor_is_ok_with_exit_zero(tmp_path, repo):
    write_json(repo, "svc/catalog-info.json", descriptor())
    code, report = run_script(tmp_path)
    assert code == 0
    assert {k: v for k, v in report.items() if k != "summary"} == {
        "status": "ok", "scanned_files": 1, "entity_count": 1, "finding_count": 0, "findings": [], "skipped": [], "truncated": False,
        "agent": "A17", "script": "plat_catalog_lint",
    }


def test_failing_descriptor_reports_findings_with_locations_and_exits_one(tmp_path, repo):
    write_json(repo, "svc/catalog-info.json", descriptor(spec__owner=None, spec__system=None, metadata__annotations={}))
    code, report = run_script(tmp_path)
    assert code == 1 and report["status"] == "fail"
    assert [(f["id"], f["severity"], f["location"]) for f in report["findings"][:4]] == [
        ("owner-missing@component:default/payments-api", "blocker", "svc/catalog-info.json"),
        ("docs-missing@component:default/payments-api", "major", "svc/catalog-info.json"),
        ("runbook-missing@component:default/payments-api", "major", "svc/catalog-info.json"),
        ("system-missing@component:default/payments-api", "major", "svc/catalog-info.json"),
    ]
    assert set(report["findings"][0]) == {"id", "severity", "summary", "location"}
    assert report["finding_count"] == len(report["findings"])


def test_minor_and_info_findings_alone_do_not_fail(tmp_path, repo):
    doc = descriptor(metadata__description=None)
    doc["metadata"]["labels"] = {"tier": "tier-1"}
    doc["spec"]["lifecycle"] = "experimental"
    doc["metadata"]["annotations"] = {}
    write_json(repo, "catalog-info.json", doc)
    code, report = run_script(tmp_path)
    assert code == 0 and report["status"] == "ok"
    assert {f["severity"] for f in report["findings"]} <= {"minor", "info"} and report["findings"]


def test_json_array_and_each_entity_gets_a_doc_location(tmp_path, repo):
    write_json(repo, "catalog-info.json", [descriptor("a-one"), descriptor("b-two", spec__owner=None)])
    code, report = run_script(tmp_path)
    assert code == 1 and report["entity_count"] == 2
    assert [(f["id"], f["location"]) for f in report["findings"]] == [("owner-missing@component:default/b-two", "catalog-info.json#doc2")]


@needs_yaml
def test_multi_document_yaml_with_locations(tmp_path, repo):
    write_yaml(repo, "catalog-info.yaml", descriptor("a-one"), descriptor("b-two", spec__lifecycle=None))
    code, report = run_script(tmp_path)
    assert code == 1
    assert [(f["id"], f["location"]) for f in report["findings"]] == [("lifecycle-missing@component:default/b-two", "catalog-info.yaml#doc2")]


@needs_yaml
@pytest.mark.parametrize("name", ["catalog-info.yaml", "catalog-info.yml", "catalog-info.json", "catalog-info.payments.yaml"])
def test_descriptor_file_names(tmp_path, repo, name):
    path = repo / name
    if name.endswith(".json"):
        path.write_text(json.dumps(descriptor(spec__owner=None)), encoding="utf-8")
    else:
        write_yaml(repo, name, descriptor(spec__owner=None))
    code, report = run_script(tmp_path)
    assert code == 1 and report["scanned_files"] == 1 and by_rule(report, "owner-missing")


@pytest.mark.parametrize("name", ["catalog-info.txt", "my-catalog-info.yaml", "catalog-info.yaml.bak", "catalog.yaml", "catalog-info.a.b.yaml"])
def test_other_file_names_are_not_descriptors(tmp_path, repo, name):
    (repo / name).write_text(json.dumps(descriptor(spec__owner=None)), encoding="utf-8")
    code, report = run_script(tmp_path)
    assert code == 0 and report["scanned_files"] == 0 and ids(report) == ["no-descriptors"]
    assert report["findings"][0]["severity"] == "info"


def test_empty_repository_reports_no_descriptors_as_info(tmp_path, repo):
    code, report = run_script(tmp_path)
    assert code == 0 and report["status"] == "ok" and ids(report) == ["no-descriptors"]


def test_tier_from_label_or_annotation_and_tiers_option(tmp_path, repo):
    via_annotation = descriptor("annotated")
    via_annotation["metadata"]["labels"] = {}
    via_annotation["metadata"]["annotations"]["example.com/tier"] = "tier-2"
    write_json(repo, "a/catalog-info.json", via_annotation)
    write_json(repo, "b/catalog-info.json", descriptor("labelled"))
    code, report = run_script(tmp_path)
    assert code == 0 and report["findings"] == []
    code, report = run_script(tmp_path, "--tiers", "gold,silver")
    assert code == 0 and sorted(f["id"] for f in by_rule(report, "tier-unknown")) == ["tier-unknown@component:default/annotated", "tier-unknown@component:default/labelled"]
    code, report = run_script(tmp_path, "--tiers", "tier-1,tier-2")
    assert report["findings"] == []


def test_blank_tiers_option_is_an_input_error(tmp_path, repo):
    code, report = run_script(tmp_path, "--tiers", " , ")
    assert code == 2 and report["status"] == "error" and report["error"]["code"] == "E-INPUT"


def test_malformed_documents_are_findings_never_crashes(tmp_path, repo):
    (repo / "a").mkdir()
    (repo / "a" / "catalog-info.json").write_text("{not json", encoding="utf-8")
    write_json(repo, "b/catalog-info.json", ["just a string", {"kind": "Component"}, {"kind": "Component", "metadata": "x"}, None])
    write_json(repo, "c/catalog-info.json", None)
    write_json(repo, "d/catalog-info.json", descriptor("no-api", apiVersion=None))
    write_json(repo, "e/catalog-info.json", descriptor("odd-api", apiVersion="example.com/v1"))
    code, report = run_script(tmp_path)
    assert code == 1
    assert [(f["severity"], f["location"]) for f in by_rule(report, "parse-error")] == [("blocker", "a/catalog-info.json")]
    assert [f["location"] for f in by_rule(report, "descriptor-invalid")] == ["b/catalog-info.json#doc1", "b/catalog-info.json#doc2", "b/catalog-info.json#doc3"]
    assert [(f["id"], f["severity"]) for f in by_rule(report, "descriptor-empty")] == [("descriptor-empty@c/catalog-info.json", "major")]
    assert [(f["id"], f["severity"]) for f in by_rule(report, "apiversion-missing")] == [("apiversion-missing@component:no-api", "major")]
    assert [(f["id"], f["severity"]) for f in by_rule(report, "apiversion-unknown")] == [("apiversion-unknown@component:odd-api", "minor")]


@needs_yaml
def test_yaml_syntax_error_is_a_blocker_finding(tmp_path, repo):
    (repo / "catalog-info.yaml").write_text("kind: [unclosed\n  metadata: {", encoding="utf-8")
    code, report = run_script(tmp_path)
    assert code == 1 and [f["severity"] for f in by_rule(report, "parse-error")] == ["blocker"]


def test_scaffolder_and_backstage_api_versions_are_accepted(tmp_path, repo):
    write_json(repo, "a/catalog-info.json", descriptor("tpl", kind="Template", apiVersion="scaffolder.backstage.io/v1beta3"))
    write_json(repo, "b/catalog-info.json", descriptor("beta", apiVersion="backstage.io/v1beta1"))
    _, report = run_script(tmp_path)
    assert not by_rule(report, "apiversion-unknown") and not by_rule(report, "apiversion-missing")


# --- cross-file dependsOn graph ---------------------------------------------------------------------------------


def test_cross_file_cycle_dangling_and_lifecycle_findings_point_at_the_right_files(tmp_path, repo):
    a = descriptor("svc-a")
    a["spec"]["dependsOn"] = ["component:default/svc-b", "component:ghost", "component:default/old-lib"]
    b = descriptor("svc-b")
    b["spec"]["dependsOn"] = ["component:default/svc-a"]
    old = descriptor("old-lib")
    old["spec"]["lifecycle"] = "deprecated"
    write_json(repo, "a/catalog-info.json", a)
    write_json(repo, "b/catalog-info.json", b)
    write_json(repo, "lib/catalog-info.json", old)
    code, report = run_script(tmp_path)
    assert code == 1
    cycle = by_rule(report, "dependency-cycle")
    assert [(f["severity"], f["location"]) for f in cycle] == [("blocker", "a/catalog-info.json")]
    assert [(f["severity"], f["location"]) for f in by_rule(report, "dangling-dependency")] == [("minor", "a/catalog-info.json")]
    assert [(f["id"], f["severity"], f["location"]) for f in by_rule(report, "deprecated-dependency")] == [
        ("deprecated-dependency@component:default/svc-a", "major", "a/catalog-info.json")
    ]


def test_dependson_rules_from_the_lint_apply_to_flattened_descriptors(tmp_path, repo):
    doc = descriptor()
    doc["spec"]["dependsOn"] = ["component:default/payments-api", "bad ref!", "resource:default/db", "resource:default/db"]
    write_json(repo, "catalog-info.json", doc)
    _, report = run_script(tmp_path)
    assert {(f["id"].split("@")[0], f["severity"]) for f in report["findings"]} >= {("dependson-self", "major"), ("dependson-invalid", "major"), ("dependson-duplicate", "minor")}


def test_graph_check_is_skipped_beyond_300_entities(tmp_path, repo):
    write_json(repo, "catalog-info.json", [descriptor(f"svc-{i}") for i in range(301)])
    code, report = run_script(tmp_path)
    assert code == 0 and report["entity_count"] == 301
    assert [(f["id"], f["severity"]) for f in by_rule(report, "graph-skipped")] == [("graph-skipped", "info")]


# --- --path ------------------------------------------------------------------------------------------------------


def test_path_limits_the_scan_to_a_directory_or_a_single_file(tmp_path, repo):
    write_json(repo, "a/catalog-info.json", descriptor("a-svc", spec__owner=None))
    write_json(repo, "b/catalog-info.json", descriptor("b-svc"))
    code, report = run_script(tmp_path, "--path", "b")
    assert code == 0 and report["scanned_files"] == 1 and report["findings"] == []
    code, report = run_script(tmp_path, "--path", "a/catalog-info.json")
    assert code == 1 and report["scanned_files"] == 1
    write_json(repo, "a/anything.json", descriptor("odd", spec__owner=None))
    code, report = run_script(tmp_path, "--path", "a/anything.json")  # an explicit file is linted whatever its name
    assert code == 1 and by_rule(report, "owner-missing")[0]["location"] == "a/anything.json"


def test_path_outside_the_root_or_missing_is_an_input_error(tmp_path, repo):
    code, report = run_script(tmp_path, "--path", "..")
    assert code == 2 and report["error"]["code"] == "E-INPUT"
    code, report = run_script(tmp_path, "--path", "nope")
    assert code == 2 and report["error"]["code"] == "E-INPUT"


# --- bounds ------------------------------------------------------------------------------------------------------


def test_oversized_binary_and_ignored_directories_are_skipped(tmp_path, repo):
    big = repo / "big" / "catalog-info.json"
    big.parent.mkdir()
    big.write_text(json.dumps(descriptor("big-one")) + " " * (1024 * 1024), encoding="utf-8")
    edge = repo / "edge" / "catalog-info.json"
    edge.parent.mkdir()
    payload = json.dumps(descriptor("edge-one"))
    edge.write_text(payload + " " * (1024 * 1024 - len(payload)), encoding="utf-8")  # exactly 1 MiB is still read
    binary = repo / "bin" / "catalog-info.json"
    binary.parent.mkdir()
    binary.write_bytes(b"\x00\x01\x02" + b"{}" * 10)
    bad_utf8 = repo / "latin" / "catalog-info.json"
    bad_utf8.parent.mkdir()
    bad_utf8.write_bytes(b'{"kind": "\xff\xfe"}')
    write_json(repo, "node_modules/pkg/catalog-info.json", descriptor("ignored", spec__owner=None))
    write_json(repo, ".git/catalog-info.json", descriptor("ignored-too", spec__owner=None))
    code, report = run_script(tmp_path)
    assert code == 0
    assert report["skipped"] == ["skipped:binary:bin/catalog-info.json", "skipped:binary:latin/catalog-info.json", "skipped:too-large:big/catalog-info.json"]
    assert (report["scanned_files"], report["entity_count"]) == (1, 1) and not by_rule(report, "owner-missing")


def test_symlinks_leaving_the_root_are_not_followed(tmp_path, repo):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_json(outside, "catalog-info.json", descriptor("outside", spec__owner=None))
    (repo / "linked-dir").symlink_to(outside, target_is_directory=True)
    (repo / "catalog-info.json").symlink_to(outside / "catalog-info.json")
    write_json(repo, "real/catalog-info.json", descriptor("inside"))
    (repo / "inner-link.json").symlink_to(repo / "real" / "catalog-info.json")
    code, report = run_script(tmp_path)
    assert code == 0 and report["skipped"] == ["skipped:symlink-outside-root:catalog-info.json"]
    assert (report["scanned_files"], report["entity_count"]) == (1, 1)
    code, report = run_script(tmp_path, "--path", "linked-dir")
    assert code == 2 and report["error"]["code"] == "E-INPUT"


def test_scan_stops_at_2000_descriptors(tmp_path, repo):
    for i in range(2001):
        write_json(repo, f"d{i:04d}/catalog-info.json", {"kind": "Group", "metadata": {"name": f"g{i}"}, "spec": {"type": "team"}, "apiVersion": "backstage.io/v1alpha1"})
    code, report = run_script(tmp_path)
    assert (report["scanned_files"], report["truncated"]) == (2000, True)
    assert [(f["id"], f["severity"]) for f in by_rule(report, "scan-truncated")] == [("scan-truncated", "minor")]
    assert report["entity_count"] == 2000 and code == 0


def test_findings_list_is_cut_but_finding_count_is_the_total(tmp_path, repo):
    write_json(repo, "catalog-info.json", [{"apiVersion": "backstage.io/v1alpha1", "kind": "Component", "metadata": {"name": f"svc-{i}"}} for i in range(200)])
    code, report = run_script(tmp_path)
    assert code == 1 and report["finding_count"] > len(report["findings"]) == 300
    assert report["findings"][0]["severity"] == "blocker"


# --- YAML dependency absent -----------------------------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("plat_catalog_lint_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_yaml_descriptors_are_skipped_as_tool_missing_when_pyyaml_is_absent(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` raises ImportError
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / "swarm-state"))
    (repo / "a").mkdir()
    (repo / "a" / "catalog-info.yaml").write_text("apiVersion: backstage.io/v1alpha1\nkind: Component\nmetadata:\n  name: x\n", encoding="utf-8")
    write_json(repo, "b/catalog-info.json", descriptor("native-json", spec__owner=None))
    (repo / "c").mkdir()
    (repo / "c" / "catalog-info.yml").write_text(json.dumps(descriptor("json-in-yaml")), encoding="utf-8")  # JSON is valid YAML: handled natively
    module = load_script()
    exit_code = module.AgentScript("A17", "plat_catalog_lint", module.run, add_args=module.add_args).main(["--root", str(repo), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["skipped"] == ["skipped:tool-missing:a/catalog-info.yaml"]
    assert [(f["id"], f["severity"], f["location"]) for f in by_rule(report, "yaml-skipped")] == [("yaml-skipped@a/catalog-info.yaml", "info", "a/catalog-info.yaml")]
    assert "skipped:tool-missing" in by_rule(report, "yaml-skipped")[0]["summary"]
    assert (report["scanned_files"], report["entity_count"]) == (2, 2)
    assert {f["location"] for f in by_rule(report, "owner-missing")} == {"b/catalog-info.json"}


def test_pyyaml_absent_without_yaml_files_changes_nothing(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / "swarm-state"))
    write_json(repo, "catalog-info.json", descriptor())
    module = load_script()
    assert module.AgentScript("A17", "plat_catalog_lint", module.run, add_args=module.add_args).main(["--root", str(repo), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skipped"] == [] and report["findings"] == []


# --- independent review: parser limits, parity with the tool, namespace resolution, secret-safety ----------------------


def test_deeply_nested_documents_are_parse_errors_never_crashes(tmp_path, repo):
    depth = 200_000
    (repo / "a").mkdir()
    (repo / "a" / "catalog-info.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    code, report = run_script(tmp_path)
    assert code == 1 and [(f["severity"], f["location"]) for f in by_rule(report, "parse-error")] == [("blocker", "a/catalog-info.json")]


@needs_yaml
def test_deeply_nested_yaml_is_a_parse_error_never_a_crash(tmp_path, repo):
    depth = 100_000
    (repo / "catalog-info.yaml").write_text("a: " + "[" * depth + "]" * depth + "\n", encoding="utf-8")
    code, report = run_script(tmp_path)
    assert code == 1 and [f["severity"] for f in by_rule(report, "parse-error")] == ["blocker"]


def test_a_self_dependency_is_reported_by_the_graph_check_as_the_tool_does(tmp_path, repo):
    doc = descriptor()
    doc["spec"]["dependsOn"] = ["payments-api"]
    write_json(repo, "catalog-info.json", doc)
    code, report = run_script(tmp_path)
    assert code == 1
    assert [(f["id"], f["severity"]) for f in by_rule(report, "self-dependency")] == [("self-dependency@component:default/payments-api", "blocker")]
    assert [f["severity"] for f in by_rule(report, "dependson-self")] == ["major"]


def test_relative_depends_on_resolves_in_the_namespace_of_the_descriptor(tmp_path, repo):
    a = descriptor("svc-a", metadata__namespace="payments")
    a["spec"]["dependsOn"] = ["svc-b", "svc-c"]
    b = descriptor("svc-b", metadata__namespace="payments")
    c = descriptor("svc-c")  # default namespace: `svc-c` from a `payments` entity is payments/svc-c, which does not exist
    for name, doc in (("a", a), ("b", b), ("c", c)):
        write_json(repo, f"{name}/catalog-info.json", doc)
    code, report = run_script(tmp_path)
    dangling = by_rule(report, "dangling-dependency")
    assert [(f["id"], f["location"]) for f in dangling] == [("dangling-dependency@component:payments/svc-a", "a/catalog-info.json")]
    assert "component:payments/svc-c" in dangling[0]["summary"]
    assert code == 0  # a dangling target is minor


def test_name_and_namespace_follow_the_upstream_grammar(tmp_path, repo):
    write_json(repo, "a/catalog-info.json", descriptor("my--svc"))
    write_json(repo, "b/catalog-info.json", descriptor("other", metadata__namespace="Team-A"))
    write_json(repo, "c/catalog-info.json", descriptor(" padded"))
    _, report = run_script(tmp_path)
    assert [(f["id"], f["location"]) for f in by_rule(report, "name-invalid")] == [("name-invalid@entry#2", "c/catalog-info.json")]
    assert [(f["id"], f["location"]) for f in by_rule(report, "namespace-invalid")] == [("namespace-invalid@component:team-a/other", "b/catalog-info.json")]


def test_skipped_list_is_bounded_and_the_summary_counts_skips(tmp_path, repo):
    for i in range(150):
        path = repo / f"d{i:03d}" / "catalog-info.json"
        path.parent.mkdir()
        path.write_bytes(b"\x00\x01")
    code, report = run_script(tmp_path)
    assert code == 0 and len(report["skipped"]) == 101 and report["skipped"][-1] == "skipped:more:50"
    assert "150 skipped" in report["summary"]


SECRET_CANARIES = {
    "aws-key-id": "AKIAIOSFODNN7EXAMPLE",
    "github-token": "ghp_" + "a1B2c3D4" * 4 + "e5F6",
    "private-key-hex": "0123456789abcdef" * 4,
    "mnemonic": "abandon ability able about above absent absorb abstract absurd abuse access accident",
    "url-credentials": "https://deploy:hunter2-s3cret@db.internal.example/prod",
}


@pytest.mark.parametrize("label", list(SECRET_CANARIES))
def test_secret_safety_no_descriptor_value_is_echoed(tmp_path, repo, label):
    secret = SECRET_CANARIES[label]
    doc = descriptor(apiVersion=secret, metadata__description=secret, spec__type=secret, spec__lifecycle=secret, spec__owner=secret, spec__system=secret)
    doc["metadata"]["annotations"] = {"backstage.io/techdocs-ref": secret, "example.com/token": secret, "example.com/runbook": secret}
    doc["metadata"]["links"] = [{"url": secret, "title": secret}]
    doc["metadata"]["labels"] = {"tier": secret}
    doc["spec"]["dependsOn"] = [secret, f"component:{secret}"]
    write_json(repo, "a/catalog-info.json", doc)
    bad_kind = descriptor("bad", kind=secret)
    write_json(repo, "b/catalog-info.json", bad_kind)
    bad_name = descriptor(secret if label in ("mnemonic", "url-credentials") else "x y", apiVersion="backstage.io/v1alpha1")
    write_json(repo, "c/catalog-info.json", bad_name)
    (repo / "d").mkdir()
    (repo / "d" / "catalog-info.json").write_text('{"name": "' + secret + '" ', encoding="utf-8")
    needle = {"mnemonic": "abandon ability", "url-credentials": "hunter2", "aws-key-id": "AKIAIOSF", "github-token": "ghp_a1B2", "private-key-hex": "01234567"}[label]
    _, report = run_script(tmp_path)
    text = json.dumps(report)
    assert secret not in text and needle not in text, text[:600]


@needs_yaml
@pytest.mark.parametrize("label", list(SECRET_CANARIES))
def test_secret_safety_yaml_parse_errors_do_not_echo_file_content(tmp_path, repo, label):
    secret = SECRET_CANARIES[label]
    (repo / "catalog-info.yaml").write_text(f"kind: Component\nmetadata:\n  name: {secret}\n  annotations: [unclosed {secret}\n\t{secret}: !!python/object:os.system {secret}\n", encoding="utf-8")
    _, report = run_script(tmp_path)
    assert by_rule(report, "parse-error")
    assert secret not in json.dumps(report) and secret[:8] not in json.dumps(report)
