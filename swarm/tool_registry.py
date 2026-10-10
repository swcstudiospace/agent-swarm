"""Tool registry over agents.json.

Registration lives on each agent as `registered_tools`, plus a top-level `shared_tools`
list. `call_tool` is the only call path: an unregistered name never resolves a handler.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_FILE = Path(os.environ.get("SWARM_AGENTS_FILE", ROOT / "agents.json"))
CONTRACT = "swarm.tool.v1"
SCHEMA_FILE = ROOT / "swarm" / "schemas" / "tool.contract.v1.json"

PREFIX = {
    "A01": "orch_",
    "A02": "req_",
    "A03": "arch_",
    "A04": "ux_",
    "A05": "be_",
    "A06": "fe_",
    "A07": "data_",
    "A08": "qa_",
    "A09": "rev_",
    "A10": "sec_",
    "A11": "devops_",
    "A12": "rel_",
    "A13": "obs_",
    "A14": "maint_",
    "A15": "docs_",
    "shared": "shared_",
}
ERROR_STATES = (
    "INVALID_INPUT",
    "UNAUTHORIZED",
    "DEPENDENCY_UNAVAILABLE",
    "TIMEOUT",
    "PARTIAL_SUCCESS",
)
TERMINAL_STATES = ("SUCCESS", *ERROR_STATES)
IDEMPOTENCY = {"idempotent", "at-least-once", "unsafe"}
EXAMPLE_KEYS = ("success", "invalid_input", "unauthorized_caller", "dependency", "timeout", "partial")
REQUIRED_FIELDS = (
    "name",
    "owner",
    "purpose",
    "input_schema",
    "output_schema",
    "errors",
    "timeout_s",
    "idempotency",
    "permitted_callers",
    "entrypoint",
    "handler",
    "examples",
)


def load_document(path: Path | None = None) -> dict:
    return json.loads(Path(path or AGENTS_FILE).read_text(encoding="utf-8"))


def iter_entries(data: dict):
    """Yield (container, tool) for lane tools then shared tools. container is an agent id or 'shared'."""
    for agent in data.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        tools = agent.get("registered_tools") or []
        if isinstance(tools, list):
            for tool in tools:
                yield str(agent.get("id")), tool
    shared = data.get("shared_tools") or []
    if isinstance(shared, list):
        for tool in shared:
            yield "shared", tool


def validate_registry(path: Path | None = None, *, root: Path | None = None) -> list[str]:
    """Actionable registration errors. Empty means every contract may be called."""
    root = root or ROOT
    try:
        data = load_document(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"agents.json: cannot read registry ({exc})"]
    errors: list[str] = []
    if data.get("tool_contract") != CONTRACT:
        errors.append(f"agents.json: tool_contract must be {CONTRACT!r}")
    errors.extend(_schema_file_errors())
    if "shared_tools" not in data or not isinstance(data.get("shared_tools"), list):
        errors.append("agents.json: shared_tools must be a list (empty until the shared pool exists)")
    agents = data.get("agents")
    if not isinstance(agents, list):
        return errors + ["agents.json: agents must be a list"]
    agent_ids = {a.get("id") for a in agents if isinstance(a, dict)}
    for agent in agents:
        if isinstance(agent, dict) and "registered_tools" in agent and not isinstance(agent["registered_tools"], list):
            errors.append(f"agent {agent.get('id')}: registered_tools must be a list")

    seen: dict[str, str] = {}
    ready: list[tuple[str, dict]] = []
    for container, tool in iter_entries(data):
        if not isinstance(tool, dict):
            errors.append(f"{container}: tool entry must be an object")
            continue
        problems, ok = _check_tool(tool, container, agent_ids, root)
        errors.extend(problems)
        if not ok:
            continue
        name = tool["name"]
        if name in seen:
            errors.append(f"tool {name}: name collides with the entry owned by {seen[name]}")
            continue
        seen[name] = tool["owner"]
        ready.append((container, tool))

    for _container, tool in ready:
        errors.extend(_check_handler(tool, root))
    return errors


def call_tool(name: str, caller: str, payload: object, *, timeout_s: float | None = None, path: Path | None = None) -> dict:
    """Call one registered tool. Unregistered names do not import a handler."""
    if not isinstance(name, str) or not name:
        return {"state": "INVALID_INPUT", "field": "name", "message": "tool name is required"}
    try:
        data = load_document(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "DEPENDENCY_UNAVAILABLE", "message": f"registry unreadable: {exc}"}
    tool = _find(data, name)
    if tool is None:
        return {"state": "INVALID_INPUT", "field": "name", "message": f"tool {name!r} is not registered"}
    if caller not in (tool.get("permitted_callers") or []):
        return {
            "state": "UNAUTHORIZED",
            "caller": caller,
            "target": name,
            "reason": "caller is not permitted",
        }
    if not isinstance(payload, dict):
        return {"state": "INVALID_INPUT", "field": "payload", "message": "payload must be an object"}
    field_errors = _payload_errors(tool.get("input_schema") or {}, payload)
    if field_errors:
        field, message = field_errors[0]
        return {"state": "INVALID_INPUT", "field": field, "message": message}
    limit = tool["timeout_s"] if timeout_s is None else timeout_s
    try:
        fn = _import_handler(tool["handler"])
        raw = _invoke(fn, payload, float(limit))
    except TimeoutError:
        return {"state": "TIMEOUT", "message": f"exceeded {limit}s"}
    except OSError as exc:
        return {"state": "DEPENDENCY_UNAVAILABLE", "message": str(exc)}
    except Exception as exc:
        return {"state": "DEPENDENCY_UNAVAILABLE", "message": f"handler failed: {exc}"}
    return _finish(tool, raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate, call, and generate the agents.json tool registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="exit 1 when any contract is incomplete, colliding, or stubbed")
    call = sub.add_parser("call", help="call one registered tool")
    call.add_argument("--name", required=True)
    call.add_argument("--caller", required=True)
    call.add_argument("--input", default="{}", dest="payload")
    docs = sub.add_parser("docs", help="write docs/tools from the registry")
    docs.add_argument("--check", action="store_true", help="fail when committed docs disagree")
    gen = sub.add_parser("gen-tests", help="write tests/generated/test_tool_contracts.py")
    gen.add_argument("--check", action="store_true", help="fail when the generated test disagrees")
    args = parser.parse_args(argv)

    if args.cmd == "check":
        errors = validate_registry()
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print("tool registry ok")
        return 0
    if args.cmd == "call":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"invalid --input JSON: {exc}", file=sys.stderr)
            return 2
        result = call_tool(args.name, args.caller, payload)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("state") in ("SUCCESS", "PARTIAL_SUCCESS") else 1
    if args.cmd == "docs":
        from swarm.tool_generate import write_docs

        return write_docs(check=args.check)
    from swarm.tool_generate import write_contract_tests

    return write_contract_tests(check=args.check)


def _schema_file_errors() -> list[str]:
    try:
        required = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))["required"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return [f"tool.contract.v1.json: unreadable ({exc})"]
    if list(required) != list(REQUIRED_FIELDS):
        return ["tool.contract.v1.json: required fields drifted from the checker"]
    return []


def _find(data: dict, name: str) -> dict | None:
    for _container, tool in iter_entries(data):
        if isinstance(tool, dict) and tool.get("name") == name:
            return tool
    return None


def _check_tool(tool: dict, container: str, agent_ids: set, root: Path) -> tuple[list[str], bool]:
    name = tool.get("name") if isinstance(tool.get("name"), str) else "?"
    owner = tool.get("owner") if isinstance(tool.get("owner"), str) else "?"
    where = f"tool {name} (owner {owner})"
    errors: list[str] = []
    missing = [key for key in REQUIRED_FIELDS if key not in tool]
    if missing:
        errors.append(f"{where}: missing {', '.join(missing)}")
    if tool.get("stub") is True:
        errors.append(f"{where}: stub contracts cannot be registered")
    if owner != container:
        errors.append(f"{where}: owner must be {container!r}, the agent (or shared list) that declares it")
    prefix = PREFIX.get(owner if isinstance(owner, str) else "")
    if prefix is None:
        errors.append(f"{where}: owner must be one of {', '.join(PREFIX)}")
    elif not isinstance(name, str) or not _conventional_name(name) or not name.startswith(prefix):
        errors.append(f"{where}: name must match {prefix}[a-z0-9_]+")
    purpose = tool.get("purpose")
    if "purpose" in tool and (not isinstance(purpose, str) or not purpose.strip() or "|" in purpose or "\n" in purpose):
        errors.append(f"{where}: purpose must be one non-empty line without '|'")
    errors.extend(_check_schemas(where, tool))
    errors.extend(_check_errors(where, tool.get("errors")))
    timeout = tool.get("timeout_s")
    if "timeout_s" in tool and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600):
        errors.append(f"{where}: timeout_s must be a number in (0, 3600]")
    if "idempotency" in tool and tool.get("idempotency") not in IDEMPOTENCY:
        errors.append(f"{where}: idempotency must be one of {', '.join(sorted(IDEMPOTENCY))}")
    callers = tool.get("permitted_callers")
    if "permitted_callers" in tool:
        if not isinstance(callers, list) or not callers or not all(isinstance(c, str) and c in agent_ids for c in callers):
            errors.append(f"{where}: permitted_callers must be a non-empty list of agent ids")
        elif len(callers) != len(set(callers)):
            errors.append(f"{where}: permitted_callers contains a duplicate")
    errors.extend(_check_entrypoint(where, tool.get("entrypoint"), tool.get("handler"), root))
    if "examples" in tool and not errors:
        errors.extend(_check_examples(where, tool, agent_ids))
    structural = bool(errors) or bool(missing) or tool.get("stub") is True
    return errors, not structural


def _conventional_name(name: str) -> bool:
    return bool(name) and name[0].islower() and all(ch.islower() or ch.isdigit() or ch == "_" for ch in name)


def _check_schemas(where: str, tool: dict) -> list[str]:
    errors = []
    for key in ("input_schema", "output_schema"):
        schema = tool.get(key)
        if key not in tool:
            continue
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append(f"{where}: {key} must be a JSON schema object with type 'object'")
    return errors


def _check_errors(where: str, errors_field) -> list[str]:
    if not isinstance(errors_field, list):
        return [] if errors_field is None else [f"{where}: errors must list {', '.join(ERROR_STATES)}"]
    if set(errors_field) != set(ERROR_STATES) or len(errors_field) != len(ERROR_STATES):
        return [f"{where}: errors must be exactly {', '.join(ERROR_STATES)}"]
    return []


def _check_entrypoint(where: str, entrypoint, handler, root: Path) -> list[str]:
    errors = []
    if isinstance(entrypoint, str):
        path = Path(entrypoint)
        if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
            errors.append(f"{where}: entrypoint {entrypoint!r} must be a file inside the repo")
        elif isinstance(handler, str) and _handler_file(handler) != path.as_posix():
            errors.append(f"{where}: handler {handler!r} must point at entrypoint {entrypoint!r}")
    if isinstance(handler, str) and _handler_file(handler) is None:
        errors.append(f"{where}: handler must look like swarm.tools.<module>:<function>")
    return errors


def _handler_file(handler: str) -> str | None:
    module, sep, func = handler.partition(":")
    if not sep or not module.startswith("swarm.tools.") or module.count(".") != 2:
        return None
    leaf = module.rsplit(".", 1)[1]
    if not _conventional_name(leaf) or not func or not _conventional_name(func):
        return None
    return f"swarm/tools/{leaf}.py"


def _check_handler(tool: dict, root: Path) -> list[str]:
    where = f"tool {tool.get('name')} (owner {tool.get('owner')})"
    try:
        _import_handler(tool["handler"])
    except Exception as exc:
        return [f"{where}: handler {tool.get('handler')!r} cannot be imported ({exc})"]
    expected = root / tool["entrypoint"]
    module = tool["handler"].split(":", 1)[0]
    found = Path(importlib.import_module(module).__file__ or "")
    if found.resolve() != expected.resolve():
        return [f"{where}: handler file {found} is not entrypoint {expected}"]
    return []


def _check_examples(where: str, tool: dict, agent_ids: set) -> list[str]:
    examples = tool.get("examples")
    if not isinstance(examples, dict):
        return [f"{where}: examples must be an object with {', '.join(EXAMPLE_KEYS)}"]
    errors = []
    extra = set(examples) - set(EXAMPLE_KEYS)
    missing = [key for key in EXAMPLE_KEYS if key not in examples]
    if extra or missing:
        errors.append(f"{where}: examples must contain exactly {', '.join(EXAMPLE_KEYS)}")
        return errors
    schema = tool["input_schema"]
    for key in ("success", "dependency", "timeout", "partial"):
        payload = examples[key]
        if not isinstance(payload, dict) or _payload_errors(schema, payload):
            errors.append(f"{where}: examples.{key} must satisfy input_schema")
    invalid = examples["invalid_input"]
    if not isinstance(invalid, dict) or not _payload_errors(schema, invalid):
        errors.append(f"{where}: examples.invalid_input must be an object that fails input_schema")
    caller = examples["unauthorized_caller"]
    if not isinstance(caller, str) or caller not in agent_ids or caller in tool["permitted_callers"]:
        errors.append(f"{where}: examples.unauthorized_caller must be an agent id that is not permitted")
    return errors


def _payload_errors(schema: dict, payload: dict) -> list[tuple[str, str]]:
    if schema.get("type") not in (None, "object"):
        return [("payload", "input_schema type must be object")]
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return [("payload", "input_schema properties must be an object")]
    errors: list[tuple[str, str]] = []
    required = schema.get("required") or []
    for key in required:
        if key not in payload:
            errors.append((str(key), f"missing required field {key}"))
    if schema.get("additionalProperties") is False:
        for key in payload:
            if key not in props:
                errors.append((str(key), f"unexpected field {key}"))
    for key, value in payload.items():
        if key not in props or not isinstance(props[key], dict):
            continue
        message = _value_error(props[key], value)
        if message:
            errors.append((str(key), message))
    return errors


def _value_error(spec: dict, value) -> str | None:
    expected = spec.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return "expected string"
        if "minLength" in spec and len(value) < spec["minLength"]:
            return f"shorter than {spec['minLength']}"
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            return f"longer than {spec['maxLength']}"
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return "expected integer"
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "expected number"
    elif expected == "boolean":
        if not isinstance(value, bool):
            return "expected boolean"
    elif expected == "object":
        if not isinstance(value, dict):
            return "expected object"
    elif expected == "array":
        if not isinstance(value, list):
            return "expected array"
    if "const" in spec and value != spec["const"]:
        return f"expected {spec['const']!r}"
    if "enum" in spec and value not in spec["enum"]:
        return f"expected one of {spec['enum']!r}"
    return None


def _import_handler(handler: str):
    module_name, func_name = handler.split(":", 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, func_name)
    if not callable(fn):
        raise TypeError(f"{handler} is not callable")
    return fn


def _invoke(fn, payload: dict, timeout_s: float):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, payload)
    try:
        return future.result(timeout=timeout_s)
    except FuturesTimeout as exc:
        raise TimeoutError(str(exc)) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _finish(tool: dict, raw) -> dict:
    if not isinstance(raw, dict) or raw.get("state") not in TERMINAL_STATES:
        return {"state": "DEPENDENCY_UNAVAILABLE", "message": "handler did not return a terminal state"}
    state = raw["state"]
    if state == "PARTIAL_SUCCESS":
        fraction = raw.get("completed_fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
            return {"state": "DEPENDENCY_UNAVAILABLE", "message": "PARTIAL_SUCCESS requires completed_fraction in [0, 1]"}
        return raw
    if state != "SUCCESS":
        return raw
    output_errors = _payload_errors(tool.get("output_schema") or {}, raw)
    if output_errors:
        field, message = output_errors[0]
        return {"state": "DEPENDENCY_UNAVAILABLE", "message": f"SUCCESS payload does not match output_schema ({field}: {message})"}
    return raw


if __name__ == "__main__":
    sys.exit(main())
