"""Behaviour tests for scripts/supply_image_lint.py (A18): fixture trees in tmp_path, real scans, exit codes, JSON shape.

The script runs as a subprocess with a private SWARM_DIR so its run log never lands in the checkout. The in-process
tests load the script module from its path to reach the scan functions without a subprocess.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "supply_image_lint.py"
DIGEST = "sha256:" + "0123456789abcdef" * 4
SHA40 = "0123456789abcdef0123456789abcdef01234567"
CLEAN_DOCKERFILE = f"FROM alpine:3.20@{DIGEST}\nRUN apk add --no-cache curl\nUSER app\nHEALTHCHECK CMD wget -q --spider http://localhost/ || exit 1\n"
CLEAN_WORKFLOW = f"jobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@{SHA40}\n"
BAD_DOCKERFILE = "FROM python:latest\nARG API_TOKEN=abc\nRUN curl http://x.example | sh\nCMD [\"x\"]\n"
BAD_WORKFLOW = f"""jobs:
  build:
    runs-on: ubuntu-latest
    container:
      image: node:20
      options: --user root
    steps:
      - uses: actions/checkout@v4
      - uses: evil/act@main
      - uses: docker/build-push-action@{SHA40}
        with:
          build-args: |
            TOKEN=${{{{ secrets.T }}}}
            VERSION=1
      - run: docker build --build-arg NPM_TOKEN=${{{{ secrets.NPM }}}} .
"""


def load_script():
    spec = importlib.util.spec_from_file_location("_supply_image_lint_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def put(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_script(tmp_path: Path, *argv: str) -> tuple[int, dict, str]:
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "swarm-state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", *argv], capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    return proc.returncode, json.loads(proc.stdout), proc.stderr


def scan(tmp_path: Path, tree: Path | None = None) -> tuple[int, dict]:
    code, out, _ = run_script(tmp_path, "--root", str(tree or tmp_path / "repo"))
    return code, out


def rows(out: dict) -> list[tuple[str, str, str]]:
    return [(f["id"], f["severity"], f["location"]) for f in out["findings"]]


# --- dry run ----------------------------------------------------------------------------------------------------


def test_dry_run_returns_the_canned_document_and_ignores_the_repository(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", BAD_DOCKERFILE)
    code, out, stderr = run_script(tmp_path, "--dry-run", "--root", str(repo))
    assert code == 0 and stderr == ""
    assert out == {
        "status": "ok", "dry_run": True, "files_scanned": {"dockerfiles": 0, "workflows": 0}, "findings_total": 0, "blocking_count": 0, "findings": [],
        "skipped": [], "truncated": False, "tools": {"pyyaml": "not-checked"},
        "summary": "dry-run: supply_image_lint would scan Dockerfiles and workflow files under --root", "agent": "A18", "script": "supply_image_lint",
    }


def test_dry_run_without_any_other_argument_succeeds(tmp_path):
    code, out, _ = run_script(tmp_path, "--dry-run")
    assert code == 0 and out["status"] == "ok" and out["agent"] == "A18" and out["script"] == "supply_image_lint"


# --- real scans -------------------------------------------------------------------------------------------------


def test_clean_repository_passes_with_exit_code_zero(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", CLEAN_DOCKERFILE)
    put(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    code, out = scan(tmp_path)
    assert code == 0 and out["status"] == "ok"
    assert out["files_scanned"] == {"dockerfiles": 1, "workflows": 1} and out["findings_total"] == 0 and out["findings"] == []
    assert out["skipped"] == [] and out["truncated"] is False and out["blocking_count"] == 0
    assert out["summary"] == "1 Dockerfile(s) and 1 workflow(s) scanned, 0 finding(s), 0 blocking"


def test_bad_repository_fails_with_located_findings_worst_first(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "svc/Dockerfile", BAD_DOCKERFILE)
    put(repo, ".github/workflows/ci.yml", BAD_WORKFLOW)
    put(repo, "svc/clean/Dockerfile.prod", CLEAN_DOCKERFILE)
    code, out = scan(tmp_path)
    assert code == 1 and out["status"] == "fail" and out["files_scanned"] == {"dockerfiles": 2, "workflows": 1}
    wf = ".github/workflows/ci.yml"
    assert rows(out) == [
        ("workflow.secret-in-build-arg", "blocker", f"{wf}:13"),
        ("workflow.secret-in-build-arg", "blocker", f"{wf}:15"),
        ("workflow.container-root", "major", wf),
        ("pin.action-not-sha", "major", f"{wf}:9"),
        ("docker.final-user-missing", "major", "svc/Dockerfile:1"),
        ("docker.latest-tag", "major", "svc/Dockerfile:1"),
        ("docker.secret-in-arg", "major", "svc/Dockerfile:2"),
        ("docker.pipe-to-shell", "major", "svc/Dockerfile:3"),
        ("pin.image-tag-only", "minor", f"{wf}:5"),
        ("pin.action-not-sha", "minor", f"{wf}:8"),
        ("docker.no-healthcheck", "minor", "svc/Dockerfile:1"),
    ]
    assert out["findings_total"] == 11 and out["blocking_count"] == 8
    assert out["summary"] == "2 Dockerfile(s) and 1 workflow(s) scanned, 11 finding(s), 8 blocking"
    assert all(set(f) == {"id", "severity", "summary", "location"} for f in out["findings"])


def test_only_minor_findings_keep_status_ok(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", "FROM alpine:3.20\nUSER app\n")
    code, out = scan(tmp_path)
    assert code == 0 and out["status"] == "ok"
    assert sorted(f["id"] for f in out["findings"]) == ["docker.base-not-digest-pinned", "docker.no-healthcheck"]
    assert out["blocking_count"] == 0


def test_dockerfile_without_from_is_a_major_finding(tmp_path):
    put(tmp_path / "repo", "Dockerfile", "# nothing here\nRUN echo hi\n")
    code, out = scan(tmp_path)
    assert code == 1 and rows(out) == [("docker.no-from", "major", "Dockerfile")]


def test_file_names_recognised_as_dockerfiles_and_workflows(tmp_path):
    repo = tmp_path / "repo"
    for name in ("Dockerfile", "api.Dockerfile", "Dockerfile.dev", "Containerfile"):
        put(repo, f"a/{name}", CLEAN_DOCKERFILE)
    put(repo, "a/notes.txt", BAD_DOCKERFILE)
    put(repo, "a/Dockerfile-notes.md", BAD_DOCKERFILE)
    put(repo, ".github/workflows/x.yaml", CLEAN_WORKFLOW)
    put(repo, "workflows/ci.yml", BAD_WORKFLOW)
    code, out = scan(tmp_path)
    assert code == 0 and out["files_scanned"] == {"dockerfiles": 4, "workflows": 1}


def test_out_of_scope_directories_are_pruned_and_build_dist_vendor_are_scanned(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", CLEAN_DOCKERFILE)
    for skipped in ("node_modules/pkg", ".git/hooks", ".venv/lib", "venv", "__pycache__", ".swarm", ".tox"):
        put(repo, f"{skipped}/Dockerfile", BAD_DOCKERFILE)
    code, out = scan(tmp_path)
    assert code == 0 and out["files_scanned"]["dockerfiles"] == 1 and out["skipped"] == []
    for scanned in ("build", "dist", "vendor"):  # build outputs and vendored trees can hold real images: never a silent pass
        put(repo, f"{scanned}/Dockerfile", BAD_DOCKERFILE)
    code, out = scan(tmp_path)
    assert code == 1 and out["status"] == "fail" and out["files_scanned"]["dockerfiles"] == 4
    assert {f["location"].split(":")[0].split("/")[0] for f in out["findings"]} == {"build", "dist", "vendor"}


def test_files_over_one_mebibyte_binaries_and_symlinks_are_skipped_not_followed(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    put(outside, "Dockerfile", BAD_DOCKERFILE)
    put(repo, "big/Dockerfile", "FROM alpine\n" + "# padding\n" * 120000)
    put(repo, "edge/Dockerfile", "FROM alpine\n" + "#" * (1024 * 1024 - len("FROM alpine\n")))
    (repo / "bin").mkdir()
    (repo / "bin" / "Dockerfile").write_bytes(b"FROM alpine\n\x00\x01\x02")
    (repo / "link").symlink_to(outside, target_is_directory=True)
    (repo / "file").mkdir()
    (repo / "file" / "Dockerfile").symlink_to(outside / "Dockerfile")
    put(repo, "ok/Dockerfile", CLEAN_DOCKERFILE)
    code, out = scan(tmp_path)
    assert (repo / "big" / "Dockerfile").stat().st_size > 1024 * 1024 and (repo / "edge" / "Dockerfile").stat().st_size == 1024 * 1024
    assert out["skipped"] == ["big/Dockerfile: over-1MiB", "bin/Dockerfile: binary", "file/Dockerfile: symlink", "link: symlink"]
    assert out["files_scanned"] == {"dockerfiles": 2, "workflows": 0}  # edge/ (exactly 1 MiB) and ok/ are read
    assert not any("outside" in f["location"] for f in out["findings"])
    # skipped inputs make the scan incomplete: its finding carries no location, so the location set gains the empty string
    assert code == 1 and {f["location"].split(":")[0] for f in out["findings"]} == {"edge/Dockerfile", ""}


def test_scan_stops_after_2000_files_and_says_so(tmp_path):
    repo = tmp_path / "repo"
    for i in range(2001):
        put(repo, f"d{i // 100:02d}/Dockerfile.{i:04d}", CLEAN_DOCKERFILE)
    code, out = scan(tmp_path)
    assert out["files_scanned"]["dockerfiles"] == 2000 and out["truncated"] is True and code == 1  # a truncated scan is incomplete, never ok
    assert rows(out) == [("scan/incomplete", "major", ""), ("scan.truncated", "info", "")]


def test_exactly_2000_files_are_not_a_truncation(tmp_path):
    repo = tmp_path / "repo"
    for i in range(2000):
        put(repo, f"d{i // 100:02d}/Dockerfile.{i:04d}", CLEAN_DOCKERFILE)
    code, out = scan(tmp_path)
    assert out["files_scanned"]["dockerfiles"] == 2000 and out["truncated"] is False and out["findings"] == []


def test_path_option_limits_the_scan_to_a_file_or_directory(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "a/Dockerfile", BAD_DOCKERFILE)
    put(repo, "b/Dockerfile", CLEAN_DOCKERFILE)
    code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", "b")
    assert code == 0 and out["files_scanned"]["dockerfiles"] == 1
    code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", "a/Dockerfile")
    assert code == 1 and out["files_scanned"]["dockerfiles"] == 1 and {f["location"].split(":")[0] for f in out["findings"]} == {"a/Dockerfile"}


def test_path_outside_root_or_missing_is_an_input_error_with_exit_code_two(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", CLEAN_DOCKERFILE)
    for path in ("..", "does-not-exist"):
        code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", path)
        assert code == 2 and out["status"] == "error" and out["error"]["code"] == "E-INPUT", path
    code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", "does-not-exist")
    assert "does not exist" in out["error"]["message"]


def test_empty_repository_is_incomplete_not_clean(tmp_path):
    (tmp_path / "repo").mkdir()
    code, out = scan(tmp_path)
    assert code == 1 and out["status"] == "fail" and out["files_scanned"] == {"dockerfiles": 0, "workflows": 0} and out["findings_total"] == 1
    assert rows(out) == [("scan/incomplete", "major", "")] and "no in-scope input was analysed" in out["findings"][0]["summary"]


# --- workflow rules (in-process) --------------------------------------------------------------------------------


def test_workflow_build_arg_secret_rules():
    module = load_script()
    text = """steps:
  - uses: docker/build-push-action@x
    with:
      build-args: |
        FROM_SECRET=${{ secrets.TOKEN }}
        PASSWORD=hunter2
        API_TOKEN=${{ vars.TOKEN }}
        VERSION=${{ github.sha }}
        - LISTED_SECRET=abc
      tags: x
  - run: |
      docker build --build-arg GH_TOKEN=${{ secrets.GITHUB_TOKEN }} --build-arg VERSION=1 --build-arg SECRET_KEY=abc .
      docker build --build-arg=DB_PASSWORD ./svc
  # - run: docker build --build-arg GH_TOKEN=${{ secrets.X }}
"""
    found = sorted((f["location"], f["id"], f["severity"]) for f in module.workflow_build_arg_findings(text, "w.yml"))
    assert found == sorted([
        ("w.yml:5", "workflow.secret-in-build-arg", "blocker"),  # fed from the secrets context
        ("w.yml:6", "workflow.secret-in-build-arg", "major"),  # literal value under a secret-like name
        ("w.yml:7", "workflow.secret-name-in-build-arg", "major"),  # secret-like name, value from vars
        ("w.yml:9", "workflow.secret-in-build-arg", "major"),  # list item form
        ("w.yml:12", "workflow.secret-in-build-arg", "blocker"),  # --build-arg NAME=${{ secrets.X }}
        ("w.yml:12", "workflow.secret-in-build-arg", "major"),  # --build-arg SECRET_KEY=abc
        ("w.yml:13", "workflow.secret-name-in-build-arg", "major"),  # --build-arg=DB_PASSWORD with no value
    ])
    assert module.workflow_build_arg_findings("steps:\n  - run: docker build --build-arg VERSION=1 .\n", "w.yml") == []


def test_workflow_container_option_rules_need_pyyaml():
    yaml = pytest.importorskip("yaml")
    module = load_script()
    text = """jobs:
  a:
    container:
      image: x@sha256:abc
      options: --user root --cpus 2
    services:
      db:
        image: postgres
        options: -u 0:0 --privileged
      cache:
        image: redis
        options: --health-cmd "redis-cli ping" --user 1000
  b:
    container: node:20
  c:
    container:
      options: --user=root
"""
    found = sorted((f["id"], f["summary"]) for f in module.workflow_option_findings(text, "w.yml", yaml))
    assert found == [
        ("workflow.container-privileged", "jobs.a.services.db runs privileged via options"),
        ("workflow.container-root", "jobs.a.container runs as root via options"),
        ("workflow.container-root", "jobs.a.services.db runs as root via options"),
        ("workflow.container-root", "jobs.c.container runs as root via options"),
    ]
    assert module.workflow_option_findings("jobs: {}\n", "w.yml", yaml) == []
    broken = module.workflow_option_findings("jobs: [unclosed\n  - x: : :\n", "w.yml", yaml)
    assert [f["id"] for f in broken] == ["workflow.unparseable"] and broken[0]["severity"] == "minor"


def test_missing_pyyaml_skips_only_the_yaml_check_and_says_so(tmp_path):
    module = load_script()
    module._load_yaml = lambda: None
    repo = tmp_path / "repo"
    put(repo, ".github/workflows/ci.yml", BAD_WORKFLOW)
    result = module.scan(repo.resolve(), repo.resolve())
    assert result["skipped"] == [".github/workflows/ci.yml: skipped:tool-missing:pyyaml (container options)"]
    assert result["tools"] == {"pyyaml": "skipped:tool-missing"}
    found = {f["id"] for f in result["findings"]}
    assert "workflow.container-root" not in found and {"workflow.secret-in-build-arg", "pin.action-not-sha", "pin.image-tag-only"} <= found
    assert result["status"] == "fail"


def test_large_workflow_skips_the_yaml_parse_but_keeps_text_checks(tmp_path):
    pytest.importorskip("yaml")
    module = load_script()
    repo = tmp_path / "repo"
    put(repo, ".github/workflows/big.yml", "# " + "x" * (300 * 1024) + "\n" + BAD_WORKFLOW)
    result = module.scan(repo.resolve(), repo.resolve())
    assert result["skipped"] == [".github/workflows/big.yml: skipped:yaml-too-large (container options)"]
    assert "pin.action-not-sha" in {f["id"] for f in result["findings"]} and "workflow.container-root" not in {f["id"] for f in result["findings"]}
    assert result["tools"] == {"pyyaml": "available"}


# --- independent review: nothing that looks like a credential is echoed ---------------------------------------------------------------

CANARY_PW = "Zq9canaryPW81"
CANARY_TOKEN = "ghp_Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78"


def test_scan_output_never_contains_credentials_from_dockerfiles_workflows_or_yaml_errors(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", f"FROM user:{CANARY_PW}@host.example/img\nARG NPM_TOKEN={CANARY_TOKEN}\nENV API_TOKEN={CANARY_TOKEN}\n"
                            f"RUN git clone https://ci:{CANARY_PW}@git.example.com/o/r.git\nADD https://ci:{CANARY_PW}@x.example/a.tgz /a\n")
    put(repo, "svc/Dockerfile.dev", f"FROM https://ci:{CANARY_PW}@evil.example/x\n")
    put(repo, ".github/workflows/ci.yml",
        f"jobs:\n  a:\n    env: [{CANARY_TOKEN} {CANARY_PW}\n    steps:\n      - run: docker build --build-arg API_TOKEN={CANARY_TOKEN} --build-arg DB_PASSWORD={CANARY_PW} .\n"
        f"      - uses: docker://ci:{CANARY_PW}@host.example/x\n")
    put(repo, ".github/workflows/short.yml", f"a: [{CANARY_PW}\n")
    code, doc, stderr = run_script(tmp_path, "--root", str(repo))
    assert code == 1 and doc["status"] == "fail"
    text = json.dumps(doc) + stderr
    assert CANARY_PW not in text and CANARY_TOKEN not in text
    ids = {f["id"] for f in doc["findings"]}
    assert {"docker.secret-in-env", "docker.secret-in-arg", "docker.credential-in-url", "workflow.secret-in-build-arg", "workflow.unparseable"} <= ids


def test_unparseable_workflow_reports_the_line_not_the_yaml_snippet():
    module = load_script()
    yaml = pytest.importorskip("yaml")
    broken = module.workflow_option_findings(f"a: 1\nb: [{CANARY_PW}\n", "w.yml", yaml)
    assert [f["id"] for f in broken] == ["workflow.unparseable"]
    assert CANARY_PW not in broken[0]["summary"] and "line 3" in broken[0]["summary"] and "ParserError" in broken[0]["summary"]


def test_boolean_valued_build_arg_names_are_not_secrets():
    module = load_script()
    assert module.workflow_build_arg_findings("      - run: docker build --build-arg ALLOW_EMPTY_PASSWORD=yes .\n", "w.yml") == []
    flagged = module.workflow_build_arg_findings("      - run: docker build --build-arg DB_PASSWORD=hunter2 .\n", "w.yml")
    assert [f["id"] for f in flagged] == ["workflow.secret-in-build-arg"] and "hunter2" not in json.dumps(flagged)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
def test_fifo_named_dockerfile_is_skipped_and_the_valid_dockerfile_is_still_linted(tmp_path):
    repo = tmp_path / "repo"
    (repo / "img").mkdir(parents=True)
    os.mkfifo(repo / "img" / "Dockerfile")  # opening a pipe would block forever
    put(repo, "img/Dockerfile.app", CLEAN_DOCKERFILE)
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "swarm-state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(repo), "--path", "img"], capture_output=True, text=True,
                          env=env, cwd=ROOT, check=False, timeout=60)
    out = json.loads(proc.stdout)
    assert proc.returncode == 1 and out["status"] == "fail" and [f["id"] for f in out["findings"]] == ["scan/incomplete"]  # unreadable is incomplete
    assert out["files_scanned"] == {"dockerfiles": 1, "workflows": 0}
    assert out["skipped"] == ["img/Dockerfile: unreadable"]


def test_inline_build_args_secret_gives_the_same_finding_as_the_block_form():
    module = load_script()
    inline = module.workflow_build_arg_findings("          build-args: NPM_TOKEN=${{ secrets.NPM_TOKEN }}\n", "w.yml")
    block = module.workflow_build_arg_findings("          build-args: |\n            NPM_TOKEN=${{ secrets.NPM_TOKEN }}\n", "w.yml")
    assert [(f["id"], f["severity"], f["summary"]) for f in inline] == [(f["id"], f["severity"], f["summary"]) for f in block]
    assert [(f["id"], f["severity"], f["location"]) for f in inline] == [("workflow.secret-in-build-arg", "blocker", "w.yml:1")]
    assert module.workflow_build_arg_findings("          build-args: VERSION=1\n", "w.yml") == []


def test_workflow_named_with_dot_dot_segments_is_linted_not_a_crash(tmp_path):
    repo = tmp_path / "repo"
    name = ".github/workflows/a\\..\\b.yml"  # a legal Linux file name; the CLI must not route it through pin_audit's payload-path rule
    put(repo, name, BAD_WORKFLOW)
    code, out = scan(tmp_path)
    assert code == 1 and out["files_scanned"] == {"dockerfiles": 0, "workflows": 1}
    assert {f["location"].split(":")[0] for f in out["findings"]} == {name}


def test_a_path_with_no_dockerfile_or_workflow_is_incomplete_not_clean(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "README.md", "# hi\n")
    code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", "README.md")
    assert code == 1 and out["status"] == "fail" and out["files_scanned"] == {"dockerfiles": 0, "workflows": 0}
    assert rows(out) == [("scan/incomplete", "major", "")]


def test_clean_workflow_without_pyyaml_is_incomplete_not_ok(tmp_path):
    module = load_script()
    module._load_yaml = lambda: None
    repo = tmp_path / "repo"
    put(repo, ".github/workflows/ci.yml", CLEAN_WORKFLOW)
    result = module.scan(repo.resolve(), repo.resolve())
    assert result["status"] == "fail" and [f["id"] for f in result["findings"]] == ["scan/incomplete"]
    assert "skipped:tool-missing:pyyaml" in result["findings"][0]["summary"]


def test_dockerfile_findings_beyond_the_listing_cap_are_flagged_not_silently_dropped(tmp_path):
    repo = tmp_path / "repo"
    put(repo, "Dockerfile", CLEAN_DOCKERFILE + "ADD src /a\n" * 250)
    code, out = scan(tmp_path)
    assert code == 0 and out["status"] == "ok"
    marker = [f for f in out["findings"] if f["id"] == "docker.findings-unlisted"]
    assert len(marker) == 1 and marker[0]["severity"] == "info" and "50 further finding(s)" in marker[0]["summary"]


def test_path_through_a_symlink_is_refused_with_an_input_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    put(tmp_path / "outside", "Dockerfile", BAD_DOCKERFILE)
    (repo / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    code, out, _ = run_script(tmp_path, "--root", str(repo), "--path", "link")
    assert code == 2 and out["status"] == "error" and out["error"]["code"] == "E-INPUT"
    assert "symlink" in out["error"]["message"]


def test_unparseable_workflow_is_incomplete_and_the_reason_names_the_file(tmp_path):
    pytest.importorskip("yaml")
    repo = tmp_path / "repo"
    put(repo, ".github/workflows/broken.yml", "jobs: [unclosed\n  - x: : :\n")
    code, out = scan(tmp_path)
    assert code == 1 and out["status"] == "fail"  # the container-privilege check never ran on this file
    assert out["skipped"] == [".github/workflows/broken.yml: skipped:unparseable-yaml (container options)"]
    assert {f["id"] for f in out["findings"]} == {"workflow.unparseable", "scan/incomplete"}
    assert "broken.yml" in next(f["summary"] for f in out["findings"] if f["id"] == "scan/incomplete")


def test_action_metadata_needs_no_pyyaml_and_a_local_dockerfile_image_is_no_finding(tmp_path):
    module = load_script()
    module._load_yaml = lambda: None
    repo = tmp_path / "repo"
    put(repo, ".github/actions/build/action.yml", "name: build\nruns:\n  using: docker\n  image: Dockerfile\n")
    result = module.scan(repo.resolve(), repo.resolve())
    assert result["status"] == "ok" and result["skipped"] == [] and result["findings"] == []
    assert result["files_scanned"] == {"dockerfiles": 0, "workflows": 1}


def test_a_workflow_named_action_yml_is_linted_as_a_workflow(tmp_path):
    pytest.importorskip("yaml")
    repo = tmp_path / "repo"
    put(repo, ".github/workflows/action.yml", "jobs:\n  build:\n    runs-on: ubuntu-latest\n    container:\n      image: alpine:latest\n"
                                             "      options: --privileged\n    steps:\n      - run: echo hi\n")
    code, out = scan(tmp_path)
    assert code == 1 and out["status"] == "fail"
    found = {(f["id"], f["severity"]) for f in out["findings"]}
    assert ("pin.image-latest", "major") in found and ("workflow.container-privileged", "major") in found


def test_new11_an_unreadable_directory_is_a_redacted_skip_and_no_credential_reaches_the_output(tmp_path, monkeypatch):
    import types

    module = load_script()
    repo = tmp_path / "repo"
    put(repo, "u:hunter2@host/Dockerfile", CLEAN_DOCKERFILE)
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if "hunter2" in str(path):
            raise PermissionError(13, "Permission denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    out = module.scan(repo.resolve(), repo.resolve())
    assert out["status"] == "fail"
    assert "***@host: unreadable:PermissionError" in out["skipped"]
    assert "hunter2" not in json.dumps(out)
    result = module.run(types.SimpleNamespace(path="."), types.SimpleNamespace(root=repo.resolve(), dry_run=False))
    assert result["status"] == "fail" and "hunter2" not in json.dumps(result)


def test_new11_any_other_failure_in_run_is_a_redacted_fail_result_not_a_traceback(tmp_path, monkeypatch):
    import types

    module = load_script()
    repo = tmp_path / "repo"
    repo.mkdir()

    def boom(root, relative):
        raise PermissionError(13, "Permission denied", "u:hunter2@host/x")

    monkeypatch.setattr(module, "_no_symlink_on_path", boom)
    result = module.run(types.SimpleNamespace(path="."), types.SimpleNamespace(root=repo.resolve(), dry_run=False))
    assert result["status"] == "fail" and "hunter2" not in json.dumps(result)
    assert [f["id"] for f in result["findings"]] == ["scan/incomplete"] and "PermissionError" in result["summary"]


def test_new15_an_invalid_task_id_is_not_echoed_to_stdout_or_the_events_log(tmp_path, monkeypatch, capsys):
    module = load_script()
    state = tmp_path / "swarm-state"
    monkeypatch.setenv("SWARM_DIR", str(state))
    script = module.AgentScript("A18", "supply_image_lint", module.run, description=module.__doc__, add_args=module.add_args)
    rc = script.main(["--task-id", "u:hunter2@host", "--root", str(tmp_path / "repo")])
    shown = capsys.readouterr()
    assert rc == 2 and "hunter2" not in shown.out + shown.err
    assert "invalid task id: 14 characters, contains a character outside [A-Za-z0-9._-]" in shown.out
    events = (state / "events.jsonl").read_text(encoding="utf-8")
    assert "supply_image_lint.error" in events and "hunter2" not in events
    rc = script.main(["--json", "--task-id", "u:hunter2@host", "--root", str(tmp_path / "repo")])
    assert rc == 2 and "hunter2" not in capsys.readouterr().out
