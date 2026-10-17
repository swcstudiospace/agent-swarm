"""A12 release-manager lane tools: canary decisions, rollback triggers, record checks, delivery metrics.

Mission: decide canary promotion and rollback from supplied telemetry, verify release-record
completeness and promotion-gate order, and compute release-health metrics. Pure logic only;
every input comes from the payload.

Tools:
- rel_canary_verdict: promote, hold, or roll back one canary step.
- rel_rollback_trigger: find the first sustained SLO burn-rate breach.
- rel_release_record_check: list required release-record fields that are missing or empty.
- rel_change_failure_rate: failed-change share of supplied change outcomes.
- rel_deploy_frequency: deployments per week from supplied ISO deploy dates.
- rel_lead_time_p95: median and nearest-rank P95 of commit-to-deploy durations in hours.
- rel_gate_order_check: missing or out-of-order promotion gates.
"""

from datetime import date

from swarm.tools.support import respond


def canary_verdict(payload: dict) -> dict:
    def work() -> dict:
        canary = payload["canary_error_rate"]
        regression = round(canary - payload["baseline_error_rate"], 6)
        if canary > payload["error_rate_max"]:
            decision, reason = "rollback", "error_rate_over_ceiling"
        elif regression > payload["max_regression"]:
            decision, reason = "rollback", "regressed_over_baseline"
        elif payload["soak_observed_min"] < payload["soak_required_min"]:
            decision, reason = "hold", "soak_incomplete"
        else:
            decision, reason = "promote", "within_guardrails"
        return {"state": "SUCCESS", "decision": decision, "reason": reason, "regression": regression}

    return respond(payload, work)


def rollback_trigger(payload: dict) -> dict:
    def work() -> dict:
        limit = payload["slo_burn_max"]
        needed = payload["consecutive_required"]
        run = 0
        breach = -1
        for index, rate in enumerate(payload["burn_rates"]):
            run = run + 1 if rate > limit else 0
            if run >= needed:
                breach = index
                break
        return {
            "state": "SUCCESS",
            "triggered": breach >= 0,
            "breach_index": breach,
            "max_burn": max(payload["burn_rates"]),
        }

    return respond(payload, work)


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def release_record_check(payload: dict) -> dict:
    def work() -> dict:
        record = payload["record"]
        fields = list(dict.fromkeys(payload["required_fields"]))
        missing = [name for name in fields if name not in record or _is_empty(record[name])]
        return {
            "state": "SUCCESS",
            "complete": not missing,
            "missing": ",".join(missing),
            "checked": len(fields),
        }

    return respond(payload, work)


def change_failure_rate(payload: dict) -> dict:
    def work() -> dict:
        outcomes = payload["outcomes"]
        total = len(outcomes)
        failed = sum(1 for flag in outcomes if flag)
        return {"state": "SUCCESS", "total": total, "failed": failed, "rate": round(failed / total, 4)}

    return respond(payload, work)


def deploy_frequency(payload: dict) -> dict:
    def work() -> dict:
        try:
            days = sorted(date.fromisoformat(text) for text in payload["deploy_days"])
        except ValueError as exc:
            return {
                "state": "INVALID_INPUT",
                "field": "deploy_days",
                "message": f"every entry must be an ISO date YYYY-MM-DD ({exc})",
            }
        span = (days[-1] - days[0]).days + 1
        return {
            "state": "SUCCESS",
            "deploys": len(days),
            "active_days": len(set(days)),
            "span_days": span,
            "per_week": round(len(days) / span * 7, 2),
        }

    return respond(payload, work)


def lead_time_p95(payload: dict) -> dict:
    def work() -> dict:
        ordered = sorted(payload["durations_hours"])
        count = len(ordered)
        # Nearest-rank percentile: the value at rank ceil(p * n / 100), 1-based.
        median_rank = (50 * count + 99) // 100
        p95_rank = (95 * count + 99) // 100
        return {
            "state": "SUCCESS",
            "count": count,
            "median_hours": ordered[median_rank - 1],
            "p95_hours": ordered[p95_rank - 1],
        }

    return respond(payload, work)


def gate_order_check(payload: dict) -> dict:
    def work() -> dict:
        steps = payload["steps"]
        wanted = payload["required_order"]
        if len(set(wanted)) != len(wanted):
            return {"state": "INVALID_INPUT", "field": "required_order", "message": "gate names must be unique"}
        if any("," in name for name in steps + wanted):
            return {"state": "INVALID_INPUT", "field": "steps", "message": "gate names must not contain commas"}
        first: dict[str, int] = {}
        for index, name in enumerate(steps):
            first.setdefault(name, index)
        missing = [name for name in wanted if name not in first]
        out_of_order = []
        last = -1
        for name in wanted:
            if name not in first:
                continue
            if first[name] < last:
                out_of_order.append(name)
            last = max(last, first[name])
        return {
            "state": "SUCCESS",
            "ordered": not missing and not out_of_order,
            "missing": ",".join(missing),
            "out_of_order": ",".join(out_of_order),
        }

    return respond(payload, work)
