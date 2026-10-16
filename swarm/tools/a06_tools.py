"""A06 frontend checks: pure analysis of heading, image, control, tab-order, bundle, prop, and target records.

Every input comes from the payload. Nothing reads the DOM, the filesystem, the network, or the clock.
Tools:
  fe_heading_sequence_check  heading level order and skipped levels
  fe_alt_text_check          image alt presence, emptiness, generic values, decorative conflicts
  fe_form_label_check        visible form controls without an accessible name
  fe_tab_order_check         positive tabindex use and focus-order inversions
  fe_bundle_budget_check     bundle bytes against a budget and against a baseline regression
  fe_prop_type_coverage      share of component props with a concrete declared type
  fe_target_size_check       interactive targets below the minimum CSS-pixel size
"""
from __future__ import annotations

from swarm.tools.support import respond

GENERIC_ALT = frozenset({"image", "img", "photo", "picture", "icon", "graphic", "untitled"})
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
UNTYPED = frozenset({"", "any", "unknown"})


def _invalid(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _duplicate(values):
    """Return the first value that appears twice, or None."""
    seen = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def fe_heading_sequence_check(payload: dict) -> dict:
    """Heading levels must start at h1 and never descend more than one level at a time."""

    def work() -> dict:
        levels = payload["levels"]
        first_ok = levels[0] == 1
        skips = [i for i in range(1, len(levels)) if levels[i] - levels[i - 1] > 1]
        return {
            "state": "SUCCESS",
            "heading_count": len(levels),
            "first_level_ok": first_ok,
            "skip_count": len(skips),
            "skip_indexes": skips,
            "sequence_ok": first_ok and not skips,
        }

    return respond(payload, work)


def fe_alt_text_check(payload: dict) -> dict:
    """Non-decorative images need meaningful alt text; decorative images must not carry any."""

    def work() -> dict:
        images = payload["images"]
        dup = _duplicate(image["id"] for image in images)
        if dup is not None:
            return _invalid("images", f"duplicate image id {dup!r}")
        checked = missing = empty = generic = conflicts = 0
        flagged: list[str] = []
        for image in images:
            alt = image["alt"].strip()
            if image["decorative"]:
                if image["has_alt"] and alt:
                    conflicts += 1
                    flagged.append(image["id"])
                continue
            checked += 1
            if not image["has_alt"]:
                missing += 1
            elif not alt:
                empty += 1
            elif alt.lower() in GENERIC_ALT or alt.lower().endswith(IMAGE_SUFFIXES):
                generic += 1
            else:
                continue
            flagged.append(image["id"])
        return {
            "state": "SUCCESS",
            "image_count": len(images),
            "checked_count": checked,
            "missing_count": missing,
            "empty_count": empty,
            "generic_count": generic,
            "decorative_conflict_count": conflicts,
            "flagged_ids": flagged,
            "alt_ok": not flagged,
        }

    return respond(payload, work)


def fe_form_label_check(payload: dict) -> dict:
    """Every control except type hidden needs a label, a non-blank aria-label, or an aria-labelledby reference."""

    def work() -> dict:
        controls = payload["controls"]
        dup = _duplicate(control["id"] for control in controls)
        if dup is not None:
            return _invalid("controls", f"duplicate control id {dup!r}")
        visible = [control for control in controls if control["type"] != "hidden"]
        unlabeled = [
            control["id"]
            for control in visible
            if not (control["has_label"] or control["aria_label"].strip() or control["aria_labelledby"])
        ]
        return {
            "state": "SUCCESS",
            "control_count": len(controls),
            "checked_count": len(visible),
            "hidden_skipped_count": len(controls) - len(visible),
            "unlabeled_count": len(unlabeled),
            "unlabeled_ids": unlabeled,
            "labels_ok": not unlabeled,
        }

    return respond(payload, work)


def fe_tab_order_check(payload: dict) -> dict:
    """Positive tabindex values override DOM order; count them and the resulting focus-order inversions."""

    def work() -> dict:
        elements = payload["elements"]
        dup = _duplicate(element["id"] for element in elements)
        if dup is not None:
            return _invalid("elements", f"duplicate element id {dup!r}")
        positive = [element for element in elements if element["tabindex"] > 0]
        values = [element["tabindex"] for element in positive]
        inversions = sum(
            1 for i in range(len(values)) for j in range(i + 1, len(values)) if values[i] > values[j]
        )
        return {
            "state": "SUCCESS",
            "element_count": len(elements),
            "focusable_count": sum(1 for element in elements if element["tabindex"] >= 0),
            "positive_count": len(positive),
            "positive_ids": [element["id"] for element in positive],
            "inversion_count": inversions,
            "order_ok": not positive,
        }

    return respond(payload, work)


def fe_bundle_budget_check(payload: dict) -> dict:
    """Total bundle bytes must fit the budget and must not grow past the regression limit over a baseline."""

    def work() -> dict:
        chunks = payload["chunks"]
        dup = _duplicate(chunk["name"] for chunk in chunks)
        if dup is not None:
            return _invalid("chunks", f"duplicate chunk name {dup!r}")
        total = sum(chunk["bytes"] for chunk in chunks)
        budget = payload["budget_bytes"]
        baseline = payload.get("baseline_bytes")
        limit = payload.get("regression_limit_pct", 5)
        checked = baseline is not None
        regression = round((total - baseline) * 100 / baseline, 2) if checked else 0.0
        breach = checked and regression > limit
        return {
            "state": "SUCCESS",
            "total_bytes": total,
            "headroom_bytes": budget - total,
            "utilization_pct": round(total * 100 / budget, 2),
            "over_budget": total > budget,
            "largest_chunk": max(chunks, key=lambda chunk: chunk["bytes"])["name"],
            "regression_checked": checked,
            "regression_pct": regression,
            "regression_breach": breach,
            "budget_ok": total <= budget and not breach,
        }

    return respond(payload, work)


def fe_prop_type_coverage(payload: dict) -> dict:
    """Each component's props need a concrete declared type; untyped means blank, any, or unknown."""

    def work() -> dict:
        components = payload["components"]
        dup = _duplicate(component["name"] for component in components)
        if dup is not None:
            return _invalid("components", f"duplicate component name {dup!r}")
        min_ratio = payload.get("min_ratio", 1.0)
        total = typed = 0
        failing: list[str] = []
        for component in components:
            props = component["props"]
            dup_prop = _duplicate(prop["name"] for prop in props)
            if dup_prop is not None:
                return _invalid("components", f"{component['name']} repeats prop {dup_prop!r}")
            good = sum(1 for prop in props if prop["declared_type"].strip() not in UNTYPED)
            total += len(props)
            typed += good
            ratio = good / len(props) if props else 1.0
            if ratio < min_ratio:
                failing.append(component["name"])
        return {
            "state": "SUCCESS",
            "component_count": len(components),
            "prop_count": total,
            "typed_prop_count": typed,
            "coverage_ratio": round(typed / total, 4) if total else 1.0,
            "failing_components": failing,
            "components_ok": not failing,
        }

    return respond(payload, work)


def fe_target_size_check(payload: dict) -> dict:
    """Interactive targets must be at least min_px wide and tall (WCAG 2.2 SC 2.5.8 default 24 CSS px)."""

    def work() -> dict:
        targets = payload["targets"]
        dup = _duplicate(target["id"] for target in targets)
        if dup is not None:
            return _invalid("targets", f"duplicate target id {dup!r}")
        minimum = payload.get("min_px", 24)
        small = [
            target["id"]
            for target in targets
            if target["width_px"] < minimum or target["height_px"] < minimum
        ]
        return {
            "state": "SUCCESS",
            "target_count": len(targets),
            "undersized_count": len(small),
            "undersized_ids": small,
            "size_ok": not small,
        }

    return respond(payload, work)
