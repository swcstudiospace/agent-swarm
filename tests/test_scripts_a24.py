"""Behaviour tests for scripts/kpol_manifest_scan.py (A24): real-mode scans of fixture trees and the dry-run contract."""
from __future__ import annotations

import importlib
import json
import os

import pytest
from conftest import run_script

SCRIPT = "kpol_manifest_scan.py"


def scan(root, *extra):
    result = run_script(SCRIPT, "--root", str(root), "--json", *extra)
    return result, (json.loads(result.stdout) if result.stdout.strip() else None)


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def pod(name="web", *, privileged=False, sa="builder", automount=False, restricted=False):
    container = {"name": "app", "image": "ghcr.io/acme/app@sha256:" + "a" * 64}
    if privileged:
        container["securityContext"] = {"privileged": True}
    if restricted:
        container["securityContext"] = {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"},
                                        "capabilities": {"drop": ["ALL"]}}
    spec = {"containers": [container], "serviceAccountName": sa, "automountServiceAccountToken": automount}
    return {"kind": "Pod", "apiVersion": "v1", "metadata": {"name": name, "namespace": "prod"}, "spec": spec}


def ids(payload):
    return sorted((f["id"], f["severity"], f["location"]) for f in payload["findings"])


# --- dry run --------------------------------------------------------------------------------------------------


def test_dry_run_is_canned_json_that_reads_nothing(tmp_path):
    result = run_script(SCRIPT, "--dry-run", "--json")
    assert result.returncode == 0 and result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok" and payload["dry_run"] is True and payload["findings"] == []
    assert (payload["agent"], payload["script"]) == ("A24", "kpol_manifest_scan")
    assert payload["tools"] == {"yaml": "not-checked"}
    missing = run_script(SCRIPT, "--dry-run", "--json", "--root", str(tmp_path / "does-not-exist"), "--path", "nope")
    assert missing.returncode == 0 and json.loads(missing.stdout) == payload


def test_dry_run_is_deterministic():
    first = run_script(SCRIPT, "--dry-run", "--json")
    assert run_script(SCRIPT, "--dry-run", "--json").stdout == first.stdout


# --- real scans -----------------------------------------------------------------------------------------------


def test_clean_tree_exits_zero_with_no_findings(tmp_path):
    write_json(tmp_path / "k8s" / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 0
    assert payload["status"] == "ok" and payload["findings"] == [] and payload["files_scanned"] == 1 and payload["manifest_count"] == 1
    assert payload["summary"] == "1 file(s) scanned, 0 finding(s), 0 major or worse"
    assert set(payload) == {"status", "level", "files_scanned", "files_skipped", "manifest_count", "tools", "skipped", "limits_hit", "findings", "summary",
                            "agent", "script"}
    assert payload["limits_hit"] == [] and payload["skipped"] == [] and payload["level"] == "baseline"


def test_pss_rbac_and_service_account_findings_with_file_locations(tmp_path):
    write_json(tmp_path / "apps" / "web.json", {"kind": "Deployment", "metadata": {"name": "web", "namespace": "prod"},
                                                "spec": {"template": {"spec": {"hostNetwork": True, "containers": [
                                                    {"name": "app", "image": "x", "securityContext": {"privileged": True}}]}}}})
    write_json(tmp_path / "rbac" / "roles.json", {"kind": "List", "items": [
        {"kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]},
        {"kind": "ClusterRoleBinding", "metadata": {"name": "root"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"},
         "subjects": [{"kind": "User", "name": "alice"}]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ids(payload) == sorted([
        ("pss.host-namespaces:Deployment/prod/web", "blocker", "apps/web.json"), ("pss.privileged:Deployment/prod/web", "blocker", "apps/web.json"),
        ("sa.automount-enabled:Deployment/prod/web", "major", "apps/web.json"), ("sa.default-account:Deployment/prod/web", "minor", "apps/web.json"),
        ("rbac.admin-equivalent:ClusterRole/wide", "blocker", "rbac/roles.json"),
        ("rbac.cluster-admin-binding:ClusterRoleBinding/root", "blocker", "rbac/roles.json")])
    assert payload["summary"] == "2 file(s) scanned, 6 finding(s), 5 major or worse"
    assert [f["severity"] for f in payload["findings"]] == ["blocker"] * 4 + ["major", "minor"]
    assert payload["manifest_count"] == 3


def test_minor_and_info_findings_do_not_fail_the_scan(tmp_path):
    write_json(tmp_path / "pod.json", pod(automount=True))
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert [(f["id"], f["severity"]) for f in payload["findings"]] == [("sa.automount-enabled:Pod/prod/web", "minor")]


def test_level_option_selects_baseline_or_restricted(tmp_path):
    write_json(tmp_path / "pod.json", pod())
    baseline, base_payload = scan(tmp_path)
    assert baseline.returncode == 0 and base_payload["findings"] == []
    restricted, payload = scan(tmp_path, "--level", "restricted")
    assert restricted.returncode == 1 and payload["level"] == "restricted"
    assert sorted(f["id"].split(":")[0] for f in payload["findings"]) == ["pss.capabilities-drop", "pss.privilege-escalation", "pss.run-as-non-root", "pss.seccomp"]
    write_json(tmp_path / "good.json", pod("good", restricted=True))
    only_good = run_script(SCRIPT, "--root", str(tmp_path), "--path", "good.json", "--level", "restricted", "--json")
    assert only_good.returncode == 0 and json.loads(only_good.stdout)["findings"] == []
    bad_level = run_script(SCRIPT, "--root", str(tmp_path), "--level", "privileged", "--json")
    assert bad_level.returncode == 2


def test_rbac_findings_resolve_across_files_and_map_back_to_the_defining_file(tmp_path):
    write_json(tmp_path / "roles.json", {"kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"]}]})
    write_json(tmp_path / "binds" / "b.json", {"kind": "ClusterRoleBinding", "metadata": {"name": "b"}, "roleRef": {"kind": "ClusterRole", "name": "wide"},
                                               "subjects": [{"kind": "ServiceAccount", "name": "ci", "namespace": "build"}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1
    assert ids(payload) == [("rbac.binding-to-risky-role:ClusterRoleBinding/b", "major", "binds/b.json"), ("rbac.secret-read:ClusterRole/wide", "major", "roles.json")]


def test_legacy_service_account_token_secret_is_reported_with_its_file(tmp_path):
    write_json(tmp_path / "secrets" / "t.json", {"kind": "Secret", "type": "kubernetes.io/service-account-token", "metadata": {"name": "tok", "namespace": "prod"}})
    write_json(tmp_path / "secrets" / "opaque.json", {"kind": "Secret", "type": "Opaque", "metadata": {"name": "x", "namespace": "prod"}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1
    assert ids(payload) == [("sa.legacy-token-secret:Secret/prod/tok", "major", "secrets/t.json")]


def test_service_account_manifests_in_the_tree_change_the_automount_decision(tmp_path):
    write_json(tmp_path / "sa.json", {"kind": "ServiceAccount", "metadata": {"name": "builder", "namespace": "prod"}, "automountServiceAccountToken": False})
    workload = pod(automount=True)
    del workload["spec"]["automountServiceAccountToken"]
    write_json(tmp_path / "pod.json", workload)
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["findings"] == []


def test_rejected_manifests_are_listed_as_skipped_not_fatal(tmp_path):
    write_json(tmp_path / "broken.json", {"kind": "Deployment", "metadata": {"name": "d"}, "spec": {}})
    write_json(tmp_path / "other.json", {"kind": "ConfigMap", "metadata": {"name": "c"}})
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"][0].startswith("broken.json pss: INVALID_INPUT workloads")
    assert payload["files_scanned"] == 2 and payload["files_skipped"] == len(payload["skipped"])


# --- bounds ---------------------------------------------------------------------------------------------------


def test_oversized_binary_non_utf8_and_unparseable_files_are_skipped_with_reasons(tmp_path):
    (tmp_path / "big.json").write_text(json.dumps({"kind": "Pod", "pad": "x" * (1024 * 1024 + 1)}))
    exactly = tmp_path / "exact.json"
    prefix, suffix = '{"kind": "ConfigMap", "metadata": {"name": "c"}, "pad": "', '"}'
    exactly.write_text(prefix + "x" * (1024 * 1024 - len(prefix) - len(suffix)) + suffix)
    assert exactly.stat().st_size == 1024 * 1024
    (tmp_path / "bin.json").write_bytes(b'{"kind": "Pod"}\x00\x01')
    (tmp_path / "latin.json").write_bytes('{"kind": "Pod", "x": "caf\xe9"}'.encode("latin-1"))
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "deep.json").write_text("[" * 100000 + "]" * 100000)
    result, payload = scan(tmp_path)
    assert result.returncode == 0
    assert payload["skipped"] == ["bad.json: parse-error", "big.json: too-large", "bin.json: binary", "deep.json: parse-error", "latin.json: not-utf8"]
    assert payload["files_scanned"] == 1 and payload["files_skipped"] == 5


def test_symlinks_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_json(outside / "evil.json", pod(privileged=True))
    root = tmp_path / "root"
    root.mkdir()
    write_json(root / "ok.json", pod())
    os.symlink(outside / "evil.json", root / "link.json")
    os.symlink(outside, root / "linkdir")
    result, payload = scan(root)
    assert result.returncode == 0 and payload["findings"] == []
    assert payload["skipped"] == ["link.json: symlink"] and payload["files_scanned"] == 1
    escaped = run_script(SCRIPT, "--root", str(root), "--path", "link.json", "--json")
    assert escaped.returncode == 2 and json.loads(escaped.stdout)["status"] == "error"


def test_tool_and_vcs_directories_are_not_walked(tmp_path):
    for skipped_dir in (".git", "node_modules", ".swarm", "vendor", "dist"):
        write_json(tmp_path / skipped_dir / "p.json", pod(privileged=True))
    write_json(tmp_path / "src" / "p.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["files_scanned"] == 1


def test_file_count_is_capped_at_2000(tmp_path):
    for index in range(2003):
        (tmp_path / f"f{index:04d}.json").write_text('{"kind": "ConfigMap", "metadata": {"name": "c"}}')
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["files_scanned"] == 2000 and payload["limits_hit"] == ["max-files:2000"]
    (tmp_path / "f2002.json").unlink()
    (tmp_path / "f2001.json").unlink()
    (tmp_path / "f2000.json").unlink()
    _, exact = scan(tmp_path)
    assert exact["files_scanned"] == 2000 and exact["limits_hit"] == []


def test_non_manifest_extensions_are_ignored_and_a_single_file_path_works(tmp_path):
    (tmp_path / "notes.txt").write_text(json.dumps(pod(privileged=True)))
    write_json(tmp_path / "a.json", pod(privileged=True))
    write_json(tmp_path / "b.json", pod("b"))
    both = scan(tmp_path)[1]
    assert both["files_scanned"] == 2
    single = scan(tmp_path, "--path", "b.json")[1]
    assert single["files_scanned"] == 1 and single["findings"] == []
    assert scan(tmp_path, "--path", "notes.txt")[1]["files_scanned"] == 0


def test_path_errors_exit_two_with_a_taxonomy_error(tmp_path):
    for bad in ("../elsewhere", "missing.json"):
        result = run_script(SCRIPT, "--root", str(tmp_path), "--path", bad, "--json")
        payload = json.loads(result.stdout)
        assert result.returncode == 2 and payload["status"] == "error" and payload["error"]["code"] == "E-INPUT"


def test_output_is_deterministic_and_ordered_worst_first(tmp_path):
    write_json(tmp_path / "z.json", pod("z", privileged=True))
    write_json(tmp_path / "a.json", pod("a", privileged=True, sa="default", automount=True))
    first = run_script(SCRIPT, "--root", str(tmp_path), "--json")
    assert run_script(SCRIPT, "--root", str(tmp_path), "--json").stdout == first.stdout
    severities = [f["severity"] for f in json.loads(first.stdout)["findings"]]
    assert severities == sorted(severities, key=lambda s: -{"info": 0, "minor": 1, "major": 2, "blocker": 3}[s])


# --- YAML -----------------------------------------------------------------------------------------------------

YAML_DOC = """\
apiVersion: v1
kind: Pod
metadata: {name: web, namespace: prod}
spec:
  serviceAccountName: builder
  automountServiceAccountToken: false
  containers:
  - name: app
    image: x
    securityContext: {privileged: true}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: c}
"""


def scan_in_process(root):
    module = importlib.import_module("swarm.tools.support").script("kpol_manifest_scan")
    return module.scan(root, root, "baseline")


def test_yaml_is_scanned_when_pyyaml_imports_and_reported_as_tool_missing_otherwise(tmp_path, yaml_mode):
    (tmp_path / "pod.yaml").write_text(YAML_DOC)
    write_json(tmp_path / "pod.json", pod("jsonpod", privileged=True))
    payload = scan_in_process(tmp_path)
    found = {(f["id"], f["location"]) for f in payload["findings"]}
    assert ("pss.privileged:Pod/prod/jsonpod", "pod.json") in found
    if yaml_mode == "pyyaml":
        assert ("pss.privileged:Pod/prod/web", "pod.yaml") in found
        assert payload["tools"] == {"yaml": "available"} and payload["skipped"] == [] and payload["files_scanned"] == 2
        assert not any(f["id"] == "skipped:tool-missing" for f in payload["findings"])
    else:
        assert all(loc != "pod.yaml" for _, loc in found)
        assert payload["tools"] == {"yaml": "skipped:tool-missing"} and payload["skipped"] == ["pod.yaml: tool-missing"]
        marker = next(f for f in payload["findings"] if f["id"] == "skipped:tool-missing")
        assert marker["severity"] == "info" and marker["summary"].startswith("PyYAML is not importable: 1 YAML file(s) were not scanned")
        assert payload["status"] == "fail" and payload["files_scanned"] == 1


def test_yaml_only_tree_without_pyyaml_stays_ok(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    (tmp_path / "a.yml").write_text(YAML_DOC)
    payload = scan_in_process(tmp_path)
    assert payload["status"] == "ok" and payload["files_scanned"] == 0
    assert [f["id"] for f in payload["findings"]] == ["skipped:tool-missing"]


def test_invalid_yaml_is_skipped_as_a_parse_error(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "helm.yaml").write_text("image: {{ .Values.image }}\n  bad: [")
    (tmp_path / "empty.yaml").write_text("")
    (tmp_path / "scalar.yaml").write_text("just text")
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["skipped"] == ["helm.yaml: parse-error"] and payload["files_scanned"] == 2


# --- independent review: regressions ----------------------------------------------------------------------------------


def _bomb(levels=8, fan=9):
    """A small YAML file whose aliases expand to fan**levels nodes (billion laughs)."""
    lines = ["kind: Pod", "metadata: {name: p}", "spec: {containers: [{name: c, image: x}]}", "lol:", "  a: &a [x, x, x, x, x, x, x, x, x]"]
    previous = "a"
    for name in "bcdefghi"[: levels - 1]:
        lines.append(f"  {name}: &{name} [{', '.join('*' + previous for _ in range(fan))}]")
        previous = name
    return "\n".join(lines) + "\n"


def test_yaml_alias_bomb_and_recursive_anchor_are_refused_before_anything_is_built(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "bomb.yaml").write_text(_bomb())
    (tmp_path / "loop.yaml").write_text("a: &a [*a]\n")
    write_json(tmp_path / "ok.json", pod("ok"))
    assert (tmp_path / "bomb.yaml").stat().st_size < 1024
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["skipped"] == ["bomb.yaml: parse-error", "loop.yaml: parse-error"]
    assert payload["files_scanned"] == 1 and payload["files_skipped"] == len(payload["skipped"]) == 2


def test_yaml_with_a_modest_number_of_aliases_still_parses(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "shared.yaml").write_text("kind: Pod\nmetadata: {name: web, namespace: prod}\nspec:\n  containers:\n"
                                          "  - &c {name: app, image: x, securityContext: {privileged: true}}\n  - {<<: *c, name: second}\n")
    _, payload = scan(tmp_path)
    # the merge key `<<: *c` copies the privileged container, so both containers are reported
    assert sorted(f["summary"] for f in payload["findings"] if f["id"].startswith("pss.privileged")) == [
        "containers/app runs privileged (violates baseline)", "containers/second runs privileged (violates baseline)"]
    assert payload["files_scanned"] == 1 and payload["skipped"] == []


def test_yaml_non_string_keys_and_dates_do_not_stop_the_scan(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "odd.yaml").write_text("kind: Pod\nmetadata:\n  name: odd\n  annotations:\n    1: one\n    2026-01-01: day\nspec:\n  containers: [{name: c, image: x}]\n")
    write_json(tmp_path / "bad.json", pod("bad", privileged=True))
    _, payload = scan(tmp_path)
    assert payload["skipped"] == [] and payload["files_scanned"] == 2
    assert ("pss.privileged:Pod/prod/bad", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_handler_error_on_one_manifest_is_recorded_not_fatal(tmp_path, monkeypatch):
    module = importlib.import_module("swarm.tools.support").script("kpol_manifest_scan")

    def boom(_payload):
        raise TypeError("secret detail that must not be echoed")

    monkeypatch.setattr(module, "sa_token_audit", boom)
    write_json(tmp_path / "pod.json", pod("p", privileged=True))
    payload = module.scan(tmp_path, tmp_path, "baseline")
    assert payload["skipped"] == ["pod.json sa: handler-error TypeError"] and payload["status"] == "fail"
    assert "secret detail" not in json.dumps(payload)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_named_like_a_manifest_is_skipped_not_read(tmp_path):
    os.mkfifo(tmp_path / "pipe.yaml")
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["skipped"] == ["pipe.yaml: not-a-regular-file"] and payload["files_scanned"] == 1


def test_an_object_defined_in_two_files_lists_both_files(tmp_path):
    wide = {"kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}
    write_json(tmp_path / "a" / "role.json", wide)
    write_json(tmp_path / "b" / "role.json", wide)
    _, payload = scan(tmp_path)
    assert [(f["id"], f["location"]) for f in payload["findings"]] == [("rbac.admin-equivalent:ClusterRole/wide", "a/role.json, b/role.json")]


def test_findings_never_echo_secret_values_found_in_manifests(tmp_path):
    canary = "ghp_" + "Z" * 36
    document = pod("leaky")
    document["spec"]["containers"][0]["env"] = [{"name": "TOKEN", "value": canary}]
    document["spec"]["containers"][0]["command"] = ["run", canary]
    document["metadata"]["annotations"] = {"container.apparmor.security.beta.kubernetes.io/app": canary}
    write_json(tmp_path / "pod.json", document)
    write_json(tmp_path / "secret.json", {"kind": "Secret", "type": "kubernetes.io/service-account-token", "metadata": {"name": "t", "namespace": "prod"},
                                          "data": {"token": canary}, "stringData": {"token": canary}})
    result, payload = scan(tmp_path)
    assert canary not in result.stdout and canary not in result.stderr and payload["findings"]
