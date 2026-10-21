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
    write_json(tmp_path / "apps" / "web.json", {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "prod"},
                                                "spec": {"template": {"spec": {"hostNetwork": True, "containers": [
                                                    {"name": "app", "image": "x", "securityContext": {"privileged": True}}]}}}})
    write_json(tmp_path / "rbac" / "roles.json", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "List", "items": [
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "root"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"},
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
    write_json(tmp_path / "roles.json", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"]}]})
    write_json(tmp_path / "binds" / "b.json", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "b"}, "roleRef": {"kind": "ClusterRole", "name": "wide"},
                                               "subjects": [{"kind": "ServiceAccount", "name": "ci", "namespace": "build"}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1
    assert ids(payload) == [("rbac.binding-to-risky-role:ClusterRoleBinding/b", "major", "binds/b.json"), ("rbac.secret-read:ClusterRole/wide", "major", "roles.json")]


def test_legacy_service_account_token_secret_is_reported_with_its_file(tmp_path):
    write_json(tmp_path / "secrets" / "t.json", {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/service-account-token", "metadata": {"name": "tok", "namespace": "prod"}})
    write_json(tmp_path / "secrets" / "opaque.json", {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "x", "namespace": "prod"}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1
    assert ids(payload) == [("sa.legacy-token-secret:Secret/prod/tok", "major", "secrets/t.json")]


def test_service_account_manifests_in_the_tree_change_the_automount_decision(tmp_path):
    write_json(tmp_path / "sa.json", {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "builder", "namespace": "prod"}, "automountServiceAccountToken": False})
    workload = pod(automount=True)
    del workload["spec"]["automountServiceAccountToken"]
    write_json(tmp_path / "pod.json", workload)
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["findings"] == []


def test_rejected_manifests_are_listed_as_skipped_not_fatal(tmp_path):
    write_json(tmp_path / "broken.json", {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "d"}, "spec": {}})
    write_json(tmp_path / "other.json", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail" and "scan/incomplete" in {f["id"] for f in payload["findings"]}
    assert payload["skipped"][0].startswith("broken.json pss: INVALID_INPUT workloads")
    assert payload["files_scanned"] == 2 and payload["files_skipped"] == len(payload["skipped"])


# --- bounds ---------------------------------------------------------------------------------------------------


def test_oversized_binary_non_utf8_and_unparseable_files_are_skipped_with_reasons(tmp_path):
    (tmp_path / "big.json").write_text(json.dumps({"kind": "Pod", "pad": "x" * (1024 * 1024 + 1)}))
    exactly = tmp_path / "exact.json"
    prefix, suffix = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "pad": "', '"}'
    exactly.write_text(prefix + "x" * (1024 * 1024 - len(prefix) - len(suffix)) + suffix)
    assert exactly.stat().st_size == 1024 * 1024
    (tmp_path / "bin.json").write_bytes(b'{"kind": "Pod"}\x00\x01')
    (tmp_path / "latin.json").write_bytes('{"kind": "Pod", "x": "caf\xe9"}'.encode("latin-1"))
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "deep.json").write_text("[" * 100000 + "]" * 100000)
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
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
    assert result.returncode == 1 and payload["status"] == "fail" and [f["id"] for f in payload["findings"]] == ["scan/incomplete"]
    assert payload["skipped"] == ["link.json: symlink", "linkdir: symlink-directory"] and payload["files_scanned"] == 1
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
        (tmp_path / f"f{index:04d}.json").write_text('{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}}')
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["files_scanned"] == 2000 and payload["limits_hit"] == ["max-files:2000"]
    assert payload["status"] == "fail" and [f["id"] for f in payload["findings"]] == ["scan/incomplete"]
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


def test_yaml_only_tree_without_pyyaml_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    (tmp_path / "a.yml").write_text(YAML_DOC)
    payload = scan_in_process(tmp_path)
    assert payload["status"] == "fail" and payload["files_scanned"] == 0
    assert sorted(f["id"] for f in payload["findings"]) == ["scan/incomplete", "skipped:tool-missing"]


def test_invalid_yaml_is_skipped_as_a_parse_error(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "helm.yaml").write_text("image: {{ .Values.image }}\n  bad: [")
    (tmp_path / "empty.yaml").write_text("")
    (tmp_path / "scalar.yaml").write_text("just text")
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["skipped"] == ["helm.yaml: parse-error"] and payload["files_scanned"] == 2


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
    assert result.returncode == 1 and payload["skipped"] == ["bomb.yaml: parse-error", "loop.yaml: parse-error"]
    assert payload["files_scanned"] == 1 and payload["files_skipped"] == len(payload["skipped"]) == 2


def test_yaml_with_a_modest_number_of_aliases_still_parses(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "shared.yaml").write_text("apiVersion: v1\nkind: Pod\nmetadata: {name: web, namespace: prod}\nspec:\n  containers:\n"
                                          "  - &c {name: app, image: x, securityContext: {privileged: true}}\n  - {<<: *c, name: second}\n")
    _, payload = scan(tmp_path)
    # the merge key `<<: *c` copies the privileged container, so both containers are reported
    assert sorted(f["summary"] for f in payload["findings"] if f["id"].startswith("pss.privileged")) == [
        "containers/app runs privileged (violates baseline)", "containers/second runs privileged (violates baseline)"]
    assert payload["files_scanned"] == 1 and payload["skipped"] == []


def test_yaml_non_string_keys_and_dates_do_not_stop_the_scan(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "odd.yaml").write_text("apiVersion: v1\nkind: Pod\nmetadata:\n  name: odd\n  annotations:\n    1: one\n    2026-01-01: day\nspec:\n  containers: [{name: c, image: x}]\n")
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
    assert result.returncode == 1 and payload["skipped"] == ["pipe.yaml: not-a-regular-file"] and payload["files_scanned"] == 1


def test_an_object_defined_in_two_files_lists_both_files(tmp_path):
    wide = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "wide"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}
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
    write_json(tmp_path / "secret.json", {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/service-account-token", "metadata": {"name": "t", "namespace": "prod"},
                                          "data": {"token": canary}, "stringData": {"token": canary}})
    result, payload = scan(tmp_path)
    assert canary not in result.stdout and canary not in result.stderr and payload["findings"]


def test_a_workload_the_tool_rejects_fails_the_scan_and_its_message_is_not_echoed(tmp_path):
    document = pod("p")
    document["spec"]["containers"] = [{"name": f"c{i}", "image": "x"} for i in range(201)]
    document["spec"]["containers"][0]["securityContext"] = {"privileged": True}
    write_json(tmp_path / "pod.json", document)
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"][0] == "pod.json pss: INVALID_INPUT workloads"
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}
    assert "more than" not in json.dumps(payload)


def test_a_scan_that_analysed_nothing_is_incomplete_not_ok(tmp_path):
    (tmp_path / "README.txt").write_text("not a manifest")
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail" and payload["files_scanned"] == 0
    assert [(f["id"], f["severity"]) for f in payload["findings"]] == [("scan/incomplete", "major")]
    assert payload["findings"][0]["summary"].endswith("no in-scope input was analysed")


def test_the_incomplete_finding_names_each_skipped_input_by_label_and_path(tmp_path):
    write_json(tmp_path / "pod.json", pod())
    (tmp_path / "bad.json").write_text("{not json")
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    incomplete = next(f for f in payload["findings"] if f["id"] == "scan/incomplete")
    assert (incomplete["severity"], incomplete["location"]) == ("major", "")
    assert incomplete["summary"].endswith("bad.json: parse-error")


def test_a_list_nested_too_deep_is_reported_not_silently_dropped(tmp_path):
    innermost = {"kind": "Pod", "apiVersion": "v1", "metadata": {"name": "p", "namespace": "prod"},
                 "spec": {"containers": [{"name": "app", "image": "x", "securityContext": {"privileged": True}}]}}
    write_json(tmp_path / "nested.json", {"apiVersion": "v1", "kind": "List", "items": [{"apiVersion": "v1", "kind": "List", "items": [{"apiVersion": "v1", "kind": "List", "items": [innermost]}]}]})
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["nested.json: list-nesting-limit:2 at items[0].items[0]"] and payload["files_scanned"] == 2
    assert not [f for f in payload["findings"] if f["id"].endswith("Pod/prod/p")]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_a_list_item_that_is_not_an_object_is_reported_not_silently_dropped(tmp_path):
    write_json(tmp_path / "items.json", {"apiVersion": "v1", "kind": "List", "items": ["just text", pod("ok")]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["items.json: list-item-not-object at items[0]"] and payload["files_scanned"] == 1


def test_a_symlinked_directory_is_reported_and_its_contents_are_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_json(outside / "evil.json", pod("evil", privileged=True))
    root = tmp_path / "root"
    root.mkdir()
    write_json(root / "ok.json", pod())
    os.symlink(outside, root / "linkdir")
    result, payload = scan(root)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["linkdir: symlink-directory"] and payload["files_scanned"] == 1
    assert not [f for f in payload["findings"] if f["id"].endswith("Pod/prod/evil")]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_an_unreadable_subdirectory_is_reported_not_skipped_silently(tmp_path, monkeypatch):
    module = importlib.import_module("swarm.tools.support").script("kpol_manifest_scan")
    write_json(tmp_path / "ok.json", pod())
    locked = tmp_path / "locked"
    locked.mkdir()
    write_json(locked / "pod.json", pod("hidden", privileged=True))
    real_walk = os.walk

    def walk_with_denied_subdir(top, *args, **kwargs):
        onerror = kwargs.get("onerror")
        for current, dirs, names in real_walk(top, *args, **kwargs):
            yield current, dirs, names
        if onerror is not None:
            onerror(PermissionError(13, "Permission denied", str(locked)))

    monkeypatch.setattr(module.os, "walk", walk_with_denied_subdir)
    payload = module.scan(tmp_path, tmp_path, "baseline")
    assert payload["status"] == "fail"
    assert payload["skipped"] == ["locked: unreadable-directory"]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_a_list_whose_items_is_not_a_list_is_reported_not_dropped(tmp_path):
    write_json(tmp_path / "list.json", {"apiVersion": "v1", "kind": "PodList", "items": "pod"})
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-items-not-a-list"] and payload["files_scanned"] == 2
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_a_document_without_a_string_kind_is_reported_not_dropped(tmp_path):
    write_json(tmp_path / "values.json", {"apiVersion": "v1", "metadata": {"name": "x"}})
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["values.json: missing-kind"] and payload["files_scanned"] == 2
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_documents_outside_the_kubernetes_scope_are_passed_over_silently(tmp_path):
    write_json(tmp_path / "package.json", {"name": "app", "version": "1.0.0", "scripts": {"test": "pytest"}})
    write_json(tmp_path / ".github" / "workflows" / "ci.json", {"name": "CI", "on": {"push": {}},
                                                                "jobs": {"build": {"runs-on": "ubuntu-latest", "steps": [{"run": "make"}]}}})
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == [] and payload["files_scanned"] == 2


def test_a_yaml_workflow_is_passed_over_silently_when_pyyaml_is_available(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "ci.yml").write_text("name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make\n")
    _, payload = scan(tmp_path)
    assert payload["status"] == "ok" and payload["skipped"] == [] and payload["files_scanned"] == 1


def test_a_kind_without_an_api_version_is_reported_and_still_analysed(tmp_path):
    write_json(tmp_path / "pod.json", {"kind": "Pod", "metadata": {"name": "p", "namespace": "prod"},
                                       "spec": {"containers": [{"name": "app", "image": "x", "securityContext": {"privileged": True}}]}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["pod.json: missing-apiVersion"]
    assert ("pss.privileged:Pod/prod/p", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_helm_chart_file_beside_a_clean_pod_is_passed_over_silently(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: app\nversion: 1.0.0\n")
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == [] and payload["files_scanned"] == 2


def test_a_kind_less_wrapper_with_items_cannot_hide_a_privileged_pod(tmp_path):
    write_json(tmp_path / "wrapper.json", {"items": [pod("bad", privileged=True), {"name": "ignored"}, "text"]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/bad", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_generic_payload_with_a_non_kubernetes_api_version_and_data_is_passed_over(tmp_path):
    write_json(tmp_path / "payload.json", {"apiVersion": "2", "data": {"k": "v"}})
    write_json(tmp_path / "pod.json", pod())
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == [] and payload["files_scanned"] == 2


def test_a_group_api_version_with_a_body_and_no_kind_is_reported_as_missing_kind(tmp_path):
    write_json(tmp_path / "pdb.json", {"apiVersion": "policy/v1beta1", "spec": {"minAvailable": 1}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["pdb.json: missing-kind"] and payload["files_scanned"] == 1


def test_a_privileged_pod_in_a_nested_array_is_analysed(tmp_path):
    write_json(tmp_path / "nested.json", [[pod("bad", privileged=True)]])
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/bad", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_privileged_pod_in_an_items_array_is_analysed(tmp_path):
    write_json(tmp_path / "wrapped.json", {"items": [[pod("wrapped", privileged=True)]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/wrapped", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_three_level_nested_array_records_the_nesting_limit_and_fails(tmp_path):
    write_json(tmp_path / "deep.json", [[[pod("deep", privileged=True)]]])
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["deep.json: list-nesting-limit:2 at [0][0]"]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_a_mapping_items_on_a_kindless_wrapper_is_analysed_as_one_element(tmp_path):
    write_json(tmp_path / "single.json", {"items": pod("single", privileged=True)})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/single", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_mapping_items_inside_an_array_or_a_nested_wrapper_is_analysed(tmp_path):
    write_json(tmp_path / "array.json", [{"items": pod("inarray", privileged=True)}])
    write_json(tmp_path / "nested.json", {"items": [{"items": pod("nested", privileged=True)}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    found = {(f["id"], f["severity"]) for f in payload["findings"]}
    assert ("pss.privileged:Pod/prod/inarray", "blocker") in found and ("pss.privileged:Pod/prod/nested", "blocker") in found
    assert payload["skipped"] == []


def test_scalar_items_on_a_kindless_wrapper_stay_silent(tmp_path):
    write_json(tmp_path / "generic.json", {"items": "text"})
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == []


def test_a_privileged_pod_in_an_array_inside_a_list_is_analysed(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "List", "apiVersion": "v1", "items": [[pod("inlist", privileged=True)]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/inlist", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_scalar_item_in_a_list_keeps_the_list_item_not_object_reason(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "List", "apiVersion": "v1", "items": [pod("ok"), "x"]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-not-object at items[1]"]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def _pod_body():
    return {"metadata": {"name": "body", "namespace": "prod"}, "spec": pod("body", privileged=True)["spec"]}


def test_a_kind_less_pod_body_in_an_array_inside_a_pod_list_is_reported_not_inherited(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [[_pod_body()]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0][0]"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_kind_less_pod_body_as_a_mapping_item_of_a_pod_list_is_reported_not_inherited(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [_pod_body()]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_scalar_inside_an_array_in_a_pod_list_keeps_the_list_item_not_object_reason(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [["x", _pod_body()]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-not-object at items[0][0]", "list.json: list-item-without-kind at items[0][1]"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_kind_less_generic_record_in_a_pod_list_is_reported_not_judged_as_a_pod(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"foo": 1}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_kind_less_service_shaped_body_in_a_pod_list_array_gets_no_pod_finding(tmp_path):
    service_body = {"metadata": {"name": "svc", "namespace": "prod"}, "spec": {"ports": [{"port": 80}]}}
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [[service_body]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0][0]"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_pod_item_with_its_own_kind_in_a_pod_list_array_is_analysed(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [[pod("own", privileged=True)]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/own", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []


def test_a_service_item_with_its_own_kind_in_a_pod_list_gives_no_pod_finding(tmp_path):
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "svc", "namespace": "prod"}, "spec": {"ports": [{"port": 80}]}}
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [service]})
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["findings"] == [] and payload["skipped"] == []


def test_a_three_level_array_inside_a_pod_list_records_the_nesting_limit(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [[[pod("deep", privileged=True)]]]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-nesting-limit:2 at items[0][0]"]
    assert "scan/incomplete" in {f["id"] for f in payload["findings"]}


def test_a_kind_less_wrapper_inside_a_pod_list_reports_the_item_and_analyses_the_pod_inside(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"items": [pod("inside", privileged=True)]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert ("pss.privileged:Pod/prod/inside", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_kind_less_item_with_no_items_list_keeps_only_the_reason(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"name": "bare"}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert [f["id"] for f in payload["findings"]] == ["scan/incomplete"]


def test_a_three_level_kind_less_wrapper_chain_inside_a_pod_list_hits_the_nesting_limit(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"items": [{"items": [pod("deep", privileged=True)]}]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]", "list.json: list-item-without-kind at items[0].items[0]",
                                  "list.json: list-nesting-limit:2 at items[0].items[0].items"]
    assert not [f for f in payload["findings"] if f["id"].startswith(("pss.", "sa."))]


def test_a_two_level_kind_less_wrapper_chain_inside_a_pod_list_analyses_the_pod_at_depth_two(tmp_path):
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"items": [pod("two", privileged=True)]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert ("pss.privileged:Pod/prod/two", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_single_mapping_items_on_a_kind_less_list_item_is_judged_as_one_element(tmp_path):
    solo = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "solo", "namespace": "prod"},
            "spec": {"containers": [{"name": "app", "image": "x", "securityContext": {"privileged": True}}], "serviceAccountName": "builder",
                     "automountServiceAccountToken": False}}
    write_json(tmp_path / "list.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"name": "x", "items": solo}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-without-kind at items[0]"]
    assert ("pss.privileged:Pod/prod/solo", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_kind_less_candidate_with_items_reports_missing_kind_and_analyses_the_pod_inside(tmp_path):
    write_json(tmp_path / "top.json", {"apiVersion": "v1", "items": [pod("inside", privileged=True)]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["top.json: missing-kind"]
    assert ("pss.privileged:Pod/prod/inside", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_kind_less_candidate_inside_an_array_with_items_analyses_the_pod_inside(tmp_path):
    write_json(tmp_path / "array.json", [{"apiVersion": "v1", "items": [pod("arrayed", privileged=True)]}])
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["array.json: missing-kind at [0]"]
    assert ("pss.privileged:Pod/prod/arrayed", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_kind_less_candidate_with_no_items_list_keeps_only_its_reason(tmp_path):
    write_json(tmp_path / "bare.json", {"apiVersion": "v1", "metadata": {"name": "x"}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["bare.json: missing-kind"]
    assert [f["id"] for f in payload["findings"]] == ["scan/incomplete"]


def test_a_two_level_kind_less_candidate_chain_reports_one_reason_per_level_and_analyses_the_pod(tmp_path):
    write_json(tmp_path / "list.json", {"apiVersion": "v1", "items": [{"apiVersion": "v1", "items": [pod("p2", privileged=True)]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: missing-kind", "list.json: missing-kind at items[0]"]
    assert ("pss.privileged:Pod/prod/p2", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_a_kind_less_candidate_items_get_their_own_reasons_at_their_paths_and_the_pod_is_analysed(tmp_path):
    body = {"metadata": {"name": "b", "namespace": "prod"}, "spec": pod("b", privileged=True)["spec"]}
    write_json(tmp_path / "list.json", {"apiVersion": "v1", "items": ["x", body, pod("p", privileged=True)]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: list-item-not-object at items[0]", "list.json: list-item-without-kind at items[1]",
                                  "list.json: missing-kind"]
    assert ("pss.privileged:Pod/prod/p", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}


def test_generic_data_in_a_nested_list_records_no_nesting_limit(tmp_path):
    write_json(tmp_path / "generic.json", [[{"name": "x", "items": [1, 2]}]])
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == []


def test_a_pod_cut_off_by_the_nesting_limit_still_records_the_limit(tmp_path):
    write_json(tmp_path / "cut.json", [[[pod("cut", privileged=True)]]])
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["cut.json: list-nesting-limit:2 at [0][0]"]


def test_two_nested_candidates_lacking_an_api_version_each_get_their_own_path_reason_and_their_pods_are_analysed(tmp_path):
    write_json(tmp_path / "list.json", {"apiVersion": "v1", "kind": "List", "items": [
        {"kind": "List", "items": [pod("a", privileged=True)]},
        {"kind": "List", "items": [pod("b", privileged=True)]}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["list.json: missing-apiVersion at items[0]", "list.json: missing-apiVersion at items[1]"]
    found = {(f["id"], f["severity"]) for f in payload["findings"]}
    assert ("pss.privileged:Pod/prod/a", "blocker") in found and ("pss.privileged:Pod/prod/b", "blocker") in found


def test_a_single_mapping_items_value_of_a_candidate_is_reported_at_items_with_no_index(tmp_path):
    write_json(tmp_path / "w1.json", {"apiVersion": "v1", "items": {"apiVersion": "v1", "metadata": {"name": "x"}}})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["w1.json: missing-kind", "w1.json: missing-kind at items"]


def test_a_single_mapping_items_of_a_kind_less_list_item_is_reported_at_items_with_no_index(tmp_path):
    write_json(tmp_path / "w2.json", {"kind": "PodList", "apiVersion": "v1", "items": [{"name": "x", "items": {"name": "y"}}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["w2.json: list-item-without-kind at items[0]", "w2.json: list-item-without-kind at items[0].items"]


def test_two_list_items_with_string_items_each_get_their_own_reason(tmp_path):
    write_json(tmp_path / "c1.json", {"kind": "PodList", "apiVersion": "v1", "items": [
        {"kind": "List", "apiVersion": "v1", "items": "a"}, {"kind": "List", "apiVersion": "v1", "items": "b"}]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["c1.json: list-items-not-a-list at items[0]", "c1.json: list-items-not-a-list at items[1]"]


def test_two_depth_cuts_at_different_positions_each_get_their_own_reason(tmp_path):
    a = [[pod("a", privileged=True)]]
    b = [[pod("b", privileged=True)]]
    write_json(tmp_path / "c2.json", {"kind": "PodList", "apiVersion": "v1", "items": [a, b]})
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["c2.json: list-nesting-limit:2 at items[0][0]", "c2.json: list-nesting-limit:2 at items[1][0]"]


def test_a_bare_v2_payload_beside_data_is_passed_over_silently(tmp_path):
    write_json(tmp_path / "payload.json", {"apiVersion": "v2", "data": {"count": 3}})
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == []


def test_a_bare_v2_payload_nested_in_arrays_records_no_nesting_limit(tmp_path):
    write_json(tmp_path / "payload.json", [[[{"apiVersion": "v2", "data": {"count": 3}}]]])
    result, payload = scan(tmp_path)
    assert result.returncode == 0 and payload["status"] == "ok"
    assert payload["skipped"] == [] and payload["findings"] == []


def test_a_grouped_api_version_with_data_is_still_a_candidate_and_reports_missing_kind_at_its_position(tmp_path):
    write_json(tmp_path / "scaler.json", [{"apiVersion": "autoscaling/v2", "data": {"count": 3}}])
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert payload["skipped"] == ["scaler.json: missing-kind at [0]"]


def test_a_v1_pod_is_still_analysed(tmp_path):
    write_json(tmp_path / "pod.json", pod("p", privileged=True))
    result, payload = scan(tmp_path)
    assert result.returncode == 1 and payload["status"] == "fail"
    assert ("pss.privileged:Pod/prod/p", "blocker") in {(f["id"], f["severity"]) for f in payload["findings"]}
    assert payload["skipped"] == []
