"""Behaviour tests for the pre-v2.2 A10 lane tools (ROLE-01): output values for specific inputs, every branch.

Tools: sec_line_entropy, sec_credential_scan, sec_cvss_band, sec_dependency_age, sec_license_risk,
sec_tls_min_version, sec_header_presence, sec_kev_match_count, sec_sbom_summary.
Calls go through swarm.tool_registry.call_tool as A10 (schema validation + authorization + handler); a few tests call
the handler directly. Modules are resolved lazily because conftest evicts swarm* modules between tests.
"""
import importlib
import json
import time

import pytest


def call(name: str, payload: dict, caller: str = "A10") -> dict:
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    assert result["message"], result


# ---- sec_cvss_band -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("score", "severity", "blocking"),
    [
        (0, "none", False),
        (0.0, "none", False),
        (0.1, "low", False),
        (3.9, "low", False),
        (4.0, "medium", False),
        (6.9, "medium", False),
        (7.0, "high", True),
        (8.9, "high", True),
        (9.0, "critical", True),
        (10, "critical", True),
    ],
)
def test_cvss_band_boundaries_and_gate_threshold(score, severity, blocking):
    assert call("sec_cvss_band", {"score": score}) == {"state": "SUCCESS", "severity": severity, "gate_blocking": blocking}


def test_cvss_band_blocking_starts_at_high_not_medium():
    medium, high = call("sec_cvss_band", {"score": 6.99}), call("sec_cvss_band", {"score": 7.0})
    assert (medium["severity"], medium["gate_blocking"]) == ("medium", False)
    assert (high["severity"], high["gate_blocking"]) == ("high", True)


@pytest.mark.parametrize("score", [-0.1, 10.01, 11])
def test_cvss_band_out_of_range_names_score(score):
    invalid(call("sec_cvss_band", {"score": score}), "score")


@pytest.mark.parametrize("score", ["7.0", None, True, [7]])
def test_cvss_band_non_number_is_invalid(score):
    invalid(call("sec_cvss_band", {"score": score}), "score")


def test_cvss_band_missing_and_unknown_fields():
    invalid(call("sec_cvss_band", {}), "score")
    invalid(call("sec_cvss_band", {"score": 5, "extra": 1}), "extra")


def test_cvss_band_refuses_other_callers_and_answers_probes():
    assert call("sec_cvss_band", {"score": 5}, caller="A01")["state"] == "UNAUTHORIZED"
    assert call("sec_cvss_band", {"score": 5, "probe": "partial"})["state"] == "PARTIAL_SUCCESS"
    assert call("sec_cvss_band", {"score": 5, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


# ---- sec_dependency_age --------------------------------------------------------------------------

def age(released, as_of, max_age_days):
    return call("sec_dependency_age", {"released": released, "as_of": as_of, "max_age_days": max_age_days})


def test_dependency_age_prompt_example_input():
    # prompts/A10-security.md quotes age_days 1019 for this input; the calendar difference is 1018 (366 + 365 + 287).
    assert age("2024-01-01", "2026-10-15", 365) == {"state": "SUCCESS", "age_days": 1018, "stale": True}


def test_dependency_age_exact_day_counts():
    assert age("2024-01-01", "2024-01-01", 1) == {"state": "SUCCESS", "age_days": 0, "stale": False}
    assert age("2024-01-01", "2024-01-31", 30)["age_days"] == 30
    assert age("2023-01-01", "2024-01-01", 400)["age_days"] == 365
    assert age("2024-01-01", "2025-01-01", 400)["age_days"] == 366  # leap year


def test_dependency_age_stale_only_strictly_above_limit():
    assert age("2024-01-01", "2024-01-11", 10) == {"state": "SUCCESS", "age_days": 10, "stale": False}
    assert age("2024-01-01", "2024-01-12", 10) == {"state": "SUCCESS", "age_days": 11, "stale": True}
    assert age("2024-01-01", "2024-01-02", 1)["stale"] is False


def test_dependency_age_leap_day_is_a_real_date_only_in_leap_years():
    assert age("2024-02-29", "2024-03-10", 10) == {"state": "SUCCESS", "age_days": 10, "stale": False}
    invalid(age("2023-02-29", "2024-03-10", 10), "released")
    invalid(age("2024-01-01", "2023-02-29", 10), "as_of")


@pytest.mark.parametrize("bad", ["2024-13-01", "2024-00-10", "2024-01-32", "2024/01/01", "2024-1-011", "abcd-ef-gh", "2024-01-0 "])
def test_dependency_age_malformed_released_names_released(bad):
    invalid(age(bad, "2024-02-01", 5), "released")


@pytest.mark.parametrize("bad", ["2024-13-01", "2024-01-1x", "01-01-2024"])
def test_dependency_age_malformed_as_of_names_as_of(bad):
    invalid(age("2024-01-01", bad, 5), "as_of")


def test_dependency_age_release_in_the_future_is_invalid():
    result = age("2024-02-02", "2024-02-01", 5)
    invalid(result, "released")
    assert "after as_of" in result["message"]


def test_dependency_age_released_is_checked_before_as_of():
    invalid(age("nope-nope-", "also-bad-x", 5), "released")


@pytest.mark.parametrize("limit", [0, -1, 3651, 1.5, "10"])
def test_dependency_age_limit_bounds(limit):
    invalid(age("2024-01-01", "2024-02-01", limit), "max_age_days")


def test_dependency_age_limit_extremes_accepted():
    assert age("2024-01-01", "2024-02-01", 1)["stale"] is True
    assert age("2024-01-01", "2024-02-01", 3650)["stale"] is False


def test_dependency_age_non_ascii_digits_are_not_a_date():
    invalid(age("\u0662\u0660\u0662\u0664-\u0660\u0661-\u0660\u0661", "2024-02-01", 5), "released")


# ---- sec_license_risk ----------------------------------------------------------------------------

def licenses(found, allowed):
    return call("sec_license_risk", {"licenses": found, "allowlist": allowed})


def test_license_risk_all_allowed():
    assert licenses(["MIT", "Apache-2.0"], ["MIT", "Apache-2.0", "BSD-3-Clause"]) == {
        "state": "SUCCESS", "license_ok": True, "unapproved": [], "unapproved_count": 0,
    }


def test_license_risk_reports_sorted_unapproved():
    assert licenses(["MIT", "LGPL-3.0", "GPL-3.0", "AGPL-3.0"], ["MIT"]) == {
        "state": "SUCCESS", "license_ok": False, "unapproved": ["AGPL-3.0", "GPL-3.0", "LGPL-3.0"], "unapproved_count": 3,
    }


def test_license_risk_allowlist_match_is_case_insensitive_and_trimmed():
    result = licenses([" mit ", "APACHE-2.0", "GPL-3.0"], ["MIT", " apache-2.0 "])
    assert result["unapproved"] == ["GPL-3.0"]
    assert result["license_ok"] is False


def test_license_risk_unapproved_keeps_the_original_spelling_trimmed():
    assert licenses(["  GpL-3.0  "], ["MIT"])["unapproved"] == ["GpL-3.0"]


def test_license_risk_repeated_identifier_counted_once():
    result = licenses(["GPL-3.0", "GPL-3.0", "MIT"], ["MIT"])
    assert (result["unapproved"], result["unapproved_count"]) == (["GPL-3.0"], 1)


def test_license_risk_empty_allowlist_rejects_everything():
    result = licenses(["MIT"], [])
    assert (result["license_ok"], result["unapproved"], result["unapproved_count"]) == (False, ["MIT"], 1)


def test_license_risk_compound_expression_is_one_identifier():
    result = licenses(["MIT OR Apache-2.0"], ["MIT", "Apache-2.0"])
    assert result["unapproved"] == ["MIT OR Apache-2.0"]


def test_license_risk_blank_entries_name_their_field():
    invalid(licenses(["   "], ["MIT"]), "licenses")
    invalid(licenses(["MIT"], ["  "]), "allowlist")
    invalid(licenses(["MIT", ""], ["MIT"]), "licenses")
    invalid(licenses([], ["MIT"]), "licenses")


def test_license_risk_blank_licenses_reported_before_blank_allowlist():
    invalid(licenses([" "], [" "]), "licenses")


def test_license_risk_size_limits():
    invalid(licenses(["x"] * 1001, ["MIT"]), "licenses")
    invalid(licenses(["x" * 129], ["MIT"]), "licenses")
    invalid(licenses(["MIT"], ["y"] * 1001), "allowlist")


# ---- sec_tls_min_version -------------------------------------------------------------------------

def tls(protocols, minimum="TLS1.2"):
    return call("sec_tls_min_version", {"protocols": protocols, "minimum_version": minimum})


def test_tls_compliant_set():
    assert tls(["TLS1.2", "TLS1.3"]) == {"state": "SUCCESS", "compliant": True, "below_minimum": [], "unrecognized": []}


def test_tls_weak_protocols_are_reported_in_canonical_form_sorted():
    result = tls(["TLSv1.1", "SSLv3", "TLS 1.0", "tls1.3", "SSLv2"])
    assert result["compliant"] is False
    assert result["below_minimum"] == ["SSL2", "SSL3", "TLS1.0", "TLS1.1"]
    assert result["unrecognized"] == []


@pytest.mark.parametrize("spelling", ["TLS1.2", "TLSv1.2", "TLS 1.2", "tls1.2", "tlsv1.2", "TLS-1.2", " TLS1.2 "])
def test_tls_spellings_of_the_minimum_are_compliant(spelling):
    assert tls([spelling])["compliant"] is True


def test_tls_minimum_1_3_flags_1_2():
    assert tls(["TLS1.2", "TLS1.3"], "TLS1.3") == {
        "state": "SUCCESS", "compliant": False, "below_minimum": ["TLS1.2"], "unrecognized": [],
    }
    assert tls(["TLS1.3"], "TLS1.3")["compliant"] is True


def test_tls_unknown_protocols_are_unrecognized_not_compliant():
    result = tls(["TLS1.2", "TLS1.4", "QUIC", "TLSv1"])
    assert result["compliant"] is False
    assert result["unrecognized"] == ["QUIC", "TLS1.4", "TLSv1"]  # raw spelling, trimmed, sorted
    assert result["below_minimum"] == []


def test_tls_duplicates_collapse():
    result = tls(["SSLv3", "SSL3", "ssl 3", "TLS1.2", "TLS1.2"])
    assert result["below_minimum"] == ["SSL3"]


def test_tls_blank_protocol_names_protocols():
    invalid(tls(["TLS1.2", "   "]), "protocols")
    invalid(tls([]), "protocols")
    invalid(tls(["x"] * 33), "protocols")


def test_tls_minimum_must_be_one_of_the_supported_floors():
    invalid(tls(["TLS1.2"], "TLS1.0"), "minimum_version")
    invalid(tls(["TLS1.2"], "tls1.2"), "minimum_version")


# ---- sec_header_presence -------------------------------------------------------------------------

def headers(observed, required):
    return call("sec_header_presence", {"headers": observed, "required_headers": required})


def test_headers_all_present():
    assert headers(["Content-Type", "Strict-Transport-Security"], ["strict-transport-security"]) == {
        "state": "SUCCESS", "complete": True, "missing": [], "missing_count": 0,
    }


def test_headers_missing_are_lowercased_and_sorted():
    result = headers(["Content-Type"], ["X-Frame-Options", "Content-Security-Policy", "Strict-Transport-Security"])
    assert result == {
        "state": "SUCCESS", "complete": False, "missing_count": 3,
        "missing": ["content-security-policy", "strict-transport-security", "x-frame-options"],
    }


def test_headers_comparison_is_case_and_whitespace_insensitive():
    assert headers(["X-FRAME-OPTIONS "], [" x-frame-options"])["complete"] is True


def test_headers_required_duplicates_count_once():
    result = headers([], ["X-Frame-Options", "x-frame-options", "X-Content-Type-Options"])
    assert (result["missing"], result["missing_count"]) == (["x-content-type-options", "x-frame-options"], 2)


def test_headers_empty_observed_set_is_valid():
    assert headers([], ["Referrer-Policy"])["missing"] == ["referrer-policy"]


def test_headers_extra_observed_headers_are_ignored():
    assert headers(["a", "b", "c"], ["a"])["complete"] is True


def test_headers_blank_names_name_their_field():
    invalid(headers(["  "], ["a"]), "headers")
    invalid(headers([], ["  "]), "required_headers")
    invalid(headers(["a"], []), "required_headers")


def test_headers_blank_observed_reported_before_blank_required():
    invalid(headers([" "], [" "]), "headers")


def test_headers_size_limits():
    invalid(headers(["h"] * 257, ["a"]), "headers")
    invalid(headers([], ["h"] * 65), "required_headers")


# ---- sec_kev_match_count -------------------------------------------------------------------------

def kev(cves, listed):
    return call("sec_kev_match_count", {"cve_ids": cves, "kev_ids": listed})


def test_kev_prompt_example_matches_one():
    assert kev(["CVE-2021-44228", "CVE-2023-0001", "cve-2021-44228"], ["CVE-2021-44228", "CVE-2022-22965"]) == {
        "state": "SUCCESS", "checked_count": 2, "matched": ["CVE-2021-44228"], "matched_count": 1, "blocking": True,
    }


def test_kev_no_overlap_does_not_block():
    assert kev(["CVE-2023-0001"], ["CVE-2021-44228"]) == {
        "state": "SUCCESS", "checked_count": 1, "matched": [], "matched_count": 0, "blocking": False,
    }


def test_kev_matches_sorted_and_distinct():
    result = kev(["CVE-2022-0002", "CVE-2021-0001", "CVE-2022-0002"], ["CVE-2021-0001", "CVE-2022-0002", "CVE-2022-0002"])
    assert result["matched"] == ["CVE-2021-0001", "CVE-2022-0002"]
    assert (result["checked_count"], result["matched_count"]) == (2, 2)


def test_kev_case_and_whitespace_normalised():
    result = kev([" cve-2021-44228 "], ["Cve-2021-44228"])
    assert result["matched"] == ["CVE-2021-44228"] and result["blocking"] is True


def test_kev_empty_lists_are_valid_and_not_blocking():
    assert kev([], []) == {"state": "SUCCESS", "checked_count": 0, "matched": [], "matched_count": 0, "blocking": False}
    assert kev([], ["CVE-2021-44228"])["blocking"] is False
    assert kev(["CVE-2021-44228"], [])["blocking"] is False


@pytest.mark.parametrize("bad", ["CVE-21-1234", "CVE-2021-123", "CVE2021-1234", "2021-1234", "CVE-2021-12a4", "GHSA-1234-5678"])
def test_kev_malformed_cve_ids_name_cve_ids(bad):
    invalid(kev(["CVE-2021-44228", bad], ["CVE-2021-44228"]), "cve_ids")


def test_kev_malformed_kev_entries_name_kev_ids_and_cve_ids_win_when_both_bad():
    invalid(kev(["CVE-2021-44228"], ["not-a-cve"]), "kev_ids")
    invalid(kev(["bad"], ["also-bad"]), "cve_ids")


def test_kev_long_numeric_suffix_is_accepted():
    assert kev(["CVE-2024-1234567"], ["CVE-2024-1234567"])["blocking"] is True


def test_kev_size_limits():
    invalid(kev(["CVE-2021-0001"] * 5001, []), "cve_ids")
    invalid(kev([], ["CVE-2021-0001"] * 20001), "kev_ids")
    invalid(kev(["x" * 33], []), "cve_ids")


def test_kev_non_ascii_digits_are_rejected_not_silently_unmatched():
    result = kev(["CVE-\uff12\uff10\uff12\uff11-44228"], ["CVE-2021-44228"])  # swarm/tools/a10_tools.py:35 (_CVE_ID)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "cve_ids"


# ---- sec_sbom_summary ----------------------------------------------------------------------------

def sbom(components):
    return call("sec_sbom_summary", {"components": components})


def test_sbom_prompt_example_counts():
    assert sbom([{"name": "requests", "version": "2.32.3"}, {"name": "left-pad", "version": "^1.3.0"}, {"name": "flask"}]) == {
        "state": "SUCCESS", "component_count": 3, "unpinned_count": 2, "unpinned": ["flask", "left-pad"], "all_pinned": False,
    }


@pytest.mark.parametrize("version", ["1", "1.2", "1.2.3", "0.0.0", "v1.2.3", "1.2.3-rc.1", "1.2.3+build.5", "1.2.3-beta.2+exp.sha", "  1.2.3  "])
def test_sbom_pinned_versions(version):
    result = sbom([{"name": "pkg", "version": version}])
    assert (result["unpinned_count"], result["all_pinned"]) == (0, True)


@pytest.mark.parametrize(
    "version",
    ["^1.3.0", "~1.3", ">=1.0", "<2", "1.x", "1.*", "*", "latest", "main", "1.2.3-", "1.2.3 - 2.0.0", "1..2", "v", "", "   ", "1.2.3-beta_1"],
)
def test_sbom_unpinned_versions(version):
    result = sbom([{"name": "pkg", "version": version}])
    assert (result["unpinned_count"], result["unpinned"], result["all_pinned"]) == (1, ["pkg"], False)


def test_sbom_missing_version_is_unpinned():
    result = sbom([{"name": "flask"}])
    assert result["unpinned"] == ["flask"] and result["component_count"] == 1


def test_sbom_all_pinned_summary():
    assert sbom([{"name": "a", "version": "1.0.0"}, {"name": "b", "version": "2.0.0"}]) == {
        "state": "SUCCESS", "component_count": 2, "unpinned_count": 0, "unpinned": [], "all_pinned": True,
    }


def test_sbom_names_are_trimmed_and_sorted():
    result = sbom([{"name": "  zlib "}, {"name": "alpha", "version": "latest"}])
    assert result["unpinned"] == ["alpha", "zlib"]


def test_sbom_duplicate_names_count_per_component_but_list_name_once():
    result = sbom([{"name": "dup", "version": "1.0.0"}, {"name": "dup", "version": "^1"}, {"name": "dup"}])
    assert (result["component_count"], result["unpinned_count"], result["unpinned"]) == (3, 2, ["dup"])


def test_sbom_malformed_components_name_components():
    invalid(sbom([]), "components")
    invalid(sbom([{"name": "   "}]), "components")
    invalid(sbom([{"name": "ok"}, {"version": "1.0.0"}]), "components")
    invalid(sbom([{"name": "ok", "extra": 1}]), "components")
    invalid(sbom([{"name": "x", "version": "1" * 129}]), "components")
    invalid(sbom([{"name": "x" * 257}]), "components")


def test_sbom_non_ascii_digit_version_is_not_pinned():
    result = sbom([{"name": "x", "version": "\u0661.\u0662.\u0663"}])  # swarm/tools/a10_tools.py:38 (_PINNED_VERSION)
    assert result["unpinned"] == ["x"]


# ---- sec_credential_scan -------------------------------------------------------------------------

def scan(lines):
    return call("sec_credential_scan", {"lines": lines})


def rules(lines):
    return [(f["line"], f["rule"]) for f in scan(lines)["findings"]]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("key = AKIAABCDEFGHIJKLMNOP", "aws_access_key_id"),
        ("ASIAABCDEFGHIJKLMNOP", "aws_access_key_id"),
        ("token ghp_" + "a1B2" * 9, "github_token"),
        ("gho_" + "a" * 36, "github_token"),
        ("xoxb-1234567890-abcdefgh", "slack_token"),
        ("sk_live_" + "a" * 16, "stripe_live_key"),
        ("rk_live_" + "b" * 20, "stripe_live_key"),
        ("AIza" + "a" * 35, "google_api_key"),
        ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefghijkl", "jwt"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key_block"),
        ("-----BEGIN PRIVATE KEY-----", "private_key_block"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private_key_block"),
        ("password = hunter2hunter2", "generic_secret_assignment"),
        ("DB-PASSWORD:abcdefgh", "generic_secret_assignment"),
        ("my_api-key = 'abcdefgh'", "generic_secret_assignment"),
        ("client_secret: abcdefgh1", "generic_secret_assignment"),
    ],
)
def test_credential_scan_each_rule_reports_name_and_line(line, rule):
    assert scan(["nothing here", line]) == {
        "state": "SUCCESS", "finding_count": 1, "findings": [{"rule": rule, "line": 2}],
    }


@pytest.mark.parametrize(
    "line",
    [
        "sk_test_" + "a" * 16,
        "AKIAABCDEFGHIJKLMNO",  # 15 chars after AKIA is 19 total: too short
        "ghp_" + "a" * 35,
        "ghx_" + "a" * 36,
        "xoxz-1234567890",
        "AIza" + "a" * 34,
        "-----BEGIN PUBLIC KEY-----",
        "password = short",
        "api_key=<your-key-here>",
        "token: ${SECRET_TOKEN}",
        "SECRET=********",
        "auth_token=abcdefg",
        "just an ordinary sentence about passwords",
        "",
    ],
)
def test_credential_scan_near_misses_do_not_fire(line):
    assert scan([line])["findings"] == []


def test_credential_scan_line_numbers_are_one_based_and_ordered():
    found = rules(["ok", "AKIAABCDEFGHIJKLMNOP", "ok", "-----BEGIN PRIVATE KEY-----", "sk_live_" + "z" * 16])
    assert found == [(2, "aws_access_key_id"), (4, "private_key_block"), (5, "stripe_live_key")]


def test_credential_scan_one_line_can_hit_several_rules_in_rule_order():
    line = "password=AKIAABCDEFGHIJKLMNOP"
    assert rules([line]) == [(1, "aws_access_key_id"), (1, "generic_secret_assignment")]


def test_credential_scan_two_matches_of_one_rule_on_a_line_report_once():
    result = scan(["AKIAABCDEFGHIJKLMNOP and ASIAABCDEFGHIJKLMNOP"])
    assert result["finding_count"] == 1


def test_credential_scan_never_returns_the_matched_value():
    secrets = ["AKIAABCDEFGHIJKLMNOP", "ghp_" + "Zy9X" * 9, "hunter2hunter2", "sk_live_" + "q" * 16]
    result = scan([f"x = {secrets[0]}", f"t {secrets[1]}", f"password = {secrets[2]}", secrets[3]])
    assert result["finding_count"] == 4
    blob = json.dumps(result)
    assert all(secret not in blob for secret in secrets)
    assert set(result["findings"][0]) == {"rule", "line"}


def test_credential_scan_empty_input_is_clean():
    assert scan([]) == {"state": "SUCCESS", "finding_count": 0, "findings": []}


def test_credential_scan_finding_count_matches_findings():
    result = scan(["AKIAABCDEFGHIJKLMNOP"] * 50)
    assert result["finding_count"] == len(result["findings"]) == 50


def test_credential_scan_input_limits_and_types():
    invalid(call("sec_credential_scan", {"lines": ["a"] * 10001}), "lines")
    invalid(call("sec_credential_scan", {"lines": ["a" * 4097]}), "lines")
    invalid(call("sec_credential_scan", {"lines": [1]}), "lines")
    invalid(call("sec_credential_scan", {"lines": "AKIAABCDEFGHIJKLMNOP"}), "lines")
    invalid(call("sec_credential_scan", {}), "lines")


def test_credential_scan_line_at_the_length_limit_is_accepted():
    line = "# " * 2038 + "AKIAABCDEFGHIJKLMNOP"
    assert len(line) == 4096
    assert rules([line]) == [(1, "aws_access_key_id")]


_PATHOLOGICAL_LINES = {
    "identifier run": "a" * 200_000,
    "repeated secret word": "password" * 25_000,
    "repeated word and underscore": "secret_" * 28_000,
    "dash separated jwt starts": "-eyJaaaaaaaaaaaa" * 12_500,
    "dot separated jwt starts": "eyJaaaaaaaaaaaa." * 12_500,
    "slack starts": "xoxb-" * 40_000,
    "github starts": "ghp_" * 50_000,
    "whitespace after identifier": "password" + " " * 200_000,
    "assignment without value": "password=" * 22_000,
    "private key header starts": "-----BEGIN " * 18_000,
}


@pytest.mark.parametrize("name", sorted(_PATHOLOGICAL_LINES))
def test_credential_scan_is_linear_a_200kb_line_finishes_in_under_a_second(name):
    # A quadratic pattern needs minutes for 200 KB (0.55 s for 4 KB before the rewrite), so this bound cannot flake.
    module = importlib.import_module("swarm.tools.a10_tools")
    started = time.perf_counter()
    module.credential_scan({"lines": [_PATHOLOGICAL_LINES[name]]})
    assert time.perf_counter() - started < 1.0


def test_credential_scan_jwt_after_a_dash_is_still_detected():
    token = "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10
    dashed = "-eyJ-" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10
    assert rules(["x-" + token, dashed, "x" + token]) == [(1, "jwt"), (2, "jwt")]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("my-password=abcdefgh", ["generic_secret_assignment"]),
        ("xpassword = 'abcdefgh'", ["generic_secret_assignment"]),
        ("foo=password=verylongvalue", ["generic_secret_assignment"]),
        ("db_api-key: abcdefgh1", ["generic_secret_assignment"]),
        ("password=short", []),
        ("passwords are great", []),
        ("pass word=abcdefgh", []),
    ],
)
def test_credential_scan_generic_assignment_keeps_its_detection_after_the_linear_rewrite(line, expected):
    assert [rule for _, rule in rules([line])] == expected


# ---- sec_line_entropy ----------------------------------------------------------------------------

def entropy(text) -> dict:
    return call("sec_line_entropy", {"text": text})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aaaa", 0.0),
        ("a", 0.0),
        ("ab", 1.0),
        ("aabb", 1.0),
        ("abcd", 2.0),
        ("aabbccdd", 2.0),
        ("abcdefgh", 3.0),
        ("aaab", 0.8112781244591328),
    ],
)
def test_line_entropy_exact_shannon_values(text, expected):
    result = entropy(text)
    assert result["state"] == "SUCCESS"
    assert result["entropy"] == pytest.approx(expected, abs=1e-12)


def test_line_entropy_is_order_independent_and_case_sensitive():
    assert entropy("abcabc")["entropy"] == entropy("cbaacb")["entropy"]
    assert entropy("aA")["entropy"] == pytest.approx(1.0)
    assert entropy("aa")["entropy"] == pytest.approx(0.0)


def test_line_entropy_counts_whitespace_and_unicode_characters():
    assert entropy("a b ")["entropy"] == pytest.approx(1.5)
    assert entropy("\u00e9\u00e8")["entropy"] == pytest.approx(1.0)
    assert entropy("   ")["entropy"] == pytest.approx(0.0)


def test_line_entropy_matches_the_value_sec_gate_uses():
    sec_gate = importlib.import_module("swarm.tools.support").script("sec_gate")
    for text in ("hunter2hunter2", "AKIAABCDEFGHIJKLMNOP", "ghp_Zy9XZy9XZy9X"):
        assert entropy(text)["entropy"] == sec_gate._entropy(text)


def test_line_entropy_random_looking_secret_clears_the_gate_threshold():
    assert entropy("aB3dE6gH9jK2mN5pQ8sT")["entropy"] > 3.2
    assert entropy("passwordpassword")["entropy"] < 3.2


def test_line_entropy_rejects_empty_and_non_string():
    invalid(entropy(""), "text")
    invalid(entropy(5), "text")
    invalid(call("sec_line_entropy", {}), "text")
    invalid(call("sec_line_entropy", {"text": "a", "extra": 1}), "extra")


def test_line_entropy_refuses_other_callers_and_answers_probes():
    assert call("sec_line_entropy", {"text": "abc"}, caller="A05")["state"] == "UNAUTHORIZED"
    assert call("sec_line_entropy", {"text": "abc", "probe": "partial"})["state"] == "PARTIAL_SUCCESS"


def test_line_entropy_long_uniform_text_is_zero():
    assert entropy("z" * 2000)["entropy"] == pytest.approx(0.0)


def test_line_entropy_counts_each_distinct_character_once():
    class Counting(str):
        calls = 0

        def count(self, *args, **kwargs):
            Counting.calls += 1
            return super().count(*args, **kwargs)

    module = importlib.import_module("swarm.tools.batch_06_10")
    result = module.line_entropy({"text": Counting("abcd" * 500)})  # scripts/sec_gate.py _entropy
    assert result["entropy"] == pytest.approx(2.0)
    assert Counting.calls == 0  # one Counter pass, no per-character str.count


def test_line_entropy_refuses_text_longer_than_the_schema_maximum():
    assert entropy("ab" * 50_000)["entropy"] == pytest.approx(1.0)  # exactly 100000 characters
    invalid(entropy("a" * 100_001), "text")
