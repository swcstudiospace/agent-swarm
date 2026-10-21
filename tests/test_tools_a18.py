"""Behaviour tests for the A18 Supply-Chain Security Engineer tools (the twelve supply_* analyses).

Every assertion is on output values for specific inputs, boundaries, INVALID_INPUT fields and callers. The registry
document is the live registry plus this lane's intake fragment while it is unmerged, so the file passes before and
after `scripts/_registry_intake.py --apply` merges and deletes the fragment.

tests/conftest.py evicts swarm* modules between tests: modules are resolved through importlib at call time.
"""
from __future__ import annotations

import copy
import functools
import importlib
import base64
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A18", "a18-supply-chain", "supply_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "supply_dockerfile_lint", "supply_base_image_policy", "supply_sbom_validate", "supply_sbom_diff", "supply_slsa_level",
    "supply_provenance_match", "supply_signature_policy", "supply_vex_apply", "supply_pin_audit", "supply_license_policy",
    "supply_retention_plan", "supply_attestation_bundle",
]
HEX = "0123456789abcdef" * 4
DIGEST = f"sha256:{HEX}"
OTHER_DIGEST = "sha256:" + "fedcba9876543210" * 4
AS_OF = "2026-03-01T00:00:00+00:00"


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _fragment_entries() -> list[dict]:
    """Entries still waiting in the lane's intake fragment(s): <slug>.tools.json and <slug>.<shard>.tools.json."""
    entries: list[dict] = []
    for path in sorted(INTAKE.glob(f"{SLUG}.*tools.json")):
        entries.extend(json.loads(path.read_text(encoding="utf-8")))
    return entries


@functools.cache
def lane_document() -> dict:
    """The registry plus this lane's fragment entries that are not merged yet."""
    document = copy.deepcopy(_registry().load_document())
    agent = next(a for a in document["agents"] if a["id"] == AGENT)
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(e) for e in _fragment_entries() if e["name"] not in present)
    return document


def lane_tools() -> list[dict]:
    return next(a for a in lane_document()["agents"] if a["id"] == AGENT)["registered_tools"]


def call(name: str, payload, caller: str = AGENT) -> dict:
    return _registry().call_tool(name, caller, payload, data=lane_document())


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def invalid(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result
    return result


# --- registration ---------------------------------------------------------------------------------------------


def test_lane_registers_exactly_its_tools_as_read_only_owner_calls():
    tools = lane_tools()
    assert [t["name"] for t in tools] == TOOL_NAMES
    assert 10 <= len(tools) <= 15
    for tool in tools:
        suffix = tool["name"][len(PREFIX):]
        assert tool["name"].startswith(PREFIX) and tool["owner"] == AGENT and tool["permitted_callers"] == [AGENT]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["entrypoint"] == f"swarm/tools/{AGENT.lower()}_tools.py"
        assert tool["handler"] == f"swarm.tools.{AGENT.lower()}_tools:{suffix}"
        assert "probe" in tool["input_schema"]["properties"] and "|" not in tool["purpose"]
        assert tool["output_schema"]["properties"]["state"] == {"type": "string", "const": "SUCCESS"}


def test_lane_entries_validate_in_a_copy_of_the_registry(tmp_path):
    copy_path = tmp_path / "agents.json"
    copy_path.write_text(json.dumps(lane_document()), encoding="utf-8")
    errors = _registry().validate_registry(copy_path)
    mine = [e for e in errors if AGENT in e or any(name in e for name in TOOL_NAMES)]
    assert mine == []


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_refuses_other_callers_and_malformed_payloads(name):
    sample = next(t for t in lane_tools() if t["name"] == name)["examples"]["success"]
    assert call(name, sample, caller="A01")["state"] == "UNAUTHORIZED"
    assert call(name, {})["state"] == "INVALID_INPUT"
    assert call(name, [sample])["field"] == "payload"
    assert call(name, {**sample, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call(name, {**sample, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


def ids(result: dict) -> list[str]:
    return [f["id"] for f in result["findings"]]


def severity_of(result: dict, finding_id: str) -> str:
    return next(f["severity"] for f in result["findings"] if f["id"] == finding_id)


def at(result: dict, finding_id: str) -> list[str]:
    return [f["location"] for f in result["findings"] if f["id"] == finding_id]


# --- supply_dockerfile_lint -------------------------------------------------------------------------------------

CLEAN = f"FROM alpine:3.20@{DIGEST}\nRUN apk add --no-cache curl\nUSER app\nHEALTHCHECK CMD wget -q --spider http://localhost/ || exit 1\n"


def lint(content: str, **extra) -> dict:
    return ok("supply_dockerfile_lint", {"content": content, **extra})


def test_dockerfile_lint_clean_file_has_no_findings_and_reports_facts():
    result = lint(CLEAN)
    assert result == {
        "state": "SUCCESS", "stages": 1, "instructions": 4, "final_base": f"alpine:3.20@{DIGEST}", "final_user": "app",
        "has_healthcheck": True, "verdict": "pass", "findings_total": 0, "blocking_count": 0, "findings": [],
    }


@pytest.mark.parametrize("base", ["node", "node:latest", "docker.io/library/node:LATEST".lower()])
def test_dockerfile_lint_flags_untagged_and_latest_bases_as_major(base):
    result = lint(f"FROM {base}\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(result) == ["docker.latest-tag"]
    assert result["verdict"] == "fail" and result["blocking_count"] == 1
    assert at(result, "docker.latest-tag") == ["line 1"]


def test_dockerfile_lint_digest_beats_latest_tag():
    assert ids(lint(f"FROM node:latest@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n")) == []


def test_dockerfile_lint_tag_only_base_is_minor_unless_digest_pin_required():
    content = "FROM node:20.11\nUSER app\nHEALTHCHECK CMD true\n"
    relaxed = lint(content)
    assert ids(relaxed) == ["docker.base-not-digest-pinned"] and severity_of(relaxed, "docker.base-not-digest-pinned") == "minor"
    assert relaxed["verdict"] == "pass"
    strict = lint(content, require_digest_pin=True)
    assert severity_of(strict, "docker.base-not-digest-pinned") == "major" and strict["verdict"] == "fail"


def test_dockerfile_lint_invalid_digest_and_reference_are_major():
    result = lint("FROM alpine@sha256:abc\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(result) == ["docker.base-invalid-reference"] and "digest" in result["findings"][0]["summary"]
    assert ids(lint("FROM Alpine:3\nUSER app\nHEALTHCHECK CMD true\n")) == ["docker.base-invalid-reference"]


def test_dockerfile_lint_resolves_global_arg_defaults_in_from():
    content = "ARG BASE=node:latest\nFROM ${BASE}\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(content)) == ["docker.latest-tag"]
    assert ids(lint("ARG TAG=3.20\nFROM alpine:$TAG\nUSER app\nHEALTHCHECK CMD true\n")) == ["docker.base-not-digest-pinned"]
    unresolved = lint("ARG BASE\nFROM ${BASE}\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.base-unresolved-arg"] and unresolved["verdict"] == "fail"  # an unchecked base is not a pass
    assert lint("ARG BASE\nFROM ${BASE:-alpine:latest}\nUSER app\nHEALTHCHECK CMD true\n")["findings"][0]["id"] == "docker.latest-tag"


def test_dockerfile_lint_scratch_and_earlier_stage_bases_are_not_registry_references():
    content = f"FROM golang:1.22@{DIGEST} AS build\nRUN go build -o /out\nFROM scratch\nCOPY --from=build /out /out\nUSER 65532\nHEALTHCHECK CMD [\"/out\",\"-h\"]\n"
    result = lint(content)
    assert ids(result) == [] and result["stages"] == 2 and result["final_base"] == "scratch"


def test_dockerfile_lint_final_user_rules():
    missing = lint("FROM alpine:3@" + DIGEST + "\nHEALTHCHECK CMD true\n")
    assert ids(missing) == ["docker.final-user-missing"] and missing["final_user"] == ""
    for user in ("root", "0", "0:0", "root:root"):
        result = lint(f"FROM alpine:3@{DIGEST}\nUSER {user}\nHEALTHCHECK CMD true\n")
        assert ids(result) == ["docker.final-user-root"], user
        assert at(result, "docker.final-user-root") == ["line 2"]
    for user in ("1000", "app:app"):
        assert ids(lint(f"FROM alpine:3@{DIGEST}\nUSER {user}\nHEALTHCHECK CMD true\n")) == [], user
    unresolved = lint(f"FROM alpine:3@{DIGEST}\nUSER ${{UID}}\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.final-user-unresolved"] and unresolved["verdict"] == "fail"


def test_dockerfile_lint_only_the_final_stage_decides_user_and_inherits_from_earlier_stage():
    builder_root = f"FROM alpine:3@{DIGEST} AS build\nUSER root\nFROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(builder_root)) == []
    inherited = f"FROM alpine:3@{DIGEST} AS base\nUSER app\nHEALTHCHECK CMD true\nFROM base\nRUN true\n"
    result = lint(inherited)
    assert ids(result) == [] and result["final_user"] == "app" and result["has_healthcheck"] is True
    last_wins = f"FROM alpine:3@{DIGEST}\nUSER app\nUSER root\nHEALTHCHECK CMD true\n"
    assert ids(lint(last_wins)) == ["docker.final-user-root"]


def test_dockerfile_lint_healthcheck_rules():
    content = f"FROM alpine:3@{DIGEST}\nUSER app\n"
    missing = lint(content)
    assert ids(missing) == ["docker.no-healthcheck"] and severity_of(missing, "docker.no-healthcheck") == "minor"
    assert missing["has_healthcheck"] is False and missing["verdict"] == "pass"
    assert ids(lint(content, require_healthcheck=False)) == []
    none = lint(content + "HEALTHCHECK NONE\n")
    assert ids(none) == ["docker.no-healthcheck"] and "HEALTHCHECK NONE" in none["findings"][0]["summary"]


def test_dockerfile_lint_add_rules():
    head = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(head + "ADD rootfs.tar.gz /\nADD app.tgz /opt/\n")) == []
    local = lint(head + "ADD src /app\n")
    assert ids(local) == ["docker.add-instead-of-copy"] and severity_of(local, "docker.add-instead-of-copy") == "minor"
    remote = lint(head + "ADD https://example.com/tool.tar.gz /tmp/\n")
    assert ids(remote) == ["docker.add-remote-unverified"] and remote["verdict"] == "fail"
    assert ids(lint(head + f"ADD --checksum=sha256:{HEX} https://example.com/tool.tar.gz /tmp/\n")) == []
    assert ids(lint(head + 'ADD ["a b", "/dst/"]\n')) == ["docker.add-instead-of-copy"]
    assert ids(lint(head + "COPY src /app\n")) == []


def test_dockerfile_lint_package_cache_rules():
    head = f"FROM debian:12@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(head + "RUN apt-get update && apt-get install -y curl\n")) == ["docker.pkg-cache-apt"]
    assert ids(lint(head + "RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*\n")) == []
    assert ids(lint(head + "RUN --mount=type=cache,target=/var/cache/apt apt-get install -y curl\n")) == []
    assert ids(lint(head + "RUN apk add curl\n")) == ["docker.pkg-cache-apk"]
    assert ids(lint(head + "RUN apk add --no-cache curl\n")) == []
    assert ids(lint(head + "RUN dnf install -y curl\n")) == ["docker.pkg-cache-rpm"]
    assert ids(lint(head + "RUN dnf install -y curl && dnf clean all\n")) == []
    assert ids(lint(head + "RUN pip install requests\n")) == ["docker.pip-cache"]
    assert ids(lint(head + "RUN python -m pip install --no-cache-dir requests\n")) == []
    assert ids(lint(head.replace("USER app", "ENV PIP_NO_CACHE_DIR=1\nUSER app") + "RUN pip3 install requests\n")) == []
    assert ids(lint(head + "RUN apt-get update\n")) == []


def test_dockerfile_lint_package_cache_setting_is_inherited_from_an_in_file_parent_stage():
    # Docker keeps a parent stage's ENV for its children: PIP_NO_CACHE_DIR set in the base silences the child's pip install
    head = f"FROM python:3@{DIGEST} AS base\nENV PIP_NO_CACHE_DIR=1\nFROM base\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(head + "RUN pip install requests\n")) == []
    assert ids(lint(f"FROM python:3@{DIGEST} AS base\nFROM base\nUSER app\nHEALTHCHECK CMD true\nRUN pip install requests\n")) == ["docker.pip-cache"]


def test_dockerfile_lint_pipe_to_shell_and_credential_urls():
    head = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(head + "RUN curl -fsSL https://get.example.com | sh\n")) == ["docker.pipe-to-shell"]
    assert ids(lint(head + "RUN wget -qO- https://x.example | sudo bash\n")) == ["docker.pipe-to-shell"]
    assert ids(lint(head + "RUN curl -fsSLo /tmp/x https://x.example && sh /tmp/x\n")) == []
    leak = lint(head + "RUN git clone https://ci:s3cret@git.example.com/org/repo.git\n")
    assert ids(leak) == ["docker.credential-in-url"] and severity_of(leak, "docker.credential-in-url") == "major"
    assert ids(lint(head + "RUN --mount=type=secret,id=netrc git clone https://ci:s3cret@git.example.com/r.git\n")) == []
    env_leak = lint(head + "ENV REPO=https://ci:s3cret@git.example.com/org/repo.git\n")
    assert ids(env_leak) == ["docker.credential-in-url"] and severity_of(env_leak, "docker.credential-in-url") == "blocker"


def test_dockerfile_lint_secrets_in_env_and_arg():
    head = f"FROM alpine:3@{DIGEST}\n"
    tail = "USER app\nHEALTHCHECK CMD true\n"
    env = lint(head + "ENV DB_PASSWORD=hunter2\n" + tail)
    assert ids(env) == ["docker.secret-in-env"] and severity_of(env, "docker.secret-in-env") == "blocker" and env["verdict"] == "fail"
    assert ids(lint(head + "ENV API_KEY abc123\n" + tail)) == ["docker.secret-in-env"]
    assert ids(lint(head + "ENV GITHUB_TOKEN=$GH\n" + tail)) == ["docker.secret-in-env"]
    assert ids(lint(head + "ENV DB_PASSWORD=\n" + tail)) == []
    arg = lint(head + "ARG NPM_TOKEN\n" + tail)
    assert ids(arg) == ["docker.secret-in-arg"] and severity_of(arg, "docker.secret-in-arg") == "major"
    assert ids(lint(head + "ARG PASSWORD_FILE=/run/secrets/pw\nENV AWS_ACCESS_KEY_ID=AKIAXXXX\nENV TOKENIZERS_PARALLELISM=false\nARG VERSION=1\n" + tail)) == []
    assert ids(lint(head + "ARG PRIVATE_KEY\nARG secretKey\nARG dbPassword\n" + tail)) == ["docker.secret-in-arg"] * 3


def test_dockerfile_lint_flags_copy_of_credential_files():
    head = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    result = lint(head + "COPY .env /app/.env\nCOPY ./keys/id_rsa /root/.ssh/\nCOPY app.py /app/\n")
    assert ids(result) == ["docker.copy-secret-file"] * 2


def test_dockerfile_lint_single_stage_build_tooling_is_minor_and_multi_stage_is_fine():
    head = f"FROM golang:1.22@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n"
    single = lint(head + "RUN go build -o /app ./...\n")
    assert ids(single) == ["docker.single-stage-build-tools"] and at(single, "docker.single-stage-build-tools") == ["line 4"]
    multi = f"FROM golang:1.22@{DIGEST} AS build\nRUN go build -o /app ./...\nFROM alpine:3@{DIGEST}\nCOPY --from=build /app /app\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(multi)) == []
    assert ids(lint(head + "RUN echo make-dir\n")) == []


def test_dockerfile_lint_handles_continuations_comments_heredocs_and_escape_directive():
    content = (
        f"FROM debian:12@{DIGEST}\n"
        "# a comment\n"
        "RUN apt-get update \\\n"
        "    # comment inside the continuation\n"
        "    && apt-get install -y curl \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
        "RUN <<EOF\n"
        "curl -fsSL https://x.example | sh\n"
        "EOF\n"
        "USER app\nHEALTHCHECK CMD true\n"
    )
    result = lint(content)
    assert ids(result) == ["docker.pipe-to-shell"] and at(result, "docker.pipe-to-shell") == ["line 7"]
    assert result["instructions"] == 5
    backtick = f"# escape=`\nFROM mcr.microsoft.com/windows/servercore@{DIGEST}\nRUN apk add `\n  curl\nUSER app\nHEALTHCHECK CMD true\n"
    assert ids(lint(backtick)) == ["docker.pkg-cache-apk"]


def test_dockerfile_lint_findings_are_sorted_worst_first_and_counted():
    result = lint("FROM node\nENV DB_PASSWORD=x\nRUN pip install x\n")
    assert [f["severity"] for f in result["findings"]] == ["blocker", "major", "major", "minor", "minor"]
    assert result["findings_total"] == 5 and result["blocking_count"] == 3


def test_dockerfile_lint_caps_the_listed_findings_but_counts_all():
    content = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n" + "ADD src /a\n" * 250
    result = lint(content)
    assert result["findings_total"] == 250 and len(result["findings"]) == 200


def test_dockerfile_lint_rejects_content_without_from():
    invalid("supply_dockerfile_lint", {"content": "RUN echo hi\n"}, "content")
    invalid("supply_dockerfile_lint", {"content": "# only a comment\nARG X=1\n"}, "content")
    invalid("supply_dockerfile_lint", {"content": "FROM alpine", "require_healthcheck": "yes"}, "require_healthcheck")
    invalid("supply_dockerfile_lint", {"content": ""}, "content")


# --- supply_base_image_policy -----------------------------------------------------------------------------------


def policy(images: list, **extra) -> dict:
    payload = {"images": images, "allowed_registries": ["ghcr.io/acme", "docker.io/library"], "require_digest": False, "as_of": AS_OF, **extra}
    return ok("supply_base_image_policy", payload)


def test_base_image_policy_accepts_allowed_pinned_images():
    result = policy([{"ref": f"ghcr.io/acme/base:1.2@{DIGEST}"}, {"ref": f"alpine:3.20@{DIGEST}"}, {"ref": f"index.docker.io/library/alpine@{DIGEST}"}])
    assert result == {"state": "SUCCESS", "images_checked": 3, "violating_images": 0, "verdict": "pass", "findings_total": 0, "blocking_count": 0, "findings": []}


def test_base_image_policy_registry_rules_match_on_path_boundaries_and_aliases():
    result = policy([{"ref": f"ghcr.io/acme-evil/base:1@{DIGEST}"}, {"ref": f"quay.io/acme/base:1@{DIGEST}"}, {"ref": f"ghcr.io/acme/team/base:1@{DIGEST}"},
                     {"ref": f"docker.io/someone/tool:1@{DIGEST}"}])
    assert sorted(at(result, "image.registry-not-allowed")) == [f"docker.io/someone/tool:1@{DIGEST}", f"ghcr.io/acme-evil/base:1@{DIGEST}", f"quay.io/acme/base:1@{DIGEST}"]
    assert result["violating_images"] == 3 and result["verdict"] == "fail"
    whole_host = ok("supply_base_image_policy", {"images": [{"ref": f"registry.example.com:5000/any/thing:1@{DIGEST}"}], "allowed_registries": ["registry.example.com:5000"],
                                                   "require_digest": False, "as_of": AS_OF})
    assert whole_host["findings_total"] == 0
    hub = ok("supply_base_image_policy", {"images": [{"ref": f"nginx:1.27@{DIGEST}"}], "allowed_registries": ["index.docker.io"], "require_digest": False, "as_of": AS_OF})
    assert hub["findings_total"] == 0


def test_base_image_policy_latest_and_digest_requirement():
    result = policy([{"ref": "ghcr.io/acme/a"}, {"ref": "ghcr.io/acme/b:latest"}, {"ref": "ghcr.io/acme/c:1.0"}])
    assert sorted(ids(result)) == ["image.latest-tag", "image.latest-tag", "image.not-digest-pinned"]
    assert severity_of(result, "image.not-digest-pinned") == "minor" and result["violating_images"] == 2
    strict = policy([{"ref": "ghcr.io/acme/c:1.0"}], require_digest=True)
    assert severity_of(strict, "image.not-digest-pinned") == "major" and strict["verdict"] == "fail"


def test_base_image_policy_age_limit_boundary_is_strictly_greater():
    exact = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-12-01T00:00:00+00:00"}], max_age_days=90)
    assert exact["findings_total"] == 0
    over = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-11-30T23:59:59+00:00"}], max_age_days=90)
    assert ids(over) == ["image.too-old"] and over["verdict"] == "fail"
    zero = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": AS_OF}], max_age_days=0)
    assert zero["findings_total"] == 0
    unknown = policy([{"ref": f"alpine:3@{DIGEST}"}], max_age_days=30)
    assert ids(unknown) == ["image.age-unknown"] and unknown["verdict"] == "fail"  # the requested age check did not run
    assert policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2020-01-01T00:00:00+00:00"}])["findings_total"] == 0


def test_base_image_policy_age_uses_instants_not_wall_clock_dates():
    exact = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-12-01T05:30:00+05:30"}], max_age_days=90)  # = 2025-12-01T00:00:00Z
    assert ids(exact) == []
    over = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-12-01T05:29:59+05:30"}], max_age_days=90)
    assert ids(over) == ["image.too-old"]


@pytest.mark.parametrize(("eol", "expected"), [
    ("2026-02-28", ["image.end-of-life"]),
    ("2026-03-01", ["image.end-of-life"]),
    ("2026-03-02", ["image.eol-soon"]),
    ("2026-05-30", ["image.eol-soon"]),
    ("2026-05-31", []),
    ("2026-03-02T00:00:00+00:00", ["image.eol-soon"]),
])
def test_base_image_policy_eol_boundaries_with_default_90_day_warning(eol, expected):
    assert ids(policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": eol}])) == expected


def test_base_image_policy_eol_warning_window_is_configurable_and_end_of_life_fails():
    assert ids(policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": "2026-03-11"}], eol_warning_days=9)) == []
    assert ids(policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": "2026-03-11"}], eol_warning_days=10)) == ["image.eol-soon"]
    eol = policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": "2025-01-01"}])
    assert eol["verdict"] == "fail" and eol["violating_images"] == 1
    soon = policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": "2026-04-01"}])
    assert soon["verdict"] == "pass" and soon["violating_images"] == 0


def test_base_image_policy_rejects_semantic_problems_naming_the_field():
    base = {"images": [{"ref": f"alpine:3@{DIGEST}"}], "allowed_registries": ["docker.io"], "require_digest": False, "as_of": AS_OF}
    invalid("supply_base_image_policy", {**base, "as_of": "2026-03-01T00:00:00"}, "as_of")
    invalid("supply_base_image_policy", {**base, "as_of": "yesterday"}, "as_of")
    invalid("supply_base_image_policy", {**base, "allowed_registries": ["bad registry"]}, "allowed_registries")
    invalid("supply_base_image_policy", {**base, "allowed_registries": ["/"]}, "allowed_registries")
    invalid("supply_base_image_policy", {**base, "images": [{"ref": "Alpine Linux"}]}, "images")
    invalid("supply_base_image_policy", {**base, "images": [{"ref": "alpine@sha256:123"}]}, "images")
    invalid("supply_base_image_policy", {**base, "images": [{"ref": "alpine:3", "created_at": "2026-01-01"}]}, "images")
    invalid("supply_base_image_policy", {**base, "images": [{"ref": "alpine:3", "created_at": "2026-03-02T00:00:00+00:00"}]}, "images")
    invalid("supply_base_image_policy", {**base, "images": [{"ref": "alpine:3", "eol_date": "March"}]}, "images")


# --- supply_sbom_validate ---------------------------------------------------------------------------------------


def cdx_component(name="zlib", version="1.3.1", **extra) -> dict:
    component = {"type": "library", "name": name, "version": version, "bom-ref": name, "supplier": {"name": "Acme"}, "purl": f"pkg:generic/{name}@{version}"}
    component.update(extra)
    return {k: v for k, v in component.items() if v is not None}


def cdx(components=None, root=True, dependencies="auto", **metadata) -> str:
    meta = {"timestamp": "2026-02-01T10:00:00Z", "authors": [{"name": "build-bot"}], **metadata}
    if root:
        meta["component"] = cdx_component("app", "1.0.0")
    comps = [cdx_component()] if components is None else components
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.5", "metadata": meta, "components": comps}
    if dependencies == "auto":
        refs = [c["bom-ref"] for c in comps if c.get("bom-ref")]
        doc["dependencies"] = [{"ref": "app", "dependsOn": refs}] + [{"ref": r, "dependsOn": []} for r in refs]
    elif dependencies is not None:
        doc["dependencies"] = dependencies
    return json.dumps(doc)


def validate(document: str) -> dict:
    return ok("supply_sbom_validate", {"document": document})


def element(result: dict, name: str) -> dict:
    return next(e for e in result["ntia_elements"] if e["element"] == name)


def test_sbom_validate_compliant_cyclonedx():
    result = validate(cdx())
    assert result["format"] == "cyclonedx" and result["spec_version"] == "1.5" and result["component_count"] == 2
    assert result["ntia_compliant"] is True and result["verdict"] == "pass" and result["findings_total"] == 0
    assert [e["element"] for e in result["ntia_elements"]] == [
        "supplier_name", "component_name", "component_version", "unique_identifier", "dependency_relationship", "sbom_author", "timestamp"]
    assert element(result, "supplier_name") == {"element": "supplier_name", "satisfied": True, "covered": 2, "total": 2}
    assert element(result, "timestamp") == {"element": "timestamp", "satisfied": True, "covered": 1, "total": 1}


def test_sbom_validate_reports_component_level_ntia_gaps_with_counts():
    components = [cdx_component("a", "1", supplier=None), cdx_component("b", "", purl=None), cdx_component("c", "3", supplier=None, purl="")]
    result = validate(cdx(components))
    assert result["component_count"] == 4 and result["ntia_compliant"] is False and result["verdict"] == "fail"
    assert element(result, "supplier_name")["covered"] == 2 and element(result, "component_version")["covered"] == 3
    assert element(result, "unique_identifier")["covered"] == 2 and element(result, "component_name")["satisfied"] is True
    assert sorted(ids(result)) == ["ntia.component_version", "ntia.supplier_name", "ntia.unique_identifier"]
    summary = next(f["summary"] for f in result["findings"] if f["id"] == "ntia.supplier_name")
    assert "2 of 4" in summary and "a" in summary and "c" in summary


def test_sbom_validate_accepts_cpe_or_swid_as_unique_identifier():
    components = [cdx_component("a", "1", purl=None, cpe="cpe:2.3:a:acme:a:1:*:*:*:*:*:*:*"), cdx_component("b", "1", purl=None, swid={"tagId": "x", "name": "b"})]
    assert element(validate(cdx(components)), "unique_identifier")["satisfied"] is True


def test_sbom_validate_author_may_be_a_tool_and_timestamp_needs_an_offset():
    no_author = validate(cdx(authors=[]))
    assert element(no_author, "sbom_author")["satisfied"] is False and ids(no_author) == ["ntia.sbom_author"]
    assert element(validate(cdx(authors=[], tools=[{"vendor": "anchore", "name": "syft"}])), "sbom_author")["satisfied"] is True
    assert element(validate(cdx(authors=[], tools={"components": [{"name": "syft"}]})), "sbom_author")["satisfied"] is True
    assert element(validate(cdx(authors=[{"email": "sec@example.com"}])), "sbom_author")["satisfied"] is True
    naive = validate(cdx(timestamp="2026-02-01T10:00:00"))
    assert element(naive, "timestamp")["satisfied"] is False and ids(naive) == ["ntia.timestamp"]
    assert element(validate(cdx(timestamp="2026-02-01T10:00:00+02:00")), "timestamp")["satisfied"] is True


def test_sbom_validate_dependency_relationship_rules():
    components = [cdx_component("a", "1"), cdx_component("b", "2")]
    assert element(validate(cdx(components, dependencies=None)), "dependency_relationship")["satisfied"] is False
    leaf_only = [{"ref": "app"}, {"ref": "a"}, {"ref": "b"}]
    assert element(validate(cdx(components, dependencies=leaf_only)), "dependency_relationship")["satisfied"] is False
    one = validate(cdx([cdx_component("a", "1")], dependencies=[{"ref": "app"}]))
    assert element(one, "dependency_relationship")["satisfied"] is True
    dangling = validate(cdx(components, dependencies=[{"ref": "app", "dependsOn": ["a", "ghost"]}]))
    assert "sbom.dangling-reference" in ids(dangling) and element(dangling, "dependency_relationship")["satisfied"] is False
    assert at(dangling, "sbom.dangling-reference") == ["ghost"]


def test_sbom_validate_structure_findings():
    dup = validate(cdx([cdx_component("a", "1"), cdx_component("a", "2")]))
    assert "sbom.duplicate-bom-ref" in ids(dup) and dup["verdict"] == "fail"
    empty = validate(cdx([]))
    assert "sbom.no-components" in ids(empty) and empty["component_count"] == 1
    newer = validate(cdx().replace('"1.5"', '"1.8"'))  # 1.7 is a published version (see test_sbom_validate_accepts_cyclonedx_1_7...)
    assert ids(newer) == ["sbom.unknown-spec-version"] and newer["verdict"] == "pass" and newer["spec_version"] == "1.8"


def test_sbom_validate_counts_nested_components():
    nested = cdx_component("outer", "1", components=[cdx_component("inner", "2")])
    result = validate(cdx([nested]))
    assert result["component_count"] == 3 and element(result, "component_version")["covered"] == 3


def spdx(**overrides) -> str:
    doc = {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT", "name": "app", "documentNamespace": "https://example.com/spdx/app-1",
        "creationInfo": {"created": "2026-02-01T10:00:00Z", "creators": ["Tool: syft-1.0", "Organization: Acme"]},
        "packages": [
            {"name": "app", "SPDXID": "SPDXRef-app", "versionInfo": "1.0.0", "supplier": "Organization: Acme", "downloadLocation": "NOASSERTION",
             "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": "pkg:generic/app@1.0.0"}],
             "licenseConcluded": "MIT"},
            {"name": "zlib", "SPDXID": "SPDXRef-zlib", "versionInfo": "1.3.1", "supplier": "Organization: zlib.net", "downloadLocation": "NOASSERTION",
             "externalRefs": [{"referenceCategory": "SECURITY", "referenceType": "cpe23Type", "referenceLocator": "cpe:2.3:a:zlib:zlib:1.3.1:*:*:*:*:*:*:*"}]},
        ],
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-app"},
            {"spdxElementId": "SPDXRef-app", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": "SPDXRef-zlib"},
        ],
    }
    doc.update(overrides)
    return json.dumps(doc)


def test_sbom_validate_compliant_spdx():
    result = validate(spdx())
    assert result["format"] == "spdx" and result["spec_version"] == "2.3" and result["component_count"] == 2
    assert result["ntia_compliant"] is True and result["findings_total"] == 0 and result["verdict"] == "pass"


def test_sbom_validate_spdx_document_level_findings():
    assert "sbom.spdx-data-license" in ids(validate(spdx(dataLicense="MIT")))
    assert "sbom.spdx-document-id" in ids(validate(spdx(SPDXID="SPDXRef-doc")))
    assert "sbom.spdx-missing-field" in ids(validate(spdx(documentNamespace="")))
    assert ids(validate(spdx(spdxVersion="SPDX-2.1"))) == ["sbom.unknown-spec-version"]
    bad_creator = validate(spdx(creationInfo={"created": "2026-02-01T10:00:00Z", "creators": ["syft"]}))
    assert sorted(ids(bad_creator)) == ["ntia.sbom_author", "sbom.spdx-bad-creator"]
    no_time = validate(spdx(creationInfo={"created": "2026-02-01T10:00:00", "creators": ["Tool: x"]}))
    assert ids(no_time) == ["ntia.timestamp"]


def test_sbom_validate_spdx_supplier_noassertion_does_not_count_and_relationships_are_checked():
    doc = json.loads(spdx())
    doc["packages"][1]["supplier"] = "NOASSERTION"
    result = validate(json.dumps(doc))
    assert element(result, "supplier_name")["covered"] == 1 and ids(result) == ["ntia.supplier_name"]
    doc = json.loads(spdx())
    doc["relationships"] = [doc["relationships"][1]]
    assert ids(validate(json.dumps(doc))) == ["sbom.no-root"]
    doc["relationships"].append({"spdxElementId": "SPDXRef-app", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": "SPDXRef-ghost"})
    assert "sbom.dangling-reference" in ids(validate(json.dumps(doc)))
    doc = json.loads(spdx())
    doc["relationships"] = doc["relationships"][:1]
    assert ids(validate(json.dumps(doc))) == ["ntia.dependency_relationship"]
    doc = json.loads(spdx())
    doc["packages"][1]["SPDXID"] = "SPDXRef-app"
    assert "sbom.duplicate-spdxid" in ids(validate(json.dumps(doc)))
    doc = json.loads(spdx())
    doc["packages"] = []
    doc["relationships"] = doc["relationships"][:1]
    assert "sbom.no-components" in ids(validate(json.dumps(doc)))


def test_sbom_validate_rejects_unreadable_documents_naming_the_field():
    invalid("supply_sbom_validate", {"document": "{not json"}, "document")
    invalid("supply_sbom_validate", {"document": "[1, 2]"}, "document")
    invalid("supply_sbom_validate", {"document": json.dumps({"spdxVersion": "SPDX-3.0"})}, "document")
    invalid("supply_sbom_validate", {"document": json.dumps({"bomFormat": "Other"})}, "document")
    invalid("supply_sbom_validate", {"document": "x"}, "document")


# --- supply_sbom_diff -------------------------------------------------------------------------------------------


def diff(before: str, after: str) -> dict:
    return ok("supply_sbom_diff", {"before": before, "after": after})


def lic(*ids_: str) -> list:
    return [{"license": {"id": i}} for i in ids_]


def test_sbom_diff_identical_sboms_are_unchanged():
    result = diff(cdx(), cdx())
    assert result["verdict"] == "unchanged" and result["findings_total"] == 0
    assert (result["added_count"], result["removed_count"], result["version_changed_count"], result["license_changed_count"]) == (0, 0, 0, 0)


def test_sbom_diff_reports_added_removed_and_upgrades():
    before = cdx([cdx_component("zlib", "1.3.1"), cdx_component("gone", "1.0")], root=False)
    after = cdx([cdx_component("zlib", "1.3.2"), cdx_component("fresh", "2.0")], root=False)
    result = diff(before, after)
    assert result["added"] == [{"name": "fresh", "version": "2.0"}] and result["removed"] == [{"name": "gone", "version": "1.0"}]
    assert result["version_changed"] == [{"name": "zlib", "before": "1.3.1", "after": "1.3.2", "direction": "upgrade"}]
    assert (result["added_count"], result["removed_count"], result["version_changed_count"], result["downgrade_count"]) == (1, 1, 1, 0)
    assert result["verdict"] == "changed" and result["findings_total"] == 0


def test_sbom_diff_downgrade_is_a_major_finding_and_needs_review():
    result = diff(cdx([cdx_component("zlib", "1.10.0")], root=False), cdx([cdx_component("zlib", "1.9.9")], root=False))
    assert result["version_changed"][0]["direction"] == "downgrade" and result["downgrade_count"] == 1
    assert ids(result) == ["sbom.version-downgrade"] and result["blocking_count"] == 1 and result["verdict"] == "review"


def test_sbom_diff_version_comparison_is_numeric_per_segment_and_tolerates_prefixes_and_suffixes():
    def direction(old: str, new: str) -> str:
        return diff(cdx([cdx_component("x", old)], root=False), cdx([cdx_component("x", new)], root=False))["version_changed"][0]["direction"]

    assert direction("1.9.0", "1.10.0") == "upgrade"
    assert direction("v2.0.0", "v1.9.0") == "downgrade"
    assert direction("1.0.0", "1.0.0-rc1") == "downgrade"  # semver.org item 11: a pre-release is lower than its release
    assert direction("1.0.0-rc1", "1.0.1-rc1") == "upgrade"
    assert direction("1.0", "1.0.0") == "changed"
    assert direction("1.0", "1.0.1") == "upgrade"
    assert direction("abc", "def") == "changed"


def test_sbom_diff_license_change_is_minor_and_review():
    before = cdx([cdx_component("lib", "1.0", licenses=lic("MIT"))], root=False)
    after = cdx([cdx_component("lib", "1.0", licenses=lic("GPL-3.0-only"))], root=False)
    result = diff(before, after)
    assert result["license_changed"] == [{"name": "lib", "before": "MIT", "after": "GPL-3.0-only"}]
    assert ids(result) == ["sbom.license-changed"] and result["verdict"] == "review" and result["blocking_count"] == 0
    expr = cdx([cdx_component("lib", "1.0", licenses=[{"expression": "MIT OR Apache-2.0"}])], root=False)
    assert diff(expr, expr)["license_changed_count"] == 0
    multi_a = cdx([cdx_component("lib", "1.0", licenses=lic("MIT", "Apache-2.0"))], root=False)
    multi_b = cdx([cdx_component("lib", "1.0", licenses=lic("Apache-2.0", "MIT"))], root=False)
    assert diff(multi_a, multi_b)["license_changed_count"] == 0


def test_sbom_diff_identity_is_purl_without_version_and_works_across_formats():
    left = cdx([cdx_component("zlib", "1.3.1", purl="pkg:generic/zlib@1.3.1?arch=x86")], root=False)
    right = cdx([cdx_component("renamed", "1.3.2", purl="pkg:generic/zlib@1.3.2")], root=False)
    assert diff(left, right)["version_changed"] == [{"name": "renamed", "before": "1.3.1", "after": "1.3.2", "direction": "upgrade"}]
    doc = json.loads(spdx())
    doc["packages"][1]["externalRefs"] = [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": "pkg:generic/zlib@1.3.1"}]
    mixed = diff(json.dumps(doc), cdx([cdx_component("zlib", "1.3.1")], root=False))
    assert mixed["added_count"] == 0 and mixed["version_changed_count"] == 0 and mixed["removed"] == [{"name": "app", "version": "1.0.0"}]


def test_sbom_diff_multiple_versions_of_one_component_are_listed_individually():
    before = cdx([cdx_component("x", "1", **{"bom-ref": "x1"}), cdx_component("x", "2", **{"bom-ref": "x2"})], root=False, dependencies=None)
    after = cdx([cdx_component("x", "2", **{"bom-ref": "x2"}), cdx_component("x", "3", **{"bom-ref": "x3"}), cdx_component("x", "4", **{"bom-ref": "x4"})], root=False, dependencies=None)
    result = diff(before, after)
    assert result["added"] == [{"name": "x", "version": "3"}, {"name": "x", "version": "4"}]
    assert result["removed"] == [{"name": "x", "version": "1"}] and result["version_changed_count"] == 0


def test_sbom_diff_rejects_unreadable_sides_naming_the_field():
    invalid("supply_sbom_diff", {"before": "{bad", "after": cdx()}, "before")
    invalid("supply_sbom_diff", {"before": cdx(), "after": json.dumps({"hello": 1})}, "after")


# --- supply_slsa_level and supply_provenance_match ----------------------------------------------------------------

BUILDER = "https://github.com/actions/runner/github-hosted"


def statement(**changes) -> dict:
    base = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "ghcr.io/acme/app", "digest": {"sha256": HEX}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {"buildType": "https://actions.github.io/buildtypes/workflow/v1", "externalParameters": {"workflow": {"ref": "refs/heads/main"}}},
            "runDetails": {"builder": {"id": BUILDER}},
        },
    }
    base.update(changes)
    return base


def dsse(stmt: dict) -> str:
    payload = base64.b64encode(json.dumps(stmt).encode()).decode()
    return json.dumps({"payloadType": "application/vnd.in-toto+json", "payload": payload, "signatures": [{"sig": "x"}]})


def slsa(prov, **facts) -> dict:
    payload = {"provenance": prov if isinstance(prov, str) else json.dumps(prov), "signature_verified": False, "trusted_builder_ids": [],
               "isolated_build": False, "signing_key_isolated": False, "required_level": 1, **facts}
    return ok("supply_slsa_level", payload)


def test_slsa_level_1_when_provenance_is_well_formed_but_unverified():
    result = slsa(statement())
    assert result["level"] == 1 and result["meets_required"] is True and result["verdict"] == "pass" and result["findings_total"] == 0
    assert result["predicate_version"] == "v1" and result["builder_id"] == BUILDER and result["subject_count"] == 1
    assert result["build_type"] == "https://actions.github.io/buildtypes/workflow/v1"
    assert result["next_level_gaps"] == [
        "the provenance signature has not been verified", f"builder {BUILDER!r} is not on the trusted hosted-builder list"]


def test_slsa_level_2_needs_verified_signature_and_trusted_builder():
    result = slsa(statement(), signature_verified=True, trusted_builder_ids=[BUILDER], required_level=2)
    assert result["level"] == 2 and result["verdict"] == "pass" and len(result["next_level_gaps"]) == 2
    untrusted = slsa(statement(), signature_verified=True, trusted_builder_ids=[BUILDER + "/"], required_level=2)
    assert untrusted["level"] == 1 and untrusted["verdict"] == "fail" and ids(untrusted) == ["slsa.l2-builder-untrusted"]
    unsigned = slsa(statement(), signature_verified=False, trusted_builder_ids=[BUILDER], required_level=2)
    assert unsigned["level"] == 1 and ids(unsigned) == ["slsa.l2-signature-unverified"] and unsigned["blocking_count"] == 1


def test_slsa_level_3_needs_isolation_and_key_protection_on_top_of_level_2():
    full = slsa(statement(), signature_verified=True, trusted_builder_ids=[BUILDER], isolated_build=True, signing_key_isolated=True, required_level=3)
    assert full["level"] == 3 and full["meets_required"] is True and full["next_level_gaps"] == [] and full["findings_total"] == 0
    partial = slsa(statement(), signature_verified=True, trusted_builder_ids=[BUILDER], isolated_build=True, required_level=3)
    assert partial["level"] == 2 and partial["verdict"] == "fail" and ids(partial) == ["slsa.l3-signing-key-exposed"]
    assert partial["next_level_gaps"] == ["the signing key is not attested unreachable from user-defined build steps"]
    skipped_l2 = slsa(statement(), isolated_build=True, signing_key_isolated=True, required_level=3)
    assert skipped_l2["level"] == 1 and sorted(ids(skipped_l2)) == ["slsa.l2-builder-untrusted", "slsa.l2-signature-unverified"]


def test_slsa_level_0_for_incomplete_provenance_and_findings_only_up_to_required_level():
    broken = statement(subject=[{"name": "x", "digest": {"sha1": "a" * 40}}])
    broken["predicate"] = {"buildDefinition": {"buildType": "", "externalParameters": {}}, "runDetails": {"builder": {}}}
    relaxed = slsa(broken, required_level=0)
    assert relaxed["level"] == 0 and relaxed["verdict"] == "pass" and relaxed["findings_total"] == 0 and relaxed["builder_id"] == ""
    strict = slsa(broken, required_level=1)
    assert strict["verdict"] == "fail" and sorted(ids(strict)) == [
        "slsa.l1-build-type", "slsa.l1-builder-id", "slsa.l1-external-parameters", "slsa.l1-subject"]
    assert slsa(statement(subject=[]), required_level=1)["level"] == 0


def test_slsa_level_v02_provenance_is_mapped_by_builder_and_build_type():
    v02 = statement(predicateType="https://slsa.dev/provenance/v0.2", predicate={"builder": {"id": BUILDER}, "buildType": "https://example.com/build/v1"})
    result = slsa(v02, signature_verified=True, trusted_builder_ids=[BUILDER], required_level=2)
    assert result["level"] == 2 and result["predicate_version"] == "v0.2" and ids(result) == ["slsa.predicate-v0.2"]
    assert severity_of(result, "slsa.predicate-v0.2") == "info" and result["verdict"] == "pass"


def test_slsa_level_reads_dsse_envelopes_and_sigstore_bundles():
    expected = slsa(statement())
    assert slsa(dsse(statement())) == expected
    bundle = json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3", "dsseEnvelope": json.loads(dsse(statement()))})
    assert slsa(bundle) == expected
    urlsafe = dsse(statement(subject=[{"name": "a?>>>", "digest": {"sha256": HEX}}]))
    raw = json.loads(urlsafe)
    raw["payload"] = base64.urlsafe_b64encode(base64.b64decode(raw["payload"])).decode().rstrip("=")
    assert slsa(json.dumps(raw))["level"] == 1


def test_slsa_level_rejects_unusable_attestations_naming_the_field():
    base = {"signature_verified": False, "trusted_builder_ids": [], "isolated_build": False, "signing_key_isolated": False, "required_level": 1}
    invalid("supply_slsa_level", {**base, "provenance": "nope"}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": "[]"}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": json.dumps({"hello": "world"})}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": json.dumps(statement(predicateType="https://cyclonedx.org/bom"))}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": json.dumps(statement(predicateType="https://slsa.dev/provenance/v0.1"))}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": json.dumps({"payloadType": "x", "payload": "%%%"})}, "provenance")
    invalid("supply_slsa_level", {**base, "provenance": json.dumps(statement()), "required_level": 4}, "required_level")


def match(prov, digest=DIGEST, **extra) -> dict:
    return ok("supply_provenance_match", {"provenance": prov if isinstance(prov, str) else json.dumps(prov), "artifact_digest": digest, **extra})


def test_provenance_match_finds_the_artifact_digest_among_subjects():
    result = match(statement(), artifact_name="ghcr.io/acme/app")
    assert result == {"state": "SUCCESS", "matched": True, "algorithm": "sha256", "matched_subject": "ghcr.io/acme/app", "subject_count": 1,
                      "verdict": "pass", "findings_total": 0, "blocking_count": 0, "findings": []}
    many = statement(subject=[{"name": "a", "digest": {"sha256": OTHER_DIGEST[7:]}}, {"name": "b", "digest": {"sha256": HEX}}])
    assert match(many)["matched_subject"] == "b"
    assert match(dsse(statement()))["matched"] is True


def test_provenance_match_is_case_insensitive_on_hex():
    upper = statement(subject=[{"name": "x", "digest": {"sha256": HEX.upper()}}])
    assert match(upper)["matched"] is True and match(statement(), digest="sha256:" + HEX.upper())["matched"] is True


def test_provenance_match_digest_not_among_subjects_is_a_blocker():
    result = match(statement(), digest=OTHER_DIGEST)
    assert result["matched"] is False and result["matched_subject"] == "" and result["verdict"] == "fail"
    assert ids(result) == ["provenance.digest-not-in-subjects"] and severity_of(result, "provenance.digest-not-in-subjects") == "blocker"


def test_provenance_match_same_name_with_other_digest_is_reported_as_mismatch():
    result = match(statement(), digest=OTHER_DIGEST, artifact_name="ghcr.io/acme/app")
    assert sorted(ids(result)) == ["provenance.digest-mismatch", "provenance.digest-not-in-subjects"] and result["blocking_count"] == 2
    assert match(statement(), digest=OTHER_DIGEST, artifact_name="other")["findings_total"] == 1


def test_provenance_match_name_must_agree_when_given():
    result = match(statement(), artifact_name="ghcr.io/acme/other")
    assert result["matched"] is False and ids(result) == ["provenance.name-mismatch"] and result["matched_subject"] == "ghcr.io/acme/app"
    assert result["verdict"] == "fail"
    twin = statement(subject=[{"name": "a", "digest": {"sha256": HEX}}, {"name": "b", "digest": {"sha256": HEX}}])
    assert match(twin, artifact_name="b")["matched_subject"] == "b"


def test_provenance_match_other_algorithms_and_missing_subjects():
    sha512 = "f" * 128
    stmt = statement(subject=[{"name": "x", "digest": {"sha512": sha512}}])
    assert match(stmt, digest=f"sha512:{sha512}")["matched"] is True
    assert match(stmt)["matched"] is False
    none = match(statement(subject=[]))
    assert none["matched"] is False and ids(none) == ["provenance.no-subject"] and none["verdict"] == "fail"


def test_provenance_match_rejects_malformed_inputs_naming_the_field():
    for digest in ("sha256:abc", "md5:" + "a" * 32, "nodigest-at-all", "sha256:" + "g" * 64):
        invalid("supply_provenance_match", {"provenance": json.dumps(statement()), "artifact_digest": digest}, "artifact_digest")
    invalid("supply_provenance_match", {"provenance": "{", "artifact_digest": DIGEST}, "provenance")
    invalid("supply_provenance_match", {"provenance": json.dumps({"_type": "https://example.com/other", "predicateType": "x"}), "artifact_digest": DIGEST}, "provenance")


# --- supply_signature_policy ------------------------------------------------------------------------------------

ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = "https://github.com/acme/app/.github/workflows/release.yml"


def signer(identity=f"{WORKFLOW}@refs/tags/v1.0.0", issuer=ISSUER, verified=True, log=True) -> dict:
    return {"signer": identity, "issuer": issuer, "verified": verified, "in_transparency_log": log}


def rule(name="release", identity=f"{WORKFLOW}@refs/tags/*", issuer=ISSUER, required=True) -> dict:
    return {"name": name, "issuer": issuer, "identity": identity, "required": required}


def sigpolicy(signatures, rules=None, minimum=0, log=False) -> dict:
    return ok("supply_signature_policy", {"signatures": signatures, "rules": rules or [rule()], "min_valid_signatures": minimum, "require_transparency_log": log})


def test_signature_policy_passes_when_a_verified_signature_matches_the_rule():
    result = sigpolicy([signer()], minimum=1, log=True)
    assert result == {"state": "SUCCESS", "valid_signatures": 1, "unverified_signatures": 0, "rules_satisfied": 1, "required_rules_missing": [],
                      "unexpected_signers": [], "verdict": "pass", "findings_total": 0, "blocking_count": 0, "findings": []}


@pytest.mark.parametrize(("pattern", "identity", "expected"), [
    ("abc", "abc", True), ("abc", "abcd", False), ("*", "anything", True), ("a*c", "abc", True), ("a*c", "ac", True),
    ("a*c", "ab", False), ("a*b*c", "aXbYc", True), ("a*b*c", "abc", True), ("a*b*c", "ac", False), ("a*b*b", "ab", False),
    ("a*b*b", "abb", True), ("ab*bc", "abc", False), ("ab*bc", "abbc", True), ("*x*", "x", True), ("*x*", "y", False), ("a**c", "ac", True),
    ("v1.*", "v1x2", False), ("(a+)", "(a+)", True),
])
def test_signature_policy_identity_glob_semantics(pattern, identity, expected):
    result = sigpolicy([signer(identity=identity)], rules=[rule(identity=pattern)])
    assert (result["rules_satisfied"] == 1) is expected


def test_signature_policy_missing_required_signer_is_a_blocker():
    result = sigpolicy([signer(identity=f"{WORKFLOW}@refs/heads/main")])
    assert result["rules_satisfied"] == 0 and result["required_rules_missing"] == ["release"] and result["verdict"] == "fail"
    assert ids(result) == ["signature.required-signer-missing", "signature.unexpected-signer"]
    assert severity_of(result, "signature.required-signer-missing") == "blocker"
    assert sigpolicy([])["required_rules_missing"] == ["release"]


def test_signature_policy_issuer_must_match_exactly():
    result = sigpolicy([signer(issuer="https://accounts.google.com")])
    assert result["required_rules_missing"] == ["release"]


def test_signature_policy_optional_rule_may_be_unmet():
    rules = [rule(), rule(name="notary", identity="notary@acme.example", issuer="https://accounts.google.com", required=False)]
    result = sigpolicy([signer()], rules=rules)
    assert result["rules_satisfied"] == 1 and result["verdict"] == "pass" and result["findings_total"] == 0


def test_signature_policy_unverified_signature_is_a_blocker_and_does_not_count():
    result = sigpolicy([signer(verified=False)], minimum=1)
    assert result["unverified_signatures"] == 1 and result["valid_signatures"] == 0 and result["verdict"] == "fail"
    assert sorted(ids(result)) == ["signature.required-signer-missing", "signature.too-few", "signature.verification-failed"]
    assert severity_of(result, "signature.verification-failed") == "blocker"
    both = sigpolicy([signer(verified=False), signer()])
    assert both["verdict"] == "fail" and both["valid_signatures"] == 1 and ids(both) == ["signature.verification-failed"]


def test_signature_policy_transparency_log_requirement():
    unlogged = signer(log=False)
    assert sigpolicy([unlogged])["verdict"] == "pass"
    strict = sigpolicy([unlogged], log=True)
    assert strict["valid_signatures"] == 0 and strict["required_rules_missing"] == ["release"]
    assert "signature.not-in-transparency-log" in ids(strict) and severity_of(strict, "signature.not-in-transparency-log") == "major"


def test_signature_policy_minimum_counts_distinct_issuer_signer_pairs():
    twice = sigpolicy([signer(), signer()], minimum=2)
    assert twice["valid_signatures"] == 2 and ids(twice) == ["signature.too-few"] and severity_of(twice, "signature.too-few") == "major"
    other = signer(identity="https://github.com/acme/app/.github/workflows/release.yml@refs/tags/v1.0.1")
    assert sigpolicy([signer(), other], minimum=2)["findings_total"] == 0
    assert sigpolicy([], rules=[rule(required=False)], minimum=0)["verdict"] == "pass"


def test_signature_policy_unexpected_signers_are_minor_and_sorted():
    result = sigpolicy([signer(), signer(identity="zed"), signer(identity="alice")])
    assert result["unexpected_signers"] == ["alice", "zed"] and result["verdict"] == "pass"
    assert [f["severity"] for f in result["findings"]] == ["minor", "minor"]


def test_signature_policy_rejects_invalid_rules_naming_the_field():
    base = {"signatures": [signer()], "min_valid_signatures": 0, "require_transparency_log": False}
    invalid("supply_signature_policy", {**base, "rules": [rule(), rule()]}, "rules")
    invalid("supply_signature_policy", {**base, "rules": [rule(name=" ")]}, "rules")
    invalid("supply_signature_policy", {**base, "rules": [rule(identity=" ")]}, "rules")
    invalid("supply_signature_policy", {**base, "rules": [rule(issuer="  ")]}, "rules")
    invalid("supply_signature_policy", {**base, "rules": []}, "rules")


# --- supply_vex_apply -------------------------------------------------------------------------------------------

REQUESTS = "pkg:pypi/requests@2.31.0"
T0 = "2026-02-01T00:00:00+00:00"
T1 = "2026-02-02T00:00:00+00:00"


def vuln(vid="CVE-2026-0001", product=REQUESTS, severity="high") -> dict:
    return {"id": vid, "product": product, "severity": severity}


def stmt(vid="CVE-2026-0001", products=(REQUESTS,), status="not_affected", timestamp=T0, justification="vulnerable_code_not_in_execute_path", **extra) -> dict:
    out = {"vulnerability": vid, "products": list(products), "status": status, "timestamp": timestamp, **extra}
    if justification:
        out["justification"] = justification
    return out


def vex(vulns, statements) -> dict:
    return ok("supply_vex_apply", {"vulnerabilities": vulns, "statements": statements})


def test_vex_apply_justified_not_affected_suppresses_the_finding():
    result = vex([vuln()], [stmt()])
    assert result["verdict"] == "clear" and result["exposed_count"] == 0 and result["suppressed_count"] == 1 and result["total"] == 1
    assert result["suppressed"] == [{"id": "CVE-2026-0001", "product": REQUESTS, "status": "not_affected", "justification": "vulnerable_code_not_in_execute_path"}]
    assert result["findings_total"] == 0 and result["unmatched_statements"] == 0 and result["ignored_statements"] == 0


def test_vex_apply_without_statements_everything_is_exposed_as_unassessed():
    result = vex([vuln(), vuln("CVE-2026-0002", severity="critical")], [])
    assert result["verdict"] == "exposed" and result["exposed_count"] == 2
    assert [e["id"] for e in result["exposed"]] == ["CVE-2026-0002", "CVE-2026-0001"]
    assert {e["status"] for e in result["exposed"]} == {"unassessed"}


def test_vex_apply_not_affected_needs_justification_or_impact_statement():
    impact = vex([vuln()], [stmt(justification="", impact_statement="Only built for the CLI.")])
    assert impact["verdict"] == "clear" and impact["suppressed"][0]["justification"] == ""
    bare = vex([vuln()], [stmt(justification="")])
    assert bare["verdict"] == "exposed" and bare["ignored_statements"] == 1 and bare["exposed"][0]["status"] == "unassessed"
    assert sorted(ids(bare)) == ["CVE-2026-0001", "vex.not-affected-unjustified"]
    assert severity_of(bare, "vex.not-affected-unjustified") == "major"
    blank = vex([vuln()], [stmt(justification="", impact_statement="   ")])
    assert blank["ignored_statements"] == 1


def test_vex_apply_newest_statement_wins_and_invalid_ones_do_not_shadow_older_ones():
    older_ok = vex([vuln()], [stmt(timestamp=T0), stmt(status="affected", timestamp=T1, justification="")])
    assert older_ok["verdict"] == "exposed" and older_ok["exposed"][0]["status"] == "affected"
    newer_ok = vex([vuln()], [stmt(status="affected", timestamp=T0, justification=""), stmt(timestamp=T1)])
    assert newer_ok["verdict"] == "clear"
    shadow = vex([vuln()], [stmt(status="affected", timestamp=T0, justification=""), stmt(timestamp=T1, justification="")])
    assert shadow["exposed"][0]["status"] == "affected" and shadow["ignored_statements"] == 1


def test_vex_apply_equal_timestamps_resolve_conservatively_even_across_offsets():
    same_instant = "2026-02-01T01:00:00+01:00"
    result = vex([vuln()], [stmt(timestamp=T0), stmt(status="affected", timestamp=same_instant, justification="")])
    assert result["exposed"][0]["status"] == "affected"
    result = vex([vuln()], [stmt(status="fixed", timestamp=T0, justification=""), stmt(status="under_investigation", timestamp=T0, justification="")])
    assert result["exposed"][0]["status"] == "under_investigation"
    result = vex([vuln()], [stmt(status="fixed", timestamp=T0, justification=""), stmt(timestamp=T0)])
    assert result["suppressed"][0]["status"] == "fixed"


def test_vex_apply_status_semantics():
    assert vex([vuln()], [stmt(status="fixed", justification="")])["suppressed"][0]["status"] == "fixed"
    investigating = vex([vuln()], [stmt(status="under_investigation", justification="")])
    assert investigating["exposed"][0]["status"] == "under_investigation" and investigating["verdict"] == "exposed"


def test_vex_apply_product_matching_follows_package_url_identity():
    versionless = vex([vuln()], [stmt(products=["pkg:pypi/requests"])])
    assert versionless["suppressed_count"] == 1
    other_version = vex([vuln()], [stmt(products=["pkg:pypi/requests@2.30.0"])])
    assert other_version["exposed_count"] == 1 and other_version["unmatched_statements"] == 1
    qualified = vex([vuln(product="pkg:deb/debian/openssl@3.0.11?arch=amd64")], [stmt(products=["pkg:deb/debian/openssl@3.0.11"])])
    assert qualified["suppressed_count"] == 1
    mixed_case_type = vex([vuln()], [stmt(products=["pkg:PyPI/requests@2.31.0"])])
    assert mixed_case_type["suppressed_count"] == 1
    other_name = vex([vuln()], [stmt(products=["pkg:pypi/urllib3@2.31.0"])])
    assert other_name["exposed_count"] == 1
    image = "ghcr.io/acme/app@" + DIGEST
    assert vex([vuln(product=image)], [stmt(products=[image])])["suppressed_count"] == 1
    assert vex([vuln(product=image)], [stmt(products=["ghcr.io/acme/app"])])["exposed_count"] == 1
    scoped = vex([vuln(product="pkg:npm/%40scope/pkg@1.0.0")], [stmt(products=["pkg:npm/%40scope/pkg"])])
    assert scoped["suppressed_count"] == 1


def test_vex_apply_vulnerability_ids_compare_case_insensitively_and_unmatched_statements_are_counted():
    result = vex([vuln("GHSA-abcd-efgh-ijkl")], [stmt("ghsa-ABCD-efgh-IJKL"), stmt("CVE-2000-1")])
    assert result["suppressed"][0]["id"] == "GHSA-abcd-efgh-ijkl" and result["unmatched_statements"] == 1


def test_vex_apply_finding_severity_follows_vulnerability_severity():
    vulns = [vuln(f"CVE-2026-{n}", severity=s) for n, s in enumerate(["critical", "high", "medium", "low", "unknown"], 1)]
    result = vex(vulns, [])
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [
        ("CVE-2026-1", "blocker"), ("CVE-2026-2", "major"), ("CVE-2026-3", "minor"), ("CVE-2026-5", "minor"), ("CVE-2026-4", "info")]
    assert result["blocking_count"] == 2 and [e["severity"] for e in result["exposed"]] == ["critical", "high", "medium", "unknown", "low"]


def test_vex_apply_rejects_semantic_problems_naming_the_field():
    invalid("supply_vex_apply", {"vulnerabilities": [vuln()], "statements": [stmt(timestamp="2026-02-01T00:00:00")]}, "statements")
    invalid("supply_vex_apply", {"vulnerabilities": [vuln()], "statements": [stmt(timestamp="soon")]}, "statements")
    invalid("supply_vex_apply", {"vulnerabilities": [vuln()], "statements": [stmt(justification="because")]}, "statements")
    invalid("supply_vex_apply", {"vulnerabilities": [vuln(), vuln()], "statements": []}, "vulnerabilities")
    invalid("supply_vex_apply", {"vulnerabilities": [vuln(vid="  ")], "statements": []}, "vulnerabilities")
    invalid("supply_vex_apply", {"vulnerabilities": [vuln()], "statements": [{**stmt(), "status": "resolved"}]}, "statements")


# --- supply_pin_audit -------------------------------------------------------------------------------------------

SHA40 = "0123456789abcdef0123456789abcdef01234567"


def audit(*files: tuple[str, str]) -> dict:
    return ok("supply_pin_audit", {"files": [{"path": p, "content": c} for p, c in files]})


def line_of(content: str, needle: str) -> int:
    return next(i for i, line in enumerate(content.split("\n"), 1) if needle in line)


def finding_rows(result: dict) -> list[tuple[str, str, str]]:
    return sorted((f["id"], f["severity"], f["location"]) for f in result["findings"])


WORKFLOW_YAML = f"""name: ci
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    container: node:20
    services:
      db:
        image: postgres@{DIGEST}
    steps:
      - uses: actions/checkout@{SHA40} # v4.1.1
      - uses: actions/setup-node@v4
      - uses: third/party@v1
      - uses: third/rolling@main
      - uses: ./local-action
      - uses: docker://alpine:3.20
      - uses: owner/repo/.github/workflows/x.yml@abc
      - uses: noref/action
      # - uses: commented/out@v1
      - uses: "quoted/action@v2"
      - uses: dyn/action@${{{{ matrix.ref }}}}
"""


def test_pin_audit_github_actions_rules():
    path = ".github/workflows/ci.yml"
    result = audit((path, WORKFLOW_YAML))
    n = lambda needle: f"{path}:{line_of(WORKFLOW_YAML, needle)}"  # noqa: E731
    assert finding_rows(result) == sorted([
        ("pin.action-no-ref", "major", n("noref/action")),
        ("pin.action-not-sha", "major", n("third/party@v1")),
        ("pin.action-not-sha", "major", n("third/rolling@main")),
        ("pin.action-not-sha", "major", n("owner/repo/.github")),
        ("pin.action-not-sha", "major", n("quoted/action")),
        ("pin.action-not-sha", "minor", n("setup-node@v4")),
        ("pin.action-image-unpinned", "minor", n("docker://alpine")),
        ("pin.image-tag-only", "minor", n("container: node:20")),
    ])
    assert (result["references"], result["unpinned"], result["pinned_pct"]) == (10, 8, 20.0)
    assert result["by_kind"] == [{"kind": "github-actions", "references": 10, "unpinned": 8}]
    assert result["verdict"] == "fail" and result["files_audited"] == 1 and result["files_skipped"] == 0


def test_pin_audit_actions_sha_forms_and_mutable_branch_severity():
    path = ".github/workflows/x.yaml"
    sha256 = "a" * 64
    content = f"""steps:
  - uses: a/b@{SHA40[:-1]}
  - uses: a/c@{sha256}
  - uses: actions/checkout@main
  - uses: github/codeql-action/init@v3
  - uses: a/d@{SHA40.upper()}
"""
    result = audit((path, content))
    assert finding_rows(result) == sorted([
        ("pin.action-not-sha", "major", f"{path}:2"), ("pin.action-not-sha", "major", f"{path}:4"),
        ("pin.action-not-sha", "minor", f"{path}:5"), ("pin.action-not-sha", "major", f"{path}:6")])
    assert "mutable branch" in next(f["summary"] for f in result["findings"] if f["location"].endswith(":4"))
    assert audit(("pkg/.github/workflows/ci.yml", f"steps:\n  - uses: a/b@{SHA40}\n"))["unpinned"] == 0
    assert audit((".github/actions/setup/action.yml", "runs:\n  steps:\n    - uses: a/b@v1\n"))["unpinned"] == 1
    assert audit((".github/workflows/notes.txt", "uses: a/b@v1\n"))["files_audited"] == 0


def test_pin_audit_workflow_images_and_paths_are_normalised():
    content = f"services:\n  a:\n    image: redis:latest\n  b:\n    image: redis:7\n  c:\n    image: redis@{DIGEST}\n  d:\n    image: ${{{{ env.IMG }}}}\n"
    result = audit(("./.github\\workflows\\ci.yml", content))
    assert finding_rows(result) == [("pin.image-latest", "major", ".github/workflows/ci.yml:3"), ("pin.image-tag-only", "minor", ".github/workflows/ci.yml:5")]
    assert result["references"] == 3


def test_pin_audit_dockerfile_from_lines():
    content = f"ARG BASE\nFROM node:latest AS build\nFROM alpine:3 AS mid\nFROM build AS again\nFROM ${{BASE}}\nFROM scratch\nFROM debian@{DIGEST}\nFROM ubuntu\n"
    result = audit(("svc/Dockerfile", content))
    assert finding_rows(result) == [
        ("pin.base-unresolved-arg", "major", "svc/Dockerfile:5"), ("pin.image-latest", "major", "svc/Dockerfile:2"),
        ("pin.image-latest", "major", "svc/Dockerfile:8"), ("pin.image-tag-only", "minor", "svc/Dockerfile:3")]
    assert (result["references"], result["unpinned"]) == (5, 4)  # the unresolved FROM is a reference that cannot be shown pinned
    for name in ("Dockerfile", "api.Dockerfile", "Dockerfile.prod", "Containerfile"):
        assert audit((f"x/{name}", "FROM node\n"))["files_audited"] == 1, name


def test_pin_audit_requirements_files():
    content = f"""# comment
requests==2.31.0
flask>=2.0
django==4.*
numpy
-r other.txt
--index-url https://example.com/simple
pkg[extra]==1.0 ; python_version < "3.9"
git+https://github.com/o/r.git@{SHA40}#egg=r
git+https://github.com/o/r2.git@main#egg=r2
-e git+https://github.com/o/r3.git#egg=r3
name @ https://example.com/x.whl#sha256={HEX}
"""
    result = audit(("requirements.txt", content))
    assert finding_rows(result) == [
        ("pin.requirement-not-exact", "minor", "requirements.txt:3"), ("pin.requirement-not-exact", "minor", "requirements.txt:4"),
        ("pin.requirement-not-exact", "minor", "requirements.txt:5"), ("pin.requirement-url-unpinned", "major", "requirements.txt:10"),
        ("pin.requirement-url-unpinned", "major", "requirements.txt:11"), ("pin.requirements-no-hashes", "info", "requirements.txt")]
    assert (result["references"], result["unpinned"]) == (9, 5)
    hashed = audit(("requirements-dev.txt", f"requests==2.31.0 \\\n    --hash=sha256:{HEX}\nflask===3.0.0 --hash=sha256:{HEX}\n"))
    assert hashed["findings_total"] == 0 and hashed["references"] == 2
    assert audit(("requirements.txt", "# nothing\n"))["findings_total"] == 0
    assert audit(("constraints.txt", "x>=1\n"))["unpinned"] == 1


def test_pin_audit_package_json():
    deps = {"a": "1.2.3", "b": "^1.2.3", "c": "latest", "d": f"github:o/r#{SHA40}", "e": "github:o/r#main", "f": "workspace:*", "g": "npm:left-pad@1.3.0",
            "h": "~1", "i": "*", "j": "https://example.com/y.tgz", "k": "o/r", "l": 5}
    content = json.dumps({"dependencies": deps, "devDependencies": {"z": "1.0.0-beta.1"}, "peerDependencies": {"react": "^18"}})
    result = audit(("web/package.json", content))
    rows = {(f["summary"].split(" ")[0], f["severity"]) for f in result["findings"] if f["id"] == "pin.npm-not-exact"}
    assert rows == {("dependencies.b", "minor"), ("dependencies.c", "major"), ("dependencies.e", "major"), ("dependencies.h", "minor"),
                    ("dependencies.i", "major"), ("dependencies.j", "major"), ("dependencies.k", "major")}
    assert (result["references"], result["unpinned"]) == (11, 7)
    assert ids(result).count("pin.no-lockfile") == 1 and at(result, "pin.no-lockfile") == ["web/package.json"]
    locked = audit(("web/package.json", content), ("web/package-lock.json", ""))
    assert "pin.no-lockfile" not in ids(locked) and locked["files_audited"] == 1 and locked["files_skipped"] == 0
    elsewhere = audit(("web/package.json", content), ("package-lock.json", ""))
    assert "pin.no-lockfile" in ids(elsewhere)
    root_locked = audit(("package.json", json.dumps({"dependencies": {"a": "1.0.0"}})), ("yarn.lock", ""))
    assert root_locked["findings_total"] == 0


def test_pin_audit_package_json_exact_version_forms_and_unparseable_files():
    exact = {"dependencies": {"a": "1.0.0", "b": "v2.0.0", "c": "=3.0.0", "d": "4.0.0+build.5", "e": "5.0.0-rc.1", "f": "1.0", "g": "1.x"}}
    result = audit(("package.json", json.dumps(exact)), ("package-lock.json", ""))
    assert sorted(f["summary"].split(" ")[0] for f in result["findings"]) == ["dependencies.f", "dependencies.g"]
    bad = audit(("package.json", "{oops"))
    assert finding_rows(bad) == [("pin.unparseable", "major", "package.json")] and bad["unpinned"] == 0
    assert finding_rows(audit(("package.json", "[]"))) == [("pin.unparseable", "major", "package.json")]


def test_pin_audit_totals_across_kinds_and_skipped_files():
    result = audit(("README.md", "# hi"), ("Dockerfile", "FROM alpine:3\n"), ("requirements.txt", "a==1\n--hash\n"),
                   (".github/workflows/ci.yml", f"steps:\n  - uses: a/b@{SHA40}\n"))
    assert result["files_audited"] == 3 and result["files_skipped"] == 1 and result["references"] == 3 and result["unpinned"] == 1
    assert result["pinned_pct"] == 66.67
    assert [k["kind"] for k in result["by_kind"]] == ["dockerfile", "github-actions", "requirements"]
    assert result["verdict"] == "pass"
    empty = audit(("notes.txt", "x"))
    assert empty["references"] == 0 and empty["pinned_pct"] == 100.0 and empty["by_kind"] == []


def test_pin_audit_rejects_bad_paths_naming_the_field():
    invalid("supply_pin_audit", {"files": [{"path": "a/Dockerfile", "content": ""}, {"path": "./a/Dockerfile", "content": ""}]}, "files")
    invalid("supply_pin_audit", {"files": [{"path": "/etc/Dockerfile", "content": ""}]}, "files")
    invalid("supply_pin_audit", {"files": [{"path": "../x/Dockerfile", "content": ""}]}, "files")
    invalid("supply_pin_audit", {"files": []}, "files")


# --- supply_license_policy --------------------------------------------------------------------------------------


def licenses(*expressions: str, allow=("MIT", "Apache-2.0", "BSD-3-Clause"), deny=(), categories=()) -> dict:
    components = [{"name": f"c{i}", "license": e} for i, e in enumerate(expressions)]
    return ok("supply_license_policy", {"components": components, "allow": list(allow), "deny": list(deny), "deny_categories": list(categories)})


def statuses(result: dict) -> tuple[int, int, int]:
    return result["allowed_count"], result["denied_count"], result["review_count"]


def test_license_policy_allowed_denied_and_review_counts_and_verdict():
    result = licenses("MIT", "GPL-3.0-only", "Proprietary-EULA", deny=["GPL-3.0-only"])
    assert result["total"] == 3 and statuses(result) == (1, 1, 1) and result["verdict"] == "fail"
    assert sorted((f["id"], f["severity"], f["location"]) for f in result["findings"]) == [("license.denied", "major", "c1"), ("license.review", "minor", "c2")]
    assert licenses("MIT", "Apache-2.0")["verdict"] == "pass"
    review = licenses("MIT", "Proprietary-EULA")
    assert review["verdict"] == "review" and review["blocking_count"] == 0


def test_license_policy_expression_operators_and_precedence():
    deny = ["GPL-3.0-only"]
    assert statuses(licenses("MIT OR GPL-3.0-only", deny=deny)) == (1, 0, 0)
    assert statuses(licenses("GPL-3.0-only OR GPL-2.0-only", deny=deny + ["GPL-2.0-only"])) == (0, 1, 0)
    assert statuses(licenses("MIT AND GPL-3.0-only", deny=deny)) == (0, 1, 0)
    assert statuses(licenses("MIT AND Apache-2.0")) == (1, 0, 0)
    assert statuses(licenses("MIT AND Unknown-1.0")) == (0, 0, 1)
    assert statuses(licenses("GPL-3.0-only AND Unknown-1.0", deny=deny)) == (0, 1, 0)
    assert statuses(licenses("Unknown-1.0 OR GPL-3.0-only", deny=deny)) == (0, 0, 1)
    assert statuses(licenses("(MIT OR Apache-2.0) AND BSD-3-Clause")) == (1, 0, 0)
    assert statuses(licenses("MIT OR Apache-2.0 AND GPL-3.0-only", deny=deny)) == (1, 0, 0)  # AND binds tighter: MIT OR (Apache AND GPL)
    assert statuses(licenses("(MIT OR Apache-2.0) AND GPL-3.0-only", deny=deny)) == (0, 1, 0)
    assert statuses(licenses("mit or apache-2.0", allow=["mit"])) == (1, 0, 0)
    assert statuses(licenses("MIT", allow=["mit"])) == (1, 0, 0)


def test_license_policy_denied_summary_names_the_offending_licenses():
    result = licenses("MIT AND GPL-3.0-only AND AGPL-3.0-only", deny=["GPL-3.0-only"], categories=["network-copyleft"])
    summary = result["findings"][0]["summary"]
    assert "GPL-3.0-only" in summary and "AGPL-3.0-only (network-copyleft)" in summary


def test_license_policy_deprecated_spdx_ids_are_normalised():
    assert statuses(licenses("GPL-2.0+", categories=["strong-copyleft"])) == (0, 1, 0)
    assert statuses(licenses("GPL-2.0-or-later", deny=["GPL-2.0+"])) == (0, 1, 0)
    assert statuses(licenses("GPL-3.0", deny=["GPL-3.0-only"])) == (0, 1, 0)
    assert statuses(licenses("LGPL-2.1", allow=["LGPL-2.1-only"])) == (1, 0, 0)
    assert statuses(licenses("AGPL-3.0", categories=["network-copyleft"])) == (0, 1, 0)


def test_license_policy_explicit_lists_beat_categories_and_exceptions_are_matched():
    assert statuses(licenses("GPL-3.0-only", allow=["GPL-3.0-only"], categories=["strong-copyleft"])) == (1, 0, 0)
    assert statuses(licenses("GPL-3.0-only", deny=["GPL-3.0-only"], categories=[])) == (0, 1, 0)
    classpath = "GPL-2.0-only WITH Classpath-exception-2.0"
    assert statuses(licenses(classpath, categories=["strong-copyleft"])) == (0, 1, 0)
    assert statuses(licenses(classpath, allow=[classpath], categories=["strong-copyleft"])) == (1, 0, 0)
    assert statuses(licenses(classpath.lower(), allow=[classpath], categories=["strong-copyleft"])) == (1, 0, 0)
    assert statuses(licenses(classpath, allow=["GPL-2.0-only"])) == (1, 0, 0)
    assert statuses(licenses(classpath, deny=[classpath], allow=["GPL-2.0-only"])) == (0, 1, 0)
    assert statuses(licenses("GPL-2.0-only", allow=[], deny=[classpath], categories=[])) == (0, 0, 1)


@pytest.mark.parametrize(("category", "spdx_ids"), [
    ("weak-copyleft", ["LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0", "CDDL-1.1"]),
    ("strong-copyleft", ["GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later"]),
    ("network-copyleft", ["AGPL-3.0-only", "AGPL-3.0-or-later"]),
    ("source-available", ["SSPL-1.0", "BUSL-1.1", "Elastic-2.0"]),
])
def test_license_policy_category_table_rows(category, spdx_ids):
    result = licenses(*spdx_ids, allow=[], categories=[category])
    assert statuses(result) == (0, len(spdx_ids), 0)
    other = licenses(*spdx_ids, allow=[], categories=[c for c in ("weak-copyleft", "strong-copyleft", "network-copyleft", "source-available") if c != category])
    assert statuses(other) == (0, 0, len(spdx_ids))


def test_license_policy_permissive_licenses_are_never_denied_by_category():
    permissive = ["MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Zlib", "BSL-1.0", "CC0-1.0", "Unlicense", "PostgreSQL",
                  "BlueOak-1.0.0", "PSF-2.0"]
    result = licenses(*permissive, allow=[], categories=["weak-copyleft", "strong-copyleft", "network-copyleft", "source-available"])
    assert statuses(result) == (0, 0, len(permissive))


def test_license_policy_missing_and_unparseable_licenses_need_review():
    result = licenses("", "NOASSERTION", "none", "MIT OR", "(MIT", "MIT)", "MIT AND AND Apache-2.0", "MIT WITH", "AND", "bad id!", "(" * 20 + "MIT" + ")" * 20)
    assert statuses(result) == (0, 0, 11)
    assert sorted(set(ids(result))) == ["license.missing", "license.unparseable"] and ids(result).count("license.missing") == 3
    assert licenses("(" * 16 + "MIT" + ")" * 16)["allowed_count"] == 1


def test_license_policy_rejects_bad_policy_lists_naming_the_field():
    base = {"components": [{"name": "a", "license": "MIT"}], "allow": [], "deny": [], "deny_categories": []}
    invalid("supply_license_policy", {**base, "allow": ["MIT AND GPL-3.0-only"]}, "allow")
    invalid("supply_license_policy", {**base, "deny": ["GPL-2.0-only WITH"]}, "deny")
    invalid("supply_license_policy", {**base, "allow": ["GPL-3.0"], "deny": ["GPL-3.0-only"]}, "deny")
    invalid("supply_license_policy", {**base, "allow": ["mit"], "deny": ["MIT"]}, "deny")
    invalid("supply_license_policy", {**base, "deny_categories": ["copyleft"]}, "deny_categories")


# --- supply_retention_plan --------------------------------------------------------------------------------------

DIGESTS = {name: "sha256:" + format(i, "x") * 64 for i, name in enumerate("abcdefg", 1)}


def tag(name: str, digest: str = "a", pushed: str = "2025-01-01T00:00:00+00:00") -> dict:
    return {"tag": name, "digest": DIGESTS[digest], "pushed_at": pushed}


def plan(tags, **extra) -> dict:
    payload = {"tags": tags, "keep_latest": 0, "min_age_days": 0, "protected_prefixes": [], "protected_digests": [], "max_delete_pct": 100, "as_of": AS_OF, **extra}
    return ok("supply_retention_plan", payload)


def test_retention_plan_applies_each_keep_reason_in_priority_order():
    tags = [tag("v1.0.0", "a"), tag("v1.1.0", "b", "2025-06-01T00:00:00+00:00"), tag("main", "c", "2026-02-27T00:00:00+00:00"), tag("pr-1", "d"),
            tag("pr-2", "e"), tag("pr-3", "f", "2026-02-15T00:00:00+00:00")]
    result = plan(tags, keep_latest=1, min_age_days=30, protected_prefixes=["v"], protected_digests=[DIGESTS["d"]])
    assert result["delete_tags"] == ["pr-2"] and result["tags_total"] == 6 and result["keep_count"] == 5 and result["delete_count"] == 1
    assert result["keep_reasons"] == [{"reason": "in-use-digest", "count": 1}, {"reason": "protected-prefix", "count": 2}, {"reason": "recent-count", "count": 1},
                                      {"reason": "min-age", "count": 1}]
    assert result["delete_pct"] == 16.67 and result["orphaned_digests"] == 1 and result["verdict"] == "pass"
    both = plan([tag("v1", "a")], protected_prefixes=["v"], protected_digests=[DIGESTS["a"]])
    assert both["keep_reasons"][0] == {"reason": "in-use-digest", "count": 1} and both["keep_reasons"][1]["count"] == 0
    newest_and_prefix = plan([tag("v1", "a", "2026-02-27T00:00:00+00:00")], keep_latest=1, protected_prefixes=["v"])
    assert newest_and_prefix["keep_reasons"][1] == {"reason": "protected-prefix", "count": 1}


def test_retention_plan_min_age_boundary_keeps_only_strictly_younger_tags():
    exactly = plan([tag("old", "a", "2026-01-30T00:00:00+00:00")], min_age_days=30)
    assert exactly["delete_tags"] == ["old"]
    younger = plan([tag("new", "a", "2026-01-30T00:00:01+00:00")], min_age_days=30)
    assert younger["delete_tags"] == [] and younger["keep_reasons"][3] == {"reason": "min-age", "count": 1}
    assert plan([tag("now", "a", AS_OF)], min_age_days=0)["delete_tags"] == ["now"]


def test_retention_plan_keep_latest_orders_by_time_then_tag_name():
    tags = [tag("c", "a", "2026-01-01T00:00:00+00:00"), tag("b", "b", "2026-02-01T00:00:00+00:00"), tag("a", "c", "2026-02-01T00:00:00+00:00")]
    assert plan(tags, keep_latest=1)["delete_tags"] == ["b", "c"]
    assert plan(tags, keep_latest=2)["delete_tags"] == ["c"]
    assert plan(tags, keep_latest=0)["delete_tags"] == ["a", "b", "c"]
    assert plan(tags, keep_latest=1000)["delete_tags"] == []
    offset = [tag("x", "a", "2026-02-01T01:00:00+02:00"), tag("y", "b", "2026-01-31T23:30:00+00:00")]  # x is 30 minutes older than y
    assert plan(offset, keep_latest=1)["delete_tags"] == ["x"]


def test_retention_plan_orphaned_digests_count_only_digests_losing_every_tag():
    shared = [tag("keep", "a", "2026-02-28T00:00:00+00:00"), tag("alias", "a"), tag("gone1", "b"), tag("gone2", "b")]
    result = plan(shared, keep_latest=1)
    assert result["delete_tags"] == ["alias", "gone1", "gone2"] and result["orphaned_digests"] == 1


def test_retention_plan_delete_ratio_circuit_breaker_is_strictly_greater():
    tags = [tag("a", "a"), tag("b", "b"), tag("c", "c", "2026-02-28T00:00:00+00:00"), tag("d", "d", "2026-02-27T00:00:00+00:00")]
    at_limit = plan(tags, keep_latest=2, max_delete_pct=50)
    assert at_limit["delete_count"] == 2 and at_limit["delete_pct"] == 50.0 and at_limit["verdict"] == "pass"
    over = plan(tags, keep_latest=2, max_delete_pct=49.9)
    assert over["verdict"] == "fail" and ids(over) == ["retention.delete-ratio-exceeded"] and over["delete_tags"] == ["a", "b"]
    assert plan(tags, keep_latest=2, max_delete_pct=0)["verdict"] == "fail"
    assert plan(tags, keep_latest=4, max_delete_pct=0)["verdict"] == "pass"


def test_retention_plan_empty_listing_and_unlisted_protected_digest():
    empty = plan([])
    assert empty["tags_total"] == 0 and empty["delete_pct"] == 0.0 and empty["verdict"] == "pass" and empty["findings_total"] == 0
    missing = plan([tag("a", "a")], keep_latest=1, protected_digests=[DIGESTS["b"], DIGESTS["b"].upper().replace("SHA256", "sha256")])
    assert ids(missing) == ["retention.protected-digest-unlisted"] and missing["verdict"] == "pass" and missing["findings"][0]["severity"] == "minor"


def test_retention_plan_rejects_semantic_problems_naming_the_field():
    base = {"tags": [tag("a")], "keep_latest": 0, "min_age_days": 0, "protected_prefixes": [], "protected_digests": [], "max_delete_pct": 100, "as_of": AS_OF}
    invalid("supply_retention_plan", {**base, "as_of": "2026-03-01"}, "as_of")
    invalid("supply_retention_plan", {**base, "tags": [tag("-bad")]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [tag("has space")]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [tag("a" * 129)]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [tag("a"), tag("a", "b")]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [{"tag": "a", "digest": "sha256:abc", "pushed_at": AS_OF}]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [tag("a", pushed="2026-03-01T00:00:01+00:00")]}, "tags")
    invalid("supply_retention_plan", {**base, "tags": [tag("a", pushed="2025-01-01T00:00:00")]}, "tags")
    invalid("supply_retention_plan", {**base, "protected_digests": ["latest"]}, "protected_digests")
    assert plan([tag("a", pushed=AS_OF)])["tags_total"] == 1


# --- supply_attestation_bundle ----------------------------------------------------------------------------------

PREDICATES = {
    "provenance": "https://slsa.dev/provenance/v1", "sbom": "https://cyclonedx.org/bom", "vex": "https://openvex.dev/ns/v0.2.0",
    "vuln-scan": "https://cosign.sigstore.dev/attestation/vuln/v1", "vsa": "https://slsa.dev/verification_summary/v1",
}


def att(kind_or_type: str, digest=DIGEST, signed=True, verified=True) -> dict:
    return {"predicate_type": PREDICATES.get(kind_or_type, kind_or_type), "subject_digest": digest, "signed": signed, "signature_verified": verified}


def bundle(attestations, required, digest=DIGEST) -> dict:
    return ok("supply_attestation_bundle", {"artifact_digest": digest, "attestations": attestations, "required_kinds": required})


def test_attestation_bundle_complete_when_every_required_kind_is_bound_signed_and_verified():
    kinds = ["provenance", "sbom", "vex", "vuln-scan", "vsa"]
    result = bundle([att(k) for k in kinds], kinds)
    assert result == {"state": "SUCCESS", "required_count": 5, "satisfied_count": 5, "missing_kinds": [], "present_kinds": ["provenance", "sbom", "vex", "vsa", "vuln-scan"],
                      "complete": True, "verdict": "pass", "findings_total": 0, "blocking_count": 0, "findings": []}
    assert bundle([att("provenance")], ["provenance", "provenance"])["required_count"] == 1


@pytest.mark.parametrize(("predicate_type", "kind"), [
    ("https://slsa.dev/provenance/v0.2", "provenance"), ("https://slsa.dev/provenance/v1", "provenance"),
    ("https://slsa.dev/verification_summary/v1", "vsa"), ("https://cyclonedx.org/bom", "sbom"), ("https://cyclonedx.org/bom/v1.5", "sbom"),
    ("https://spdx.dev/Document", "sbom"), ("https://openvex.dev/ns/v0.2.0", "vex"), ("https://cosign.sigstore.dev/attestation/vuln/v1", "vuln-scan"),
])
def test_attestation_bundle_predicate_type_to_kind_mapping(predicate_type, kind):
    result = bundle([att(predicate_type)], [kind])
    assert result["complete"] is True and result["present_kinds"] == [kind]


def test_attestation_bundle_missing_kind_is_a_blocker():
    result = bundle([att("provenance")], ["provenance", "sbom"])
    assert result["complete"] is False and result["missing_kinds"] == ["sbom"] and result["satisfied_count"] == 1 and result["verdict"] == "fail"
    assert ids(result) == ["attestation.missing"] and severity_of(result, "attestation.missing") == "blocker" and at(result, "attestation.missing") == ["sbom"]


def test_attestation_bundle_attestation_for_another_digest_does_not_count():
    result = bundle([att("sbom", digest=OTHER_DIGEST)], ["sbom"])
    assert ids(result) == ["attestation.digest-mismatch"] and result["missing_kinds"] == ["sbom"] and result["blocking_count"] == 1
    assert bundle([att("sbom", digest=DIGEST.upper().replace("SHA256", "sha256"))], ["sbom"])["complete"] is True
    assert bundle([att("sbom", digest=OTHER_DIGEST), att("sbom")], ["sbom"])["complete"] is True


def test_attestation_bundle_unsigned_and_unverified_are_major():
    unsigned = bundle([att("sbom", signed=False, verified=False)], ["sbom"])
    assert ids(unsigned) == ["attestation.unsigned"] and severity_of(unsigned, "attestation.unsigned") == "major" and unsigned["complete"] is False
    unverified = bundle([att("sbom", verified=False)], ["sbom"])
    assert ids(unverified) == ["attestation.signature-unverified"] and unverified["verdict"] == "fail"
    assert bundle([att("sbom", signed=False, verified=False), att("sbom")], ["sbom"])["complete"] is True
    assert bundle([att("sbom", verified=False), att("sbom", signed=False, verified=False)], ["sbom"])["findings"][0]["id"] == "attestation.signature-unverified"


def test_attestation_bundle_unrecognized_predicates_are_informational():
    result = bundle([att("provenance"), att("https://example.com/custom/v1")], ["provenance"])
    assert result["complete"] is True and ids(result) == ["attestation.unrecognized-type"] and result["verdict"] == "pass"
    assert result["present_kinds"] == ["provenance"]
    assert bundle([att("https://slsa.dev/provenance")], ["provenance"])["complete"] is False  # no trailing path: not a known provenance predicate


def test_attestation_bundle_rejects_malformed_inputs_naming_the_field():
    good = [att("sbom")]
    invalid("supply_attestation_bundle", {"artifact_digest": "sha256:short", "attestations": good, "required_kinds": ["sbom"]}, "artifact_digest")
    invalid("supply_attestation_bundle", {"artifact_digest": DIGEST, "attestations": [att("sbom", digest="nope")], "required_kinds": ["sbom"]}, "attestations")
    invalid("supply_attestation_bundle", {"artifact_digest": DIGEST, "attestations": [att("sbom", signed=False, verified=True)], "required_kinds": ["sbom"]}, "attestations")
    invalid("supply_attestation_bundle", {"artifact_digest": DIGEST, "attestations": good, "required_kinds": ["sbom", "unknown"]}, "required_kinds")
    invalid("supply_attestation_bundle", {"artifact_digest": DIGEST, "attestations": good, "required_kinds": []}, "required_kinds")


# --- outputs stay inside their declared bounds for oversized document text ---------------------------------------


def test_document_supplied_strings_are_clipped_to_the_declared_output_bounds():
    long_base = "registry.example.com/" + "a" * 400
    docker = lint(f"FROM {long_base}\nUSER {'u' * 400}\n")
    assert len(docker["final_base"]) == 300 and len(docker["final_user"]) == 300 and ids(docker)[0] == "docker.base-invalid-reference"
    long_builder = "https://builders.example.com/" + "b" * 400
    prov = statement()
    prov["predicate"]["runDetails"]["builder"]["id"] = long_builder
    prov["predicate"]["buildDefinition"]["buildType"] = "t" * 400
    prov["subject"][0]["name"] = "s" * 400
    result = slsa(prov)
    assert len(result["builder_id"]) == 300 and len(result["build_type"]) == 300
    assert len(match(prov)["matched_subject"]) == 300
    big_name = cdx_component("n" * 400, "9" * 300, licenses=lic("L" * 400))
    assert len(validate(cdx([big_name]).replace('"1.5"', '"' + "7" * 100 + '"'))["spec_version"]) == 40
    changed = diff(cdx([cdx_component("n" * 400, "1", licenses=lic("L" * 400))], root=False), cdx([cdx_component("n" * 400, "2", licenses=lic("M" * 400))], root=False))
    assert len(changed["version_changed"][0]["name"]) == 300 and len(changed["license_changed"][0]["before"]) == 300
    only_added = diff(cdx([], root=False), cdx([big_name], root=False))
    assert len(only_added["added"][0]["version"]) == 200


# --- independent review of A18 (regressions found by re-computing every tool by hand) ------------------------------------

URL_CRED = "https://ci-user:Zq9canaryPW81@host.example/org/repo.git"
GH_TOKEN = "ghp_Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
CANARIES = ("Zq9canaryPW81", GH_TOKEN, AWS_KEY, "tok3ncanary", "pw4canary", "abandon ability")
MNEMONIC = "abandon ability able about above absent absorb abstract absurd abuse access accident"
PEM_HEX = "7c" * 32  # a 64-hex private key is also digest-shaped: invalid digests must never be echoed


def _canary_payloads() -> list[tuple[str, dict]]:
    doc = json.loads(cdx())
    doc["specVersion"] = GH_TOKEN
    doc["components"][0]["bom-ref"] = URL_CRED
    doc["dependencies"] = [{"ref": "app", "dependsOn": [AWS_KEY, GH_TOKEN]}]
    spdx_doc = json.loads(spdx())
    spdx_doc["relationships"].append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": URL_CRED})
    spdx_doc["creationInfo"]["creators"] = ["Tool: x", f"Secret: {GH_TOKEN}"]
    prov = statement()
    prov["predicate"]["runDetails"]["builder"]["id"] = URL_CRED
    prov["predicate"]["buildDefinition"]["buildType"] = f"{GH_TOKEN}"
    prov["subject"][0]["name"] = URL_CRED
    purl_cred = f"pkg:npm/leftpad@1.0.0?repository_url={URL_CRED}"
    return [
        ("supply_dockerfile_lint", {"content": f"FROM {URL_CRED}\n"}),
        ("supply_dockerfile_lint", {"content": "FROM ci-user:Zq9canaryPW81@host.example/img\nUSER 1\n"}),
        ("supply_dockerfile_lint", {"content": f"FROM alpine@{DIGEST}\nARG NPM_TOKEN={GH_TOKEN}\nENV DB_PASSWORD={AWS_KEY}\nENV REPO={URL_CRED}\n"
                                              f"RUN git clone {URL_CRED}\nADD {URL_CRED} /x\nCOPY .env /x\nUSER 1\n"}),
        ("supply_base_image_policy", {"images": [{"ref": URL_CRED}], "allowed_registries": ["docker.io"], "require_digest": True, "as_of": AS_OF}),
        ("supply_base_image_policy", {"images": [{"ref": "ci-user:Zq9canaryPW81@host.example/img", "created_at": GH_TOKEN}], "allowed_registries": ["docker.io"],
                                      "require_digest": True, "as_of": AS_OF}),
        ("supply_base_image_policy", {"images": [{"ref": f"{URL_CRED.split('//')[1].split('@')[1].split('/')[0]}/img:1"}], "allowed_registries": [URL_CRED],
                                      "require_digest": True, "as_of": AS_OF}),
        ("supply_sbom_validate", {"document": json.dumps(doc)}),
        ("supply_sbom_validate", {"document": json.dumps(spdx_doc)}),
        ("supply_sbom_diff", {"before": json.dumps(doc), "after": cdx([cdx_component(URL_CRED, GH_TOKEN)], root=False)}),
        ("supply_slsa_level", {"provenance": json.dumps(prov), "signature_verified": True, "trusted_builder_ids": [], "isolated_build": False,
                               "signing_key_isolated": False, "required_level": 2}),
        ("supply_provenance_match", {"provenance": json.dumps(prov), "artifact_digest": OTHER_DIGEST, "artifact_name": URL_CRED}),
        ("supply_provenance_match", {"provenance": json.dumps(prov), "artifact_digest": DIGEST, "artifact_name": GH_TOKEN}),
        ("supply_signature_policy", {"signatures": [{"signer": URL_CRED, "issuer": AWS_KEY, "verified": False, "in_transparency_log": False},
                                                     {"signer": GH_TOKEN, "issuer": URL_CRED, "verified": True, "in_transparency_log": True}],
                                     "rules": [{"name": "r", "issuer": URL_CRED, "identity": AWS_KEY, "required": True}], "min_valid_signatures": 3,
                                     "require_transparency_log": False}),
        ("supply_vex_apply", {"vulnerabilities": [{"id": GH_TOKEN, "product": purl_cred, "severity": "high"}], "statements": []}),
        ("supply_vex_apply", {"vulnerabilities": [{"id": "CVE-1", "product": purl_cred, "severity": "high"}],
                              "statements": [{"vulnerability": "CVE-1", "products": [purl_cred], "status": "not_affected", "timestamp": "2026-01-01T00:00:00Z",
                                              "justification": URL_CRED}]}),
        ("supply_vex_apply", {"vulnerabilities": [{"id": "CVE-1", "product": purl_cred, "severity": "high"}],
                              "statements": [{"vulnerability": "CVE-1", "products": [purl_cred], "status": "affected", "timestamp": GH_TOKEN}]}),
        ("supply_pin_audit", {"files": [
            {"path": "requirements.txt", "content": f"git+{URL_CRED}@main#egg=x\npkg @ {URL_CRED}\n"},
            {"path": "package.json", "content": json.dumps({"dependencies": {"a": f"git+{URL_CRED}#main", "b": URL_CRED}})},
            {"path": ".github/workflows/ci.yml", "content": f"jobs:\n  a:\n    steps:\n      - uses: docker://ci-user:Zq9canaryPW81@host.example/x\n"
                                                           f"      - uses: {GH_TOKEN}/x@main\n    container: ci-user:Zq9canaryPW81@host.example/img\n"},
            {"path": "Dockerfile", "content": f"FROM {URL_CRED}\n"},
        ]}),
        ("supply_license_policy", {"components": [{"name": GH_TOKEN, "license": f"MIT {GH_TOKEN}"}, {"name": "b", "license": f"({AWS_KEY}"}],
                                   "allow": ["MIT"], "deny": [], "deny_categories": []}),
        ("supply_license_policy", {"components": [{"name": "a", "license": "MIT"}], "allow": [GH_TOKEN, "x y z"], "deny": [], "deny_categories": []}),
        ("supply_retention_plan", {"tags": [{"tag": "ci-user:Zq9canaryPW81@host", "digest": DIGEST, "pushed_at": AS_OF}], "keep_latest": 0, "min_age_days": 0,
                                   "protected_prefixes": [], "protected_digests": [], "max_delete_pct": 100, "as_of": AS_OF}),
        ("supply_retention_plan", {"tags": [], "keep_latest": 0, "min_age_days": 0, "protected_prefixes": [], "protected_digests": [PEM_HEX, GH_TOKEN],
                                   "max_delete_pct": 100, "as_of": AS_OF}),
        ("supply_attestation_bundle", {"artifact_digest": DIGEST, "required_kinds": ["sbom"],
                                       "attestations": [{"predicate_type": URL_CRED, "subject_digest": DIGEST, "signed": True, "signature_verified": True}]}),
        ("supply_attestation_bundle", {"artifact_digest": PEM_HEX, "required_kinds": ["sbom"], "attestations": []}),
        ("supply_license_policy", {"components": [{"name": MNEMONIC, "license": MNEMONIC}], "allow": ["MIT"], "deny": [], "deny_categories": []}),
        ("supply_sbom_diff", {"before": cdx([cdx_component(MNEMONIC, "1")], root=False), "after": cdx([], root=False)}),
        ("supply_signature_policy", {"signatures": [{"signer": MNEMONIC, "issuer": MNEMONIC, "verified": True, "in_transparency_log": True}],
                                     "rules": [{"name": "r", "issuer": "x", "identity": MNEMONIC, "required": True}], "min_valid_signatures": 1,
                                     "require_transparency_log": False}),
        ("supply_attestation_bundle", {"artifact_digest": DIGEST, "required_kinds": ["sbom"],
                                       "attestations": [{"predicate_type": "x", "subject_digest": PEM_HEX, "signed": True, "signature_verified": True}]}),
    ]


@pytest.mark.parametrize("index", range(len(_canary_payloads())))
def test_no_tool_echoes_a_credential_it_was_given(index):
    name, payload = _canary_payloads()[index]
    result = call(name, payload)
    assert result["state"] in ("SUCCESS", "INVALID_INPUT"), result
    text = json.dumps(result)
    for canary in (*CANARIES, PEM_HEX):
        assert canary not in text, (name, canary, text[:600])


def test_credentials_are_still_reported_by_kind_and_location_only():
    leak = lint(f"FROM alpine@{DIGEST}\nUSER 1\nHEALTHCHECK CMD true\nRUN git clone {URL_CRED}\nENV DB_PASSWORD={AWS_KEY}\n")
    assert ids(leak) == ["docker.secret-in-env", "docker.credential-in-url"]
    assert at(leak, "docker.credential-in-url") == ["line 4"] and "ENV DB_PASSWORD stores a secret" in leak["findings"][0]["summary"]


def test_url_userinfo_is_redacted_but_docker_action_digests_are_not_mangled():
    result = audit((".github/workflows/ci.yml", f"jobs:\n  a:\n    steps:\n      - uses: docker://alpine@{DIGEST}\n"))
    assert ids(result) == [] and result["references"] == 1
    result = audit((".github/workflows/ci.yml", f"jobs:\n  a:\n    steps:\n      - uses: docker://alpine:3.20@{DIGEST}\n      - uses: docker://alpine:3.20\n"))
    assert [f["summary"] for f in result["findings"]] == ["docker://alpine:3.20 is not pinned by digest"]


# --- dockerfile_lint: arithmetic shift is not a heredoc, boolean and URL-ish names are not secrets ----------------------


def test_dockerfile_lint_shell_arithmetic_shift_does_not_swallow_the_rest_of_the_file():
    content = f"FROM alpine:3@{DIGEST}\nRUN echo $((1<<N))\nUSER root\nHEALTHCHECK CMD true\n"
    result = lint(content)
    assert ids(result) == ["docker.final-user-root"] and at(result, "docker.final-user-root") == ["line 3"]
    assert result["instructions"] == 4 and result["final_user"] == "root"
    both = lint(f"FROM alpine:3@{DIGEST}\nRUN cat <<EOF >/x\ntext\nEOF\nRUN echo $((1<<N))\nUSER root\nHEALTHCHECK CMD true\n")
    assert ids(both) == ["docker.final-user-root"] and both["instructions"] == 5


def test_dockerfile_lint_names_that_only_mention_a_secret_are_not_secrets():
    head, tail = f"FROM alpine:3@{DIGEST}\n", "USER app\nHEALTHCHECK CMD true\n"
    harmless = "ENV TOKEN_URL=https://auth.example/token\nENV SECRET_ARN=arn:aws:x\nENV MYSQL_ALLOW_EMPTY_PASSWORD=yes\nENV PASSWORD_MIN_LENGTH=12\nARG ALLOW_EMPTY_PASSWORD=no\n"
    assert ids(lint(head + harmless + tail)) == []
    real = lint(head + "ENV DB_PASSWORD=hunter2\nENV SECRET_KEY_BASE=abc\nARG NPM_TOKEN\n" + tail)
    assert ids(real) == ["docker.secret-in-env"] * 2 + ["docker.secret-in-arg"]


# --- sbom: CycloneDX 1.7 exists, malformed documents never crash ---------------------------------------------------


def test_sbom_validate_accepts_cyclonedx_1_7_and_flags_only_unknown_versions():
    # CycloneDX publishes bom-1.7.schema.json (ECMA-424 2nd edition); JSON starts at 1.2.
    assert ids(validate(cdx().replace('"1.5"', '"1.7"'))) == []
    for unknown in ("1.1", "1.8", "2.0"):
        newer = validate(cdx().replace('"1.5"', f'"{unknown}"'))
        assert ids(newer) == ["sbom.unknown-spec-version"] and newer["verdict"] == "pass" and newer["spec_version"] == unknown


@pytest.mark.parametrize("shape", ["license-int", "licenses-str", "tools-dict-of-scalars", "externalRefs-int", "refType-list", "relType-dict", "relationships-int"])
def test_sbom_tools_return_a_result_for_structurally_wrong_documents(shape):
    doc = json.loads(cdx())
    sp = json.loads(spdx())
    if shape == "license-int":
        doc["components"][0]["licenses"] = 7
    elif shape == "licenses-str":
        doc["components"][0]["licenses"] = "MIT"
    elif shape == "tools-dict-of-scalars":
        doc["metadata"]["authors"], doc["metadata"]["tools"] = [], {"components": {"name": "x"}, "services": 3}
    elif shape == "externalRefs-int":
        sp["packages"][0]["externalRefs"] = 5
    elif shape == "refType-list":
        sp["packages"][0]["externalRefs"] = [{"referenceType": ["purl"], "referenceLocator": "pkg:x/y@1"}]
    elif shape == "relType-dict":
        sp["relationships"].append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": {"a": 1}, "relatedSpdxElement": "SPDXRef-DOCUMENT"})
    else:
        sp["relationships"] = 5
    for document in (json.dumps(doc), json.dumps(sp)):
        assert call("supply_sbom_validate", {"document": document})["state"] == "SUCCESS"
        assert call("supply_sbom_diff", {"before": document, "after": document})["state"] == "SUCCESS"


# --- sbom_diff: version direction follows semver precedence ----------------------------------------------------------


def test_sbom_diff_prerelease_precedence_follows_semver_section_11():
    def direction(old: str, new: str) -> str:
        return diff(cdx([cdx_component("x", old)], root=False), cdx([cdx_component("x", new)], root=False))["version_changed"][0]["direction"]

    # semver.org/#spec-item-11: 1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for lower, higher in zip(chain, chain[1:]):
        assert direction(lower, higher) == "upgrade", (lower, higher)
        assert direction(higher, lower) == "downgrade", (higher, lower)
    assert direction("2.0.0", "2.0.0-rc1") == "downgrade"
    assert direction("1.0.0+build5", "1.0.0+build4") == "changed"  # build metadata does not take part in precedence
    assert direction("1:2.3", "2:1.0") == "changed"  # epochs and distro revisions are not semver: no direction is claimed
    assert direction("1.0.0rc1", "1.0.0") == "changed"


# --- vex_apply: purl qualifiers narrow a statement ---------------------------------------------------------------------


def test_vex_statement_for_one_architecture_does_not_suppress_another():
    amd64 = "pkg:deb/debian/openssl@3.0.11?arch=amd64"
    arm64 = "pkg:deb/debian/openssl@3.0.11?arch=arm64"
    only_arm = vex([vuln(product=amd64)], [stmt(products=[arm64])])
    assert (only_arm["exposed_count"], only_arm["suppressed_count"], only_arm["unmatched_statements"]) == (1, 0, 1)
    assert vex([vuln(product=arm64)], [stmt(products=[arm64])])["suppressed_count"] == 1
    assert vex([vuln(product=amd64)], [stmt(products=["pkg:deb/debian/openssl@3.0.11"])])["suppressed_count"] == 1  # no qualifier = any variant
    assert vex([vuln(product=amd64)], [stmt(products=["pkg:deb/debian/openssl@3.0.11?arch=amd64&distro=bookworm"])])["suppressed_count"] == 0
    assert vex([vuln(product=amd64 + "&distro=bookworm")], [stmt(products=[amd64])])["suppressed_count"] == 1
    assert vex([vuln(product="pkg:deb/debian/openssl@3.0.11?ARCH=amd64")], [stmt(products=[amd64])])["suppressed_count"] == 1  # keys are lowercase by spec


# --- retention_plan: a plan may not delete everything; a shared digest is called out ---------------------------------------


def test_retention_plan_that_deletes_every_tag_is_a_blocker_even_with_a_100_percent_limit():
    tags = [tag("a", "a"), tag("b", "b")]
    result = plan(tags, max_delete_pct=100)
    assert result["delete_tags"] == ["a", "b"] and result["keep_count"] == 0 and result["verdict"] == "fail"
    assert ids(result) == ["retention.deletes-everything"] and severity_of(result, "retention.deletes-everything") == "blocker" and result["blocking_count"] == 1
    one_kept = plan(tags, keep_latest=1, max_delete_pct=100)
    assert one_kept["verdict"] == "pass" and ids(one_kept) == []
    assert plan([], max_delete_pct=100)["verdict"] == "pass"


def test_retention_plan_warns_when_a_deleted_tag_shares_its_digest_with_a_kept_tag():
    shared = [tag("keep", "a", "2026-02-28T00:00:00+00:00"), tag("alias", "a"), tag("other", "b")]
    result = plan(shared, keep_latest=1)
    assert result["delete_tags"] == ["alias", "other"] and result["verdict"] == "pass"
    assert ids(result) == ["retention.shared-digest"] and severity_of(result, "retention.shared-digest") == "minor" and at(result, "retention.shared-digest") == ["alias"]
    assert "1 tag(s)" in result["findings"][0]["summary"] and "by tag" in result["findings"][0]["summary"]
    assert ids(plan([tag("a", "a"), tag("b", "b")], keep_latest=1)) == []


# --- small boundaries ------------------------------------------------------------------------------------------------


def test_base_image_policy_counts_violating_images_per_entry_and_words_ages_and_days_correctly():
    twice = policy([{"ref": "alpine:latest"}, {"ref": "alpine:latest"}])
    assert twice["images_checked"] == 2 and twice["violating_images"] == 2
    just_over = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-11-30T23:59:59+00:00"}], max_age_days=90)
    assert ids(just_over) == ["image.too-old"] and just_over["findings"][0]["summary"] == "image is 91 days old (rounded up), over the 90 day limit"
    exactly = policy([{"ref": f"alpine:3@{DIGEST}", "created_at": "2025-12-01T00:00:00+00:00"}], max_age_days=90)
    assert ids(exactly) == []
    one_day = policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": "2026-03-02"}])
    assert one_day["findings"][0]["summary"] == "support ends on 2026-03-02 (1 day)"


@pytest.mark.parametrize(("predicate_type", "kind"), [
    ("https://cyclonedx.org/bomX", ""), ("https://spdx.dev/Documents", ""), ("https://openvex.dev/nsfoo", ""), ("https://cyclonedx.org/bom/", ""),
    ("https://cyclonedx.org/bom/v1.6", "sbom"), ("https://spdx.dev/Document/v2.3", "sbom"),
])
def test_attestation_predicate_kinds_match_on_path_boundaries(predicate_type, kind):
    result = bundle([att(predicate_type)], ["sbom"])
    assert (result["present_kinds"] == ["sbom"]) is (kind == "sbom")
    assert (ids(result) == []) is (kind == "sbom")


def test_dockerfile_lint_ignores_a_byte_order_mark_and_names_the_base_image_caveat_for_a_missing_user():
    bom = lint("\ufeff" + f"FROM alpine:3@{DIGEST}\nHEALTHCHECK CMD true\n")
    assert ids(bom) == ["docker.final-user-missing"] and bom["stages"] == 1 and bom["final_base"] == f"alpine:3@{DIGEST}"
    assert bom["findings"][0]["summary"] == "final stage has no USER instruction; the container runs as root unless the base image sets a non-root user"


# --- exact values recomputed independently of the implementation (tables checked by hand and against a second implementation) -------------


@pytest.mark.parametrize(("pattern", "text", "expected"), [
    ("*", "x", True), ("a*", "a", True), ("*a", "a", True), ("a*a", "a", False), ("a*a", "aa", True), ("a*b*c", "abc", True), ("a*b*c", "acb", False),
    ("a*b*b", "abb", True), ("a*b*b", "ab", False), ("**", "x", True), ("a**b", "ab", True), ("*a*", "bab", True), ("ab*ba", "aba", False),
    ("ab*ba", "abba", True), ("a/*", "a/b/c", True), ("a/*", "a", False), ("a", "a", True), ("a", "b", False),
])
def test_signature_identity_glob_matches_like_a_dot_star_regex(pattern, text, expected):
    rule = {"name": "r", "issuer": "https://i", "identity": pattern, "required": True}
    signature = {"signer": text or "x", "issuer": "https://i", "verified": True, "in_transparency_log": True}
    result = ok("supply_signature_policy", {"signatures": [signature], "rules": [rule], "min_valid_signatures": 0, "require_transparency_log": False})
    assert (result["rules_satisfied"] == 1) is expected


def test_glob_helper_agrees_with_the_regex_translation_on_empty_strings():
    module = importlib.import_module("swarm.tools.a18_tools")
    assert [module._glob_match(p, "") for p in ("*", "", "a*", "**")] == [True, True, False, True]


@pytest.mark.parametrize(("expression", "verdict"), [
    ("MIT OR GPL-3.0-only", "pass"), ("MIT AND GPL-3.0-only", "fail"), ("(MIT OR Apache-2.0) AND BSD-3-Clause", "pass"),
    ("MIT OR GPL-3.0-only AND LGPL-2.1-only", "pass"), ("GPL-3.0-only OR LGPL-2.1-only", "review"), ("GPL-3.0-only WITH Classpath-exception-2.0", "fail"),
    ("MIT AND (Apache-2.0 OR ISC)", "pass"), ("BSD-3-Clause AND Exc-1.0 OR MIT", "pass"), ("Unknown-1.0 OR GPL-3.0-only", "review"),
    ("Unknown-1.0 AND MIT", "review"), ("GPL-3.0-only AND Unknown-1.0", "fail"), ("MIT OR (Apache-2.0", "review"), ("MIT AND", "review"),
])
def test_license_expression_verdicts_match_an_independent_evaluation(expression, verdict):
    result = licenses(expression, allow=("MIT", "Apache-2.0", "BSD-3-Clause"), deny=("GPL-3.0-only",))
    assert result["verdict"] == verdict, (expression, result["findings"])


@pytest.mark.parametrize(("sig", "provenance_ok", "trusted", "isolated", "key", "level"), [
    (True, True, True, True, True, 3), (True, True, True, True, False, 2), (True, True, True, False, True, 2), (True, True, False, True, True, 1),
    (False, True, True, True, True, 1), (False, True, False, False, False, 1), (True, False, True, True, True, 0), (False, False, False, False, False, 0),
])
def test_slsa_level_is_the_longest_run_of_satisfied_levels(sig, provenance_ok, trusted, isolated, key, level):
    prov = statement()
    if not provenance_ok:
        prov["subject"] = [{"name": "x", "digest": {"sha1": "a" * 40}}]
    result = slsa(prov, signature_verified=sig, trusted_builder_ids=[BUILDER] if trusted else [], isolated_build=isolated, signing_key_isolated=key, required_level=3)
    assert result["level"] == level and result["meets_required"] is (level >= 3)
    assert result["verdict"] == ("pass" if level >= 3 else "fail")


@pytest.mark.parametrize(("eol", "as_of", "expected"), [
    ("2026-03-01", "2026-03-01T00:00:00+00:00", "image.end-of-life"), ("2026-02-28", "2026-03-01T00:00:00+00:00", "image.end-of-life"),
    ("2026-03-02", "2026-03-01T23:59:59+00:00", "image.eol-soon"), ("2026-05-30", "2026-03-01T00:00:00+00:00", "image.eol-soon"),
    ("2026-05-31", "2026-03-01T00:00:00+00:00", None), ("2026-03-01", "2026-02-28T23:30:00-05:00", "image.eol-soon"),
    ("2026-03-01", "2026-03-01T01:00:00+14:00", "image.end-of-life"),
])
def test_base_image_eol_boundaries_use_the_calendar_date_of_as_of_in_its_own_offset(eol, as_of, expected):
    result = policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": eol}], as_of=as_of)
    assert ids(result) == ([expected] if expected else [])


def test_vex_newest_statement_wins_across_offsets_and_ties_go_to_the_worse_status():
    product = REQUESTS
    older_but_later_clock = stmt(status="affected", timestamp="2026-01-01T03:00:00+05:00", justification=None)  # 2025-12-31T22:00Z
    newer = stmt(status="fixed", timestamp="2025-12-31T23:00:00Z", justification=None)
    older_but_later_clock.pop("justification", None), newer.pop("justification", None)
    assert vex([vuln(product=product)], [older_but_later_clock, newer])["suppressed_count"] == 1
    tie_a = stmt(status="fixed", timestamp="2026-01-01T00:00:00+00:00")
    tie_b = stmt(status="under_investigation", timestamp="2026-01-01T01:00:00+01:00")
    tie = vex([vuln(product=product)], [tie_a, tie_b])
    assert tie["suppressed_count"] == 0 and tie["exposed"][0]["status"] == "under_investigation"


def test_sbom_validate_ntia_coverage_counts_are_exact():
    comps = [cdx_component("a"), cdx_component("b", supplier=None), cdx_component("c", version=""), cdx_component("d", purl=None)]
    result = validate(cdx(comps))
    by = {e["element"]: (e["covered"], e["total"], e["satisfied"]) for e in result["ntia_elements"]}
    assert by["supplier_name"] == (4, 5, False) and by["component_name"] == (5, 5, True) and by["component_version"] == (4, 5, False)
    assert by["unique_identifier"] == (4, 5, False) and by["dependency_relationship"] == (1, 1, True)
    assert result["component_count"] == 5 and result["ntia_compliant"] is False
    assert [f["summary"] for f in result["findings"] if f["id"] == "ntia.supplier_name"] == ["Supplier Name missing on 1 of 5 components (e.g. b)"]


# --- outputs stay far below the 256 KiB result limit for adversarial but schema-valid inputs ------------------------------------------------


def _json_size(result: dict) -> int:
    return len(json.dumps(result))


def test_sbom_diff_with_maximal_names_versions_and_licenses_stays_well_below_the_result_limit():
    def document(version: str, license_id: str) -> str:
        components = [{"type": "library", "name": f"n{i:03d}" + "x" * 296, "version": version * 200, "licenses": [{"license": {"name": license_id * 300}}]} for i in range(200)]
        return json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components})

    result = ok("supply_sbom_diff", {"before": document("1", "L"), "after": document("2", "M")})
    assert (result["version_changed_count"], result["license_changed_count"], result["added_count"]) == (200, 200, 0)
    assert _json_size(result) < 200_000
    assert 0 < len(result["version_changed"]) < 200 and result["version_changed"][0]["name"].startswith("n000")  # leading entries, in key order


def test_vex_apply_with_maximal_products_stays_well_below_the_result_limit():
    vulns = [{"id": f"CVE-2026-{i:05d}", "product": f"pkg:generic/{i:03d}" + "p" * 280, "severity": "critical"} for i in range(300)]
    result = ok("supply_vex_apply", {"vulnerabilities": vulns, "statements": []})
    assert (result["total"], result["exposed_count"], result["findings_total"]) == (300, 300, 300)
    assert _json_size(result) < 200_000 and len(result["findings"]) < 200 and result["findings"][0]["id"] == "CVE-2026-00000"


def test_pin_audit_and_dockerfile_findings_with_maximal_locations_stay_below_the_limit():
    content = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n" + "".join(f"ADD {'s' * 280}{i:03d} /a\n" for i in range(250))
    result = lint(content)
    assert result["findings_total"] == 250 and _json_size(result) < 200_000


def test_dockerfile_lint_unterminated_heredoc_markers_are_scanned_in_linear_time():
    import time

    content = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n" + "RUN echo <<X\n" * 7000
    started = time.perf_counter()
    result = lint(content)
    assert time.perf_counter() - started < 1.0  # was quadratic: every marker re-scanned the rest of the file
    assert result["instructions"] == 7003 and ids(result) == []


def test_base_image_policy_eol_date_must_be_a_date_optionally_followed_by_a_real_time():
    for good in ("2026-03-02", "2026-03-02T00:00:00Z", "2026-03-02 00:00:00"):
        assert ids(policy([{"ref": f"alpine:3@{DIGEST}", "eol_date": good}])) == ["image.eol-soon"], good
    for bad in ("2026-03-02 garbage", "2026-03-02T", "2026-02-30", "2026-3-2", "20260302", "2026-03-02Tnoon"):
        invalid("supply_base_image_policy", {"images": [{"ref": f"alpine:3@{DIGEST}", "eol_date": bad}], "allowed_registries": ["docker.io"], "require_digest": False, "as_of": AS_OF}, "images")


def test_dockerfile_lint_tab_after_a_keyword_separates_it_like_a_space():
    tabbed = f"FROM\talpine:3@{DIGEST}\nUSER\troot\nHEALTHCHECK\tCMD true\n"
    assert ids(lint(tabbed)) == ids(lint(tabbed.replace("\t", " "))) == ["docker.final-user-root"]
    assert at(lint(tabbed), "docker.final-user-root") == ["line 2"]
    app = f"FROM\talpine:3@{DIGEST}\nUSER\tapp\nHEALTHCHECK\tCMD true\n"
    assert ids(lint(app)) == ids(lint(app.replace("\t", " ")))


def test_dockerfile_lint_heredoc_terminators_are_bisected_not_rescanned_per_marker():
    import time

    content = f"FROM alpine:3@{DIGEST}\nUSER app\nHEALTHCHECK CMD true\n" + "".join(f"RUN echo $((1<<N{i}))\n" for i in range(20000))
    started = time.perf_counter()
    result = lint(content)
    assert time.perf_counter() - started < 5.0  # was quadratic: about 10 s just to parse 20000 RUN lines
    assert result["instructions"] == 20003 and ids(result) == []


def test_sbom_validate_spdx_noassertion_version_is_not_a_present_version():
    doc = json.loads(spdx())
    doc["packages"][1]["versionInfo"] = "NOASSERTION"
    result = validate(json.dumps(doc))
    assert element(result, "component_version")["covered"] == 1 and result["ntia_compliant"] is False
    assert ids(result) == ["ntia.component_version"] and result["verdict"] == "fail"


def test_sbom_diff_removed_versionless_scoped_npm_component_is_seen():
    before = cdx([cdx_component("@s/a", "", purl="pkg:npm/@s/a"), cdx_component("@s/b", "", purl="pkg:npm/@s/b")], root=False)
    after = cdx([cdx_component("@s/a", "", purl="pkg:npm/@s/a")], root=False)
    result = diff(before, after)
    assert result["removed_count"] == 1 and result["removed"][0]["name"] == "@s/b" and result["verdict"] == "changed"


def test_vex_apply_scoped_npm_product_matches_its_versioned_vulnerability_both_ways():
    versionless_statement = vex([vuln(product="pkg:npm/@s/a@1.0.0")], [stmt(products=["pkg:npm/@s/a"], status="not_affected")])
    assert versionless_statement["verdict"] == "clear" and versionless_statement["suppressed_count"] == 1
    assert versionless_statement["unmatched_statements"] == 0
    versioned_statement = vex([vuln(product="pkg:npm/@s/a")], [stmt(products=["pkg:npm/@s/a@1.0.0"], status="not_affected")])
    assert versioned_statement["verdict"] == "exposed" and versioned_statement["unmatched_statements"] == 1


def test_pin_audit_unverifiable_workflow_image_is_a_finding_not_skipped():
    result = audit((".github/workflows/ci.yml", "jobs:\n  a:\n    container:\n      image: Node:20\n"))
    assert finding_rows(result) == [("pin.image-invalid-reference", "major", ".github/workflows/ci.yml:4")]
    assert (result["references"], result["unpinned"]) == (1, 1)


def test_retention_plan_names_the_input_position_of_a_bad_protected_digest():
    payload = {"tags": [tag("a")], "keep_latest": 0, "min_age_days": 0, "protected_prefixes": [], "max_delete_pct": 100, "as_of": AS_OF,
               "protected_digests": [DIGESTS["a"], DIGESTS["a"], "bad"]}
    bad = call("supply_retention_plan", payload)
    assert bad["state"] == "INVALID_INPUT" and bad["field"] == "protected_digests" and "protected_digests[2]" in bad["message"]


def test_dockerfile_lint_final_user_is_expanded_from_build_args():
    # a global ARG (before FROM) is not visible inside the stage, so USER $U is unresolved; a bare stage ARG takes the global default
    assert ids(lint(f"ARG U=root\nFROM alpine:3@{DIGEST}\nUSER $U\nHEALTHCHECK CMD true\n")) == ["docker.final-user-unresolved"]
    assert ids(lint(f"ARG U=root\nFROM alpine:3@{DIGEST}\nARG U\nUSER $U\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nARG U=root\nUSER ${{U}}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    unresolved = lint(f"ARG UID\nFROM alpine:3@{DIGEST}\nUSER ${{UID}}\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.final-user-unresolved"] and severity_of(unresolved, "docker.final-user-unresolved") == "major"
    assert unresolved["verdict"] == "fail"
    # the global default 1000 is not visible in the stage, so ${UID} stays unresolved (Docker expands it to an empty user)
    assert ids(lint(f"ARG UID=1000\nFROM alpine:3@{DIGEST}\nUSER ${{UID}}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-unresolved"]


def test_pin_audit_action_metadata_docker_image_is_pinned_like_a_workflow_docker_action():
    action = audit((".github/actions/x/action.yml", "runs:\n  using: docker\n  image: 'docker://alpine:3.19'\n"))
    workflow = audit((".github/workflows/ci.yml", "steps:\n  - uses: docker://alpine:3.19\n"))
    assert finding_rows(action) == [("pin.action-image-unpinned", "minor", ".github/actions/x/action.yml:3")]
    assert [(f["id"], f["severity"]) for f in action["findings"]] == [(f["id"], f["severity"]) for f in workflow["findings"]]
    assert (action["references"], action["unpinned"], action["verdict"]) == (1, 1, "pass")


def test_pin_audit_action_metadata_local_dockerfile_image_is_no_registry_reference():
    result = audit((".github/actions/x/action.yml", "runs:\n  using: docker\n  image: Dockerfile\n  args: []\n"))
    assert result["findings_total"] == 0 and result["references"] == 0 and result["verdict"] == "pass"
    assert audit((".github/actions/x/action.yml", "runs:\n  image: ./docker/Dockerfile.prod\n"))["findings_total"] == 0


def test_vex_apply_never_echoes_a_credential_that_has_no_scheme():
    bad = invalid("supply_vex_apply", {"vulnerabilities": [vuln()], "statements": [stmt(justification="u:hunter2@host/x")]}, "statements")
    assert "hunter2" not in json.dumps(bad) and "not an OpenVEX justification" in bad["message"]
    unjustified = vex([vuln()], [stmt(vid="u:hunter2@host/x", justification="")])
    assert "hunter2" not in json.dumps(unjustified) and ids(unjustified) == ["CVE-2026-0001", "vex.not-affected-unjustified"]


def test_signature_policy_masks_a_rule_name_that_carries_a_credential():
    result = ok("supply_signature_policy", {"signatures": [], "rules": [{"name": "u:hunter2@host/x", "issuer": "https://issuer.example",
                                                                          "identity": "release", "required": True}],
                                             "min_valid_signatures": 0, "require_transparency_log": False})
    assert result["required_rules_missing"] == ["(value with an @, not echoed)"] and "hunter2" not in json.dumps(result)


def test_sbom_validate_masks_a_spec_version_that_carries_a_credential():
    spdx_result = validate(spdx(spdxVersion="SPDX-2.3u:hunter2@h"))
    assert spdx_result["spec_version"] == "(value with an @, not echoed)" and "sbom.unknown-spec-version" in ids(spdx_result)
    assert "hunter2" not in json.dumps(spdx_result)
    cdx_result = validate(cdx().replace('"1.5"', '"1.5u:hunter2@h"'))
    assert cdx_result["spec_version"] == "(value with an @, not echoed)" and "hunter2" not in json.dumps(cdx_result)


def test_dockerfile_lint_env_values_expand_in_order_before_user():
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nENV A=root\nENV B=$A\nUSER $B\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    braced = lint(f"FROM alpine:3@{DIGEST}\nENV A=root\nENV B=${{A}}\nUSER ${{B}}\nHEALTHCHECK CMD true\n")
    assert ids(braced) == ["docker.final-user-root"] and braced["verdict"] == "fail"
    # B took A's value when B was set; a later ENV A=app does not change B
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nENV A=root\nENV B=$A\nENV A=app\nUSER $B\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(f"ARG A=root\nARG B=${{A}}\nFROM alpine:3@{DIGEST}\nUSER $B\nHEALTHCHECK CMD true\n")) == ["docker.final-user-unresolved"]
    unresolved = lint(f"ARG X\nFROM alpine:3@{DIGEST}\nENV B=$X\nUSER $B\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.final-user-unresolved"] and unresolved["verdict"] == "fail"


def test_dockerfile_lint_global_arg_defaults_do_not_reach_a_stage_unless_redeclared():
    # the exact input: a global ARG U=1000 with a stage USER ${U:-0} runs as root in Docker, because the stage does not redeclare U
    assert ids(lint(f"ARG U=1000\nFROM alpine:3@{DIGEST}\nUSER ${{U:-0}}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(f"ARG U=1000\nFROM alpine:3@{DIGEST}\nARG U\nUSER ${{U:-0}}\nHEALTHCHECK CMD true\n")) == []


def test_dockerfile_lint_substitution_forms_this_reader_does_not_evaluate_are_unresolved():
    # Docker: ${U:+root} is root when U is set; the reader does not evaluate :+, so the user is unresolved, never a pass
    unresolved = lint(f"FROM alpine:3@{DIGEST}\nENV U=app\nUSER ${{U:+root}}\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.final-user-unresolved"] and unresolved["verdict"] == "fail"
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nENV U=app\nUSER ${{U:-root}}\nHEALTHCHECK CMD true\n")) == []


def test_dockerfile_lint_stage_arg_reaches_a_child_stage_through_from():
    # Docker: a stage's ARG is a build variable of a child that does FROM it, redeclared or not; a global ARG is not (see above)
    base = f"FROM alpine:3@{DIGEST} AS base\nARG U=root\nFROM base\n"
    assert ids(lint(base + "USER ${U:-1000}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(base + "ARG U\nUSER ${U:-1000}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(base + "ARG U=app\nUSER ${U:-1000}\nHEALTHCHECK CMD true\n")) == []
    assert ids(lint(base + "ENV U=app\nUSER $U\nHEALTHCHECK CMD true\n")) == []
    assert ids(lint(f"FROM alpine:3@{DIGEST} AS base\nARG U=root\nFROM base AS mid\nFROM mid\nUSER ${{U:-1000}}\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(f"FROM alpine:3@{DIGEST} AS base\nARG U=root\nFROM scratch\nUSER ${{U:-1000}}\nHEALTHCHECK CMD true\n")) == []


def test_dockerfile_lint_env_pairs_of_one_instruction_expand_against_the_earlier_environment():
    # Docker expands every pair of one ENV against the environment from before it: V takes the earlier U=app, not this instruction's root
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nENV U=app\nENV U=root V=$U\nUSER $V\nHEALTHCHECK CMD true\n")) == []
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nENV U=root\nENV U=app V=$U\nUSER $V\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]


def test_signature_policy_never_echoes_a_credential_without_a_scheme():
    result = ok("supply_signature_policy", {"signatures": [
        {"signer": "u:hunter2@host/x", "issuer": "https://issuer.example", "verified": False, "in_transparency_log": True},
        {"signer": "u:hunter2@host/y", "issuer": "https://issuer.example", "verified": True, "in_transparency_log": True}],
        "rules": [{"name": "release", "issuer": "https://issuer.example", "identity": "release", "required": False}],
        "min_valid_signatures": 0, "require_transparency_log": False})
    assert "hunter2" not in json.dumps(result)
    assert result["unexpected_signers"] == ["(value with an @, not echoed)"] and "signature.verification-failed" in ids(result)


def test_unresolved_base_with_a_credential_is_never_echoed_by_lint_or_audit():
    result = lint("ARG X\nFROM u:hunter2@host/${X}\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(result) == ["docker.base-unresolved-arg"] and "hunter2" not in json.dumps(result)
    pinned = audit(("Dockerfile", "ARG X\nFROM u:hunter2@host/${X}\nUSER app\n"))
    assert "hunter2" not in json.dumps(pinned) and finding_rows(pinned) == [("pin.base-unresolved-arg", "major", "Dockerfile:2")]


def test_signature_policy_names_ordinary_at_signs_and_masks_only_userinfo():
    result = sigpolicy([signer(identity="audit@corp")], rules=[rule(name="release@corp", identity="nobody@corp")])
    assert result["required_rules_missing"] == ["release@corp"] and result["unexpected_signers"] == ["audit@corp"]
    assert "release@corp" in next(f["summary"] for f in result["findings"] if f["id"] == "signature.required-signer-missing")


def test_signature_policy_required_summary_never_echoes_a_credential_in_identity_or_issuer():
    result = ok("supply_signature_policy", {"signatures": [], "rules": [{"name": "release", "issuer": "https://u:hunter2@h.example",
                                                                          "identity": "u:hunter2@host/x", "required": True}],
                                             "min_valid_signatures": 0, "require_transparency_log": False})
    summary = next(f["summary"] for f in result["findings"] if f["id"] == "signature.required-signer-missing")
    assert "hunter2" not in json.dumps(result) and "release" in summary
    bare_issuer = ok("supply_signature_policy", {"signatures": [], "rules": [{"name": "release", "issuer": "u:hunter2@h.example",
                                                                                "identity": "x", "required": True}],
                                                  "min_valid_signatures": 0, "require_transparency_log": False})
    assert "hunter2" not in json.dumps(bare_issuer)


def test_dockerfile_lint_env_takes_precedence_over_a_later_arg_of_the_same_name():
    env_then_arg = lint(f"FROM alpine:3@{DIGEST}\nENV U=root\nARG U=app\nUSER $U\nHEALTHCHECK CMD true\n")
    assert ids(env_then_arg) == ["docker.final-user-root"] and env_then_arg["verdict"] == "fail"
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nARG U=app\nENV U=root\nUSER $U\nHEALTHCHECK CMD true\n")) == ["docker.final-user-root"]
    assert ids(lint(f"FROM alpine:3@{DIGEST}\nARG U=app\nUSER $U\nHEALTHCHECK CMD true\n")) == []


def test_userinfo_whose_password_holds_a_slash_or_question_mark_is_masked_to_the_last_at():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for bare in ("u:Sup/3r@host", "u:Sup?3r@host", "u:p@ss@host"):
        assert "Sup" not in a18._echo_value(bare) and "ss@" not in a18._echo_value(bare), bare
    for url in ("https://u:Sup/3r@h/x", "https://u:Sup?3r@h/x"):
        assert "Sup" not in a18._scrub(url) and a18._scrub(url).startswith("https://***@h"), url
    unresolved = lint("ARG X\nFROM u:Sup/3r@host/${X}\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(unresolved) == ["docker.base-unresolved-arg"] and "Sup" not in json.dumps(unresolved)


def test_uppercase_digest_algorithm_is_displayed_like_the_lowercase_form():
    upper = f"SHA256:{'a' * 64}"
    result = lint(f"FROM alpine:3.19@{upper}\nUSER app\nHEALTHCHECK CMD true\n")
    assert ids(result) == [] and result["final_base"] == f"alpine:3.19@{upper}"
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._echo_value(f"alpine:3.19@{upper}") == f"alpine:3.19@{upper}"


def test_dockerfile_lint_env_inherited_from_an_in_file_parent_stage_takes_precedence_over_a_child_arg():
    inherited = lint(f"FROM alpine@{DIGEST} AS base\nENV U=root\nFROM base\nARG U=app\nUSER $U\nHEALTHCHECK CMD true\n")
    assert ids(inherited) == ["docker.final-user-root"] and inherited["verdict"] == "fail"
    two_hops = lint(f"FROM alpine@{DIGEST} AS base\nENV U=root\nFROM base AS mid\nFROM mid\nARG U=app\nUSER $U\nHEALTHCHECK CMD true\n")
    assert ids(two_hops) == ["docker.final-user-root"]
    assert ids(lint(f"FROM alpine@{DIGEST} AS base\nENV U=root\nFROM base\nENV U=app\nUSER $U\nHEALTHCHECK CMD true\n")) == []
    assert ids(lint(f"FROM alpine@{DIGEST} AS base\nFROM base\nARG U=app\nUSER $U\nHEALTHCHECK CMD true\n")) == []


def test_bare_userinfo_with_an_empty_user_is_masked():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._echo_value(":hunter2@host") == "(value with an @, not echoed)"
    result = ok("supply_signature_policy", {"signatures": [], "rules": [{"name": "release", "issuer": ":hunter2@host", "identity": "x",
                                                                          "required": True}],
                                             "min_valid_signatures": 0, "require_transparency_log": False})
    assert "hunter2" not in json.dumps(result)


def test_ecosystem_references_and_ordinary_at_names_stay_visible():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for reference in ("npm:@s/a@1.0.0", "gh:o/r@v1", "pkg:npm/@s/a@1.0.0", "release@corp", "audit@corp",
                      "https://github.com/acme/app/.github/workflows/release.yml@refs/tags/v1.0.0", "alpine:3.19@sha256:" + "a" * 64,
                      "alpine:3.19@SHA256:" + "A" * 64, "ghcr.io/acme/base:1.2@sha256:" + "a" * 64):
        assert a18._echo_value(reference) == reference, reference


def test_bare_userinfo_with_a_password_holding_slash_question_or_at_is_masked():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for credential in ("u:Sup/3r@host", "u:Sup?3r@host", "u:p@ss@host", "ci-user:Zq9canaryPW81@host.example/img", "https://u:Sup/3r@h/x"):
        assert a18._echo_value(credential) == "(value with an @, not echoed)", credential


def test_known_limit_an_ecosystem_user_with_a_slash_in_the_password_displays():
    # Documented limit of a18_tools._bare_credential: gh:Sup/3r@host is structurally a repository reference, so its secret shows.
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._echo_value("gh:Sup/3r@host") == "gh:Sup/3r@host"


def test_ecosystem_user_without_a_slash_in_the_password_is_masked():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for credential in ("npm:hunter2@host", "go:hunter2@host"):
        assert a18._echo_value(credential) == "(value with an @, not echoed)", credential


def test_npm_scope_exemption_is_for_the_npm_user_only_and_the_closed_list_holds():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for credential in ("u:@abc/Zq9canaryPW81@host", "ci-user:@svc/Zq9canaryPW81@host.example/img", "u:@scope/x@host",
                       "gh:hunter2@host", "go:hunter2@host", "oci:hunter2@host", "pkg:hunter2@host"):
        assert a18._echo_value(credential) == "(value with an @, not echoed)", credential
    for reference in ("npm:@s/a@1.0.0", "gh:o/r@v1", "pkg:npm/@s/a@1.0.0"):
        assert a18._echo_value(reference) == reference, reference


def _alias_chain(stages: int) -> str:
    """An ENV at the root, `stages` stages each named by alias and built FROM the previous one, and an ARG and USER at the end."""
    lines = [f"FROM alpine@{DIGEST} AS s0", "ENV U=root"]
    lines += [f"FROM s{i - 1} AS s{i}" for i in range(1, stages)]
    lines += [f"FROM s{stages - 1}", "ARG U=app", "USER $U", "HEALTHCHECK CMD true"]
    return "\n".join(lines) + "\n"


def test_dockerfile_lint_1100_stage_alias_chain_gives_a_verdict_without_recursion():
    result = lint(_alias_chain(1100))
    assert ids(result) == ["docker.final-user-root"] and result["verdict"] == "fail"


def test_dockerfile_lint_5000_stage_alias_chain_gives_a_verdict_in_linear_time():
    import time

    started = time.perf_counter()
    result = lint(_alias_chain(5000))
    assert time.perf_counter() - started < 5.0
    assert ids(result) == ["docker.final-user-root"] and result["verdict"] == "fail"


def test_closed_list_slash_must_come_before_the_first_at_of_the_password():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for credential in ("gh:hunter2@github.com/o/r@v1", "npm:hunter2@host/x@v1", "oci:hunter2@host"):
        assert a18._echo_value(credential) == "(value with an @, not echoed)", credential
    for reference in ("gh:o/r@v1", "pkg:npm/@s/a@1.0.0", "npm:@s/a@1.0.0", "gh:Sup/3r@host"):
        assert a18._echo_value(reference) == reference, reference


# NEW-7: each echo site that used to print a bare user:secret@host in full now goes through the shared _scrub (via _show and _f).
def test_new7_pin_action_not_sha_from_uses_masks_the_credential():
    result = audit((".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - uses: u:hunter2@host/x@v1\n"))
    assert ids(result) == ["pin.action-not-sha"] and "hunter2" not in json.dumps(result)


def test_new7_pin_npm_not_exact_masks_the_dependency_name_and_value():
    result = audit(("package.json", json.dumps({"dependencies": {"u:hunter2@host/x": "^1.0.0", "b": "u:hunter2@host/y"}})), ("package-lock.json", ""))
    assert "pin.npm-not-exact" in ids(result) and "hunter2" not in json.dumps(result)


def test_new7_provenance_masks_the_artifact_name_and_the_matched_subject():
    mismatch = match(statement(), artifact_name="u:hunter2@host/x")
    assert "provenance.name-mismatch" in ids(mismatch) and "hunter2" not in json.dumps(mismatch)
    subject = match(statement(subject=[{"name": "u:hunter2@host/x", "digest": {"sha256": HEX}}]))
    assert subject["matched"] is True and subject["matched_subject"] == "***@host/x" and "hunter2" not in json.dumps(subject)


def test_new7_sbom_diff_masks_an_added_component_name():
    result = diff(cdx([], root=False), cdx([cdx_component("u:hunter2@host/x", "1", purl=None)], root=False))
    assert result["added_count"] == 1 and "hunter2" not in json.dumps(result)


def test_new7_license_policy_masks_an_unparseable_license():
    result = licenses("u:hunter2@host/x")
    assert ids(result) == ["license.unparseable"] and "hunter2" not in json.dumps(result)


def test_new7_attestation_bundle_masks_an_unrecognized_predicate_type():
    result = bundle([att("u:hunter2@host/x"), att("provenance")], ["provenance"])
    assert "attestation.unrecognized-type" in ids(result) and "hunter2" not in json.dumps(result)


def test_new7_vex_apply_masks_an_exposed_product_and_its_message():
    result = vex([vuln(product="u:hunter2@host/x")], [])
    assert result["exposed_count"] == 1 and "hunter2" not in json.dumps(result)


def test_new7_sbom_validate_masks_a_bad_spdx_creator():
    result = validate(spdx(creationInfo={"created": "2026-02-01T10:00:00Z", "creators": ["u:hunter2@host/x", "Tool: syft-1.0"]}))
    assert "sbom.spdx-bad-creator" in ids(result) and "hunter2" not in json.dumps(result)


def test_new7_shared_mask_replaces_only_the_credential_span():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._scrub("uses: u:hunter2@host/x@v1") == "uses: ***@v1"


def test_new7_ordinary_values_pass_the_shared_mask_unchanged():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for ordinary in ("zlib", "pkg:pypi/requests@2.31.0", "pkg:generic/zlib@1.3.1?arch=x86", "MIT OR Apache-2.0",
                     "GPL-2.0-only WITH Classpath-exception-2.0", "release@corp", "npm:@s/a@1.0.0", "gh:o/r@v1",
                     "https://github.com/acme/app/.github/workflows/release.yml@refs/tags/v1.0.0", "docker://alpine:3.19",
                     "alpine:3.19@sha256:" + "a" * 64):
        assert a18._scrub(ordinary) == ordinary, ordinary


def test_new7_known_limits_still_display_through_the_shared_mask():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for value in ("gh:Sup/3r@host", "gh:o/hunter2@host", "bob@example.com"):
        assert a18._scrub(value) == value, value


def test_new8_passwords_longer_than_500_characters_are_masked_in_the_bare_and_scheme_forms():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    for secret, template in (("a" * 501, "u:{}@host"), ("a" * 501, "https://u:{}@host/x"), ("b" * 5000, "u:{}@host"),
                             ("Zq9" * 2000, "https://u:{}@host/x")):
        value = template.format(secret)
        assert secret not in a18._scrub(value) and secret not in a18._echo_value(value), (len(secret), template)


def test_new8_a_long_ordinary_value_without_an_at_is_unchanged():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    ordinary = "pkg:pypi/" + "a" * 100000
    assert a18._scrub(ordinary) == ordinary and a18._echo_value(ordinary) == ordinary


def test_new8_long_tokens_are_scanned_in_linear_time():
    import time

    a18 = importlib.import_module("swarm.tools.a18_tools")
    adversarial = ["a:" * 50000 + "h", "a:" * 50000 + "@sha256:" + "0" * 64, "://a" * 25000 + "@h", "a:" * 524288 + "@h"]
    for value in adversarial:  # a 100,000-character token and a 1 MiB value, with colons and scheme repeats and no usable @
        started = time.perf_counter()
        a18._scrub(value)
        a18._echo_value(value)
        assert time.perf_counter() - started < 5.0, len(value)
    assert a18._scrub("a:" * 50000 + "@h") == "***@h"


def _load_cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_supply_image_lint_for_tools_tests", ROOT / "scripts" / "supply_image_lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new9_a_credential_in_a_directory_name_is_redacted_in_locations_and_skipped_entries(tmp_path):
    cli = _load_cli()
    repo = tmp_path / "repo"
    (repo / "u:hunter2@host").mkdir(parents=True)
    (repo / "u:hunter2@host" / "Dockerfile").write_text(f"FROM alpine:3@{DIGEST}\nUSER root\nHEALTHCHECK CMD true\n", encoding="utf-8")
    (repo / "u:hunter2@host" / "Dockerfile.big").write_bytes(b"FROM alpine\n" + b"#" * (1024 * 1024))
    out = cli.scan(repo, repo)
    assert [f["location"] for f in out["findings"] if f["id"] == "docker.final-user-root"] == ["***@host/Dockerfile:2"]
    assert out["skipped"] == ["***@host/Dockerfile.big: over-1MiB"] and out["status"] == "fail"
    assert "hunter2" not in json.dumps(out)


def test_new9_an_ordinary_directory_name_is_unchanged_in_locations_and_skipped_entries(tmp_path):
    cli = _load_cli()
    repo = tmp_path / "repo"
    (repo / "service").mkdir(parents=True)
    (repo / "service" / "Dockerfile").write_text(f"FROM alpine:3@{DIGEST}\nUSER root\nHEALTHCHECK CMD true\n", encoding="utf-8")
    (repo / "service" / "Dockerfile.big").write_bytes(b"FROM alpine\n" + b"#" * (1024 * 1024))
    out = cli.scan(repo, repo)
    assert [f["location"] for f in out["findings"] if f["id"] == "docker.final-user-root"] == ["service/Dockerfile:2"]
    assert out["skipped"] == ["service/Dockerfile.big: over-1MiB"]


def test_new9_the_path_in_a_missing_path_error_is_redacted(tmp_path):
    import types

    from swarm.errors import SwarmError

    cli = _load_cli()
    with pytest.raises(SwarmError) as raised:
        cli.run(types.SimpleNamespace(path="u:hunter2@host/missing"), types.SimpleNamespace(root=tmp_path, dry_run=False))
    assert "hunter2" not in str(raised.value) and "does not exist" in str(raised.value)


def test_new12_a_reference_does_not_shield_a_credential_later_in_its_token():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._scrub("gh:o/r@x:hunter2@host") == "gh:o/r@***@host"
    assert a18._scrub("npm:@s/a@1.0.0,u:hunter2@host") == "npm:@s/a@1.0.0,***@host"
    assert a18._scrub("npm:@s/a@u:hunter2@host") == "npm:@s/a@***@host"
    assert a18._scrub("gh:o/r@v1:hunter2@host") == "gh:o/r@***@host"
    for value in ("gh:o/r@x:hunter2@host", "npm:@s/a@1.0.0,u:hunter2@host", "npm:@s/a@u:hunter2@host", "gh:o/r@v1:hunter2@host"):
        assert "hunter2" not in a18._scrub(value) and "hunter2" not in a18._echo_value(value), value
    assert a18._scrub("gh:o/r@v1") == "gh:o/r@v1" and a18._scrub("npm:@s/a@1.0.0") == "npm:@s/a@1.0.0"


def test_new12_a_token_with_two_colons_that_are_both_credentials_is_masked():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    both = a18._scrub("u:hunter2@x:pw@host")
    assert "hunter2" not in both and "pw" not in both and both == "***@host"
    assert a18._scrub("https://u:hunter2@x:pw@h/x") == "https://***@h/x"


def test_new14_a_user_longer_than_300_characters_is_masked_in_the_bare_and_scheme_forms():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    long_user = "a" * 100000
    assert "hunter2" not in a18._scrub(long_user + ":hunter2@host") and a18._scrub(long_user + ":hunter2@host") == "***@host"
    assert a18._scrub("://" + long_user + ":hunter2@host") == "://***@host"
    assert a18._scrub("://" + long_user + "@host") == "://***@host"
    assert a18._echo_value("a" * 5000 + ":hunter2@host") == "(value with an @, not echoed)"


def test_new14_adversarial_shapes_at_100k_and_1mib_each_finish_in_under_a_second():
    import time

    a18 = importlib.import_module("swarm.tools.a18_tools")
    for n in (100_000, 1 << 20):
        shapes = [
            "a:" * (n // 2),
            "a:" * (n // 2) + "@",
            "a" * n + ":hunter2@host",
            "://" + "a" * n + ":hunter2@host",
            "://" + "a" * n + "@host",
            "://a" * (n // 4),
            "://a" * (n // 4) + "@h",
            "gh:o/r@" * (n // 7),
            "npm:@s/a@" * (n // 9),
            "gh:o/r@x:hunter2@" * (n // 16),
            "u:@" * (n // 3),
        ]
        for value in shapes:
            for function in (a18._scrub, a18._echo_value):
                started = time.perf_counter()
                function(value)
                assert time.perf_counter() - started < 1.0, (n, value[:20], function.__name__)


def test_new16_an_npm_scoped_reference_with_an_at_in_its_version_is_the_documented_class():
    a18 = importlib.import_module("swarm.tools.a18_tools")
    assert a18._echo_value("npm:@s/hunter2@host") == "npm:@s/hunter2@host"
    assert a18._scrub("npm:@s/hunter2@host") == "npm:@s/hunter2@host"
    assert a18._scrub("npm:@s/x:hunter2@host") == "npm:@s/***@host"
    assert "hunter2" not in a18._echo_value("npm:@s/x:hunter2@host")
