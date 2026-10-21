"""Behaviour tests for the v2.1 A14 lane tools that had only generated contract probes.

Covers maint_spec_pin, maint_patch_priority, maint_patch_deadline, maint_semver_bump, maint_eol_distance,
maint_stale_age, maint_debt_priority, maint_license_change, maint_changelog_check, and the output values of
maint_unpinned_count. maint_hotfix_decision and maint_version_affected are covered in tests/test_tools_a14.py.

Every expected value is derived by hand from the handler rules in swarm/tools/a14_tools.py (scores, window
arithmetic, semver precedence, SPDX set comparison) and the A14 prompt, not copied from a run.
"""
import pytest

from swarm.tool_registry import call_tool


def run(name: str, payload: dict, caller: str = "A14") -> dict:
    return call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result


# ---------------------------------------------------------------- maint_spec_pin

@pytest.mark.parametrize(
    "spec, pin",
    [
        ("==1.2.3", "pinned"),
        ("===1.2.3", "pinned"),
        ("1.2.3", "pinned"),
        ("v1.2.3", "pinned"),
        ("=1.2.3", "pinned"),
        ('"1.2.3"', "pinned"),
        ("  ==1.2.3  ", "pinned"),
        ("*", "wildcard"),
        ("latest", "wildcard"),
        ("LATEST", "wildcard"),
        ("x", "wildcard"),
        ("any", "wildcard"),
        (">=0", "wildcard"),
        ("*.*", "wildcard"),
        ("^1.2.3", "range"),
        ("~1.2.3", "range"),
        ("~=1.4", "range"),
        (">=2.0,<3", "range"),
        ("<2", "range"),
        ("1", "range"),
        ("1.x", "range"),
        ("== 1.2.*", "range"),  # PEP 440 allows whitespace after the operator; a wildcard component stays a range
        ("==1.2.*", "range"),
        ("==*", "wildcard"),
        ("==", "wildcard"),
        ("== *", "wildcard"),
        ("== 1.2.3", "pinned"),
        ("=== 1.2.3", "pinned"),
        ("==1", "pinned"),
        ("==1.2.3rc1", "pinned"),
        ("==1.2.3,<2", "range"),  # compound specs are ranges even when they start with an exact pin
        ("==1.2.3 || ^2", "range"),
        ("== 1.2.3 , < 2", "range"),
        ("==abc", "range"),
        ("=1", "range"),
        ("1.2.3,<2", "range"),
    ],
)
def test_spec_pin_classification_table(spec, pin):
    assert run("maint_spec_pin", {"spec": spec}) == {"state": "SUCCESS", "pin": pin}


def test_spec_pin_empty_spec_is_rejected_by_the_schema_but_a_wildcard_for_the_handler():
    invalid(run("maint_spec_pin", {"spec": ""}), "spec")
    from swarm.tools.batch_11_15 import spec_pin

    assert spec_pin({"spec": ""}) == {"state": "SUCCESS", "pin": "wildcard"}


def test_spec_pin_malformed_and_unauthorized():
    invalid(run("maint_spec_pin", {}), "spec")
    invalid(run("maint_spec_pin", {"spec": 123}), "spec")
    invalid(run("maint_spec_pin", {"spec": "1.0.0", "extra": 1}), "extra")
    assert run("maint_spec_pin", {"spec": "1.0.0"}, caller="A10")["state"] == "UNAUTHORIZED"


@pytest.mark.parametrize("spec", ["1.2.x", "1.2.X", "1.2.3.x", "1.2.*", "==1.2.*", "v1.2.x"])
def test_spec_pin_x_range_is_not_an_exact_pin(spec):
    """The module docstring lists `1.x` as a range; `1.2.x` is the same kind of floating spec."""
    assert run("maint_spec_pin", {"spec": spec})["pin"] == "range"


@pytest.mark.parametrize("spec", ["1.2.3-rc.1", "1.2.3+build5", "1.2.3+build.x", "1.2.3-x.1", "1.2.3rc1"])
def test_spec_pin_prerelease_and_build_tails_stay_pinned(spec):
    assert run("maint_spec_pin", {"spec": spec})["pin"] == "pinned"


def test_unpinned_count_counts_x_ranges_as_unpinned():
    result = run("maint_unpinned_count", {"specs": ["1.2.3", "1.2.x", "1.2.X"]})
    assert (result["pinned"], result["unpinned"], result["wildcard"]) == (1, 2, 0)


# ---------------------------------------------------------------- maint_unpinned_count (output values)

def test_unpinned_count_totals_for_the_documented_example():
    result = run("maint_unpinned_count", {"specs": ["==1.2.3", ">=2.0,<3", "*", ""]})
    assert result == {
        "state": "SUCCESS",
        "total": 4,
        "pinned": 1,
        "unpinned": 3,
        "wildcard": 2,
        "unpinned_pct": 75.0,
    }


def test_unpinned_count_percentage_is_rounded_to_one_decimal():
    result = run("maint_unpinned_count", {"specs": ["==1.0.0", "^1.0", "^2.0"]})
    assert (result["unpinned"], result["unpinned_pct"]) == (2, 66.7)
    result = run("maint_unpinned_count", {"specs": ["1.0.0", "2.0.0", "3.0.0"]})
    assert (result["unpinned"], result["unpinned_pct"], result["wildcard"]) == (0, 0.0, 0)


def test_unpinned_count_single_wildcard_is_one_hundred_percent():
    result = run("maint_unpinned_count", {"specs": ["latest"]})
    assert (result["total"], result["pinned"], result["wildcard"], result["unpinned_pct"]) == (1, 0, 1, 100.0)


def test_unpinned_count_empty_list_is_invalid_for_both_schema_and_handler():
    invalid(run("maint_unpinned_count", {"specs": []}), "specs")
    from swarm.tools.a14_tools import unpinned_count

    invalid(unpinned_count({"specs": []}), "specs")


def test_unpinned_count_size_limits_and_types():
    ok = run("maint_unpinned_count", {"specs": ["==1.0.0"] * 500})
    assert (ok["state"], ok["total"], ok["pinned"]) == ("SUCCESS", 500, 500)
    assert run("maint_unpinned_count", {"specs": ["==1.0.0"] * 501})["state"] == "INVALID_INPUT"
    assert run("maint_unpinned_count", {"specs": ["^" + "1" * 200]})["state"] == "INVALID_INPUT"
    assert run("maint_unpinned_count", {"specs": "==1.0.0"})["state"] == "INVALID_INPUT"
    assert run("maint_unpinned_count", {"specs": [1]})["state"] == "INVALID_INPUT"


# ---------------------------------------------------------------- maint_patch_priority

def prio(**kw) -> dict:
    payload = {"cvss": 5, "kev": False, "exploit_maturity": "none", "blast_radius": "isolated", "usage_pct": 0, **kw}
    return run("maint_patch_priority", payload)


def test_patch_priority_documented_example_is_immediate():
    # 9.8*4 + 15 (functional) + 5 (service) + 60*0.15 + 10 (KEV) = 78.2 -> 78
    result = prio(cvss=9.8, kev=True, exploit_maturity="functional", blast_radius="service", usage_pct=60)
    assert result == {"state": "SUCCESS", "score": 78, "tier": "immediate", "immediate": True}


@pytest.mark.parametrize(
    "kwargs, score",
    [
        ({"cvss": 0}, 0),
        ({"cvss": 2.5}, 10),
        ({"cvss": 5}, 20),
        ({"cvss": 0, "exploit_maturity": "poc"}, 10),
        ({"cvss": 0, "exploit_maturity": "functional"}, 15),
        ({"cvss": 0, "exploit_maturity": "weaponized"}, 20),
        ({"cvss": 0, "blast_radius": "service"}, 5),
        ({"cvss": 0, "blast_radius": "platform"}, 10),
        ({"cvss": 0, "blast_radius": "internet-facing"}, 15),
        ({"cvss": 0, "usage_pct": 40}, 6),
        ({"cvss": 0, "usage_pct": 100}, 15),
    ],
)
def test_patch_priority_each_term_contributes_its_documented_points(kwargs, score):
    assert prio(**kwargs)["score"] == score


def test_patch_priority_tier_boundaries_at_40_and_70():
    # 7.25*4 + 10 = 39 -> low; 7.5*4 + 10 = 40 -> normal
    assert (prio(cvss=7.25, exploit_maturity="poc")["score"], prio(cvss=7.25, exploit_maturity="poc")["tier"]) == (39, "low")
    assert (prio(cvss=7.5, exploit_maturity="poc")["score"], prio(cvss=7.5, exploit_maturity="poc")["tier"]) == (40, "normal")
    # 8*4 + 20 + 15 = 67; usage 14 adds 2.1 -> 69 (normal); usage 20 adds 3 -> 70 (high)
    base = {"cvss": 8, "exploit_maturity": "weaponized", "blast_radius": "internet-facing"}
    assert (prio(**base, usage_pct=14)["score"], prio(**base, usage_pct=14)["tier"]) == (69, "normal")
    assert (prio(**base, usage_pct=20)["score"], prio(**base, usage_pct=20)["tier"]) == (70, "high")


def test_patch_priority_kev_forces_immediate_even_with_a_tiny_score():
    result = prio(cvss=0, kev=True)
    assert result == {"state": "SUCCESS", "score": 10, "tier": "immediate", "immediate": True}


def test_patch_priority_cvss_nine_is_immediate_but_just_below_is_not():
    assert prio(cvss=9.0) == {"state": "SUCCESS", "score": 36, "tier": "immediate", "immediate": True}
    assert prio(cvss=8.9) == {"state": "SUCCESS", "score": 36, "tier": "low", "immediate": False}


def test_patch_priority_maximum_inputs_stay_at_one_hundred():
    result = prio(cvss=10, kev=True, exploit_maturity="weaponized", blast_radius="internet-facing", usage_pct=100)
    assert result == {"state": "SUCCESS", "score": 100, "tier": "immediate", "immediate": True}


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"cvss": 10.1}, "cvss"),
        ({"cvss": -0.1}, "cvss"),
        ({"cvss": "9.8"}, "cvss"),
        ({"cvss": True}, "cvss"),
        ({"kev": "yes"}, "kev"),
        ({"exploit_maturity": "high"}, "exploit_maturity"),
        ({"blast_radius": "global"}, "blast_radius"),
        ({"usage_pct": 101}, "usage_pct"),
        ({"usage_pct": -1}, "usage_pct"),
        ({"usage_pct": 50.5}, "usage_pct"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_patch_priority_malformed_input_names_the_field(bad, field):
    invalid(prio(**bad), field)


def test_patch_priority_missing_fields_and_caller():
    invalid(run("maint_patch_priority", {}), "cvss")
    invalid(run("maint_patch_priority", {"cvss": 5, "kev": False, "exploit_maturity": "none", "blast_radius": "isolated"}), "usage_pct")
    ok = {"cvss": 5, "kev": False, "exploit_maturity": "none", "blast_radius": "isolated", "usage_pct": 0}
    assert run("maint_patch_priority", ok, caller="A05")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_patch_deadline

def deadline(**kw) -> dict:
    payload = {"detected_at": "2026-10-15T09:00:00Z", "kev": False, "severity": "high", **kw}
    return run("maint_patch_deadline", payload)


def test_patch_deadline_sla_window_is_added_to_detection_time():
    assert deadline(sla_hours=168) == {
        "state": "SUCCESS",
        "deadline": "2026-10-22T09:00:00Z",
        "window_hours": 168,
        "basis": "sla",
    }


def test_patch_deadline_kev_gets_24_hours_regardless_of_severity_and_sla():
    result = deadline(kev=True, severity="low", sla_hours=720)
    assert result == {"state": "SUCCESS", "deadline": "2026-10-16T09:00:00Z", "window_hours": 24, "basis": "kev"}
    assert deadline(kev=True)["window_hours"] == 24  # sla_hours not required for KEV


def test_patch_deadline_critical_without_kev_gets_24_hours_with_critical_basis():
    result = deadline(severity="critical", sla_hours=720)
    assert (result["deadline"], result["window_hours"], result["basis"]) == ("2026-10-16T09:00:00Z", 24, "critical")
    assert deadline(severity="critical")["basis"] == "critical"


def test_patch_deadline_kev_and_critical_reports_kev_basis():
    assert deadline(kev=True, severity="critical")["basis"] == "kev"


def test_patch_deadline_rolls_over_day_year_and_leap_day():
    assert deadline(detected_at="2026-12-31T23:00:00Z", sla_hours=2)["deadline"] == "2027-01-01T01:00:00Z"
    assert deadline(detected_at="2028-02-28T12:00:00Z", kev=True)["deadline"] == "2028-02-29T12:00:00Z"
    assert deadline(detected_at="2027-02-28T12:00:00Z", kev=True)["deadline"] == "2027-03-01T12:00:00Z"


def test_patch_deadline_sla_bounds():
    assert deadline(sla_hours=1)["deadline"] == "2026-10-15T10:00:00Z"
    assert deadline(sla_hours=8760)["deadline"] == "2027-10-15T09:00:00Z"
    invalid(deadline(sla_hours=0), "sla_hours")
    invalid(deadline(sla_hours=8761), "sla_hours")
    invalid(deadline(sla_hours=1.5), "sla_hours")


def test_patch_deadline_missing_sla_names_the_field_unless_urgent():
    result = deadline(severity="medium")
    invalid(result, "sla_hours")
    assert "KEV or critical" in result["message"]


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-15 09:00:00Z",  # wrong separator, right length
        "2026-02-30T09:00:00Z",  # impossible day
        "2026-10-15T25:00:00Z",  # impossible hour
        "2026-10-15T09:60:00Z",  # impossible minute
        "2026-10-15T09:00:00+0",  # wrong zone marker, right length
        "2026-10-15T09:00:00",  # 19 chars: caught by the schema
        "2026-10-15T09:00:00.5Z"[:20],  # fractional seconds, right length
        "",
    ],
)
def test_patch_deadline_malformed_timestamp_names_detected_at(stamp):
    invalid(deadline(detected_at=stamp, sla_hours=24), "detected_at")


def test_patch_deadline_bad_timestamp_is_rejected_even_for_kev():
    invalid(deadline(detected_at="2026-13-15T09:00:00Z", kev=True), "detected_at")


def test_patch_deadline_malformed_other_fields_and_caller():
    invalid(deadline(severity="urgent", sla_hours=8), "severity")
    invalid(deadline(kev=1, sla_hours=8), "kev")
    invalid(run("maint_patch_deadline", {}), "detected_at")
    assert deadline(sla_hours=8)["state"] == "SUCCESS"
    assert run("maint_patch_deadline", {"detected_at": "2026-10-15T09:00:00Z", "kev": True, "severity": "low"}, caller="A01")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_semver_bump

def bump(old: str, new: str) -> dict:
    return run("maint_semver_bump", {"from": old, "to": new})


@pytest.mark.parametrize(
    "old, new, kind, breaking, autonomy",
    [
        ("1.4.2", "1.5.0", "minor", False, "L2"),
        ("1.4.2", "2.0.0", "major", True, "L3"),
        ("1.4.2", "1.4.3", "patch", False, "L2"),
        ("0.3.1", "0.4.0", "minor", True, "L3"),  # 0.x minor is breaking
        ("0.3.1", "0.3.2", "patch", False, "L2"),
        ("0.9.0", "1.0.0", "major", True, "L3"),
        ("1.9.0", "1.10.0", "minor", False, "L2"),  # numeric, not lexical
        ("1.4.2", "1.4.2", "none", False, "none"),
        ("1.2.0", "1.1.9", "downgrade", False, "none"),
        ("2.0.0", "1.99.99", "downgrade", False, "none"),
        ("1.0.0", "0.9.9", "downgrade", False, "none"),
    ],
)
def test_semver_bump_kinds_breaking_and_autonomy(old, new, kind, breaking, autonomy):
    assert bump(old, new) == {"state": "SUCCESS", "kind": kind, "breaking": breaking, "autonomy": autonomy}


@pytest.mark.parametrize(
    "old, new, kind",
    [
        ("2.0.0-rc.1", "2.0.0", "prerelease"),  # a release outranks its own prerelease
        ("2.0.0", "2.0.0-rc.1", "downgrade"),
        ("1.0.0-alpha.2", "1.0.0-alpha.10", "prerelease"),  # numeric identifiers compare as numbers
        ("1.0.0-alpha.10", "1.0.0-alpha.2", "downgrade"),
        ("1.0.0-alpha", "1.0.0-alpha.1", "prerelease"),  # a longer identifier list outranks its prefix
        ("1.0.0-alpha.1", "1.0.0-beta", "prerelease"),  # alphanumeric identifiers compare lexically
        ("1.0.0-1", "1.0.0-alpha", "prerelease"),  # numeric identifiers rank below alphanumeric
        ("1.0.0-alpha", "1.0.0-1", "downgrade"),
        ("1.0.0-rc.1", "1.0.0-rc.1", "none"),
    ],
)
def test_semver_bump_prerelease_precedence(old, new, kind):
    result = bump(old, new)
    assert result["kind"] == kind and result["breaking"] is False


def test_semver_bump_crossing_a_major_from_a_prerelease_is_still_major():
    assert bump("1.9.9", "2.0.0-rc.1") == {"state": "SUCCESS", "kind": "major", "breaking": True, "autonomy": "L3"}
    assert bump("1.0.0-rc.1", "2.0.0")["kind"] == "major"


def test_semver_bump_build_metadata_is_ignored():
    assert bump("1.0.0+build.1", "1.0.0+build.2") == {"state": "SUCCESS", "kind": "none", "breaking": False, "autonomy": "none"}
    assert bump("1.0.0+a", "1.0.1+b")["kind"] == "patch"


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "01.2.3", "1.2.3.4", "1.2.3-", "latest", " 1.2.3", "1.2.3 ", "1.2.-3"])
def test_semver_bump_rejects_malformed_from_and_to(bad):
    invalid(bump(bad, "1.0.0"), "from")
    invalid(bump("1.0.0", bad), "to")


def test_semver_bump_checks_from_before_to_and_bounds_length():
    invalid(bump("nope", "also-nope"), "from")
    invalid(run("maint_semver_bump", {"from": "", "to": "1.0.0"}), "from")
    invalid(run("maint_semver_bump", {"from": "1.0.0", "to": "1." * 65 + "0"}), "to")
    invalid(run("maint_semver_bump", {"from": "1.0.0"}), "to")
    assert run("maint_semver_bump", {"from": "1.0.0", "to": "1.0.1"}, caller="A10")["state"] == "UNAUTHORIZED"


@pytest.mark.parametrize("bad", ["1.0.0-rc..1", "1.0.0-.rc", "1.0.0-rc.", "1.0.0-01", "1.0.0-rc.01", "1.0.0+", "1.0.0+a..b", "1.0.0-rc_1"])
def test_semver_bump_rejects_empty_and_leading_zero_prerelease_identifiers(bad):
    """Semver 2.0.0 sections 9-10: identifiers MUST NOT be empty, numeric prerelease identifiers MUST NOT have leading zeros."""
    invalid(bump(bad, "1.0.0"), "from")
    invalid(bump("1.0.0", bad), "to")


# ---------------------------------------------------------------- maint_eol_distance

def eol(today: str, eol_date: str, in_production: bool = True) -> dict:
    return run("maint_eol_distance", {"today": today, "eol_date": eol_date, "in_production": in_production})


def test_eol_distance_documented_example_is_within_90_days():
    assert eol("2026-10-15", "2026-12-31") == {
        "state": "SUCCESS",
        "days_to_eol": 77,
        "status": "within_90_days",
        "upgrade_epic": True,
        "compliance_finding": False,
    }


def test_eol_distance_ninety_days_is_inside_and_ninety_one_is_supported():
    inside = eol("2026-10-15", "2027-01-13")
    assert (inside["days_to_eol"], inside["status"], inside["upgrade_epic"]) == (90, "within_90_days", True)
    outside = eol("2026-10-15", "2027-01-14")
    assert (outside["days_to_eol"], outside["status"], outside["upgrade_epic"]) == (91, "supported", False)
    assert outside["compliance_finding"] is False


def test_eol_distance_on_the_eol_day_is_not_yet_passed():
    result = eol("2026-10-15", "2026-10-15")
    assert result == {
        "state": "SUCCESS",
        "days_to_eol": 0,
        "status": "within_90_days",
        "upgrade_epic": True,
        "compliance_finding": False,
    }


def test_eol_distance_passed_is_a_compliance_finding_only_in_production():
    prod = eol("2026-10-15", "2026-10-14", in_production=True)
    assert prod == {
        "state": "SUCCESS",
        "days_to_eol": -1,
        "status": "passed",
        "upgrade_epic": True,
        "compliance_finding": True,
    }
    assert eol("2026-10-15", "2026-10-14", in_production=False)["compliance_finding"] is False
    assert eol("2026-10-15", "2025-10-15")["days_to_eol"] == -365


def test_eol_distance_counts_leap_days_across_years():
    assert eol("2027-12-31", "2028-03-01")["days_to_eol"] == 61  # 31 (Jan) + 29 (leap Feb) + 1
    assert eol("2026-12-31", "2027-03-01")["days_to_eol"] == 60


@pytest.mark.parametrize(
    "today, eol_date, field",
    [
        ("2026-02-30", "2027-01-01", "today"),
        ("2026/10/15", "2027-01-01", "today"),
        ("2026-1-05 ", "2027-01-01", "today"),
        ("2026-10-15", "2027-02-29", "eol_date"),
        ("2026-10-15", "20270101xx", "eol_date"),
        ("2026-10-15", "not a date", "eol_date"),
        ("2026-13-01", "2027-13-01", "today"),  # today is checked first
    ],
)
def test_eol_distance_malformed_dates_name_the_field(today, eol_date, field):
    invalid(eol(today, eol_date), field)


def test_eol_distance_accepts_a_leap_day_only_in_leap_years():
    assert eol("2028-02-29", "2028-02-29")["days_to_eol"] == 0
    invalid(eol("2027-02-29", "2028-02-29"), "today")


def test_eol_distance_missing_wrongly_typed_and_unauthorized():
    invalid(run("maint_eol_distance", {}), "today")
    invalid(run("maint_eol_distance", {"today": "2026-10-15", "eol_date": "2026-12-31"}), "in_production")
    invalid(run("maint_eol_distance", {"today": "2026-10-15", "eol_date": "2026-12-31", "in_production": "true"}), "in_production")
    ok = {"today": "2026-10-15", "eol_date": "2026-12-31", "in_production": True}
    assert run("maint_eol_distance", ok, caller="A12")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_stale_age

def stale(installed: str, latest: str, threshold: int) -> dict:
    return run(
        "maint_stale_age",
        {"installed_released": installed, "latest_released": latest, "threshold_days": threshold},
    )


def test_stale_age_documented_example():
    # Jan 10 is day 10 of 2026, Oct 1 is day 274, so 264 days.
    assert stale("2026-01-10", "2026-10-01", 180) == {
        "state": "SUCCESS",
        "age_days": 264,
        "stale": True,
        "days_over_threshold": 84,
    }


def test_stale_age_threshold_boundary_is_strictly_greater():
    at = stale("2026-01-01", "2026-01-11", 10)
    assert at == {"state": "SUCCESS", "age_days": 10, "stale": False, "days_over_threshold": 0}
    over = stale("2026-01-01", "2026-01-12", 10)
    assert over == {"state": "SUCCESS", "age_days": 11, "stale": True, "days_over_threshold": 1}
    under = stale("2026-01-01", "2026-01-05", 10)
    assert under == {"state": "SUCCESS", "age_days": 4, "stale": False, "days_over_threshold": 0}


def test_stale_age_same_day_is_zero_and_not_stale():
    assert stale("2026-05-05", "2026-05-05", 1) == {"state": "SUCCESS", "age_days": 0, "stale": False, "days_over_threshold": 0}


def test_stale_age_counts_leap_days():
    assert stale("2028-02-28", "2028-03-01", 365)["age_days"] == 2
    assert stale("2027-02-28", "2027-03-01", 365)["age_days"] == 1
    assert stale("2026-01-01", "2027-01-01", 3650)["age_days"] == 365
    assert stale("2027-01-01", "2028-01-01", 3650)["age_days"] == 365
    assert stale("2028-01-01", "2029-01-01", 3650)["age_days"] == 366


def test_stale_age_latest_before_installed_names_latest_released():
    result = stale("2026-10-01", "2026-09-30", 30)
    invalid(result, "latest_released")
    assert "must not precede" in result["message"]


@pytest.mark.parametrize(
    "installed, latest, field",
    [
        ("2026-02-30", "2026-10-01", "installed_released"),
        ("2026/01/10", "2026-10-01", "installed_released"),
        ("2026-01-10", "2026-10-32", "latest_released"),
        ("2026-01-10", "latest....", "latest_released"),
        ("bad-date!", "also-bad!!", "installed_released"),  # installed is checked first
    ],
)
def test_stale_age_malformed_dates_name_the_field(installed, latest, field):
    invalid(stale(installed, latest, 30), field)


def test_stale_age_threshold_bounds_and_missing_fields():
    assert stale("2026-01-01", "2026-01-02", 1)["state"] == "SUCCESS"
    assert stale("2026-01-01", "2026-01-02", 3650)["state"] == "SUCCESS"
    invalid(stale("2026-01-01", "2026-01-02", 0), "threshold_days")
    invalid(stale("2026-01-01", "2026-01-02", 3651), "threshold_days")
    invalid(run("maint_stale_age", {}), "installed_released")
    invalid(run("maint_stale_age", {"installed_released": "2026-01-01", "latest_released": "2026-01-02"}), "threshold_days")
    ok = {"installed_released": "2026-01-01", "latest_released": "2026-01-02", "threshold_days": 5}
    assert run("maint_stale_age", ok, caller="A05")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_debt_priority

def debt(impact: int, recurrence: int, effort_h: float) -> dict:
    return run("maint_debt_priority", {"impact": impact, "recurrence": recurrence, "effort_h": effort_h})


def test_debt_priority_documented_example():
    # 100 * (16/25) * 8/(8+16) = 21.3
    assert debt(4, 4, 16) == {"state": "SUCCESS", "score": 21, "proposed_iteration": "I+3"}


def test_debt_priority_maximum_benefit_and_negligible_effort_is_one_hundred():
    assert debt(5, 5, 0.001) == {"state": "SUCCESS", "score": 100, "proposed_iteration": "I+1"}
    # effort 8 halves the score: 100 * 1 * 8/16
    assert debt(5, 5, 8) == {"state": "SUCCESS", "score": 50, "proposed_iteration": "I+2"}


def test_debt_priority_iteration_boundaries_at_70_and_40():
    assert debt(5, 5, 3.5) == {"state": "SUCCESS", "score": 70, "proposed_iteration": "I+1"}  # 69.57 rounds up
    assert debt(5, 5, 3.6) == {"state": "SUCCESS", "score": 69, "proposed_iteration": "I+2"}
    assert debt(5, 5, 12) == {"state": "SUCCESS", "score": 40, "proposed_iteration": "I+2"}
    assert debt(5, 5, 12.5) == {"state": "SUCCESS", "score": 39, "proposed_iteration": "I+3"}


def test_debt_priority_is_symmetric_in_impact_and_recurrence():
    assert debt(5, 2, 8)["score"] == debt(2, 5, 8)["score"] == 20  # 100 * 10/25 * 0.5
    assert debt(3, 4, 8)["score"] == 24  # 100 * 12/25 * 0.5


def test_debt_priority_floor_is_zero_and_long_effort_lowers_the_score():
    assert debt(1, 1, 400) == {"state": "SUCCESS", "score": 0, "proposed_iteration": "I+3"}
    assert debt(5, 5, 400)["score"] == 2  # 100 * 8/408
    assert debt(1, 1, 1)["score"] == 4  # 100 * (1/25) * 8/9 = 3.56


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"impact": 0}, "impact"),
        ({"impact": 6}, "impact"),
        ({"impact": 2.5}, "impact"),
        ({"impact": "3"}, "impact"),
        ({"recurrence": 0}, "recurrence"),
        ({"recurrence": 6}, "recurrence"),
        ({"effort_h": 0}, "effort_h"),
        ({"effort_h": -1}, "effort_h"),
        ({"effort_h": 400.5}, "effort_h"),
        ({"effort_h": "8"}, "effort_h"),
    ],
)
def test_debt_priority_malformed_input_names_the_field(bad, field):
    payload = {"impact": 3, "recurrence": 3, "effort_h": 8, **bad}
    invalid(run("maint_debt_priority", payload), field)


def test_debt_priority_missing_fields_and_unauthorized():
    invalid(run("maint_debt_priority", {}), "impact")
    invalid(run("maint_debt_priority", {"impact": 3, "recurrence": 3}), "effort_h")
    assert run("maint_debt_priority", {"impact": 3, "recurrence": 3, "effort_h": 8}, caller="A09")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_license_change

def lic(previous: str, current: str, allowlist: list[str]) -> dict:
    return run(
        "maint_license_change",
        {"previous_license": previous, "current_license": current, "allowlist": allowlist},
    )


def flags(result: dict) -> tuple:
    assert result["state"] == "SUCCESS", result
    return (result["changed"], result["allowed"], result["copyleft_introduced"], result["requires_review"])


def test_license_unchanged_and_allowlisted_needs_no_review():
    assert lic("MIT", "MIT", ["MIT"]) == {
        "state": "SUCCESS",
        "changed": False,
        "allowed": True,
        "copyleft_introduced": False,
        "requires_review": False,
    }


def test_license_unchanged_but_not_allowlisted_still_requires_review():
    assert flags(lic("BSD-3-Clause", "BSD-3-Clause", ["MIT"])) == (False, False, False, True)


def test_license_change_to_an_allowlisted_license_still_requires_review():
    assert flags(lic("MIT", "Apache-2.0", ["MIT", "Apache-2.0"])) == (True, True, False, True)


def test_license_change_to_copyleft_introduces_copyleft_and_is_not_allowed():
    assert lic("MIT", "GPL-3.0-only", ["MIT"]) == {
        "state": "SUCCESS",
        "changed": True,
        "allowed": False,
        "copyleft_introduced": True,
        "requires_review": True,
    }


@pytest.mark.parametrize("copyleft", ["GPL-2.0", "AGPL-3.0", "LGPL-3.0+", "MPL-2.0", "EPL-2.0", "CDDL-1.0", "EUPL-1.2"])
def test_license_copyleft_families_are_recognised(copyleft):
    assert lic("MIT", copyleft, ["MIT"])["copyleft_introduced"] is True


def test_license_copyleft_already_present_is_not_newly_introduced():
    assert flags(lic("GPL-2.0-only", "GPL-3.0-only", ["GPL-3.0-only"])) == (True, True, False, True)
    # one copyleft clause remaining in an OR expression counts as already present
    assert lic("MIT OR GPL-2.0-only", "GPL-2.0-only", ["GPL-2.0-only"])["copyleft_introduced"] is False


def test_license_matching_is_case_insensitive_and_allowlist_is_trimmed():
    assert flags(lic("mit", "MIT", ["  Mit "])) == (False, True, False, False)


def test_license_expression_order_and_operator_case_do_not_matter():
    assert flags(lic("MIT OR Apache-2.0", "Apache-2.0 OR MIT", ["MIT", "Apache-2.0"])) == (False, True, False, False)
    assert flags(lic("(MIT AND Apache-2.0)", "mit and apache-2.0", ["MIT", "Apache-2.0"])) == (False, True, False, False)


def test_license_narrowing_an_or_expression_counts_as_a_change():
    assert flags(lic("MIT OR Apache-2.0", "MIT", ["MIT", "Apache-2.0"])) == (True, True, False, True)


def test_license_with_exception_keeps_only_the_base_identifier():
    assert flags(lic("Apache-2.0 WITH LLVM-exception", "Apache-2.0", ["Apache-2.0"])) == (False, True, False, False)
    # the exception name is not itself required on the allowlist
    assert lic("GPL-2.0-only WITH Classpath-exception-2.0", "GPL-2.0-only WITH Classpath-exception-2.0", ["GPL-2.0-only"])["allowed"] is True


def test_license_every_identifier_of_a_compound_must_be_allowlisted():
    assert lic("MIT", "MIT AND ISC", ["MIT"])["allowed"] is False
    assert lic("MIT", "MIT AND ISC", ["MIT", "ISC"])["allowed"] is True


def test_license_blank_allowlist_entries_allow_nothing():
    assert lic("MIT", "MIT", ["   "])["allowed"] is False
    assert lic("MIT", "MIT", ["   ", "MIT"])["allowed"] is True


def test_license_without_an_identifier_names_the_field():
    invalid(lic("()", "MIT", ["MIT"]), "previous_license")
    invalid(lic("MIT", "()", ["MIT"]), "current_license")
    invalid(lic("AND", "MIT", ["MIT"]), "previous_license")
    invalid(lic("OR", "OR", ["MIT"]), "previous_license")  # previous is checked first
    invalid(lic("   ", "MIT", ["MIT"]), "previous_license")


def test_license_size_limits_and_malformed_input():
    assert lic("MIT", "MIT", ["MIT"] * 200)["state"] == "SUCCESS"
    invalid(lic("MIT", "MIT", ["MIT"] * 201), "allowlist")
    invalid(lic("MIT", "MIT", []), "allowlist")
    invalid(lic("MIT", "MIT", [""]), "allowlist")
    invalid(lic("MIT", "MIT", "MIT"), "allowlist")
    invalid(lic("M" * 501, "MIT", ["MIT"]), "previous_license")
    assert lic("M" * 500, "MIT", ["MIT"])["state"] == "SUCCESS"
    invalid(lic("", "MIT", ["MIT"]), "previous_license")
    invalid(run("maint_license_change", {"previous_license": "MIT", "current_license": "MIT"}), "allowlist")
    assert run("maint_license_change", {"previous_license": "MIT", "current_license": "MIT", "allowlist": ["MIT"]}, caller="A12")["state"] == "UNAUTHORIZED"


# ---------------------------------------------------------------- maint_changelog_check

def changelog(entry: str, version: str) -> dict:
    return run("maint_changelog_check", {"entry": entry, "version": version})


VALID = "## [1.2.0] - 2026-10-15\n### Added\n- New thing\n### Fixed\n- A bug\n"


def test_changelog_valid_release_entry():
    assert changelog(VALID, "1.2.0") == {"state": "SUCCESS", "valid": True, "problem_count": 0, "first_problem": ""}


def test_changelog_valid_unreleased_entry_and_multiline_bullet():
    entry = "## [Unreleased]\n### Changed\n- Reworded the parser\n  across two lines\n"
    assert changelog(entry, "Unreleased")["valid"] is True


def test_changelog_leading_blank_lines_crlf_and_trailing_space_are_tolerated():
    assert changelog("\n\n" + VALID.replace("\n", "\r\n"), "1.2.0")["valid"] is True
    assert changelog("## [1.2.0] - 2026-10-15   \n### Added   \n- x   \n", "1.2.0")["valid"] is True


def test_changelog_blank_only_entry_is_empty():
    assert changelog("\n\n  \n", "1.2.0") == {
        "state": "SUCCESS",
        "valid": False,
        "problem_count": 1,
        "first_problem": "entry is empty",
    }


def test_changelog_heading_version_mismatch():
    result = changelog(VALID, "1.3.0")
    assert result["valid"] is False and result["problem_count"] == 1
    assert result["first_problem"] == "heading version '1.2.0' does not match '1.3.0'"
    assert changelog("## [Unreleased]\n### Added\n- x\n", "1.0.0")["first_problem"] == "heading version 'Unreleased' does not match '1.0.0'"
    assert changelog(VALID, "Unreleased")["first_problem"] == "heading version '1.2.0' does not match 'Unreleased'"


def test_changelog_release_heading_needs_a_date():
    result = changelog("## [1.2.0]\n### Added\n- x\n", "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 1, "release heading is missing its date")


def test_changelog_impossible_date_is_reported_with_the_text():
    result = changelog("## [1.2.0] - 2026-02-30\n### Added\n- x\n", "1.2.0")
    assert result["first_problem"] == "release date '2026-02-30' is not a valid YYYY-MM-DD date"
    result = changelog("## [1.2.0] - 15-10-2026\n### Added\n- x\n", "1.2.0")
    assert result["first_problem"] == "release date '15-10-2026' is not a valid YYYY-MM-DD date"


def test_changelog_unreleased_must_not_carry_a_date():
    result = changelog("## [Unreleased] - 2026-10-15\n### Added\n- x\n", "Unreleased")
    assert (result["valid"], result["first_problem"]) == (False, "Unreleased heading must not carry a date")


@pytest.mark.parametrize(
    "heading",
    ["## 1.2.0 - 2026-10-15", "# [1.2.0] - 2026-10-15", "## [1.2.0] 2026-10-15", "## [1.2.0] - 2026-10-15 extra", "### [1.2.0] - 2026-10-15", "[1.2.0]"],
)
def test_changelog_malformed_heading_is_reported(heading):
    result = changelog(f"{heading}\n### Added\n- x\n", "1.2.0")
    assert result["valid"] is False
    assert result["first_problem"] == "first line must be '## [version] - YYYY-MM-DD' or '## [Unreleased]'"


def test_changelog_unknown_category():
    result = changelog("## [1.2.0] - 2026-10-15\n### Misc\n- x\n", "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 1, "unknown category 'Misc'")


@pytest.mark.parametrize("category", ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"])
def test_changelog_each_keep_a_changelog_category_is_accepted(category):
    assert changelog(f"## [1.2.0] - 2026-10-15\n### {category}\n- x\n", "1.2.0")["valid"] is True


def test_changelog_category_names_are_case_sensitive():
    assert changelog("## [1.2.0] - 2026-10-15\n### added\n- x\n", "1.2.0")["first_problem"] == "unknown category 'added'"


def test_changelog_entry_before_any_category():
    result = changelog("## [1.2.0] - 2026-10-15\n- orphan\n### Added\n- x\n", "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 1, "entry appears before any category heading")


def test_changelog_empty_bullet():
    for bullet in ("-", "- ", "-   "):
        result = changelog(f"## [1.2.0] - 2026-10-15\n### Added\n{bullet}\n", "1.2.0")
        assert (result["valid"], result["first_problem"]) == (False, "empty entry"), bullet


def test_changelog_heading_only_has_no_category_and_no_entries():
    result = changelog("## [1.2.0] - 2026-10-15\n", "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 2, "no category heading")


def test_changelog_category_without_entries():
    result = changelog("## [1.2.0] - 2026-10-15\n### Added\n", "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 1, "no entries")


def test_changelog_second_release_heading_is_rejected():
    entry = VALID + "## [1.1.0] - 2026-09-01\n### Added\n- old\n"
    result = changelog(entry, "1.2.0")
    assert (result["valid"], result["problem_count"], result["first_problem"]) == (False, 1, "unexpected '## ' heading after the release heading")


@pytest.mark.parametrize("line", ["plain prose", "* star bullet", "#### Deep", "###Added", "\tTabbed"])
def test_changelog_unrecognized_lines_are_reported_with_the_text(line):
    result = changelog(f"## [1.2.0] - 2026-10-15\n### Added\n- x\n{line}\n", "1.2.0")
    assert result["valid"] is False
    assert result["first_problem"] == f"unrecognized line {line.rstrip()[:40]!r}"


def test_changelog_indented_continuation_is_only_valid_after_a_bullet():
    result = changelog("## [1.2.0] - 2026-10-15\n### Added\n  stray\n- x\n", "1.2.0")
    assert (result["valid"], result["first_problem"]) == (False, "unrecognized line '  stray'")


def test_changelog_long_unrecognized_line_is_truncated_to_forty_characters():
    result = changelog("## [1.2.0] - 2026-10-15\n### Added\n- x\n" + "z" * 100 + "\n", "1.2.0")
    assert result["first_problem"] == f"unrecognized line {'z' * 40!r}"


def test_changelog_problems_accumulate_in_document_order_and_first_problem_is_the_earliest():
    entry = "## [1.0.0]\n- orphan\n### Misc\n- x\nprose\n"
    result = changelog(entry, "2.0.0")
    # mismatch, missing date, orphan entry, unknown category, unrecognized line
    assert result["problem_count"] == 5
    assert result["first_problem"] == "heading version '1.0.0' does not match '2.0.0'"


def test_changelog_size_limits_and_malformed_input():
    invalid(run("maint_changelog_check", {"entry": "", "version": "1.2.0"}), "entry")
    invalid(run("maint_changelog_check", {"entry": VALID, "version": ""}), "version")
    invalid(run("maint_changelog_check", {"entry": "x" * 20001, "version": "1.2.0"}), "entry")
    invalid(run("maint_changelog_check", {"entry": VALID, "version": "v" * 65}), "version")
    invalid(run("maint_changelog_check", {"entry": 5, "version": "1.2.0"}), "entry")
    invalid(run("maint_changelog_check", {"entry": VALID}), "version")
    invalid(run("maint_changelog_check", {}), "entry")
    big = "## [1.2.0] - 2026-10-15\n### Added\n" + "- item\n" * 2500
    assert 17000 < len(big) <= 20000 and changelog(big, "1.2.0")["valid"] is True
    assert run("maint_changelog_check", {"entry": VALID, "version": "1.2.0"}, caller="A15")["state"] == "UNAUTHORIZED"
