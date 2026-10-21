"""Behaviour tests for scripts/event_schema_check.py (A19): schema compatibility plus AsyncAPI/CloudEvents/Avro scan.

Fixture trees are built in tmp_path. The CLI is run as a subprocess (real exit codes, JSON-only stdout); the PyYAML-missing
path runs in-process because the script reports `skipped:tool-missing` when its `_yaml_module()` finds no PyYAML.
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
SCRIPT = ROOT / "scripts" / "event_schema_check.py"

MSG = {"payload": {"type": "object"}, "contentType": "application/json"}
ASYNCAPI = {"asyncapi": "3.0.0", "info": {"title": "Orders", "version": "1.0.0"},
            "channels": {"orders": {"address": "orders", "messages": {"created": MSG}}},
            "operations": {"publish": {"action": "send", "channel": {"$ref": "#/channels/orders"}}}}
EVENT = {"specversion": "1.0", "id": "evt-1", "source": "/orders", "type": "com.acme.order.created",
         "time": "2026-10-20T10:00:00Z", "datacontenttype": "application/json", "data": {"id": 1}}
CLOSED_BASE = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}, "additionalProperties": False}
CLOSED_HEAD = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
               "additionalProperties": False}
AVRO_BASE = {"type": "record", "name": "Order", "fields": [{"name": "id", "type": "string"}]}
AVRO_HEAD = {"type": "record", "name": "Order", "fields": [{"name": "id", "type": "string"}, {"name": "email", "type": "string"}]}


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def put(root: Path, rel: str, content) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def cli(root: Path, *args: str, tmp_state: Path | None = None) -> tuple[int, dict]:
    state = tmp_state or root.parent / "state"
    env = {**os.environ, "SWARM_DIR": str(state)}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(root), *args], capture_output=True, text=True,
                          env=env, cwd=str(root.parent), timeout=120)
    assert proc.stderr == "" or "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def rules(result: dict) -> list[tuple[str, str, str]]:
    return [(f["severity"], f["rule"], f["location"]) for f in result["findings"]]


def load_module():
    spec = importlib.util.spec_from_file_location("_event_schema_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- dry-run contract -----------------------------------------------------------------------------------------


def test_dry_run_prints_one_json_document_reads_nothing_and_exits_zero(tmp_path):
    missing = tmp_path / "does-not-exist"
    code, result = cli(missing, "--dry-run")
    assert code == 0
    assert (result["status"], result["dry_run"], result["agent"], result["script"]) == ("ok", True, "A19", "event_schema_check")
    assert result["findings"] == [] and result["scanned"]["files_visited"] == 0
    assert result["comparison"]["performed"] is False
    assert not missing.exists()


def test_dry_run_is_deterministic(tmp_path):
    first = cli(tmp_path / "x", "--dry-run")
    second = cli(tmp_path / "y", "--dry-run", "--mode", "full")
    assert first[0] == second[0] == 0
    assert {**first[1], "comparison": None} == {**second[1], "comparison": None}
    assert second[1]["comparison"]["mode"] == "full"


# --- scanning -------------------------------------------------------------------------------------------------


def test_clean_repository_reports_ok_and_counts_descriptors(repo):
    put(repo, "api/asyncapi.json", ASYNCAPI)
    put(repo, "events/order-created.json", EVENT)
    put(repo, "schemas/order.avsc", AVRO_BASE)
    put(repo, "README.json", {"hello": "world"})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    scanned = result["scanned"]
    assert (scanned["asyncapi"], scanned["cloudevents"], scanned["avro"], scanned["candidates"]) == (1, 1, 1, 3)
    assert scanned["files_visited"] == 4 and scanned["truncated"] is False
    assert result["comparison"]["performed"] is False and result["skipped"] == []
    assert result["summary"].startswith("1 AsyncAPI, 1 CloudEvents, 1 Avro file(s) checked; 0 finding(s)")


def test_invalid_asyncapi_fails_with_located_findings_numbered_by_severity(repo):
    bad = {"asyncapi": "3.0.0", "info": {"title": "Orders"},
           "channels": {"orders": {"address": "orders", "messages": {"created": {"contentType": "application/json"}}}},
           "operations": {"publish": {"action": "emit", "channel": {"$ref": "#/channels/orders"}}}}
    put(repo, "api/asyncapi.json", bad)
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert [(f["id"], f["severity"], f["summary"]) for f in result["findings"]] == [
        ("EV-001", "blocker", "info.version is required"),
        ("EV-002", "blocker", "operations/publish: action must be send or receive"),
        ("EV-003", "minor", "channels/orders/messages/created: message has no payload schema, so consumers have no contract"),
    ]
    assert {f["location"] for f in result["findings"]} == {"api/asyncapi.json"}
    assert {f["rule"] for f in result["findings"]} == {"asyncapi"}


def test_cloudevent_descriptor_findings_and_attribute_types(repo):
    put(repo, "events/bad.json", {"specversion": "1.0", "source": "/s", "type": "created", "id": 5, "retries": 3, "ok": True,
                                   "nested": {"a": 1}, "data": "x"})
    code, result = cli(repo)
    assert code == 1
    summaries = [(f["severity"], f["rule"], f["summary"]) for f in result["findings"]]
    assert summaries == [
        ("blocker", "cloudevents-attribute-type", "attribute 'nested' must be a string, boolean or number, not dict"),
        ("major", "cloudevents-attribute-type", "core attribute 'id' must be a string"),
        ("minor", "cloudevents", "type 'created' should be prefixed with a reverse-DNS name (for example com.example.order.created)"),
        ("info", "cloudevents", "data without datacontenttype: consumers must assume application/json"),
    ]
    assert result["scanned"]["cloudevents"] == 1


def test_cloudevent_batch_array_is_validated_per_element(repo):
    put(repo, "events/batch.json", [EVENT, {**EVENT, "id": ""}])
    code, result = cli(repo)
    assert code == 1
    assert [(f["severity"], f["location"], f["summary"]) for f in result["findings"]] == [
        ("blocker", "events/batch.json#/1", "required attribute 'id' must not be empty")]
    assert result["scanned"]["cloudevents"] == 2


def test_avsc_files_are_validated_as_avro(repo):
    put(repo, "schemas/good.avsc", AVRO_BASE)
    put(repo, "schemas/broken.avsc", "{not json")
    put(repo, "schemas/unknown.avsc", {"type": "record", "name": "R", "fields": [{"name": "a", "type": "Missing"}]})
    code, result = cli(repo)
    assert code == 1
    assert rules(result) == [("blocker", "avro-invalid", "schemas/broken.avsc"), ("blocker", "avro-invalid", "schemas/unknown.avsc"),
                             ("major", "scan-incomplete", ".")]
    assert result["findings"][0]["summary"] == "the .avsc file is not valid JSON"
    assert "unknown Avro type reference 'Missing'" in result["findings"][1]["summary"]
    assert "schemas/broken.avsc: not parseable, not analysed" in result["findings"][2]["summary"]
    assert result["scanned"]["avro"] == 2 and result["scanned"]["unparsable"] == 1


def test_scan_is_limited_to_path_and_skips_vendor_directories(repo):
    put(repo, "a/asyncapi.json", {**ASYNCAPI, "info": {}})
    put(repo, "b/asyncapi.json", ASYNCAPI)
    put(repo, "node_modules/x/asyncapi.json", {**ASYNCAPI, "info": {}})
    put(repo, ".git/asyncapi.json", {**ASYNCAPI, "info": {}})
    code, result = cli(repo, "--path", "b")
    assert code == 0 and result["scanned"]["asyncapi"] == 1
    code, result = cli(repo)
    assert code == 1 and {f["location"] for f in result["findings"]} == {"a/asyncapi.json"}
    assert result["scanned"]["asyncapi"] == 2


def test_oversized_binary_and_symlinked_files_are_skipped(repo, tmp_path):
    put(repo, "big/asyncapi.json", '{"asyncapi": "3.0.0", "pad": "' + "x" * (1024 * 1024) + '"}')
    (repo / "bin").mkdir()
    (repo / "bin" / "asyncapi.json").write_bytes(b'{"asyncapi": "3.0.0"}\x00\x01\x02')
    outside = tmp_path / "outside"
    put(outside, "asyncapi.json", {**ASYNCAPI, "info": {}})
    (repo / "link.json").symlink_to(outside / "asyncapi.json")
    (repo / "linkdir").symlink_to(outside, target_is_directory=True)
    put(repo, "ok/asyncapi.json", ASYNCAPI)
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail" and rules(result) == [("major", "scan-incomplete", ".")]
    reasons = result["findings"][0]["summary"]
    assert all(text in reasons for text in ("big/asyncapi.json: larger than 1048576 bytes, not analysed",
                                            "bin/asyncapi.json: binary, not analysed", "link.json: symbolic link not followed",
                                            "linkdir: symbolic link not followed"))
    scanned = result["scanned"]
    assert (scanned["skipped_large"], scanned["skipped_binary"], scanned["skipped_symlink"], scanned["asyncapi"]) == (1, 1, 2, 1)


def test_file_exactly_at_the_size_limit_is_still_read(repo):
    payload = '{"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"}, "x-pad": "'
    padding = 1024 * 1024 - len(payload) - 2
    put(repo, "edge/asyncapi.json", payload + "p" * padding + '"}')
    assert (repo / "edge" / "asyncapi.json").stat().st_size == 1024 * 1024
    code, result = cli(repo)
    assert result["scanned"]["skipped_large"] == 0 and result["scanned"]["asyncapi"] == 1


def test_scan_stops_after_2000_files_and_reports_it(repo):
    for index in range(2001):
        put(repo, f"data/f{index:04d}.txt", "x")
    put(repo, "zz/asyncapi.json", {**ASYNCAPI, "info": {}})  # sorted after data/: beyond the cap
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail" and result["scanned"]["files_visited"] == 2000
    assert result["scanned"]["truncated"] is True and result["scanned"]["asyncapi"] == 0
    assert rules(result) == [("major", "scan-incomplete", "."), ("info", "scan-truncated", ".")]


def test_exactly_2000_files_are_not_truncated(repo):
    for index in range(2000):
        put(repo, f"data/f{index:04d}.txt", "x")
    code, result = cli(repo)
    assert result["scanned"]["files_visited"] == 2000 and result["scanned"]["truncated"] is False


# --- compatibility --------------------------------------------------------------------------------------------


def test_json_schema_backward_compatible_change_exits_zero(repo):
    put(repo, "schemas/base.json", CLOSED_BASE)
    put(repo, "schemas/head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "schemas/base.json", "--head", "schemas/head.json")
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    assert result["comparison"] == {"performed": True, "mode": "backward", "format": "json-schema", "backward_compatible": True,
                                    "forward_compatible": False, "compatible": True, "base": "schemas/base.json",
                                    "head": "schemas/head.json"}
    assert result["summary"].endswith("schema compatibility (backward): compatible")


def test_full_mode_fails_on_the_forward_break_with_a_located_finding(repo):
    put(repo, "base.json", CLOSED_BASE)
    put(repo, "head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "base.json", "--head", "head.json", "--mode", "full")
    assert code == 1 and result["status"] == "fail"
    assert result["comparison"]["compatible"] is False and result["comparison"]["forward_compatible"] is False
    assert [(f["severity"], f["rule"], f["location"], f["summary"]) for f in result["findings"]] == [
        ("blocker", "schema-compat", "base.json -> head.json",
         "[forward] $.note: accepted by the source but rejected by the closed target schema")]
    assert result["summary"].endswith("schema compatibility (full): INCOMPATIBLE")


def test_forward_mode_is_checked_in_the_other_direction(repo):
    put(repo, "base.json", CLOSED_BASE)
    put(repo, "head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "base.json", "--head", "head.json", "--mode", "forward")
    assert code == 1 and result["comparison"]["mode"] == "forward" and result["comparison"]["compatible"] is False


def test_avro_is_detected_from_the_file_and_the_schema_shape(repo):
    put(repo, "base.avsc", AVRO_BASE)
    put(repo, "head.avsc", AVRO_HEAD)
    code, result = cli(repo, "--base", "base.avsc", "--head", "head.avsc")
    assert code == 1 and result["comparison"]["format"] == "avro" and result["comparison"]["backward_compatible"] is False
    assert result["findings"][0]["summary"] == "[backward] $.email: reader field has no default and the writer does not provide it"
    put(repo, "base2.json", AVRO_BASE)
    put(repo, "head2.json", {**AVRO_HEAD, "fields": [AVRO_HEAD["fields"][0], {"name": "email", "type": "string", "default": ""}]})
    code, result = cli(repo, "--base", "base2.json", "--head", "head2.json")
    assert code == 0 and result["comparison"]["format"] == "avro" and result["comparison"]["compatible"] is True


def test_explicit_format_overrides_detection(repo):
    put(repo, "base.json", {"type": "string"})
    put(repo, "head.json", {"type": "integer"})
    auto_code, auto = cli(repo, "--base", "base.json", "--head", "head.json", "--format", "json-schema")
    assert auto_code == 1 and auto["comparison"]["format"] == "json-schema"
    assert auto["findings"][0]["summary"] == "[backward] $: type ['string'] is not accepted by target type ['integer']"


def test_unparseable_or_invalid_schemas_are_findings_not_crashes(repo):
    put(repo, "base.json", "{broken")
    put(repo, "head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "base.json", "--head", "head.json")
    assert code == 1 and result["comparison"]["performed"] is False
    assert ("blocker", "schema-invalid", "base.json") in rules(result)
    put(repo, "base.json", {"type": "datetime"})
    code, result = cli(repo, "--base", "base.json", "--head", "head.json")
    assert code == 1 and result["comparison"]["performed"] is False
    assert result["findings"][0]["rule"] == "schema-compat" and result["findings"][0]["severity"] == "blocker"
    assert "INVALID_INPUT" in result["findings"][0]["summary"]


def test_compare_and_scan_findings_are_merged_and_sorted(repo):
    put(repo, "base.json", CLOSED_BASE)
    put(repo, "head.json", CLOSED_HEAD)
    put(repo, "events/e.json", {**EVENT, "type": "created"})
    code, result = cli(repo, "--base", "base.json", "--head", "head.json", "--mode", "full")
    assert code == 1
    assert [f["id"] for f in result["findings"]] == ["EV-001", "EV-002"]
    assert [f["severity"] for f in result["findings"]] == ["blocker", "minor"]


# --- input errors (exit 2) ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--path", ".."],
        ["--path", "missing"],
        ["--base", "base.json"],
        ["--head", "head.json"],
        ["--base", "nope.json", "--head", "head.json"],
        ["--base", "base.json", "--head", "../outside.json"],
        ["--base", "base.json", "--head", "dir"],
        ["--path", "base.json"],
    ],
)
def test_bad_arguments_exit_2_with_a_taxonomy_error(repo, args):
    put(repo, "base.json", CLOSED_BASE)
    put(repo, "head.json", CLOSED_HEAD)
    put(repo.parent, "outside.json", CLOSED_HEAD)
    (repo / "dir").mkdir()
    code, result = cli(repo, *args)
    assert code == 2 and result["status"] == "error" and result["error"]["code"] == "E-INPUT"


def test_oversized_compare_input_is_an_input_error(repo):
    put(repo, "base.json", '{"x": "' + "a" * (1024 * 1024) + '"}')
    put(repo, "head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "base.json", "--head", "head.json")
    assert code == 2 and "is large" in result["error"]["message"]


# --- YAML -----------------------------------------------------------------------------------------------------


def test_yaml_asyncapi_is_linted_when_pyyaml_is_available(repo):
    pytest.importorskip("yaml")
    put(repo, "api/asyncapi.yaml", "asyncapi: 3.0.0\ninfo:\n  title: Orders\nchannels: {}\n")
    code, result = cli(repo)
    assert code == 1 and result["scanned"]["asyncapi"] == 1 and result["skipped"] == []
    assert [(f["severity"], f["summary"]) for f in result["findings"]] == [("blocker", "info.version is required")]


def test_yaml_is_reported_as_skipped_tool_missing_and_json_still_works(repo, monkeypatch, capsys):
    put(repo, "api/asyncapi.yaml", "asyncapi: 3.0.0\ninfo:\n  title: Orders\n")
    put(repo, "api/other.yml", "specversion: '1.0'\n")
    put(repo, "api/plain.yaml", "key: value\n")
    put(repo, "api/asyncapi.json", {**ASYNCAPI, "info": {}})
    module = load_module()
    monkeypatch.setenv("SWARM_DIR", str(repo.parent / "state"))
    monkeypatch.setattr(module, "_yaml_module", lambda: None)
    script = module.AgentScript("A19", "event_schema_check", module.run, add_args=module.add_args)
    code = script.main(["--json", "--root", str(repo)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["skipped"] == [{"check": "yaml-descriptors", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 2}]
    assert ("info", "tool-missing", ".") in rules(result)
    assert any(f["rule"] == "asyncapi" and f["location"] == "api/asyncapi.json" and f["severity"] == "blocker" for f in result["findings"])
    assert result["scanned"]["asyncapi"] == 1 and result["scanned"]["candidates"] == 3


def test_yaml_schema_comparison_is_skipped_without_pyyaml(repo, monkeypatch, capsys):
    put(repo, "base.yaml", "type: object\n")
    put(repo, "head.json", CLOSED_HEAD)
    module = load_module()
    monkeypatch.setenv("SWARM_DIR", str(repo.parent / "state"))
    monkeypatch.setattr(module, "_yaml_module", lambda: None)
    script = module.AgentScript("A19", "event_schema_check", module.run, add_args=module.add_args)
    code = script.main(["--json", "--root", str(repo), "--base", "base.yaml", "--head", "head.json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["status"] == "fail" and result["comparison"]["performed"] is False
    assert result["skipped"][0] == {"check": "schema-compat", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 1}
    assert ("major", "scan-incomplete", ".") in rules(result) and ("info", "tool-missing", "base.yaml") in rules(result)
    assert "base.yaml: PyYAML is needed to read it, schema comparison not performed" in result["findings"][0]["summary"]


def test_yaml_schema_files_are_compared_with_pyyaml(repo):
    pytest.importorskip("yaml")
    put(repo, "base.yaml", "type: object\nrequired: [id]\nproperties:\n  id: {type: string}\nadditionalProperties: false\n")
    put(repo, "head.yaml", "type: object\nrequired: [id]\nproperties:\n  id: {type: string}\n  note: {type: string}\n"
                           "additionalProperties: false\n")
    code, result = cli(repo, "--base", "base.yaml", "--head", "head.yaml")
    assert code == 0 and result["comparison"]["performed"] is True and result["comparison"]["compatible"] is True


# --- review regressions -----------------------------------------------------------------------------------------


def test_yaml_cloudevent_timestamps_are_strings_and_non_string_keys_do_not_crash(repo):
    pytest.importorskip("yaml")
    put(repo, "events/ok.yaml", 'specversion: "1.0"\nid: A234\nsource: https://example.com/s\ntype: com.example.created\n'
                                'time: 2018-04-05T17:31:00Z\ndatacontenttype: text/xml\ndata: "<x/>"\n')
    put(repo, "events/date.yaml", 'specversion: "1.0"\nid: A\nsource: /s\ntype: a.b\ntime: 2018-04-05\n')
    put(repo, "events/intkey.yaml", '1: a\nspecversion: "1.0"\nid: x\nsource: /s\ntype: a.b\n')
    code, result = cli(repo)
    assert code == 1
    assert [(f["severity"], f["rule"], f["location"], f["summary"]) for f in result["findings"]] == [
        ("blocker", "cloudevents-attribute-name", "events/intkey.yaml", "attribute name must be a string, not int"),
        ("major", "cloudevents", "events/date.yaml", "time '2018-04-05' is not an RFC 3339 timestamp"),
    ]
    assert result["scanned"]["cloudevents"] == 3


SECRETS = ["AKIAIOSFODNN7EXAMPLE", "ghp_aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "a3f1" * 16, "Sup3rS3cret", "hunter2hunter2"]


def test_script_findings_never_echo_secret_shaped_values(repo):
    ghp, aws, creds, pwd, hexkey = SECRETS[1], SECRETS[0], "https://deploy:Sup3rS3cret@h/x", "password=hunter2hunter2", "a3f1" * 16
    put(repo, "e.json", {"specversion": "1.0", "id": "1", "source": "/s", "type": ghp, "time": creds, "dataschema": pwd,
                         "datacontenttype": hexkey, "Token_" + aws: "v", aws + "x": 1})
    put(repo, "api.json", {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"}, "channels": {ghp: {"address": "a"}},
                           "operations": {aws: {"action": "send", "channel": {"$ref": "#/channels/" + hexkey}}}})
    put(repo, "base.json", {"type": "object", "properties": {ghp: {"type": "string", "enum": [aws]}}, "required": [ghp]})
    put(repo, "head.json", {"type": "object", "properties": {ghp: {"type": "string", "enum": [hexkey]}}, "required": [ghp, aws]})
    put(repo, "bad.avsc", {"type": "record", "name": ghp, "fields": [{"name": "a", "type": aws}]})
    code, result = cli(repo, "--base", "base.json", "--head", "head.json", "--mode", "full")
    assert code == 1 and len(result["findings"]) >= 8
    text = json.dumps(result)
    for secret in SECRETS:
        assert secret not in text, secret


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
def test_fifo_named_like_a_json_descriptor_is_skipped_and_the_valid_file_is_still_scanned(tmp_path, repo):
    (repo / "e").mkdir()
    os.mkfifo(repo / "e" / "a.json")  # opening a pipe would block forever
    put(repo, "e/b.json", EVENT)
    env = {**os.environ, "SWARM_DIR": str(tmp_path / "state")}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", "--root", str(repo), "--path", "e"], capture_output=True, text=True,
                          env=env, cwd=str(repo.parent), check=False, timeout=60)
    assert "Traceback" not in proc.stderr, proc.stderr
    result = json.loads(proc.stdout)
    assert proc.returncode == 1 and [(f["severity"], f["rule"]) for f in result["findings"]] == [("major", "scan-incomplete")]
    assert "e/a.json: unreadable, not analysed" in result["findings"][0]["summary"]
    assert result["scanned"]["cloudevents"] == 1 and result["scanned"]["skipped_binary"] == 1


def test_an_unlistable_directory_fails_the_scan_by_name_and_the_readable_file_is_still_checked(repo, monkeypatch, capsys):
    put(repo, "ok/asyncapi.json", ASYNCAPI)
    put(repo, "locked/asyncapi.json", {**ASYNCAPI, "info": {}})
    listed = os.scandir

    def refuse(path="."):
        if os.path.basename(os.fspath(path)) == "locked":
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return listed(path)

    module = load_module()
    monkeypatch.setenv("SWARM_DIR", str(repo.parent / "state"))
    monkeypatch.setattr(os, "scandir", refuse)
    script = module.AgentScript("A19", "event_schema_check", module.run, add_args=module.add_args)
    code = script.main(["--json", "--root", str(repo)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["status"] == "fail"
    incomplete = next(f for f in result["findings"] if f["rule"] == "scan-incomplete")
    assert incomplete["severity"] == "major" and "locked: directory could not be listed (PermissionError)" in incomplete["summary"]
    assert result["scanned"]["asyncapi"] == 1  # the readable file was checked


# --- QA-03 regressions ----------------------------------------------------------------------------------------------


def test_cloudevents_array_beyond_200_events_is_incomplete_not_ok(repo):
    put(repo, "events/big.json", [EVENT] * 200 + [{**EVENT, "id": ""}])
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert result["scanned"]["cloudevents"] == 200
    assert [(f["severity"], f["rule"], f["location"]) for f in result["findings"]] == [
        ("major", "scan-incomplete", "."), ("major", "cloudevents", "events/big.json")]
    assert "events/big.json: 201 events in one array, events after the first 200 not validated" in result["findings"][0]["summary"]


def test_avro_is_detected_in_json_files_and_list_typed_json_schema_does_not_crash(repo):
    put(repo, "base.json", {"type": "array", "items": "int"})
    put(repo, "head.json", {"type": "array", "items": "long"})
    code, result = cli(repo, "--base", "base.json", "--head", "head.json")
    assert code == 0 and result["status"] == "ok"
    assert result["comparison"]["format"] == "avro" and result["comparison"]["backward_compatible"] is True
    put(repo, "typed-base.json", {"type": ["string", "null"]})
    put(repo, "typed-head.json", {"type": ["string", "null"]})
    code, result = cli(repo, "--base", "typed-base.json", "--head", "typed-head.json")
    assert code == 0 and result["status"] == "ok" and result["comparison"]["format"] == "json-schema"


def test_json_schema_tuple_and_type_lists_are_never_detected_as_avro(repo):
    # N-A: a tuple `items` and a `type` list are JSON Schema; auto must agree with --format json-schema
    put(repo, "tuple-base.json", {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]})
    put(repo, "tuple-head.json", {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]})
    code, auto = cli(repo, "--base", "tuple-base.json", "--head", "tuple-head.json")
    _, explicit = cli(repo, "--base", "tuple-base.json", "--head", "tuple-head.json", "--format", "json-schema")
    assert auto["comparison"]["format"] == "json-schema" and auto["status"] == explicit["status"] == "ok"
    assert auto["comparison"] == explicit["comparison"]
    # a tuple entry that moves changes what position 0 accepts: a real JSON Schema backward break, not an avro-compatible pass
    put(repo, "null-base.json", {"type": "array", "items": [{"type": "string"}, {"type": "null"}]})
    put(repo, "null-head.json", {"type": "array", "items": [{"type": "null"}, {"type": "string"}]})
    code, auto = cli(repo, "--base", "null-base.json", "--head", "null-head.json")
    _, explicit = cli(repo, "--base", "null-base.json", "--head", "null-head.json", "--format", "json-schema")
    assert code == 1 and auto["status"] == "fail" and auto["comparison"]["format"] == "json-schema"
    assert auto["comparison"]["backward_compatible"] is explicit["comparison"]["backward_compatible"] is False
    assert ("blocker", "schema-compat") in [(f["severity"], f["rule"]) for f in auto["findings"]]
    assert auto["comparison"] == explicit["comparison"]


def test_candidates_that_are_not_analysed_name_the_file_and_fail_the_scan(repo):
    # N-B: each of these is a candidate the scanner does not analyse; none may leave the scan reported as ok
    put(repo, "events/null.json", {"asyncapi": None})
    put(repo, "events/version-only.json", {"asyncapi": {"version": "3.0.0"}})
    put(repo, "events/mixed.json", [{**EVENT}, 5])
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    incomplete = [f for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert len(incomplete) == 1 and incomplete[0]["severity"] == "major"
    summary = incomplete[0]["summary"]
    assert len(summary) <= 300
    candidates = ("events/null.json", "events/version-only.json", "events/mixed.json")
    named = [name for name in candidates if name in summary]
    omitted = len(candidates) - len(named)
    assert named, summary
    if omitted:
        assert summary.endswith(f"and {omitted} more"), summary


def test_deeply_nested_descriptor_is_a_reason_not_a_crash(repo):
    # N-B: 5000 nested brackets used to raise RecursionError out of the encoder and take the whole scan down
    put(repo, "events/deep.json", '{"asyncapi": "3.0.0", "x": ' + "[" * 5000 + "]" * 5000 + "}")
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert any(f["rule"] == "scan-incomplete" and "events/deep.json" in f["summary"] for f in result["findings"])


def test_root_array_is_an_avro_union_and_a_nested_tuple_stays_json_schema(repo):
    # R-1: a JSON Schema root is an object or a boolean, so a root array is an Avro union; inside a schema it is a tuple
    put(repo, "union-base.json", ["null", "string"])
    put(repo, "union-head.json", ["null", "string"])
    code, result = cli(repo, "--base", "union-base.json", "--head", "union-head.json")
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    assert result["comparison"]["format"] == "avro" and result["comparison"]["compatible"] is True
    put(repo, "tuple-base.json", {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]})
    put(repo, "tuple-head.json", {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]})
    code, result = cli(repo, "--base", "tuple-base.json", "--head", "tuple-head.json")
    assert code == 0 and result["status"] == "ok" and result["comparison"]["format"] == "json-schema"


def test_non_descriptor_files_that_mention_the_words_are_silent_and_a_null_asyncapi_is_a_reason(repo):
    # R-2: package.json names the parser and config.json has the word in a string: neither is a candidate's structure
    put(repo, "package.json", {"name": "app", "dependencies": {"@asyncapi/parser": "3.0.0"}})
    put(repo, "config.json", {"note": "specversion is documented here", "n": 1})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    put(repo, "events/null.json", {"asyncapi": None})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert [f["rule"] for f in result["findings"]] == ["scan-incomplete"]
    assert "events/null.json" in result["findings"][0]["summary"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
def test_a_non_regular_base_or_head_is_an_unanalysed_input_not_an_input_error(repo):
    # N-H: a FIFO is never opened; it is recorded as not analysed, so the run fails with exit 1 instead of an E-INPUT exit 2
    os.mkfifo(repo / "fifo.json")
    put(repo, "head.json", CLOSED_HEAD)
    code, result = cli(repo, "--base", "fifo.json", "--head", "head.json")
    assert code == 1 and result["status"] == "fail" and result["comparison"]["performed"] is False
    assert ("major", "scan-incomplete", ".") in rules(result)
    assert "fifo.json: not a regular file, not analysed" in result["findings"][0]["summary"]
    code, result = cli(repo, "--base", "head.json", "--head", "fifo.json")
    assert code == 1 and result["status"] == "fail" and result["comparison"]["performed"] is False


def test_root_string_and_array_items_holding_a_string_are_avro_as_at_head(repo):
    # S-1, S-2: a string root, an items string and an items list holding a string are Avro evidence; HEAD detected them as Avro
    for index, schema in enumerate(("string", {"type": "array", "items": "string"}, {"type": "array", "items": ["null", "string"]})):
        put(repo, f"base{index}.json", json.dumps(schema))
        put(repo, f"head{index}.json", json.dumps(schema))
        code, result = cli(repo, "--base", f"base{index}.json", "--head", f"head{index}.json")
        assert code == 0 and result["status"] == "ok" and result["findings"] == [], schema
        assert result["comparison"]["format"] == "avro" and result["comparison"]["compatible"] is True, schema


def test_cloudevent_nested_under_a_wrapper_is_linted_at_its_json_pointer(repo):
    # S-4: a CloudEvent under a wrapper used to pass the prefilter and be skipped; it is linted at the pointer of the event
    put(repo, "wrap.json", {"event": {"specversion": "1.0", "id": "", "source": "s", "type": "t"}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert [(f["severity"], f["location"], f["summary"]) for f in result["findings"] if f["severity"] == "blocker"] == [
        ("blocker", "wrap.json#/event", "required attribute 'id' must not be empty")]
    assert result["scanned"]["cloudevents"] == 1


def test_a_document_with_no_specversion_key_stays_silent_and_a_json_schema_property_is_not_an_event(repo):
    # S-4 keeps R-2: the word in a string is out of scope, and a schema property named specversion is not a CloudEvent
    put(repo, "silent.json", {"a": {"b": [1, {"c": "specversion"}]}})
    put(repo, "ceschema.json", {"title": "CloudEvent", "properties": {"specversion": {"type": "string"}, "id": {"type": "string"}}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []


def test_a_nested_specversion_must_be_a_string_to_be_an_event_so_a_schema_property_is_not_a_false_blocker(repo):
    # S-5: a JSON Schema whose properties map has specversion true is not a CloudEvent (HEAD passes it); a nested CloudEvent with a
    # string specversion is still checked at its pointer
    put(repo, "schema.json", {"type": "object", "properties": {"specversion": True, "id": {"type": "string"}}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    put(repo, "wrap.json", {"event": {"specversion": "1.0", "id": "", "source": "s", "type": "t"}})
    code, result = cli(repo)
    blockers = [(f["location"], f["summary"]) for f in result["findings"] if f["severity"] == "blocker"]
    assert code == 1 and blockers == [("wrap.json#/event", "required attribute 'id' must not be empty")]


def test_an_asyncapi_walk_past_its_budget_is_an_unanalysed_input_naming_the_file(repo):
    # S-6: the cyclic n=9 document stops at its replay budget: a reason naming the file, status fail, exit 1, and no walk beyond it
    n = 9
    comps = {f"C{i}": {"oneOf": [{"$ref": f"#/components/messages/C{j}"} for j in range(n) if j != i] + [{"contentType": "x"}]}
             for i in range(n)}
    put(repo, "events/cyclic.json", {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"},
                                     "channels": {"c": {"address": "a", "messages": {"m": {"$ref": "#/components/messages/C0"}}}},
                                     "components": {"messages": comps}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    incomplete = [f for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert len(incomplete) == 1 and incomplete[0]["severity"] == "major"
    assert "events/cyclic.json: AsyncAPI message walk stopped after 500000 findings copied from shared messages" in incomplete[0]["summary"]


def test_a_numeric_nested_cloudevent_is_linted_at_its_pointer(repo):
    # N-3: a nested mapping whose specversion is a numeric scalar, with source and type, is a CloudEvent candidate: the core attribute
    # finding applies there as at the root, so the file fails instead of passing silently
    put(repo, "n3.json", {"wrap": {"specversion": 1.0, "id": "", "source": "s", "type": "t"}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    found = [(f["severity"], f["location"], f["summary"]) for f in result["findings"]]
    assert ("major", "n3.json#/wrap", "core attribute 'specversion' must be a string") in found
    assert ("blocker", "n3.json#/wrap", "required attribute 'id' must not be empty") in found


def test_a_ref_chain_deeper_than_700_levels_is_a_reason_naming_the_file_and_the_chain(repo):
    # N-2: a chain past the walk's depth limit (its text stays under the size bound) stops the walk: a reason naming the file and
    # the chain, status fail, and no exception
    comps = {"M800": {"payload": {"type": "object"}, "contentType": "application/json"}}
    for index in range(799, -1, -1):
        comps[f"M{index}"] = {"oneOf": [{"$ref": f"#/components/messages/M{index + 1}"}, {"contentType": "x"}]}
    put(repo, "events/chain.json", {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"},
                                    "channels": {"c": {"address": "a", "messages": {"m": {"$ref": "#/components/messages/M0"}}}},
                                    "components": {"messages": comps}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    incomplete = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert len(incomplete) == 1
    assert ("events/chain.json: AsyncAPI message walk stopped under channels/c/messages/m: a $ref chain is deeper than 700 levels"
            in incomplete[0])


def test_a_stop_survives_truncation_and_the_scanner_still_reports_the_file_incomplete(repo):
    # N-5: 320 blockers would push the walk-stop finding out of the handler's output; the stop is pinned, so the file is incomplete
    comps = {"M800": {"payload": {"type": "object"}, "contentType": "application/json"}}
    for index in range(799, -1, -1):
        comps[f"M{index}"] = {"oneOf": [{"$ref": f"#/components/messages/M{index + 1}"}, {"contentType": "x"}]}
    doc = {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"},
           "channels": {"c": {"address": "a", "messages": {"m": {"$ref": "#/components/messages/M0"}}}},
           "components": {"messages": comps},
           "operations": {f"o{i}": {"action": "publish", "channel": {"$ref": "#/channels/c"}} for i in range(320)}}
    put(repo, "events/ops.json", doc)
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    incomplete = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert len(incomplete) == 1
    assert "events/ops.json: AsyncAPI message walk stopped under channels/c/messages/m: a $ref chain is deeper than 700 levels" in incomplete[0]


def test_a_numeric_nested_event_with_no_source_is_a_candidate_and_gets_the_core_attribute_finding(repo):
    # N-7: {wrap: {specversion: 1.0, id, type}} is a candidate, as the string form is: its numeric specversion is a core violation
    put(repo, "n7.json", {"wrap": {"specversion": 1.0, "id": "1", "type": "t"}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    found = [(f["severity"], f["location"], f["summary"]) for f in result["findings"]]
    assert ("major", "n7.json#/wrap", "core attribute 'specversion' must be a string") in found


def test_json_schema_property_maps_are_never_events_whatever_their_values(repo):
    # N-8: a properties map with specversion true and schema mappings for source and type gives six false blockers before; it is ok now,
    # and so is a properties map of scalars
    put(repo, "n8.json", {"properties": {"specversion": True, "source": {"type": "string"}, "type": {"type": "string"},
                                         "id": {"type": "string"}}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    put(repo, "n8b.json", {"properties": {"specversion": True, "id": True, "type": True}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []


def test_a_nested_string_specversion_is_a_candidate_even_without_id_type_or_source(repo):
    # S-4 restored: a nested mapping whose specversion is a string is a CloudEvent candidate whatever else it has, so a missing id is
    # the id blocker at its pointer and the file is not silent
    put(repo, "wrap.json", {"wrap": {"specversion": "1.0", "id": ""}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert ("blocker", "wrap.json#/wrap", "required attribute 'id' must not be empty") in [
        (f["severity"], f["location"], f["summary"]) for f in result["findings"]]


def test_event_data_under_a_keyword_named_key_is_linted_and_only_schema_maps_are_skipped(repo):
    # R6-2: a key named like a JSON Schema keyword (properties, definitions) that holds event data is linted at the event's pointer;
    # the exclusion applies only to schema maps, whose entries are booleans or schema objects
    put(repo, "env.json", {"envelope": {"properties": {"specversion": "1.0", "id": "", "source": "s", "type": "t"}}})
    put(repo, "defs.json", {"definitions": {"ev1": {"specversion": "1.0", "id": "", "source": "s", "type": "t"}}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    blockers = sorted((f["location"], f["summary"]) for f in result["findings"] if f["severity"] == "blocker")
    assert blockers == [("defs.json#/definitions/ev1", "required attribute 'id' must not be empty"),
                        ("env.json#/envelope/properties", "required attribute 'id' must not be empty")]


def test_numeric_nested_specversion_with_one_scalar_attribute_is_a_candidate(repo):
    # R6-3: a numeric specversion with a scalar type and no id, or with a mapping id, is a candidate: the core-attribute major is at
    # its pointer and the file fails
    put(repo, "no-id.json", {"wrap": {"specversion": 1.0, "type": "t"}})
    put(repo, "mapping-id.json", {"wrap": {"specversion": 1.0, "id": {"x": 1}, "type": "t"}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    found = [(f["location"], f["summary"]) for f in result["findings"]]
    assert ("no-id.json#/wrap", "core attribute 'specversion' must be a string") in found
    assert ("mapping-id.json#/wrap", "core attribute 'specversion' must be a string") in found


def test_keyword_named_maps_are_schema_maps_whatever_keywords_their_entries_use(repo):
    # R7-1: an object entry is event data only when one of its CloudEvents attributes holds a string or a number; these maps are schema
    put(repo, "pattern.json", {"properties": {"specversion": True, "id": True, "type": {"pattern": "^t$"}}})
    put(repo, "empty.json", {"properties": {"specversion": True, "id": True, "type": {}}})
    put(repo, "source-pattern.json", {"properties": {"specversion": True, "id": True, "source": {"pattern": "x"}}})
    put(repo, "n8.json", {"properties": {"specversion": True, "source": {"type": "string"}, "type": {"type": "string"}, "id": {"type": "string"}}})
    put(repo, "s5.json", {"type": "object", "properties": {"specversion": True, "id": {"type": "string"}}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []


def test_scan_incomplete_summary_keeps_the_whole_stop_sentence_with_long_names_and_paths(repo):
    # R7-2: the summary is capped at 300 characters, the registered findings maxLength, so a long path is labelled by its head and tail
    # and the origin is bounded; the stop sentence stays whole. The full path is the asyncapi finding's location.
    comps = {"M800": {"payload": {"type": "object"}, "contentType": "application/json"}}
    for index in range(799, -1, -1):
        comps[f"M{index}"] = {"oneOf": [{"$ref": f"#/components/messages/M{index + 1}"}, {"contentType": "x"}]}
    chain = {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"}, "components": {"messages": comps}}
    put(repo, "long.json", {**chain, "channels": {"k" * 60: {"address": "a", "messages": {"j" * 60: {"$ref": "#/components/messages/M0"}}}}})
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert summaries[0].endswith("the message count and the findings list are both partial")
    (repo / "long.json").unlink()
    path = "events/" + "e" * 101 + ".json"  # 113 characters
    assert len(path) == 113
    put(repo, path, {**chain, "channels": {"c": {"address": "a", "messages": {"m": {"$ref": "#/components/messages/M0"}}}}})
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert summaries[0].endswith("the message count and the findings list are both partial")
    assert "events/" in summaries[0] and "…" in summaries[0]  # the head of the path labels the file; the sentence stays whole
    assert any(f["rule"] == "asyncapi" and f["location"] == path for f in result["findings"])  # the full path is the finding's location


def test_present_attribute_with_a_non_scalar_value_is_never_reported_as_missing(repo):
    # R7-3: an id present with a mapping value gets its type message only; the source that is absent is still reported as missing
    put(repo, "wrap.json", {"wrap": {"specversion": 1.0, "id": {"x": 1}, "type": "t"}})
    code, result = cli(repo)
    at_wrap = [(f["severity"], f["summary"]) for f in result["findings"] if f["location"] == "wrap.json#/wrap"]
    assert ("blocker", "attribute 'id' must be a string, boolean or number, not dict") in at_wrap
    assert all(summary != "required attribute 'id' is missing" for _, summary in at_wrap)
    assert ("blocker", "required attribute 'source' is missing") in at_wrap


def test_json_schema_type_names_are_schema_keywords_and_reverse_dns_event_types_stay_event_data(repo):
    # R7-1: a `type` holding a JSON Schema type name is a schema keyword, so these valid schemas give ok; a reverse-DNS event type is still
    # event data, so an invalid event under a keyword-named key is linted at its pointer
    put(repo, "type-name.json", {"properties": {"specversion": True, "id": True, "type": {"type": "string"}}})
    put(repo, "source-name.json", {"properties": {"specversion": True, "id": True, "source": {"type": "string"}}})
    code, result = cli(repo)
    assert code == 0 and result["status"] == "ok" and result["findings"] == []
    put(repo, "event.json", {"definitions": {"ev1": {"specversion": "1.0", "id": "", "source": "s", "type": "com.example.order"}}})
    code, result = cli(repo)
    assert code == 1 and result["status"] == "fail"
    assert [(f["severity"], f["location"], f["summary"]) for f in result["findings"] if f["severity"] == "blocker"] == [
        ("blocker", "event.json#/definitions/ev1", "required attribute 'id' must not be empty")]


def _chain_document(levels: int = 800, channel: str = "c") -> dict:
    comps = {f"M{levels}": {"payload": {"type": "object"}, "contentType": "application/json"}}
    for index in range(levels - 1, -1, -1):
        comps[f"M{index}"] = {"oneOf": [{"$ref": f"#/components/messages/M{index + 1}"}, {"contentType": "x"}]}
    return {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "channels": {channel: {"address": "a", "messages": {"m": {"$ref": "#/components/messages/M0"}}}},
            "components": {"messages": comps}}


CHAIN_SENTENCE = "a $ref chain is deeper than 700 levels, the document is not fully analysed"


def test_an_oversize_file_before_a_chain_stop_keeps_the_stop_sentence_whole(repo):
    # R8-1: the oversize reason sorts first; the stop reason keeps its whole sentence and the summary stays at 300 characters
    put(repo, "a_oversize.json", '{"asyncapi": "3.0.0", "pad": "' + "x" * (1024 * 1024 + 10) + '"}')
    put(repo, "b_chain.json", _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]


def test_an_unparseable_file_before_a_chain_stop_keeps_the_stop_sentence_whole(repo):
    # R8-1: the unparseable reason sorts first; the chain stop keeps its whole sentence and the summary stays at 300 characters
    put(repo, "a_unparseable.json", '{"asyncapi": "3.0.0", broken')
    put(repo, "b_chain.json", _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]


def test_a_stop_is_named_within_the_first_five_reasons_even_after_five_unparseable_files(repo):
    # R8-2: five unparseable files a0 to a4 sort before the stop file z.json; the stop is shown and the status is still fail
    for index in range(5):
        put(repo, f"a{index}.json", '{"asyncapi": "3.0.0", broken')
    put(repo, "z.json", _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and result["status"] == "fail" and len(summaries) == 1 and len(summaries[0]) <= 300
    assert "z.json" in summaries[0] and CHAIN_SENTENCE in summaries[0]


MORE_STOPS = "and 1 more"
CLAUSE = "the message count and the findings list are both partial"


def test_a_file_name_with_a_colon_space_keeps_the_stop_sentence_and_the_clause_whole(repo):
    # R9-1: the label comes from the scanned file's name, not from splitting the reason text on ': ', so a name that holds ': ' is measured
    # as the name and the clause after the chain sentence stays whole
    name = "x" * 60 + ": " + "y" * 60 + ".json"
    put(repo, name, _chain_document(channel="k" * 60))
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0] and CLAUSE in summaries[0]


def test_two_chain_stops_with_60_character_names_both_fit_or_are_named_and_the_first_stays_whole(repo):
    # R9-2: two stops in one scan; the first keeps its whole sentence, and the second is named by its file and kind, or counted
    put(repo, "first_name_" + "a" * 50 + ".json", _chain_document(channel="k" * 60))
    put(repo, "second_name_" + "b" * 50 + ".json", _chain_document(channel="j" * 60))
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]
    assert "second_name_" in summaries[0] or summaries[0].endswith(MORE_STOPS)


def test_two_chain_stops_with_113_character_paths_name_the_second_or_count_it(repo):
    # R9-2: two 113-character paths; the first stop is whole, and the second is not dropped silently
    first, second = "events/" + "e" * 101 + ".json", "events/" + "f" * 101 + ".json"
    put(repo, first, _chain_document())
    put(repo, second, _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]
    assert "ffff" in summaries[0] or summaries[0].endswith(MORE_STOPS)


def _count_clauses(summary: str) -> list:
    """Every 'and N more' clause in a summary (R10-2, R10-3, R11-2)."""
    return [part for part in summary.split("; ") if part.startswith("and ") and part.endswith(" more")]


def test_three_stops_keep_the_first_sentence_whole_and_a_complete_count_clause(repo):
    # R10-1: three chain stops; the first keeps its sentence, and the count clause is complete, never cut to 'and 1 more stop '
    for index in range(3):
        put(repo, f"z{index}_" + "q" * 40 + ".json", _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]
    assert summaries[0].endswith("and 2 more")


def test_omitted_per_file_reasons_are_counted_not_dropped_with_long_and_short_names(repo):
    # R10-2: one chain stop and four unparseable files; the per-file reasons that do not fit are counted, so no reason is lost without a count
    put(repo, "stop.json", _chain_document())
    for index in range(4):
        put(repo, f"b{index}_" + "b" * 100 + ".json", '{"asyncapi": "3.0.0", broken')
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]
    assert summaries[0].endswith("and 4 more") and _count_clauses(summaries[0]) == ["and 4 more"]


def test_omitted_per_file_reasons_with_113_character_paths_are_counted(repo):
    # R10-2: the same with 113-character paths, where the per-file reasons used to be cut entirely with no count
    put(repo, "events/stop" + "s" * 101 + ".json", _chain_document())
    for index in range(4):
        put(repo, f"events/{'c' * 103}{index}.json", '{"asyncapi": "3.0.0", broken')
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert CHAIN_SENTENCE in summaries[0]
    assert summaries[0].endswith("and 4 more") and _count_clauses(summaries[0]) == ["and 4 more"]


def test_one_omitted_item_is_counted_as_and_1_more(repo):
    # R11-2: one omitted item gives 'and 1 more' in full, for stops and per-file reasons alike
    put(repo, "z0.json", _chain_document())
    put(repo, "z1.json", _chain_document())
    code, result = cli(repo)
    summaries = [f["summary"] for f in result["findings"] if f["rule"] == "scan-incomplete"]
    assert code == 1 and len(summaries) == 1 and len(summaries[0]) <= 300
    assert summaries[0].endswith("and 1 more")


def _recursion_stop(monkeypatch, channel: str) -> str:
    """The recursion stop the handler produces, forced in-process at chain depth 3 (no network, no keys)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from swarm.tools import a19_tools
    original = a19_tools._asyncapi_message

    def stop_at_depth_three(message, where, chain, walk):
        if len(chain) >= 3:
            raise RecursionError("forced")
        return original(message, where, chain, walk)

    monkeypatch.setattr(a19_tools, "_asyncapi_message", stop_at_depth_three)
    document = {"asyncapi": "3.0.0", "info": {"title": "T", "version": "1"},
                "channels": {channel: {"address": "a", "messages": {"m": {"$ref": "#/components/messages/M0"}}}},
                "components": {"messages": {"M0": {"oneOf": [{"$ref": "#/components/messages/M1"}]},
                                            "M1": {"oneOf": [{"$ref": "#/components/messages/M2"}]},
                                            "M2": {"oneOf": [{"$ref": "#/components/messages/M3"}]},
                                            "M3": {"contentType": "x"}}}}
    result = a19_tools.asyncapi_lint({"document": json.dumps(document)})
    return next(f["summary"] for f in result["findings"] if f["summary"].startswith(a19_tools.ASYNCAPI_WALK_STOPPED))


def _summary_of_stops(module, stops: list) -> str:
    """The summary the shared rule gives these walk stops, through the scanner's own render (R10-1)."""
    return module.incomplete_findings(module._stops_first(stops), 1)[0]["summary"]


def test_three_recursion_stops_with_60_character_names_keep_the_sentence_whole_and_the_count_complete(monkeypatch):
    # R10-1: the recursion sentence is 207 characters; three of them must keep the first whole and end on a complete 'and 2 more'
    module = load_module()
    sentence = _recursion_stop(monkeypatch, "k" * 60)
    stops = [module._Stop(f"z{index}_" + "q" * 40 + ".json", sentence) for index in range(3)]
    summary = _summary_of_stops(module, stops)
    assert len(summary) <= 300
    assert sentence in summary
    assert summary.endswith("and 2 more")


def test_three_recursion_stops_with_113_character_paths_keep_the_sentence_whole_and_the_count_complete(monkeypatch):
    # R10-1: the same with 113-character paths
    module = load_module()
    sentence = _recursion_stop(monkeypatch, "k" * 60)
    stops = [module._Stop(f"events/{index}_" + "p" * 100 + ".json", sentence) for index in range(3)]
    summary = _summary_of_stops(module, stops)
    assert len(summary) <= 300
    assert sentence in summary
    assert summary.endswith("and 2 more")
