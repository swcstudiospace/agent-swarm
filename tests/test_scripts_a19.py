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
        ("blocker", "events/batch.json[1]", "required attribute 'id' must not be empty")]
    assert result["scanned"]["cloudevents"] == 2


def test_avsc_files_are_validated_as_avro(repo):
    put(repo, "schemas/good.avsc", AVRO_BASE)
    put(repo, "schemas/broken.avsc", "{not json")
    put(repo, "schemas/unknown.avsc", {"type": "record", "name": "R", "fields": [{"name": "a", "type": "Missing"}]})
    code, result = cli(repo)
    assert code == 1
    assert rules(result) == [("blocker", "avro-invalid", "schemas/broken.avsc"), ("blocker", "avro-invalid", "schemas/unknown.avsc")]
    assert result["findings"][0]["summary"] == "the .avsc file is not valid JSON"
    assert "unknown Avro type reference 'Missing'" in result["findings"][1]["summary"]
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
    assert code == 0 and result["findings"] == []
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
    assert code == 0 and result["scanned"]["files_visited"] == 2000 and result["scanned"]["truncated"] is True
    assert result["scanned"]["asyncapi"] == 0
    assert rules(result) == [("info", "scan-truncated", ".")]


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
    assert code == 0 and result["status"] == "ok" and result["comparison"]["performed"] is False
    assert result["skipped"][0] == {"check": "schema-compat", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 1}
    assert result["findings"][0]["summary"] == "skipped:tool-missing: PyYAML is needed to read base.yaml"


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
    assert proc.returncode == 0 and result["findings"] == []
    assert result["scanned"]["cloudevents"] == 1 and result["scanned"]["skipped_binary"] == 1
