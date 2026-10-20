"""A schema-valid payload never crashes, stalls or overflows a lane tool of the v2.3 agents (A16-A30).

The registry maps a handler exception to DEPENDENCY_UNAVAILABLE, a stall to TIMEOUT and an oversized result to a withheld
PARTIAL_SUCCESS. An agent cannot tell those from a real outage, so for every registered tool a payload that satisfies the
input schema must end in SUCCESS or INVALID_INPUT (a semantic refusal that names the field). Each case starts from the
contract's own `examples.success` and mutates ONE position at a time with schema-valid extreme values: the bounds, zero,
empty and very long strings, pathological and non-ASCII text, control characters, array fan-out, an omitted optional field
and every enum member."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry

ROOT = Path(__file__).resolve().parent.parent
AGENT_IDS = [f"A{n}" for n in range(16, 31)]
LONG = 4096
FAN_OUT = 40
STRINGS = (
    "", " ", "a", "x" * LONG, "(" * 512, "a" * 1024 + "!", "é" * 300, "\u202e\u0000\x1b[31m", "../../etc/passwd", "0x" + "f" * 64,
    "1e999", "-1", "True", "null", "{{}}", "a,b,c" * 200, "https://user:pass@host:99999/p?q=1#f", "A" * 2000 + "B", "\n" * 300,
)


def _int_bound(spec: dict, key: str, default: int) -> int:
    value = spec.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _string_variants(spec: dict) -> list[str]:
    low, high = _int_bound(spec, "minLength", 0), _int_bound(spec, "maxLength", LONG)
    out = []
    for text in (*STRINGS, "a" * low, "a" * min(high, 20000)):
        if len(text) > high:
            text = (text * (high // max(len(text), 1) + 1))[:high]
        if len(text) < low:
            text = (text + "a" * low)[:low]
        out.append(text)
    return out


def _number_variants(spec: dict) -> list:
    integer = spec["type"] == "integer"
    low = spec.get("minimum", spec.get("exclusiveMinimum", -(10**9)))
    high = spec.get("maximum", spec.get("exclusiveMaximum", 10**9))
    middle = (low + high) // 2 if integer else (low + high) / 2
    out = []
    for point in (low, high, 0, 1, -1, 2, 3, middle, 10**6, 10**12, 1e-9, 0.5):
        point = int(point) if integer else point
        if "exclusiveMinimum" in spec and point <= spec["exclusiveMinimum"]:
            continue
        if "exclusiveMaximum" in spec and point >= spec["exclusiveMaximum"]:
            continue
        if "minimum" in spec and point < spec["minimum"]:
            continue
        if "maximum" in spec and point > spec["maximum"]:
            continue
        out.append(point)
    return out


def _leaf_variants(spec: dict) -> list:
    if "enum" in spec:
        return list(spec["enum"])
    kind = spec.get("type")
    if kind == "string":
        return _string_variants(spec)
    if kind in ("integer", "number"):
        return _number_variants(spec)
    if kind == "boolean":
        return [True, False]
    return []


def _positions(schema: dict, value, path: list) -> list[tuple[list, tuple]]:
    """Every (path, operation) that mutates one position of `value` to a schema-valid alternative."""
    found: list[tuple[list, tuple]] = []
    kind = schema.get("type")
    if kind == "object":
        required = set(schema.get("required") or [])
        for name, sub in (schema.get("properties") or {}).items():
            if name == "probe" or name not in value:
                continue
            found.extend(_positions(sub, value[name], [*path, name]))
            if name not in required:
                found.append(([*path, name], ("omit", None)))
    elif kind == "array":
        item = schema.get("items") or {}
        low, high = _int_bound(schema, "minItems", 0), _int_bound(schema, "maxItems", 50)
        if value:
            found.extend(_positions(item, value[0], [*path, 0]))
            found.append((path, ("fan_out", min(high, FAN_OUT))))
            if low == 0:
                found.append((path, ("set", [])))
            if len(value) < high:
                found.append((path, ("set", [*value, copy.deepcopy(value[0])])))
    else:
        found.extend((path, ("set", variant)) for variant in _leaf_variants(schema))
    return found


def _mutate(payload: dict, path: list, operation: tuple) -> dict | None:
    if not path:
        return None
    mutated = copy.deepcopy(payload)
    node = mutated
    for key in path[:-1]:
        node = node[key]
    verb, argument = operation
    if verb == "omit":
        del node[path[-1]]
    elif verb == "set":
        node[path[-1]] = argument
    else:
        node[path[-1]] = [copy.deepcopy(node[path[-1]][0]) for _ in range(argument)]
    return mutated


def _cases(tool: dict) -> list[dict]:
    base = copy.deepcopy(tool["examples"]["success"])
    seen: set[str] = set()
    cases = []
    for path, operation in _positions(tool["input_schema"], base, []):
        payload = _mutate(base, path, operation)
        if payload is None:
            continue
        key = json.dumps(payload, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            cases.append(payload)
    return cases


@pytest.fixture(scope="module")
def registry() -> dict:
    return tool_registry.load_document()


@pytest.mark.parametrize("agent_id", AGENT_IDS)
def test_a_schema_valid_payload_ends_in_success_or_a_named_refusal(registry, agent_id):
    agent = next(a for a in registry["agents"] if a["id"] == agent_id)
    assert agent["registered_tools"], agent_id
    failures = []
    calls = 0
    for tool in agent["registered_tools"]:
        for payload in _cases(tool):
            calls += 1
            result = tool_registry.call_tool(tool["name"], agent_id, payload, data=registry, timeout_s=10)
            if result["state"] == "INVALID_INPUT":
                assert result.get("field"), (tool["name"], result)
            elif result["state"] != "SUCCESS":
                failures.append(f"{tool['name']}: {result['state']} {str(result)[:160]} for {json.dumps(payload, default=str)[:200]}")
    assert calls > 10 * len(agent["registered_tools"]), f"{agent_id}: only {calls} mutated payloads were generated"
    assert not failures, f"{len(failures)} payload(s) crashed or stalled a handler:\n" + "\n".join(failures[:15])
