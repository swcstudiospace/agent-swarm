"""Behaviour tests for scripts/gitops_repo_check.py (A22): kustomization references, Argo CD / Flux sync policy, sync waves, image
pinning and plaintext secrets in a GitOps repository.

Real-mode tests build fixture trees under tmp_path and run the script as a subprocess; the optional-dependency path runs the module
in-process so PyYAML can be hidden. The rules themselves are the handlers of swarm/tools/a22_tools.py and are tested in
tests/test_tools_a22.py; these tests pin what the script reads, how it maps files to rules and what it reports.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import ROOT, run_script

needs_yaml = pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="the fixture trees are YAML (PyYAML is an optional dependency)")

SCRIPT = ROOT / "scripts" / "gitops_repo_check.py"
WAVE = "argocd.argoproj.io/sync-wave"


def put(root: Path, relative: str, text) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(text) if not isinstance(text, str) else text, encoding="utf-8")
    return path


def scan(root: Path, *args: str):
    proc = run_script("gitops_repo_check.py", "--root", str(root), "--json", *args)
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def found(result: dict) -> list[tuple[str, str, str]]:
    """(id, severity, file) of every finding."""
    return [(f["id"], f["severity"], f["file"]) for f in result["findings"]]


def ids(result: dict, check: str | None = None) -> set[str]:
    return {f["id"] for f in result["findings"] if check is None or f["check"] == check}


def load_module():
    spec = importlib.util.spec_from_file_location("gitops_repo_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_in_process(module, root: Path, capsys, *args: str):
    code = module.AgentScript("A22", "gitops_repo_check", module.run, description=module.__doc__, add_args=module.add_args).main(
        ["--root", str(root), "--json", *args])
    return code, json.loads(capsys.readouterr().out)


DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: shop
spec:
  template:
    spec:
      containers:
        - name: web
          image: ghcr.io/acme/web:1.4.2
"""


# --- dry run --------------------------------------------------------------------------------------------------


def test_dry_run_prints_only_canned_json_and_reads_nothing(tmp_path):
    put(tmp_path, "bad/secret.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\nstringData: {password: hunter2hunter2}\n")
    proc = run_script("gitops_repo_check.py", "--dry-run", "--json", "--root", str(tmp_path))
    assert proc.returncode == 0 and "Traceback" not in proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "ok" and result["dry_run"] is True and result["agent"] == "A22" and result["script"] == "gitops_repo_check"
    assert (result["files_scanned"], result["finding_count"], result["findings"], result["truncated"], result["skipped"]) == (0, 0, [], False, [])
    assert result["severity_counts"] == {"blocker": 0, "major": 0, "minor": 0, "info": 0}
    assert set(result["checks"]) == {"parse", "kustomize", "apps", "waves", "images", "secrets"}
    bare = json.loads(run_script("gitops_repo_check.py", "--dry-run", "--json").stdout)
    assert bare == result
    assert json.loads(run_script("gitops_repo_check.py", "--dry-run", "--json", "--root", str(tmp_path / "missing")).stdout) == result


@needs_yaml
def test_real_scan_has_the_same_result_shape_as_the_dry_run(tmp_path):
    put(tmp_path, "deploy.yaml", DEPLOYMENT)
    code, real = scan(tmp_path)
    dry = json.loads(run_script("gitops_repo_check.py", "--dry-run", "--json").stdout)
    assert code == 0 and real["status"] == "ok"
    assert set(dry) - {"dry_run"} <= set(real)
    assert real["findings"] == [] and real["files_scanned"] == 1 and real["finding_count"] == 0 and "0 finding(s) in 1 file(s)" in real["summary"]


# --- kustomization references ---------------------------------------------------------------------------------


@needs_yaml
def test_missing_references_and_directories_without_a_kustomization_are_blockers(tmp_path):
    put(tmp_path, "app/kustomization.yaml", "resources:\n  - deploy.yaml\n  - gone.yaml\n  - sub\n  - https://github.com/acme/base//k?ref=v1\n  - git@github.com:acme/b.git\n"
                                           "components:\n  - comp.yaml\ncrds:\n  - crd.yaml\n")
    put(tmp_path, "app/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "app/sub/readme.yaml", "note: x\n")
    put(tmp_path, "app/comp.yaml", "note: x\n")
    code, result = scan(tmp_path)
    assert code == 1 and result["status"] == "fail"
    assert found(result) == [
        ("kustomize:base-without-kustomization:sub", "blocker", "app/kustomization.yaml"),
        ("kustomize:component-not-directory:comp.yaml", "blocker", "app/kustomization.yaml"),
        ("kustomize:missing-reference:crds:crd.yaml", "blocker", "app/kustomization.yaml"),
        ("kustomize:missing-reference:resources:gone.yaml", "blocker", "app/kustomization.yaml"),
    ]
    assert result["checks"]["kustomize"] == 4 and result["severity_counts"]["blocker"] == 4


@needs_yaml
def test_file_references_outside_the_kustomization_directory_break_the_load_restrictor(tmp_path):
    put(tmp_path, "shared/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "shared/patch.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web, namespace: shop}\nspec: {replicas: 2}\n")
    put(tmp_path, "overlay/kustomization.yaml", "resources:\n  - ../shared/deploy.yaml\n  - ../shared\npatches:\n  - path: ../shared/patch.yaml\n")
    put(tmp_path, "shared/kustomization.yaml", "resources:\n  - deploy.yaml\n")
    code, result = scan(tmp_path)
    assert code == 1
    assert ids(result, "kustomize") == {"kustomize:load-restrictor:resources:../shared/deploy.yaml", "kustomize:load-restrictor:patches:../shared/patch.yaml",
                                        "kustomize:duplicate-resource:apps/Deployment/shop/web"}  # the file and the base that contains it
    assert {f["id"]: f["severity"] for f in result["findings"]}["kustomize:load-restrictor:patches:../shared/patch.yaml"] == "major"
    restrictor = next(f for f in result["findings"] if f["id"].startswith("kustomize:load-restrictor"))
    assert "LoadRestrictionsNone" in restrictor["summary"] and restrictor["location"] == "overlay/kustomization.yaml"


@needs_yaml
def test_reference_cycles_are_blockers(tmp_path):
    put(tmp_path, "a/kustomization.yaml", "resources:\n  - ../b\n")
    put(tmp_path, "b/kustomization.yaml", "resources:\n  - ../a\n")
    code, result = scan(tmp_path)
    assert code == 1 and len(found(result)) == 1
    finding = result["findings"][0]
    assert finding["id"].startswith("kustomize:cycle:") and finding["severity"] == "blocker" and "a -> b -> a" in finding["summary"]
    put(tmp_path, "self/kustomization.yaml", "resources:\n  - .\n")
    code, result = scan(tmp_path, "--path", "self")
    assert code == 1 and [f["id"] for f in result["findings"]] == ["kustomize:cycle:self"]


@needs_yaml
def test_patch_and_generator_files_must_exist_and_stay_inside(tmp_path):
    put(tmp_path, "k/kustomization.yaml",
        "resources:\n  - deploy.yaml\npatches:\n  - path: missing-patch.yaml\npatchesStrategicMerge:\n  - gone-merge.yaml\n"
        "  - |-\n    apiVersion: apps/v1\n    kind: Deployment\n    metadata:\n      name: web\n"
        "configMapGenerator:\n  - name: cfg\n    files:\n      - app.properties\n      - renamed=other.properties\n    envs: [cfg.env]\n"
        "openapi:\n  path: schema.json\n")
    put(tmp_path, "k/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "k/app.properties", "a=b\n")
    code, result = scan(tmp_path)
    assert code == 1
    assert ids(result, "kustomize") >= {"kustomize:missing-file:patches:missing-patch.yaml", "kustomize:missing-file:patchesStrategicMerge:gone-merge.yaml",
                                        "kustomize:missing-file:configMapGenerator:other.properties", "kustomize:missing-file:configMapGenerator:cfg.env",
                                        "kustomize:missing-file:openapi:schema.json"}
    assert "kustomize:missing-file:configMapGenerator:app.properties" not in ids(result)  # exists
    assert not any("web" in i and "missing-file" in i for i in ids(result))  # an inline patch is not a path


@needs_yaml
def test_references_that_leave_the_scanned_root_are_reported_not_followed(tmp_path):
    outside = tmp_path / "outside"
    put(outside, "kustomization.yaml", "resources: []\n")
    repo = tmp_path / "repo"
    put(repo, "k/kustomization.yaml", "resources:\n  - ../../outside\npatches:\n  - path: ../../outside/p.yaml\n")
    code, result = scan(repo)
    assert code == 0
    assert found(result) == [("kustomize:outside-root:patches:../../outside/p.yaml", "minor", "k/kustomization.yaml"),
                             ("kustomize:outside-root:resources:../../outside", "minor", "k/kustomization.yaml")]


@needs_yaml
def test_overlay_is_checked_against_the_resources_its_bases_render(tmp_path):
    put(tmp_path, "base/kustomization.yaml", "namePrefix: pre-\nresources:\n  - ns.yaml\n  - deploy.yaml\nconfigMapGenerator:\n  - name: cfg\n    literals: [a=b]\n")
    put(tmp_path, "base/ns.yaml", "apiVersion: v1\nkind: Namespace\nmetadata: {name: shop}\n")
    put(tmp_path, "base/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "overlay/kustomization.yaml",
        "resources:\n  - ../base\npatches:\n"
        "  - target: {kind: Deployment, name: pre-web}\n    patch: |-\n      - op: replace\n        path: /spec/replicas\n        value: 3\n"
        "  - target: {kind: Namespace, name: shop}\n    patch: |-\n      - op: add\n        path: /metadata/labels\n        value: {a: b}\n"
        "  - target: {kind: ConfigMap, name: pre-cfg}\n    patch: |-\n      - op: add\n        path: /data/x\n        value: y\n"
        "  - target: {kind: Deployment, name: web}\n    patch: |-\n      - op: remove\n        path: /spec/replicas\n")
    code, result = scan(tmp_path)
    # the prefix is applied to Deployment and generated ConfigMap, not to the Namespace; the un-prefixed target matches nothing
    assert found(result) == [("kustomize:patch-target-not-found:patches[3]", "major", "overlay/kustomization.yaml")] and code == 1


@needs_yaml
def test_patch_files_are_read_so_their_targets_can_be_checked(tmp_path):
    put(tmp_path, "k/kustomization.yaml", "resources:\n  - deploy.yaml\npatches:\n  - path: ok.yaml\n  - path: wrong.yaml\n  - path: ops.yaml\n    target: {kind: Deployment, name: web}\n"
                                         "  - path: ops.yaml\n    target: {kind: Deployment, name: nope}\npatchesStrategicMerge:\n  - merge.yaml\n")
    put(tmp_path, "k/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "k/ok.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web, namespace: shop}\nspec: {replicas: 2}\n")
    put(tmp_path, "k/wrong.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: other}\nspec: {replicas: 2}\n")
    put(tmp_path, "k/ops.yaml", "- op: replace\n  path: /spec/replicas\n  value: 2\n")
    put(tmp_path, "k/merge.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web, namespace: shop}\n---\napiVersion: v1\nkind: Service\nmetadata: {name: web}\n")
    code, result = scan(tmp_path)
    assert code == 1
    assert ids(result, "kustomize") == {"kustomize:patch-target-not-found:patches[1]", "kustomize:patch-target-not-found:patches[3]",
                                        "kustomize:patch-target-not-found:patchesStrategicMerge[1]", "kustomize:deprecated-field:patchesStrategicMerge"}


@needs_yaml
def test_duplicate_ids_across_files_and_secret_generators(tmp_path):
    put(tmp_path, "k/kustomization.yaml", "resources:\n  - a.yaml\n  - b.yaml\nsecretGenerator:\n  - name: creds\n    literals:\n      - password=hunter2hunter2\n      - note=${NOTE}\n"
                                         "  - name: files\n    files:\n      - id_rsa\n  - name: ok\n    literals:\n      - token=${TOKEN}\n")
    put(tmp_path, "k/a.yaml", DEPLOYMENT)
    put(tmp_path, "k/b.yaml", DEPLOYMENT)
    put(tmp_path, "k/id_rsa", "key\n")
    code, result = scan(tmp_path)
    assert code == 1
    assert found(result) == [
        ("kustomize:duplicate-resource:apps/Deployment/shop/web", "blocker", "k/kustomization.yaml"),
        ("secrets:secretgenerator-literals:creds", "blocker", "k/kustomization.yaml"),
        ("secrets:secretgenerator-files:files", "major", "k/kustomization.yaml")]


# --- applications -----------------------------------------------------------------------------------------------

ARGO_APP = """apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: {name: web}
spec:
  project: shop
  source: {repoURL: "https://git.example.com/acme/deploy.git", path: apps/web, targetRevision: v1.4.0}
  destination: {server: "https://kubernetes.default.svc", namespace: shop}
  syncPolicy:
    automated: {prune: true}
"""


@needs_yaml
def test_production_paths_get_the_production_rules(tmp_path):
    put(tmp_path, "clusters/prod/app.yaml", ARGO_APP)
    put(tmp_path, "clusters/staging/app.yaml", ARGO_APP)
    code, result = scan(tmp_path)
    assert code == 1
    prod = [(i, s) for i, s, f in found(result) if f == "clusters/prod/app.yaml"]
    assert prod == [("apps:prod-auto-prune", "blocker"), ("apps:prod-auto-sync", "major"), ("apps:no-retry", "minor")]
    assert [x for x in found(result) if x[2] == "clusters/staging/app.yaml"] == [("apps:no-retry", "minor", "clusters/staging/app.yaml")]


@needs_yaml
def test_prod_markers_are_whole_path_segments_and_configurable(tmp_path):
    put(tmp_path, "environments/production/app.yaml", ARGO_APP)
    put(tmp_path, "environments/preprod-lab/app.yaml", ARGO_APP)
    put(tmp_path, "environments/products/app.yaml", ARGO_APP)
    code, result = scan(tmp_path)
    flagged = {f for i, s, f in found(result) if i == "apps:prod-auto-sync"}
    assert flagged == {"environments/production/app.yaml"}  # preprod-lab splits into preprod and lab and products into products: no marker
    code, result = scan(tmp_path, "--prod-marker", "products")
    assert {f for i, s, f in found(result) if i == "apps:prod-auto-sync"} == {"environments/products/app.yaml"}


@needs_yaml
def test_flux_resources_and_application_sets(tmp_path):
    put(tmp_path, "prod/flux.yaml", "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\nmetadata: {name: apps}\nspec:\n  interval: 10m\n  prune: true\n"
                                    "  serviceAccountName: deployer\n  sourceRef: {kind: GitRepository, name: flux-system}\n---\n"
                                    "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata: {name: web}\nspec:\n  interval: 10m\n"
                                    "  chart: {spec: {chart: web, version: '1.x'}}\n  install: {remediation: {retries: 3}}\n  driftDetection: {mode: enabled}\n")
    put(tmp_path, "prod/appset.yaml", "apiVersion: argoproj.io/v1alpha1\nkind: ApplicationSet\nmetadata: {name: web}\nspec:\n  generators:\n"
                                      "    - clusters: {selector: {matchLabels: {env: prod}}}\n  template:\n    metadata: {name: 'web-{{name}}'}\n    spec:\n      project: shop\n"
                                      "      source: {repoURL: 'https://x/y.git', path: p, targetRevision: v1}\n      destination: {server: '{{server}}', namespace: shop}\n")
    code, result = scan(tmp_path)
    assert code == 1
    assert sorted(found(result)) == sorted([
        ("apps:prod-prune", "blocker", "prod/flux.yaml"), ("apps:floating-revision:chart version '1.x'", "major", "prod/flux.yaml"),
        ("apps:no-progressive-sync", "minor", "prod/appset.yaml")])


@needs_yaml
def test_non_application_resources_and_non_objects_are_ignored(tmp_path):
    put(tmp_path, "mixed.yaml", "- just\n- a list\n---\nkey: value\n---\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: c}\ndata: {a: b}\n---\n\n---\n"
                                "apiVersion: argoproj.io/v1alpha1\nkind: AppProject\nmetadata: {name: shop}\nspec: {}\n")
    code, result = scan(tmp_path)
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1


# --- sync waves -------------------------------------------------------------------------------------------------


@needs_yaml
def test_sync_waves_are_checked_per_kustomize_tree_and_per_plain_directory(tmp_path):
    put(tmp_path, "tree/base/kustomization.yaml", "resources:\n  - ns.yaml\n  - deploy.yaml\n")
    put(tmp_path, "tree/base/ns.yaml", f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: shop\n  annotations: {{{WAVE}: '1'}}\n")
    put(tmp_path, "tree/base/deploy.yaml", DEPLOYMENT.replace("  namespace: shop\n", f"  namespace: shop\n  annotations: {{{WAVE}: '0'}}\n"))
    put(tmp_path, "tree/overlay/kustomization.yaml", "resources:\n  - ../base\n")
    put(tmp_path, "loose/ns.yaml", f"apiVersion: v1\nkind: Namespace\nmetadata:\n  name: shop\n  annotations: {{{WAVE}: '1'}}\n")
    put(tmp_path, "loose/deploy.yaml", DEPLOYMENT.replace("  namespace: shop\n", f"  namespace: shop\n  annotations: {{{WAVE}: '0'}}\n"))
    put(tmp_path, "plain/deploy.yaml", DEPLOYMENT)
    put(tmp_path, "plain/ns.yaml", "apiVersion: v1\nkind: Namespace\nmetadata: {name: shop}\n")
    code, result = scan(tmp_path)
    assert code == 1
    waves = [(i, f) for i, s, f in found(result) if i.startswith("waves:")]
    assert waves == [("waves:namespace-after-use:Deployment/shop/web", "loose/"), ("waves:namespace-after-use:Deployment/shop/web", "tree/overlay/kustomization.yaml")]
    assert all(s == "blocker" for i, s, f in found(result) if i.startswith("waves:"))


@needs_yaml
def test_sync_waves_are_only_checked_where_annotations_are_used(tmp_path):
    put(tmp_path, "d/crd.yaml", "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata: {name: w.acme.io}\nspec: {group: acme.io, names: {kind: W}}\n")
    put(tmp_path, "d/cr.yaml", "apiVersion: acme.io/v1\nkind: W\nmetadata: {name: w}\n")
    code, result = scan(tmp_path)
    assert code == 0 and result["findings"] == []
    put(tmp_path, "d/cr.yaml", f"apiVersion: acme.io/v1\nkind: W\nmetadata:\n  name: w\n  annotations: {{{WAVE}: '0'}}\n")
    code, result = scan(tmp_path)
    assert code == 1 and ids(result) == {"waves:cr-with-crd-same-wave:W/w"}


# --- images -----------------------------------------------------------------------------------------------------


def deployment_with(image: str, name: str = "web", init: bool = False) -> str:
    section = "initContainers" if init else "containers"
    return f"apiVersion: apps/v1\nkind: Deployment\nmetadata: {{name: {name}}}\nspec:\n  template:\n    spec:\n      {section}:\n        - name: c\n          image: {image}\n"


@needs_yaml
def test_floating_images_are_major_and_tag_pinning_is_optional(tmp_path):
    put(tmp_path, "a.yaml", deployment_with("ghcr.io/acme/web:latest"))
    put(tmp_path, "b.yaml", deployment_with("nginx", "b", init=True))
    put(tmp_path, "c.yaml", deployment_with("ghcr.io/acme/api:2.1.0", "c"))
    put(tmp_path, "d.yaml", deployment_with("ghcr.io/acme/db@sha256:" + "a" * 64, "d"))
    put(tmp_path, "e.yaml", deployment_with("'{{ .Values.image }}'", "e"))
    code, result = scan(tmp_path)
    assert code == 1
    assert found(result) == [("images:floating-tag:ghcr.io/acme/web:latest", "major", "a.yaml"), ("images:floating-tag:nginx", "major", "b.yaml")]
    code, strict = scan(tmp_path, "--require-digest")
    assert ("images:digest-required:ghcr.io/acme/api:2.1.0", "major", "c.yaml") in found(strict) and len(found(strict)) == 3
    assert not any("sha256" in i for i, s, f in found(strict))


@needs_yaml
def test_an_image_used_in_several_files_is_reported_once_with_the_first_file(tmp_path):
    put(tmp_path, "b.yaml", deployment_with("app:latest", "b"))
    put(tmp_path, "a.yaml", deployment_with("app:latest", "a"))
    code, result = scan(tmp_path)
    assert found(result) == [("images:floating-tag:app:latest", "major", "a.yaml")] and "(+1 more file(s))" in result["findings"][0]["summary"]


@needs_yaml
def test_images_are_found_in_cron_jobs_and_kustomize_image_overrides(tmp_path):
    put(tmp_path, "cron.yaml", "apiVersion: batch/v1\nkind: CronJob\nmetadata: {name: n}\nspec:\n  jobTemplate:\n    spec:\n      template:\n        spec:\n"
                               "          containers:\n            - {name: c, image: 'job:edge'}\n")
    put(tmp_path, "k/kustomization.yaml", "images:\n  - {name: web, newName: reg.io/web, newTag: latest}\n  - {name: api, newTag: '1.0'}\n  - {name: db, digest: 'sha256:" + "b" * 64 + "'}\n  - {name: skip}\n")
    code, result = scan(tmp_path)
    assert sorted(found(result)) == sorted([("images:floating-tag:job:edge", "major", "cron.yaml"), ("images:floating-tag:reg.io/web:latest", "major", "k/kustomization.yaml")])


@needs_yaml
def test_invalid_image_references_are_minor(tmp_path):
    put(tmp_path, "a.yaml", deployment_with("'bad image'"))
    code, result = scan(tmp_path)
    assert code == 0 and [(f["id"], f["severity"]) for f in result["findings"]] == [("images:image-invalid:bad image", "minor")]


# --- secrets ----------------------------------------------------------------------------------------------------


@needs_yaml
def test_plaintext_secrets_env_values_and_credential_urls_are_found_and_never_echoed(tmp_path):
    put(tmp_path, "s.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: db}\nstringData: {password: hunter2hunter2}\n")
    put(tmp_path, "d.yaml", DEPLOYMENT.replace("          image: ghcr.io/acme/web:1.4.2\n", "          image: ghcr.io/acme/web:1.4.2\n          env:\n            - {name: API_TOKEN, value: abcdef123456}\n"
                                                 "            - {name: OK, valueFrom: {secretKeyRef: {name: db, key: password}}}\n"))
    put(tmp_path, "app.yaml", ARGO_APP.replace("https://git.example.com", "https://ci:s3cretpass@git.example.com"))
    put(tmp_path, "sealed.yaml", "apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nmetadata: {name: ss}\nspec: {encryptedData: {password: AgBy3i4OJSWK}}\n")
    code, result = scan(tmp_path)
    assert code == 1
    secrets = [(i, s, f) for i, s, f in found(result) if i.startswith("secrets:")]
    assert secrets == [
        ("secrets:credentials-in-url:Application/web:spec.source.repoURL", "blocker", "app.yaml"),
        ("secrets:plaintext-secret:Secret/db", "blocker", "s.yaml"),
        ("secrets:plaintext-env:Deployment/shop/web:spec.template.spec.containers[0].env[0]", "major", "d.yaml")]
    text = json.dumps(result)
    assert "hunter2hunter2" not in text and "s3cretpass" not in text and "abcdef123456" not in text


@needs_yaml
def test_sops_encrypted_and_placeholder_secrets_pass(tmp_path):
    put(tmp_path, "s.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: db}\nstringData: {password: 'ENC[AES256_GCM,data:abc,tag:def]'}\nsops: {version: 3.8.1}\n")
    put(tmp_path, "t.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: tpl}\nstringData: {password: '${DB_PASSWORD}', user: ''}\n")
    code, result = scan(tmp_path)
    assert code == 0 and result["findings"] == []


# --- parsing, bounds and filesystem safety -------------------------------------------------------------------------


@needs_yaml
def test_invalid_yaml_and_json_are_major_but_templated_files_are_skipped(tmp_path):
    put(tmp_path, "broken.yaml", "key: [unclosed\n  - : :\n")
    put(tmp_path, "broken.json", "{not json")
    put(tmp_path, "chart/templates/deploy.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\n")
    put(tmp_path, "ok.json", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}})
    code, result = scan(tmp_path)
    assert code == 1 and sorted(found(result)) == [("parse:broken.json", "major", "broken.json"), ("parse:broken.yaml", "major", "broken.yaml")]
    assert result["files_scanned"] == 1 and result["files_skipped"]["templated"] == 1 and result["checks"]["parse"] == 2


def test_json_manifests_and_lists_are_read(tmp_path):
    put(tmp_path, "list.json", [{"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "a"}, "data": {"p": "aHVudGVyMg=="}}, 7])
    code, result = scan(tmp_path)
    assert code == 1 and found(result) == [("secrets:plaintext-secret:Secret/a", "blocker", "list.json")]


@needs_yaml
def test_large_binary_symlinked_and_vcs_files_are_skipped(tmp_path):
    put(tmp_path, "big.yaml", "a: " + "x" * (1024 * 1024 + 10))
    (tmp_path / "bin.yaml").write_bytes(b"\x00\x01apiVersion: v1\n")
    put(tmp_path, ".git/hooks/x.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    put(tmp_path, "node_modules/p/x.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    put(outside, "secret.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink(outside / "secret.yaml", repo / "link.yaml")
    os.symlink(outside, repo / "linked-dir")
    put(repo, "ok.yaml", DEPLOYMENT)
    code, result = scan(repo)
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1 and result["files_skipped"]["symlink"] == 2
    code, result = scan(tmp_path)
    assert (result["files_skipped"]["too-large"], result["files_skipped"]["binary"], result["files_skipped"]["symlink"]) == (1, 1, 2)
    assert result["findings"] == [] and result["files_scanned"] == 1  # .git, node_modules and everything behind symlinks stay unread


@needs_yaml
def test_at_most_2000_files_are_read(tmp_path):
    for index in range(2005):
        put(tmp_path, f"f/{index:04d}.yaml", f"apiVersion: v1\nkind: ConfigMap\nmetadata: {{name: c{index}}}\n")
    code, result = scan(tmp_path)
    assert code == 0 and result["files_scanned"] == 2000 and result["truncated"] is True


@needs_yaml
def test_alias_bombs_are_skipped_not_fatal(tmp_path):
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join(['*' + chr(97 + i)] * 10)}]\n" for i in range(9))
    put(tmp_path, "bomb.yaml", bomb)
    put(tmp_path, "ok.yaml", DEPLOYMENT)
    code, result = scan(tmp_path)
    assert code == 0 and result["files_skipped"]["too-complex"] == 1 and result["files_scanned"] == 1


def test_without_pyyaml_yaml_files_are_reported_as_skipped_tool_missing_and_json_still_works(tmp_path, monkeypatch, capsys):
    put(tmp_path, "one.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    put(tmp_path, "two.yml", "kind: Pod\n")
    put(tmp_path, "s.json", {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "j"}, "data": {"p": "aHVudGVyMg=="}})
    monkeypatch.setitem(sys.modules, "yaml", None)
    code, result = run_in_process(load_module(), tmp_path, capsys)
    assert code == 1 and result["skipped"] == [{"check": "kustomize, apps, waves, images, secrets (YAML files)", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 2}]
    assert found(result) == [("secrets:plaintext-secret:Secret/j", "blocker", "s.json")]
    assert (result["files_scanned"], result["files_skipped_yaml_missing"]) == (1, 2)


@needs_yaml
def test_path_selects_a_subtree_and_must_stay_inside_the_root(tmp_path):
    put(tmp_path, "a/s.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    put(tmp_path, "b/ok.yaml", DEPLOYMENT)
    code, result = scan(tmp_path, "--path", "b")
    assert code == 0 and result["findings"] == [] and result["files_scanned"] == 1
    code, result = scan(tmp_path, "--path", "a")
    assert code == 1 and found(result) == [("secrets:plaintext-secret:Secret/s", "blocker", "a/s.yaml")]
    for bad in ("../x", "/etc", "missing", "a/s.yaml"):
        proc = run_script("gitops_repo_check.py", "--root", str(tmp_path), "--json", "--path", bad)
        assert proc.returncode == 2 and json.loads(proc.stdout)["status"] == "error", bad


@needs_yaml
def test_findings_are_sorted_worst_first_and_status_needs_a_major_finding(tmp_path):
    put(tmp_path, "a.yaml", ARGO_APP.replace("automated: {prune: true}", "automated: {}") + "---\n" + DEPLOYMENT.replace("1.4.2", "latest"))
    put(tmp_path, "b.yaml", "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\ndata: {p: aHVudGVyMg==}\n")
    code, result = scan(tmp_path)
    severities = [f["severity"] for f in result["findings"]]
    assert severities == sorted(severities, key=["blocker", "major", "minor", "info"].index)
    assert code == 1 and result["status"] == "fail" and result["severity_counts"] == {"blocker": 1, "major": 1, "minor": 1, "info": 0}
    assert result["finding_count"] == 3 and result["checks"] == {"parse": 0, "kustomize": 0, "apps": 1, "waves": 0, "images": 1, "secrets": 1}
    put(tmp_path, "b.yaml", "")
    (tmp_path / "a.yaml").write_text(ARGO_APP.replace("automated: {prune: true}", "automated: {}"), encoding="utf-8")
    code, only_minor = scan(tmp_path)
    assert code == 0 and only_minor["status"] == "ok" and [f["severity"] for f in only_minor["findings"]] == ["minor"]


@needs_yaml
def test_the_scan_is_deterministic(tmp_path):
    for name in ("z", "m", "a"):
        put(tmp_path, f"{name}/app.yaml", ARGO_APP)
        put(tmp_path, f"{name}/dep.yaml", deployment_with("x:latest", name))
    assert scan(tmp_path) == scan(tmp_path)


# --- independent review regressions ------------------------------------------------------------------------------------------


@needs_yaml
def test_review_patch_targets_in_remote_or_component_bases_are_unproven_not_missing(tmp_path):
    patch = "patches:\n  - target: {kind: Deployment, name: web}\n    patch: |-\n      - op: replace\n        path: /spec/replicas\n        value: 3\n"
    put(tmp_path, "remote/kustomization.yaml", "resources:\n  - github.com/acme/base//web?ref=v1.0.0\n" + patch)
    put(tmp_path, "components/kustomization.yaml", "components:\n  - ../comp\n" + patch)
    put(tmp_path, "local/kustomization.yaml", "resources:\n  - d.yaml\n" + patch.replace("web", "gone"))
    put(tmp_path, "local/d.yaml", DEPLOYMENT)
    _, result = scan(tmp_path)
    missing = {f["file"] for f in result["findings"] if f["id"].startswith("kustomize:patch-target-not-found")}
    assert missing == {"local/kustomization.yaml"}


@needs_yaml
def test_review_images_pinned_by_a_kustomize_images_entry_are_not_floating_in_the_base(tmp_path):
    put(tmp_path, "base/kustomization.yaml", "resources:\n  - d.yaml\n")
    put(tmp_path, "base/d.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: c\n          image: r.io/acme/web:latest\n")
    put(tmp_path, "overlays/prod/kustomization.yaml", "resources:\n  - ../../base\nimages:\n  - name: r.io/acme/web\n    newTag: 1.4.2\n")
    _, result = scan(tmp_path)
    assert not [i for i in ids(result, "images") if "latest" in i]
    put(tmp_path, "other/d.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: o\nspec:\n  template:\n    spec:\n      containers:\n        - name: c\n          image: r.io/acme/other:latest\n")
    _, result = scan(tmp_path)
    assert any("floating-tag" in i and "other" in i for i in ids(result, "images"))


@needs_yaml
def test_an_overlay_pin_does_not_hide_the_same_image_in_an_unrelated_kustomize_tree(tmp_path):
    deployment = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: c\n          image: r.io/acme/web:latest\n"
    put(tmp_path, "base/kustomization.yaml", "resources:\n  - d.yaml\n")
    put(tmp_path, "base/d.yaml", deployment)
    put(tmp_path, "overlays/prod/kustomization.yaml", "resources:\n  - ../../base\nimages:\n  - name: r.io/acme/web\n    newTag: 1.4.2\n")
    put(tmp_path, "unrelated/kustomization.yaml", "resources:\n  - d.yaml\n")
    put(tmp_path, "unrelated/d.yaml", deployment)
    _, result = scan(tmp_path)
    floating = [(f["id"], f["file"]) for f in result["findings"] if f["check"] == "images" and "latest" in f["id"]]
    assert floating == [("images:floating-tag:r.io/acme/web:latest", "unrelated/d.yaml")]


@needs_yaml
def test_a_pin_in_an_overlay_covers_the_images_of_the_components_it_includes(tmp_path):
    put(tmp_path, "comp/kustomization.yaml", "apiVersion: kustomize.config.k8s.io/v1alpha1\nkind: Component\nresources:\n  - d.yaml\n")
    put(tmp_path, "comp/d.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: c\n          image: r.io/acme/web:latest\n")
    put(tmp_path, "overlay/kustomization.yaml", "components:\n  - ../comp\nimages:\n  - name: r.io/acme/web\n    newTag: 1.4.2\n")
    _, result = scan(tmp_path)
    assert not any("floating-tag" in i for i in ids(result, "images"))


@needs_yaml
def test_review_credentials_in_non_object_documents_are_found_and_never_echoed(tmp_path):
    token = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    put(tmp_path, "chart/values-prod.yaml", f"registry:\n  url: https://svc:S3cr3tPassw0rd@git.example.com/x.git\n  token: {token}\n")
    code, result = scan(tmp_path)
    assert code == 1 and {f["id"].split(":")[1] for f in result["findings"] if f["check"] == "secrets"} == {"secret-pattern", "credentials-in-url"}
    text = json.dumps(result)
    assert token not in text and "S3cr3tPassw0rd" not in text
