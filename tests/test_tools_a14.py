"""Behaviour checks for the v2.2 A14 tools: maint_hotfix_decision and maint_version_affected."""
import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tool_registry import call_tool

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a14-maintenance.tools.json"


def _doc() -> dict:
    """The live registry plus the A14 fragment entries that the merge has not applied yet."""
    data = copy.deepcopy(tool_registry.load_document())
    agent = next(a for a in data["agents"] if a["id"] == "A14")
    present = {tool["name"] for tool in agent["registered_tools"]}
    if FRAGMENT.exists():
        for entry in json.loads(FRAGMENT.read_text(encoding="utf-8")):
            if entry["name"] not in present:
                agent["registered_tools"].append(entry)
    return data


@pytest.fixture(scope="module")
def doc() -> dict:
    return _doc()


def hotfix(doc, **kw):
    payload = {"severity": 2, "fix_est_h": 1, "rollback_available": False, "rollback_risk": "high", **kw}
    return call_tool("maint_hotfix_decision", "A14", payload, data=doc)


def affected(doc, version, rng, caller="A14"):
    return call_tool("maint_version_affected", caller, {"version": version, "affected": rng}, data=doc)


def test_fragment_entries_validate_in_a_registry_copy(tmp_path):
    copy_doc = _doc()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(copy_doc), encoding="utf-8")
    assert tool_registry.validate_registry(path) == []
    names = {t["name"] for t in next(a for a in copy_doc["agents"] if a["id"] == "A14")["registered_tools"]}
    assert {"maint_hotfix_decision", "maint_version_affected"} <= names
    assert len(names) == 12


# ---- maint_hotfix_decision ----

@pytest.mark.parametrize(
    "severity, expected",
    [(1, ("hotfix", True)), (2, ("hotfix", True)), (3, ("defer", False)), (5, ("defer", False))],
)
def test_severity_threshold_primes_the_hotfix_path(doc, severity, expected):
    """Reviewer repro (RV-E 1): severity 1 (A13's worst) returned defer/primes_hotfix false, severity 3 primed."""
    result = hotfix(doc, severity=severity)
    assert result["state"] == "SUCCESS"
    assert (result["path"], result["primes_hotfix"]) == expected


def test_sev_one_takes_the_rollback_when_a_low_risk_one_exists_and_sev_three_defers(doc):
    """Reviewer repro (RV-E 1): {severity:1, fix_est_h:1, rollback_available:true, rollback_risk:'low'} -> defer."""
    worst = hotfix(doc, severity=1, fix_est_h=1, rollback_available=True, rollback_risk="low")
    assert worst["path"] == "rollback" and worst["primes_hotfix"] is True
    ticket = hotfix(doc, severity=3, fix_est_h=1, rollback_available=True, rollback_risk="low")
    assert ticket["path"] == "defer" and ticket["primes_hotfix"] is False


def test_fix_boundary_is_strictly_below_four_hours(doc):
    assert hotfix(doc, fix_est_h=3.9)["path"] == "hotfix"
    assert hotfix(doc, fix_est_h=4.0)["path"] == "defer"
    assert hotfix(doc, fix_est_h=4)["path"] == "defer"
    assert hotfix(doc, fix_est_h=0)["path"] == "hotfix"


def test_low_risk_rollback_is_preferred_over_a_fast_hotfix(doc):
    result = hotfix(doc, fix_est_h=0.5, rollback_available=True, rollback_risk="low")
    assert result["path"] == "rollback"
    assert "preferred" in result["reason"]


def test_high_risk_rollback_yields_to_a_fast_hotfix_but_not_a_slow_one(doc):
    fast = hotfix(doc, fix_est_h=3.5, rollback_available=True, rollback_risk="high")
    slow = hotfix(doc, fix_est_h=6, rollback_available=True, rollback_risk="high")
    assert fast["path"] == "hotfix"
    assert slow["path"] == "rollback"


def test_no_rollback_and_slow_fix_is_not_a_hotfix(doc):
    result = hotfix(doc, fix_est_h=12, rollback_available=False)
    assert result["path"] == "defer" and result["primes_hotfix"] is True
    assert "escalate" in result["reason"]


@pytest.mark.parametrize(
    "bad, field",
    [
        ({"severity": 0}, "severity"),
        ({"severity": 6}, "severity"),
        ({"severity": 2.5}, "severity"),
        ({"severity": True}, "severity"),
        ({"fix_est_h": -1}, "fix_est_h"),
        ({"fix_est_h": "3"}, "fix_est_h"),
        ({"fix_est_h": 9000}, "fix_est_h"),
        ({"rollback_available": "yes"}, "rollback_available"),
        ({"rollback_risk": "medium"}, "rollback_risk"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_hotfix_malformed_input_names_the_field(doc, bad, field):
    result = hotfix(doc, **bad)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_hotfix_missing_and_empty_input(doc):
    empty = call_tool("maint_hotfix_decision", "A14", {}, data=doc)
    assert empty["state"] == "INVALID_INPUT"
    short = call_tool("maint_hotfix_decision", "A14", {"severity": 3}, data=doc)
    assert short["state"] == "INVALID_INPUT" and short["field"] in {"fix_est_h", "rollback_available", "rollback_risk"}


def test_hotfix_handler_rejects_non_finite_hours_that_the_schema_cannot_see():
    from swarm.tools.a14_tools import hotfix_decision

    base = {"severity": 2, "rollback_available": False, "rollback_risk": "low"}
    for value in (float("nan"), float("inf")):
        result = hotfix_decision({**base, "fix_est_h": value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "fix_est_h"


def test_hotfix_probes_and_authorization(doc):
    base = {"severity": 2, "fix_est_h": 1, "rollback_available": False, "rollback_risk": "low"}
    assert call_tool("maint_hotfix_decision", "A14", {**base, "probe": "dependency"}, data=doc)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool("maint_hotfix_decision", "A14", {**base, "probe": "partial"}, data=doc)["state"] == "PARTIAL_SUCCESS"
    assert call_tool("maint_hotfix_decision", "A14", {**base, "probe": "sleep"}, data=doc)["state"] == "TIMEOUT"
    assert call_tool("maint_hotfix_decision", "A10", base, data=doc)["state"] == "UNAUTHORIZED"


# ---- maint_version_affected ----

def test_open_lower_bound_and_exclusive_upper_bound(doc):
    assert affected(doc, "1.4.4", ">=1.0.0,<1.4.5") == {"state": "SUCCESS", "affected": True, "matched_clause": ">=1.0.0,<1.4.5"}
    assert affected(doc, "1.4.5", ">=1.0.0,<1.4.5")["affected"] is False
    assert affected(doc, "0.9.9", ">=1.0.0,<1.4.5")["affected"] is False


def test_inclusive_bounds(doc):
    assert affected(doc, "1.0.0", ">=1.0.0,<=1.2.0")["affected"] is True
    assert affected(doc, "1.2.0", ">=1.0.0,<=1.2.0")["affected"] is True
    assert affected(doc, "1.2.1", ">=1.0.0,<=1.2.0")["affected"] is False
    assert affected(doc, "1.0.0", ">1.0.0")["affected"] is False


def test_single_clause_and_whitespace_are_normalised(doc):
    result = affected(doc, " 2.3.4 ", "  <  2.3.5 ")
    assert result["affected"] is True and result["matched_clause"] == "<2.3.5"
    assert affected(doc, "1.0.0", ">= 1.0.0 , < 1.1.0")["matched_clause"] == ">=1.0.0,<1.1.0"


def test_not_affected_returns_empty_matched_clause(doc):
    assert affected(doc, "3.0.0", "<2.0.0") == {"state": "SUCCESS", "affected": False, "matched_clause": ""}


def test_numeric_not_lexical_ordering(doc):
    assert affected(doc, "1.10.0", "<1.9.0")["affected"] is False
    assert affected(doc, "1.9.0", "<1.10.0")["affected"] is True


def test_prerelease_precedes_its_release(doc):
    assert affected(doc, "1.5.0-rc.1", "<1.5.0")["affected"] is True
    assert affected(doc, "1.5.0", "<1.5.0-rc.1")["affected"] is False
    assert affected(doc, "1.5.0-alpha.2", "<1.5.0-alpha.10")["affected"] is True
    assert affected(doc, "1.5.0-alpha", ">=1.5.0-alpha.1")["affected"] is False
    assert affected(doc, "1.5.0+build.7", "<=1.5.0")["affected"] is True


@pytest.mark.parametrize(
    "rng",
    ["1.0.0", "=1.0.0", "==1.0.0", "<", "<1.0", "<1.0.0,", ",<1.0.0", "<1.0.0,,>0.1.0", "<1.0.0 >0.1.0", "~1.0.0", "<x.y.z"],
)
def test_malformed_range_is_invalid_input_naming_affected(doc, rng):
    result = affected(doc, "1.0.0", rng)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "affected"


def test_malformed_version_names_version(doc):
    for version in ("1.2", "v1.2.3", "latest", "1.2.3.4"):
        result = affected(doc, version, "<2.0.0")
        assert result["state"] == "INVALID_INPUT" and result["field"] == "version"


def test_empty_oversized_and_missing_input(doc):
    assert call_tool("maint_version_affected", "A14", {}, data=doc)["state"] == "INVALID_INPUT"
    assert affected(doc, "", "<1.0.0")["state"] == "INVALID_INPUT"
    assert affected(doc, "1.0.0", "")["field"] == "affected"
    assert affected(doc, "1" * 65 + ".0.0", "<1.0.0")["field"] == "version"
    assert affected(doc, "1.0.0", "<" + "1" * 600 + ".0.0")["field"] == "affected"
    too_many = ",".join(f">={i}.0.0" for i in range(9))
    assert affected(doc, "9.0.0", too_many)["field"] == "affected"
    eight = ",".join(f">={i}.0.0" for i in range(8))
    assert affected(doc, "9.0.0", eight)["affected"] is True


def test_version_affected_probes_and_callers(doc):
    base = {"version": "1.0.0", "affected": "<2.0.0"}
    assert call_tool("maint_version_affected", "A14", {**base, "probe": "dependency"}, data=doc)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool("maint_version_affected", "A14", {**base, "probe": "partial"}, data=doc)["state"] == "PARTIAL_SUCCESS"
    assert call_tool("maint_version_affected", "A14", {**base, "probe": "sleep"}, data=doc)["state"] == "TIMEOUT"
    assert affected(doc, "1.0.0", "<2.0.0", caller="A10")["state"] == "UNAUTHORIZED"
    assert call_tool("maint_version_affected", "A01", base, data=doc)["state"] == "UNAUTHORIZED"


# ---- maint_unpinned_count agrees with scripts/maint_deps.py ----

SPEC_SAMPLES = [
    "", "*", "latest", "x", "any", ">=0", "1.2.3", "v1.2.3", "==1.2.3", "===1.2.3", "=1.2.3", "1.2", "1.x", "1.2.x",
    "1.*", "^1.2.3", "~=1.2", ">=1,<2", "<3", "~1.2.3", "2.0.0-rc.1", "v2", "  ==1.0  ", '"1.2.3"', "git+https://x/y",
]


def test_unpinned_count_classifies_like_the_maint_deps_script(doc):
    """Skill-review finding: bare `1.2.3` and `v1.2.3` were `range` in the tool but `pinned` in the script, and
    `1.x` was `wildcard` in the tool but `range` in the script."""
    from swarm.tools.support import script

    pin_state = script("maint_deps")._pin_state
    expected = [pin_state(spec) for spec in SPEC_SAMPLES]
    result = call_tool("maint_unpinned_count", "A14", {"specs": SPEC_SAMPLES}, data=doc)
    assert result["state"] == "SUCCESS"
    assert result["total"] == len(SPEC_SAMPLES)
    assert result["pinned"] == expected.count("pinned")
    assert result["unpinned"] == len(SPEC_SAMPLES) - expected.count("pinned")
    assert result["wildcard"] == expected.count("wildcard")


@pytest.mark.parametrize(
    "specs, pinned, wildcard",
    [(["1.2.3", "v1.2.3", "==1.2.3"], 3, 0), (["1.x", "^1.2"], 0, 0), (["", "*", "latest"], 0, 3),
     (["== 1.2.*", "==*", "==", "==1.2.*"], 0, 2),  # a wildcard behind `==` is never a pin
     (["==1.2.3,<2", "==1.2.3 || ^2"], 0, 0),  # compound specs are ranges
     (["== 1.2.3", "=== 1.2.3", "==1"], 3, 0)],
)
def test_unpinned_count_known_cases(doc, specs, pinned, wildcard):
    result = call_tool("maint_unpinned_count", "A14", {"specs": specs}, data=doc)
    assert (result["pinned"], result["wildcard"], result["unpinned"]) == (pinned, wildcard, len(specs) - pinned)
