"""A03 architect tools: pure checks over release versions, contract fields, module and
container graphs, ADR status changes and NFR budgets. Every input comes from the payload.

Tools:
- arch_semver_bump: classify a release step and test it against the bump the change requires.
- arch_field_breaks: list breaking and additive changes between two field sets of one contract side.
- arch_layer_violations: find import edges that point up an ordered layering.
- arch_cycle_check: find dependency cycles (strongly connected components) in module edges.
- arch_c4_fanout: count C4 containers and relations and flag containers over a fan-out budget.
- arch_adr_transition: check an ADR status change against the lifecycle used by scripts/arch_adr.py.
- arch_nfr_budget: compare measured NFR values with their budgets; an unmeasured budget fails closed.
"""
from __future__ import annotations

import re

from swarm.tools.support import respond

_CORE_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_BUMP_RANK = {"none": 0, "patch": 1, "minor": 2, "major": 3}
_ADR_NEXT = {
    "proposed": {"accepted", "rejected"},
    "accepted": {"deprecated", "superseded"},
    "deprecated": {"superseded"},
    "rejected": set(),
    "superseded": set(),
}


def _invalid(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _core(value) -> tuple[int, int, int] | None:
    """MAJOR.MINOR.PATCH as integers; None for prerelease, build metadata or malformed text."""
    match = _CORE_VERSION.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def semver_bump(payload: dict) -> dict:
    """Bump between two release versions, and whether it satisfies the change's required bump.

    A breaking change requires a major bump and an additive change a minor bump, regardless of
    the 0.x major, matching the prompt's rule that breaking contract changes need a major version.
    """

    def work() -> dict:
        base = _core(payload["base_version"])
        head = _core(payload["head_version"])
        if base is None:
            return _invalid("base_version", "must be MAJOR.MINOR.PATCH with no prerelease or build suffix")
        if head is None:
            return _invalid("head_version", "must be MAJOR.MINOR.PATCH with no prerelease or build suffix")
        if head < base:
            bump = "downgrade"
        elif head == base:
            bump = "none"
        elif head[0] > base[0]:
            bump = "major"
        elif head[1] > base[1]:
            bump = "minor"
        else:
            bump = "patch"
        if payload["breaking_change"]:
            required = "major"
        elif payload["additive_change"]:
            required = "minor"
        else:
            required = "none"
        compliant = bump != "downgrade" and _BUMP_RANK[bump] >= _BUMP_RANK[required]
        return {"state": "SUCCESS", "bump": bump, "required_bump": required, "compliant": compliant}

    return respond(payload, work)


def _index(fields: list, label: str) -> tuple[dict | None, str | None]:
    index: dict = {}
    for field in fields:
        if field["name"] in index:
            return None, f"duplicate field name {field['name']} in {label}"
        index[field["name"]] = field
    return index, None


def field_breaks(payload: dict) -> dict:
    """Compare two field sets of one contract side.

    Removed fields and type changes are breaking on both sides. On the request side a new required
    field or an optional-to-required change is breaking. On the response side a required-to-optional
    change is breaking. Every other change is additive.
    """

    def work() -> dict:
        side = payload["side"]
        base, problem = _index(payload["base_fields"], "base_fields")
        if problem:
            return _invalid("base_fields", problem)
        head, problem = _index(payload["head_fields"], "head_fields")
        if problem:
            return _invalid("head_fields", problem)
        breaking: list[str] = []
        additive: list[str] = []
        for name in sorted(base):
            if name not in head:
                breaking.append(f"removed field {name}")
                continue
            before, after = base[name], head[name]
            if before["type"] != after["type"]:
                breaking.append(f"type of {name} changed {before['type']} -> {after['type']}")
            if before["required"] and not after["required"]:
                (breaking if side == "response" else additive).append(f"field {name} became optional")
            elif after["required"] and not before["required"]:
                (breaking if side == "request" else additive).append(f"field {name} became required")
        for name in sorted(head):
            if name in base:
                continue
            if head[name]["required"] and side == "request":
                breaking.append(f"new required field {name}")
            else:
                additive.append(f"added field {name}")
        return {"state": "SUCCESS", "breaking_change": bool(breaking), "breaking": breaking, "additive": additive}

    return respond(payload, work)


def layer_violations(payload: dict) -> dict:
    """Edges whose target sits in a layer above the source layer.

    layer_order runs from the outermost layer (index 0) to the innermost. A module may depend on its
    own layer or any later one. Edges touching an unassigned module are reported, not judged.
    """

    def work() -> dict:
        order = payload["layer_order"]
        if len(set(order)) != len(order):
            return _invalid("layer_order", "layer names must be unique")
        rank = {layer: index for index, layer in enumerate(order)}
        layer_of: dict[str, str] = {}
        for item in payload["assignments"]:
            module, layer = item["module"], item["layer"]
            if module in layer_of:
                return _invalid("assignments", f"module {module} is assigned twice")
            if layer not in rank:
                return _invalid("assignments", f"layer {layer} is not in layer_order")
            layer_of[module] = layer
        violations: list[str] = []
        unassigned: set[str] = set()
        checked = 0
        for edge in payload["edges"]:
            src, dst = edge["from"], edge["to"]
            if src not in layer_of or dst not in layer_of:
                unassigned.update(module for module in (src, dst) if module not in layer_of)
                continue
            checked += 1
            if rank[layer_of[dst]] < rank[layer_of[src]]:
                violations.append(f"{src} ({layer_of[src]}) -> {dst} ({layer_of[dst]})")
        return {
            "state": "SUCCESS",
            "violation_count": len(violations),
            "checked_edges": checked,
            "violations": violations,
            "unassigned": sorted(unassigned),
        }

    return respond(payload, work)


def _strongly_connected(adj: dict[str, set[str]], radj: dict[str, set[str]]) -> list[list[str]]:
    """Kosaraju's algorithm, iterative so deep graphs cannot exhaust the recursion limit."""
    seen: set[str] = set()
    finished: list[str] = []
    for root in sorted(adj):
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(sorted(adj[root])))]
        while stack:
            node, children = stack[-1]
            for child in children:
                if child not in seen:
                    seen.add(child)
                    stack.append((child, iter(sorted(adj[child]))))
                    break
            else:
                stack.pop()
                finished.append(node)
    group_of: dict[str, int] = {}
    groups: list[list[str]] = []
    for root in reversed(finished):
        if root in group_of:
            continue
        index = len(groups)
        group_of[root] = index
        members: list[str] = []
        todo = [root]
        while todo:
            node = todo.pop()
            members.append(node)
            for parent in radj[node]:
                if parent not in group_of:
                    group_of[parent] = index
                    todo.append(parent)
        groups.append(sorted(members))
    return groups


def cycle_check(payload: dict) -> dict:
    """Cyclic components of a dependency graph. A self-edge counts as a cycle of one node."""

    def work() -> dict:
        adj: dict[str, set[str]] = {}
        radj: dict[str, set[str]] = {}
        for edge in payload["edges"]:
            src, dst = edge["from"], edge["to"]
            adj.setdefault(src, set()).add(dst)
            adj.setdefault(dst, set())
            radj.setdefault(dst, set()).add(src)
            radj.setdefault(src, set())
        cyclic = [
            members
            for members in _strongly_connected(adj, radj)
            if len(members) > 1 or members[0] in adj[members[0]]
        ]
        cyclic.sort(key=lambda members: members[0])
        return {
            "state": "SUCCESS",
            "has_cycle": bool(cyclic),
            "node_count": len(adj),
            "cyclic_node_count": sum(len(members) for members in cyclic),
            "cyclic_components": [", ".join(members) for members in cyclic],
        }

    return respond(payload, work)


def c4_fanout(payload: dict) -> dict:
    """Container count, distinct relation count and per-container fan-out against a budget.

    Fan-out is the number of distinct containers a container relates to. Isolated containers have
    neither outgoing nor incoming relations.
    """

    def work() -> dict:
        containers = payload["containers"]
        known = set(containers)
        if len(known) != len(containers):
            return _invalid("containers", "container names must be unique")
        targets: dict[str, set[str]] = {name: set() for name in containers}
        for rel in payload["relations"]:
            src, dst = rel["from"], rel["to"]
            if src not in known or dst not in known:
                return _invalid("relations", f"unknown container in {src} -> {dst}")
            if src == dst:
                return _invalid("relations", f"container {src} relates to itself")
            targets[src].add(dst)
        incoming = {dst for out in targets.values() for dst in out}
        fan_out = {name: len(out) for name, out in targets.items()}
        budget = payload["fan_out_budget"]
        over = sorted(
            ((name, count) for name, count in fan_out.items() if count > budget),
            key=lambda item: (-item[1], item[0]),
        )
        return {
            "state": "SUCCESS",
            "container_count": len(containers),
            "relation_count": sum(fan_out.values()),
            "max_fan_out": max(fan_out.values(), default=0),
            "over_budget": [f"{name}={count}" for name, count in over],
            "isolated": sorted(name for name in containers if fan_out[name] == 0 and name not in incoming),
        }

    return respond(payload, work)


def adr_transition(payload: dict) -> dict:
    """Whether an ADR may move between statuses. Statuses match scripts/arch_adr.py.

    proposed -> accepted | rejected; accepted -> deprecated | superseded; deprecated -> superseded.
    rejected and superseded are terminal. Moving to superseded needs superseded_by.
    """

    def work() -> dict:
        src, dst = payload["from_status"], payload["to_status"]
        superseded_by = payload.get("superseded_by")
        if src == dst:
            allowed, reason = False, f"status is already {src}"
        elif not _ADR_NEXT[src]:
            allowed, reason = False, f"{src} is terminal"
        elif dst not in _ADR_NEXT[src]:
            allowed, reason = False, f"{src} cannot move to {dst}"
        elif dst == "superseded" and not superseded_by:
            allowed, reason = False, "superseded requires superseded_by, the id of the superseding ADR"
        else:
            allowed = True
            reason = f"{src} -> {dst} is allowed"
            if dst == "superseded":
                reason += f" by {superseded_by}"
        return {"state": "SUCCESS", "allowed": allowed, "reason": reason}

    return respond(payload, work)


def nfr_budget(payload: dict) -> dict:
    """Measured NFR values checked against their budgets.

    max budgets breach when the value exceeds the limit; min budgets breach when it falls below.
    A budget with no measurement is unmeasured and keeps within_budget false (fail closed).
    """

    def work() -> dict:
        budgets = payload["budgets"]
        names = [item["metric"] for item in budgets]
        if len(set(names)) != len(names):
            return _invalid("budgets", "budget metrics must be unique")
        measured: dict[str, float] = {}
        for item in payload["measurements"]:
            if item["metric"] in measured:
                return _invalid("measurements", f"metric {item['metric']} is measured twice")
            measured[item["metric"]] = item["value"]
        breaches: list[str] = []
        unmeasured: list[str] = []
        checked = 0
        for item in budgets:
            metric, limit, direction = item["metric"], item["limit"], item["direction"]
            if metric not in measured:
                unmeasured.append(metric)
                continue
            value = measured[metric]
            checked += 1
            if direction == "max" and value > limit:
                breaches.append(f"{metric} {value} > {limit} (max)")
            elif direction == "min" and value < limit:
                breaches.append(f"{metric} {value} < {limit} (min)")
        return {
            "state": "SUCCESS",
            "checked": checked,
            "within_budget": not breaches and not unmeasured,
            "breaches": breaches,
            "unmeasured": unmeasured,
        }

    return respond(payload, work)
