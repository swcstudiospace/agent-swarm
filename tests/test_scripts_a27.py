"""A27 scripts/audit_findings_check.py: dry-run contract, every rule on fixture trees, bounds, YAML handling, exit codes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT, run_script

SCRIPT = "audit_findings_check.py"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BUN = shutil.which("bun") or "/root/.local/share/reflex/bun/bin/bun"


def good(**patch) -> dict:
    base = {
        "id": "A-1", "title": "Reentrancy in withdraw", "severity": "high", "category": "SWC-107", "location": "src/Vault.sol:88",
        "description": "state written after the external call", "recommendation": "apply checks-effects-interactions", "status": "fixed",
        "poc": "test/Reentrancy.t.sol::testReenter", "fix_commit": COMMIT, "regression_test": True, "reaudited": True,
    }
    base.update(patch)
    return {k: v for k, v in base.items() if v is not None}


class Repo:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "repo"
        self.root.mkdir()
        self.state = tmp_path / "state"
        (self.root / "test").mkdir()
        (self.root / "test" / "Reentrancy.t.sol").write_text("// proof of concept\n", encoding="utf-8")

    def write(self, relative: str, content) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def run(self, *args: str, env: dict | None = None):
        result = run_script(SCRIPT, "--root", str(self.root), "--json", *args, env={"SWARM_DIR": str(self.state), **(env or {})})
        return result.returncode, json.loads(result.stdout), result

    def check(self, findings, name="audit/findings.json", **kwargs):
        self.write(name, {"findings": findings} if isinstance(findings, list) else findings)
        return self.run(**kwargs)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def kinds(data: dict) -> dict:
    """kind -> severity"""
    return {f["kind"]: f["severity"] for f in data["findings"]}


# --- dry run --------------------------------------------------------------------------------------------------
def test_dry_run_contract_reads_nothing(tmp_path):
    result = run_script(SCRIPT, "--dry-run", "--json", "--root", str(tmp_path / "does-not-exist"), env={"SWARM_DIR": str(tmp_path / "state")})
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout) == {
        "status": "ok", "dry_run": True, "findings": [], "summary": "dry-run: audit_findings_check would validate findings files under --root",
        "files_checked": 0, "files_skipped": [], "findings_total": 0, "by_category": {}, "by_severity": {}, "by_status": {}, "truncated": False,
        "agent": "A27", "script": "audit_findings_check",
    }
    assert not (tmp_path / "state").exists() or not any((tmp_path / "state").rglob("*.json"))


@pytest.mark.skipif(not Path(BUN).exists(), reason="bun not installed")
def test_bun_twin_dry_run_matches(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "state")}
    result = subprocess.run([BUN, str(ROOT / "scripts" / "ts" / "audit_findings_check.ts"), "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True, env=env)
    data = json.loads(result.stdout)
    assert result.returncode == 0 and data["status"] == "ok" and data["agent"] == "A27" and data["script"] == "audit_findings_check"


# --- clean file and aggregation -------------------------------------------------------------------------------
def test_clean_file_passes_and_aggregates_by_category_severity_and_status(repo):
    rc, data, _ = repo.check([
        good(),
        good(id="A-2", severity="medium", category="SWC-104", status="open", poc=None, fix_commit=None, title="Unchecked call return"),
        good(id="A-3", severity="low", category="SWC-104", status="acknowledged", poc=None, fix_commit=None, title="Low note", location="src/Other.sol:3"),
    ])
    assert rc == 0 and data["status"] == "ok" and data["findings"] == []
    assert data["files_checked"] == 1 and data["findings_total"] == 3 and data["files_skipped"] == [] and data["truncated"] is False
    assert data["by_category"] == {
        "SWC-104": {"total": 2, "open": 1, "by_severity": {"low": 1, "medium": 1}},
        "SWC-107": {"total": 1, "open": 0, "by_severity": {"high": 1}},
    }
    assert data["by_severity"] == {"high": 1, "low": 1, "medium": 1} and data["by_status"] == {"acknowledged": 1, "fixed": 1, "open": 1}
    assert data["summary"] == "3 finding(s) in 1 file(s): no major or worse problem" and data["agent"] == "A27" and data["script"] == "audit_findings_check"


def test_top_level_list_is_accepted_as_well_as_an_object(repo):
    repo.write("audit/findings.json", [good()])
    rc, data, _ = repo.run()
    assert rc == 0 and data["findings_total"] == 1


def test_no_findings_file_leaves_nothing_analysed_so_the_scan_is_incomplete(repo):
    rc, data, _ = repo.run()
    assert rc == 1 and data["status"] == "fail" and kinds(data) == {"no-findings-file": "info", "scan-incomplete": "major"} and data["files_checked"] == 0


# --- required fields and identity -----------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["id", "title", "severity", "category", "location", "description", "recommendation", "status"])
def test_each_required_field_is_enforced(repo, field):
    rc, data, _ = repo.check([good(**{field: None}, id="A-1") if field != "id" else good(id=None)])
    assert rc == 1 and data["status"] == "fail"
    assert any(f["kind"] == "missing-field" and f["severity"] == "major" and f"'{field}'" in f["summary"] for f in data["findings"])


def test_blank_required_field_counts_as_missing(repo):
    rc, data, _ = repo.check([good(description="   ")])
    assert rc == 1 and kinds(data)["missing-field"] == "major"


def test_duplicate_ids_are_major(repo):
    rc, data, _ = repo.check([good(), good(title="Another")])
    assert rc == 1 and kinds(data)["duplicate-id"] == "major"


def test_unknown_severity_and_status_are_major(repo):
    rc, data, _ = repo.check([good(severity="blocker", status="done")])
    assert rc == 1 and kinds(data) == {"invalid-severity": "major", "invalid-status": "major"}
    assert data["by_severity"] == {"invalid": 1} and data["by_status"] == {"invalid": 1}


def test_non_object_finding_is_reported(repo):
    rc, data, _ = repo.check([good(), "oops"])
    assert rc == 1 and kinds(data)["bad-finding"] == "major"


# --- severity consistency -------------------------------------------------------------------------------------
@pytest.mark.parametrize("impact,likelihood,expected", [("low", "low", "info"), ("high", "high", "critical"), ("high", "low", "medium"), ("medium", "high", "high")])
def test_severity_must_match_the_impact_likelihood_matrix(repo, impact, likelihood, expected):
    rc, data, _ = repo.check([good(severity=expected, impact=impact, likelihood=likelihood, status="open", poc=None, fix_commit=None)])
    assert "severity-mismatch" not in kinds(data)


def test_severity_mismatch_is_major_unless_the_override_is_approved(repo):
    rc, data, _ = repo.check([good(impact="medium", likelihood="low")])  # computed low, declared high
    assert rc == 1 and kinds(data)["severity-mismatch"] == "major"
    message = next(f["summary"] for f in data["findings"] if f["kind"] == "severity-mismatch")
    assert "impact x likelihood = low" in message and "L3" in message
    rc, data, _ = repo.check([good(impact="medium", likelihood="low", severity_override_approved=True)])
    assert rc == 0 and data["findings"] == []
    rc, data, _ = repo.check([good(impact="medium", likelihood="low", severity_override_approved="yes")])
    assert rc == 1 and kinds(data)["severity-mismatch"] == "major"


def test_incomplete_or_invalid_scoring_is_minor(repo):
    rc, data, _ = repo.check([good(impact="high")])
    assert rc == 0 and kinds(data) == {"incomplete-scoring": "minor"}
    rc, data, _ = repo.check([good(impact="huge", likelihood="low")])
    assert rc == 0 and kinds(data) == {"invalid-scoring": "minor"}


# --- proof of concept -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("severity", ["critical", "high"])
def test_high_and_critical_need_a_poc(repo, severity):
    rc, data, _ = repo.check([good(severity=severity, poc=None)])
    assert rc == 1 and kinds(data)["poc-missing"] == "major"


def test_medium_without_a_poc_is_fine(repo):
    rc, data, _ = repo.check([good(severity="medium", poc=None)])
    assert rc == 0 and data["findings"] == []


def test_poc_must_exist_under_the_root_with_optional_test_suffix(repo):
    rc, data, _ = repo.check([good(poc="test/Reentrancy.t.sol")])
    assert rc == 0
    rc, data, _ = repo.check([good(poc="test/Reentrancy.t.sol#testReenter")])
    assert rc == 0
    rc, data, _ = repo.check([good(poc="test/Missing.t.sol::testX")])
    assert rc == 1 and kinds(data)["poc-not-found"] == "major"
    rc, data, _ = repo.check([good(severity="medium", poc="test/Missing.t.sol")])
    assert rc == 0 and kinds(data)["poc-not-found"] == "minor"


def test_poc_path_escaping_the_root_is_refused(repo, tmp_path):
    (tmp_path / "outside.t.sol").write_text("x", encoding="utf-8")
    rc, data, _ = repo.check([good(poc="../outside.t.sol")])
    assert rc == 1 and kinds(data)["poc-outside-root"] == "major"
    rc, data, _ = repo.check([good(poc=str(tmp_path / "outside.t.sol"))])
    assert rc == 1 and kinds(data)["poc-outside-root"] == "major"


def test_poc_symlink_leading_out_of_the_root_is_refused(repo, tmp_path):
    (tmp_path / "secret.sol").write_text("x", encoding="utf-8")
    (repo.root / "test" / "link.t.sol").symlink_to(tmp_path / "secret.sol")
    rc, data, _ = repo.check([good(poc="test/link.t.sol")])
    assert rc == 1 and kinds(data)["poc-outside-root"] == "major"


# --- fix status -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["fixed", "mitigated"])
def test_fixed_or_mitigated_need_a_fix_commit(repo, status):
    rc, data, _ = repo.check([good(status=status, fix_commit=None)])
    assert rc == 1 and kinds(data)["fix-commit-missing"] == "major"


@pytest.mark.parametrize("commit,valid", [("abc1234", True), ("abc123", False), ("a" * 40, True), ("a" * 41, False), ("a" * 64, True), ("xyz1234", False)])
def test_fix_commit_must_be_a_git_object_id(repo, commit, valid):
    rc, data, _ = repo.check([good(fix_commit=commit)])
    assert ("fix-commit-invalid" not in kinds(data)) is valid and (rc == 0) is valid


def test_serious_fix_without_regression_test_or_reaudit_is_major(repo):
    rc, data, _ = repo.check([good(regression_test=False)])
    assert rc == 1 and kinds(data) == {"fix-unverified": "major"}  # the fix tool rates this unverified fix as failing too
    rc, data, _ = repo.check([good(severity="critical", regression_test=False)])
    assert rc == 1 and kinds(data) == {"fix-unverified": "major"}  # a critical fix is never reported ok without verification
    rc, data, _ = repo.check([good(reaudited=None)])
    assert rc == 1 and kinds(data) == {"fix-unverified": "major"}
    rc, data, _ = repo.check([good(severity="medium", regression_test=False, reaudited=False)])
    assert rc == 0 and data["status"] == "ok" and kinds(data) == {"fix-unverified": "minor"}


def test_open_critical_is_a_blocker_and_open_high_is_major(repo):
    rc, data, _ = repo.check([good(severity="critical", status="open", fix_commit=None)])
    assert rc == 1 and kinds(data) == {"open-blocking": "blocker"}
    rc, data, _ = repo.check([good(severity="high", status="open", fix_commit=None)])
    assert rc == 1 and kinds(data) == {"open-high": "major"}
    rc, data, _ = repo.check([good(severity="medium", status="open", fix_commit=None)])
    assert rc == 0 and data["findings"] == []


@pytest.mark.parametrize("status", ["acknowledged", "wontfix"])
def test_accepting_a_serious_finding_needs_a_risk_acceptance_reference(repo, status):
    rc, data, _ = repo.check([good(status=status, fix_commit=None)])
    assert rc == 1 and kinds(data) == {"risk-acceptance-missing": "major"}
    assert "L4" in data["findings"][0]["summary"]
    rc, data, _ = repo.check([good(status=status, fix_commit=None, risk_acceptance="RISK-17")])
    assert rc == 0 and kinds(data) == {"risk-accepted": "info"}
    rc, data, _ = repo.check([good(severity="low", status=status, fix_commit=None)])
    assert rc == 0 and data["findings"] == []


# --- duplicates -----------------------------------------------------------------------------------------------
def test_possible_duplicates_are_minor(repo):
    rc, data, _ = repo.check([good(id="A-1"), good(id="A-2", title="reentrancy IN withdraw", location="src/Vault.sol:90")])
    assert rc == 0 and kinds(data) == {"possible-duplicate": "minor"}
    assert data["findings"][0]["summary"] == "A-1 duplicates A-2"
    rc, data, _ = repo.check([good(id="A-1"), good(id="A-2", title="Reentrancy in withdraw", location="src/Other.sol:90")])
    assert data["findings"] == []


def test_duplicate_detection_notes_when_it_only_covered_the_first_200(repo):
    findings = [good(id=f"F-{i:03d}", title=f"unique title number {i} alpha{i}", location=f"src/F{i}.sol:1", severity="low", status="open", poc=None, fix_commit=None) for i in range(201)]
    rc, data, _ = repo.check(findings)
    assert rc == 1 and kinds(data) == {"dedupe-truncated": "info", "scan-incomplete": "major"} and data["findings_total"] == 201  # a truncated comparison is unanalysed input


def test_a_duplicate_pair_beyond_the_dedupe_window_is_reported_not_silently_missed(repo):
    findings = [good(id="F-000", title="shared title", location="src/Vault.sol:1", severity="low", status="open", poc=None, fix_commit=None)]
    findings += [good(id=f"F-{i:03d}", title=f"unique title number {i} alpha{i}", location=f"src/F{i}.sol:1", severity="low", status="open", poc=None, fix_commit=None) for i in range(1, 200)]
    findings.append(good(id="F-200", title="shared title", location="src/Vault.sol:2", severity="low", status="open", poc=None, fix_commit=None))
    rc, data, _ = repo.check(findings)
    assert rc == 1 and data["status"] == "fail" and kinds(data) == {"dedupe-truncated": "info", "scan-incomplete": "major"}
    assert "dedupe-truncated: audit/findings.json" in next(f["summary"] for f in data["findings"] if f["kind"] == "scan-incomplete")


# --- malformed documents --------------------------------------------------------------------------------------
def test_invalid_json_is_unparseable_major(repo):
    repo.write("audit/findings.json", "{not json")
    rc, data, _ = repo.run()
    assert rc == 1 and kinds(data) == {"unparseable": "major", "scan-incomplete": "major"} and data["files_checked"] == 1


@pytest.mark.parametrize("document", [{"issues": []}, "text", 3, {"findings": {"a": 1}}])
def test_wrong_document_shape_is_major(repo, document):
    repo.write("audit/findings.json", json.dumps(document))
    rc, data, _ = repo.run()
    assert rc == 1 and kinds(data) == {"bad-shape": "major", "scan-incomplete": "major"}


# --- discovery and bounds -------------------------------------------------------------------------------------
def test_only_findings_named_json_and_yaml_files_are_scanned_and_skip_dirs_are_pruned(repo):
    repo.write("audit/findings.json", {"findings": [good()]})
    repo.write("audit/notes.json", "{broken")
    repo.write("audit/FINDINGS-final.JSON", {"findings": [good(id="B-1")]})
    repo.write("node_modules/pkg/findings.json", "{broken")
    repo.write(".git/findings.json", "{broken")
    rc, data, _ = repo.run()
    assert rc == 0 and data["files_checked"] == 2 and data["findings_total"] == 2


def test_path_selects_one_file_even_without_findings_in_its_name(repo):
    repo.write("audit/report.json", {"findings": [good(severity="critical", poc=None)]})
    repo.write("audit/findings.json", {"findings": [good()]})
    rc, data, _ = repo.run("--path", "audit/report.json")
    assert rc == 1 and data["files_checked"] == 1 and kinds(data) == {"poc-missing": "major"}
    rc, data, _ = repo.run("--path", "audit")
    assert rc == 0 and data["files_checked"] == 1


def test_path_outside_the_root_or_missing_is_an_input_error(repo):
    result = run_script(SCRIPT, "--root", str(repo.root), "--json", "--path", "../x", env={"SWARM_DIR": str(repo.state)})
    assert result.returncode == 2 and json.loads(result.stdout)["error"]["code"] == "E-INPUT"
    result = run_script(SCRIPT, "--root", str(repo.root), "--json", "--path", "nope", env={"SWARM_DIR": str(repo.state)})
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "error"


def test_files_over_one_mebibyte_are_skipped_not_parsed(repo):
    big = {"findings": [good()], "padding": "x" * (1 << 20)}
    repo.write("audit/findings.json", big)
    rc, data, _ = repo.run()
    assert rc == 1 and data["files_checked"] == 0 and data["files_skipped"] == ["skipped:too-large: audit/findings.json"] and kinds(data) == {"scan-incomplete": "major"}
    exact = repo.write("audit/findings.json", {"findings": [good()]})
    exact.write_text(exact.read_text(encoding="utf-8") + " " * ((1 << 20) - exact.stat().st_size), encoding="utf-8")
    assert exact.stat().st_size == 1 << 20
    rc, data, _ = repo.run()
    assert rc == 0 and data["files_checked"] == 1 and data["files_skipped"] == []  # exactly 1 MiB is still read


def test_binary_files_are_skipped(repo):
    (repo.root / "audit").mkdir()
    (repo.root / "audit" / "findings.json").write_bytes(b"\x00\x01\x02 binary")
    rc, data, _ = repo.run()
    assert rc == 1 and data["files_skipped"] == ["skipped:binary: audit/findings.json"] and data["files_checked"] == 0 and kinds(data) == {"scan-incomplete": "major"}


def test_symlinks_are_never_followed(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "findings.json").write_text(json.dumps({"findings": [good(severity="critical", poc=None)]}), encoding="utf-8")
    (repo.root / "linkdir").symlink_to(outside, target_is_directory=True)
    (repo.root / "linkfile-findings.json").symlink_to(outside / "findings.json")
    rc, data, _ = repo.run()
    assert rc == 1 and data["files_checked"] == 0 and kinds(data) == {"no-findings-file": "info", "scan-incomplete": "major"}
    reason = next(f["summary"] for f in data["findings"] if f["kind"] == "scan-incomplete")
    assert "skipped:symlink: linkdir" in reason and "skipped:symlink: linkfile-findings.json" in reason
    result = run_script(SCRIPT, "--root", str(repo.root), "--json", "--path", "linkfile-findings.json", env={"SWARM_DIR": str(repo.state)})
    assert result.returncode == 2  # an explicit path that resolves outside the root is refused


def test_a_path_that_is_or_passes_through_an_in_root_symlink_is_refused(repo):
    (repo.root / "audit").mkdir()
    (repo.root / "audit" / "findings.json").write_text(json.dumps({"findings": [good()]}), encoding="utf-8")
    (repo.root / "alias.json").symlink_to(repo.root / "audit" / "findings.json")
    (repo.root / "alias-dir").symlink_to(repo.root / "audit", target_is_directory=True)
    for path in ("alias.json", "alias-dir", "alias-dir/findings.json"):
        result = run_script(SCRIPT, "--root", str(repo.root), "--json", "--path", path, env={"SWARM_DIR": str(repo.state)})
        assert result.returncode == 2 and json.loads(result.stdout)["error"]["code"] == "E-INPUT", path


def test_at_most_2000_files_are_visited(repo):
    root = repo.root / "bulk"
    root.mkdir()
    for index in range(2000):
        (root / f"f{index:04d}.txt").write_text("x", encoding="utf-8")
    (root / "zzz-findings.json").write_text(json.dumps({"findings": [good()]}), encoding="utf-8")
    rc, data, _ = repo.run("--path", "bulk")
    assert data["truncated"] is True and data["files_checked"] == 0 and data["status"] == "fail" and kinds(data)["scan-incomplete"] == "major"  # the 2001st file is never reached, so the scan is incomplete
    (root / "f0000.txt").unlink()
    rc, data, _ = repo.run("--path", "bulk")
    assert data["truncated"] is False and data["files_checked"] == 1  # exactly 2000 files are still all visited


# --- YAML -----------------------------------------------------------------------------------------------------
YAML_DOC = """findings:
  - id: A-1
    title: Reentrancy in withdraw
    severity: high
    category: SWC-107
    location: src/Vault.sol:88
    description: state written after call
    recommendation: use checks-effects-interactions
    status: open
"""


def test_yaml_findings_are_checked_when_pyyaml_is_available(repo):
    pytest.importorskip("yaml")
    repo.write("audit/findings.yaml", YAML_DOC)
    rc, data, _ = repo.run()
    assert rc == 1 and data["files_checked"] == 1 and set(kinds(data)) == {"poc-missing", "open-high"}
    repo.write("audit/findings.yaml", "findings: [unclosed")
    rc, data, _ = repo.run()
    assert rc == 1 and kinds(data) == {"unparseable": "major", "scan-incomplete": "major"}


def test_yaml_without_pyyaml_is_skipped_as_tool_missing_while_json_still_runs(repo, tmp_path):
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "yaml.py").write_text("raise ImportError('PyYAML is not installed')\n", encoding="utf-8")
    repo.write("audit/findings.yaml", YAML_DOC)
    repo.write("audit/findings.json", {"findings": [good(severity="critical", poc=None)]})
    rc, data, _ = repo.run(env={"PYTHONPATH": str(shim)})
    assert data["files_skipped"] == ["skipped:tool-missing: audit/findings.yaml (yaml needs PyYAML)"]
    assert rc == 1 and data["files_checked"] == 1 and kinds(data) == {"poc-missing": "major", "scan-incomplete": "major"}  # JSON handling is native; the skipped YAML keeps the scan incomplete


# --- ordering and exit codes ----------------------------------------------------------------------------------
def test_findings_are_sorted_by_severity_then_id_and_status_is_fail_on_major_only(repo):
    rc, data, _ = repo.check([
        good(id="B", severity="critical", status="open", poc=None, fix_commit=None),
        good(id="A", regression_test=False, title="Different bug", category="SWC-104", location="src/Other.sol:2"),
    ])
    assert rc == 1 and data["status"] == "fail"
    assert [f["severity"] for f in data["findings"]] == ["blocker", "major", "major"]
    assert data["findings"][0]["id"] == "open-blocking:audit/findings.json:B" and data["findings"][0]["location"] == "audit/findings.json"
    only_minor = repo.check([good(severity="medium", poc="test/Missing.t.sol")])
    assert only_minor[0] == 0 and only_minor[1]["status"] == "ok" and only_minor[1]["findings"][0]["severity"] == "minor"


def test_two_runs_give_identical_output(repo):
    repo.check([good(severity="critical", status="open", poc=None, fix_commit=None), good(id="A-2", status="fixed", fix_commit=None)])
    first = repo.run()[2].stdout
    assert repo.run()[2].stdout == first


# --- independent review regressions -------------------------------------------------------------------------------
@pytest.mark.parametrize("poc", ["a\x00b", "x" * 5000, "x/" * 3000, "test/" + "n" * 300 + ".t.sol"])
def test_unusable_poc_reference_is_a_finding_not_a_crash(repo, poc):
    rc, data, result = repo.check([good(poc=poc)])
    assert rc == 1 and data["status"] == "fail" and kinds(data) == {"poc-not-found": "major"}, result.stdout[:400]
    rc, data, _ = repo.check([good(severity="medium", poc=poc)])
    assert rc == 0 and kinds(data) == {"poc-not-found": "minor"}


SCRIPT_CANARIES = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "ghp": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "hex64": "ab12cd34" * 8,
    "mnemonic": "act add age ago aim air all and any arm art ask",
    "url": "https://u:hunter2secret@h.example/x",
}


def _leaks(data: dict, secret: str) -> list[str]:
    blob = json.dumps(data, ensure_ascii=False)
    pieces = [secret, "0x" + secret]
    if "@" in secret:
        pieces += ["hunter2secret"]
    if " " in secret:
        pieces += [" ".join(secret.split()[:8])]
    return [piece for piece in pieces if piece in blob]


@pytest.mark.parametrize("kind", sorted(SCRIPT_CANARIES))
def test_secret_values_in_finding_fields_are_never_echoed(repo, kind):
    secret = SCRIPT_CANARIES[kind]
    rc, data, result = repo.check([
        good(id=secret, severity="high", status="open", poc=secret, fix_commit=None),
        good(id="B", severity=secret, status=secret, category=secret, title=secret, location=f"{secret}:1", poc=secret, fix_commit=secret),
        good(id="C", status="fixed", fix_commit=secret, poc=secret),
        good(id="D", status="acknowledged", risk_acceptance=secret, poc=secret),
        good(id="E", impact=secret, likelihood="low", poc=secret),
    ])
    assert rc == 1 and data["findings"], result.stdout[:300]
    assert _leaks(data, secret) == [], (_leaks(data, secret), result.stdout[:500])
    assert _leaks({"stderr": result.stderr}, secret) == []


@pytest.mark.parametrize("kind", sorted(SCRIPT_CANARIES))
def test_secret_values_in_a_malformed_findings_file_are_never_echoed(repo, kind):
    secret = SCRIPT_CANARIES[kind]
    pytest.importorskip("yaml")
    repo.write("audit/findings.yaml", f"findings: [{secret}, unclosed")
    repo.write("audit/other-findings.json", f'{{"findings": [ {secret} ')
    rc, data, result = repo.run()
    assert rc == 1 and {f["kind"] for f in data["findings"]} == {"unparseable", "scan-incomplete"} and len(data["findings"]) == 3
    assert _leaks(data, secret) == [] and _leaks({"stderr": result.stderr}, secret) == []
    assert any("line" in f["summary"] for f in data["findings"])


# --- discovery: a named pipe named like a findings file is never opened ----------------------------------------------------
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_named_like_a_findings_file_is_skipped_and_the_valid_file_is_still_read(repo):
    (repo.root / "audit").mkdir()
    os.mkfifo(repo.root / "audit" / "audit-findings.json")  # an open() on this blocks forever with no writer
    repo.write("audit/findings.json", {"findings": [good()]})
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / SCRIPT), "--root", str(repo.root), "--json", "--path", "audit"],
        capture_output=True, text=True, timeout=60, cwd=ROOT, env={**os.environ, "SWARM_DIR": str(repo.state)},
    )
    data = json.loads(result.stdout)
    assert result.returncode == 1 and data["files_checked"] == 1 and data["findings_total"] == 1 and {f["kind"] for f in data["findings"]} == {"scan-incomplete"}  # the FIFO is skipped, so the scan is incomplete


def test_an_unlistable_subdirectory_is_an_incomplete_scan_that_names_it(repo, monkeypatch):
    import importlib
    import types

    repo.write("audit/findings.json", {"findings": [good()]})
    repo.write("locked/findings.json", {"findings": [good(id="C-1", severity="critical", status="open", fix_commit=None)]})
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    afc = importlib.import_module("audit_findings_check")
    real_scandir = os.scandir

    def unlistable(path="."):
        if str(path).endswith("locked"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", unlistable)
    result = afc.run(types.SimpleNamespace(path="."), types.SimpleNamespace(root=repo.root, dry_run=False))
    assert result["status"] == "fail" and result["files_checked"] == 1
    assert {f["kind"]: f["severity"] for f in result["findings"]} == {"scan-incomplete": "major"}  # the critical in locked/ is never read, and the scan says so
    assert "scan-error: locked (PermissionError)" in result["findings"][0]["summary"]


def test_a_mitigated_critical_with_a_verified_fix_is_residual_risk_that_fails_like_fix_verification(repo):
    from swarm.tools import a27_tools

    rc, data, _ = repo.check([good(id="M-1", severity="critical", status="mitigated", fix_commit=COMMIT, regression_test=True, reaudited=True)])
    assert rc == 1 and data["status"] == "fail" and kinds(data) == {"residual-risk": "major"}
    tool = a27_tools.fix_verification({"findings": [{"id": "M-1", "severity": "critical", "fix_status": "mitigated", "fix_commit": COMMIT, "regression_test": True, "reaudited": True}]})
    assert tool["verdict"] == "fail" and {f["id"]: f["severity"] for f in tool["findings"]} == {"FIX-M-1": "critical"}


def test_a_path_that_climbs_out_of_an_in_root_symlink_is_refused_not_read(repo):
    (repo.root / "sub").mkdir()
    repo.write("findings.json", {"findings": [good(id="C-1", severity="critical", status="open", fix_commit=None)]})
    (repo.root / "linkdir").symlink_to(repo.root / "sub", target_is_directory=True)
    result = run_script(SCRIPT, "--root", str(repo.root), "--json", "--path", "linkdir/../findings.json", env={"SWARM_DIR": str(repo.state)})
    assert result.returncode == 2 and json.loads(result.stdout)["error"]["code"] == "E-INPUT"


def test_build_and_dist_are_scanned_while_silent_cache_directories_stay_silent(repo):
    repo.write("audit/findings.json", {"findings": [good()]})
    for name in ("build", "dist"):
        repo.write(f"{name}/findings.json", {"findings": [good(id=f"{name.upper()}-1", severity="critical", status="open", fix_commit=None)]})
    silent = (".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".swarm", ".terraform")
    for index, name in enumerate(silent):
        repo.write(f"{name}/findings.json", {"findings": [good(id=f"SILENT-{index}", severity="critical", status="open", fix_commit=None)]})
    rc, data, _ = repo.run()
    assert rc == 1 and data["status"] == "fail" and data["files_checked"] == 3
    assert {f["id"] for f in data["findings"]} == {"open-blocking:build/findings.json:BUILD-1", "open-blocking:dist/findings.json:DIST-1"}
    assert "scan-incomplete" not in kinds(data)  # build and dist are analysed, so nothing is incomplete
    assert not any("SILENT-" in f["id"] or "SILENT-" in f["summary"] for f in data["findings"])  # the silent trees are never read


def test_a_symlink_named_like_a_silent_directory_is_still_a_reason(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "findings.json").write_text(json.dumps({"findings": [good(id="O-1", severity="critical", status="open", fix_commit=None)]}), encoding="utf-8")
    repo.write("audit/findings.json", {"findings": [good()]})
    (repo.root / "node_modules").symlink_to(outside, target_is_directory=True)
    rc, data, _ = repo.run()
    reason = next(f["summary"] for f in data["findings"] if f["kind"] == "scan-incomplete")
    assert rc == 1 and data["status"] == "fail" and data["files_checked"] == 1
    assert "skipped:symlink: node_modules" in reason
    assert not any("O-1" in f["id"] or "O-1" in f["summary"] for f in data["findings"])


def test_an_unverified_medium_fix_is_a_finding_in_the_cli_as_in_fix_verification(repo):
    from swarm.tools import a27_tools

    rc, data, _ = repo.check([good(id="M-2", severity="medium", poc=None, regression_test=False, reaudited=False)])
    assert rc == 0 and data["status"] == "ok" and kinds(data) == {"fix-unverified": "minor"}
    tool = a27_tools.fix_verification({"findings": [{"id": "M-2", "severity": "medium", "fix_status": "fixed", "fix_commit": COMMIT, "regression_test": False, "reaudited": False}]})
    assert tool["verdict"] == "warn" and {f["id"]: f["severity"] for f in tool["findings"]} == {"FIX-M-2": "medium"}
