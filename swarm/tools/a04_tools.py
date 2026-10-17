"""A04 UX Designer tools. Pure checks over design tokens and UX specs supplied in the payload.

- ux_token_name_check: token path convention (kebab-case dot paths, depth, roots, duplicates).
- ux_target_size_check: WCAG 2.2 SC 2.5.8 minimum target size with the spacing exception.
  Clearances are measured by the caller; overlap between undersized targets' circles is not computed.
- ux_scale_monotonic_check: tone scale strictly monotonic in WCAG relative luminance.
- ux_spacing_scale_check: spacing values as integer multiples of a base unit.
- ux_state_coverage_check: required component states covered by declared states (dotted sub-states cover their family).
- ux_focus_contrast_check: WCAG 2.2 SC 1.4.11 focus indicator contrast against each adjacent surface.
- ux_motion_duration_check: animation duration bound and reduced-motion variant presence.
- ux_breakpoint_order_check: responsive breakpoint names unique and minimum widths strictly ascending.
"""
from __future__ import annotations

import re
from collections import Counter

from swarm.tools.support import respond

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SEGMENT = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_TARGET_PX = 24
_CLEARANCE_PX = _TARGET_PX / 2
_SCALE_EPS = 1e-9


def _rgb(hex_text: str) -> tuple[int, int, int]:
    match = _HEX.match(hex_text.strip())
    if match is None:
        raise ValueError(f"invalid hex colour {hex_text!r}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def _luminance(hex_text: str) -> float:
    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(v) for v in _rgb(hex_text))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(lum_a: float, lum_b: float) -> float:
    hi, lo = max(lum_a, lum_b), min(lum_a, lum_b)
    return (hi + 0.05) / (lo + 0.05)


def _token_problem(name: str, max_depth: int, roots: list[str] | None) -> str | None:
    segments = name.split(".")
    if len(segments) > max_depth:
        return f"depth {len(segments)} exceeds max_depth {max_depth}"
    for segment in segments:
        if not _SEGMENT.match(segment):
            return f"segment {segment!r} is not kebab-case"
    if roots and segments[0] not in roots:
        return f"root {segments[0]!r} is not in roots"
    return None


def token_name_check(payload: dict) -> dict:
    def work() -> dict:
        names = payload["names"]
        max_depth = payload["max_depth"]
        roots = payload.get("roots")
        invalid = []
        for name in names:
            problem = _token_problem(name, max_depth, roots)
            if problem:
                invalid.append(f"{name}: {problem}")
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        return {
            "state": "SUCCESS",
            "checked": len(names),
            "invalid": invalid,
            "duplicates": duplicates,
            "conforming": not invalid and not duplicates,
        }

    return respond(payload, work)


def target_size_check(payload: dict) -> dict:
    def work() -> dict:
        targets = payload["targets"]
        repeated = sorted(tid for tid, count in Counter(t["id"] for t in targets).items() if count > 1)
        if repeated:
            return {"state": "INVALID_INPUT", "field": "targets", "message": f"duplicate ids: {', '.join(repeated)}"}
        meets, exception, failing = 0, [], []
        for target in targets:
            if target["width_px"] >= _TARGET_PX and target["height_px"] >= _TARGET_PX:
                meets += 1
            elif target["center_to_nearest_edge_px"] >= _CLEARANCE_PX:
                exception.append(target["id"])
            else:
                failing.append(target["id"])
        return {
            "state": "SUCCESS",
            "total": len(targets),
            "meets_minimum": meets,
            "spacing_exception": exception,
            "failing": failing,
            "compliant": not failing,
        }

    return respond(payload, work)


def scale_monotonic_check(payload: dict) -> dict:
    def work() -> dict:
        steps = payload["steps"]
        luminances = []
        for step in steps:
            try:
                luminances.append(_luminance(step["hex"]))
            except ValueError as exc:
                return {"state": "INVALID_INPUT", "field": "steps", "message": f"step {step['step']!r}: {exc}"}
        falling = payload["order"] == "light_to_dark"
        violations = []
        for i in range(1, len(steps)):
            before, after = luminances[i - 1], luminances[i]
            ok = before > after if falling else before < after
            if not ok:
                violations.append(f"{steps[i - 1]['step']}->{steps[i]['step']}")
        return {
            "state": "SUCCESS",
            "checked": len(steps),
            "luminance": [round(v, 4) for v in luminances],
            "monotonic": not violations,
            "violations": violations,
        }

    return respond(payload, work)


def spacing_scale_check(payload: dict) -> dict:
    def work() -> dict:
        base = payload["base_px"]
        multiples, off_scale = [], []
        for value in payload["values_px"]:
            units = value / base
            nearest = round(units)
            if abs(units - nearest) <= _SCALE_EPS:
                multiples.append(nearest)
            else:
                off_scale.append(value)
        return {
            "state": "SUCCESS",
            "checked": len(payload["values_px"]),
            "multiples": multiples,
            "off_scale": off_scale,
            "on_scale": not off_scale,
        }

    return respond(payload, work)


def _norm_state(state: str) -> str:
    return state.strip().lower()


def _covers(state: str, family: str) -> bool:
    return state == family or state.startswith(family + ".")


def state_coverage_check(payload: dict) -> dict:
    def work() -> dict:
        required = list(dict.fromkeys(_norm_state(s) for s in payload["required_states"]))
        declared = [_norm_state(s) for s in payload["declared_states"]]
        if "" in required:
            return {"state": "INVALID_INPUT", "field": "required_states", "message": "required states must not be blank"}
        if "" in declared:
            return {"state": "INVALID_INPUT", "field": "declared_states", "message": "declared states must not be blank"}
        missing = [family for family in required if not any(_covers(s, family) for s in declared)]
        unexpected = [s for s in declared if not any(_covers(s, family) for family in required)]
        return {
            "state": "SUCCESS",
            "required_count": len(required),
            "covered_count": len(required) - len(missing),
            "missing": missing,
            "unexpected": unexpected,
            "complete": not missing,
        }

    return respond(payload, work)


def focus_contrast_check(payload: dict) -> dict:
    def work() -> dict:
        try:
            focus = _luminance(payload["focus_hex"])
        except ValueError as exc:
            return {"state": "INVALID_INPUT", "field": "focus_hex", "message": str(exc)}
        surfaces = payload["surface_hexes"]
        try:
            ratios = [_contrast(focus, _luminance(hex_text)) for hex_text in surfaces]
        except ValueError as exc:
            return {"state": "INVALID_INPUT", "field": "surface_hexes", "message": str(exc)}
        floor = payload["required_ratio"]
        return {
            "state": "SUCCESS",
            "checked": len(ratios),
            "ratios": [round(r, 2) for r in ratios],
            "lowest_ratio": round(min(ratios), 2),
            "failing_surfaces": [hex_text for hex_text, r in zip(surfaces, ratios) if r < floor],
            "meets": all(r >= floor for r in ratios),
        }

    return respond(payload, work)


def motion_duration_check(payload: dict) -> dict:
    def work() -> dict:
        limit = payload["max_duration_ms"]
        animations = payload["animations"]
        return {
            "state": "SUCCESS",
            "checked": len(animations),
            "max_observed_ms": max(a["duration_ms"] for a in animations),
            "over_limit": [a["name"] for a in animations if a["duration_ms"] > limit],
            "without_reduced_motion": [a["name"] for a in animations if not a["has_reduced_motion_variant"]],
            "compliant": all(a["duration_ms"] <= limit and a["has_reduced_motion_variant"] for a in animations),
        }

    return respond(payload, work)


def breakpoint_order_check(payload: dict) -> dict:
    def work() -> dict:
        breakpoints = payload["breakpoints"]
        duplicates = sorted(name for name, count in Counter(b["name"] for b in breakpoints).items() if count > 1)
        out_of_order = [
            cur["name"]
            for prev, cur in zip(breakpoints, breakpoints[1:])
            if cur["min_width_px"] <= prev["min_width_px"]
        ]
        ordered = sorted(breakpoints, key=lambda b: (b["min_width_px"], b["name"]))
        return {
            "state": "SUCCESS",
            "count": len(breakpoints),
            "valid": not duplicates and not out_of_order,
            "starts_at_zero": breakpoints[0]["min_width_px"] == 0,
            "out_of_order": out_of_order,
            "duplicate_names": duplicates,
            "sorted_names": [b["name"] for b in ordered],
        }

    return respond(payload, work)
