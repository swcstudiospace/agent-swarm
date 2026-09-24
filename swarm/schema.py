"""Stdlib-only validator for the Draft 2020-12 subset used by swarm/schemas/*.json.

Supported keywords: type, required, enum, properties, items, additionalProperties.
Schemas are read-only at runtime; nothing in the swarm writes them.
"""
from __future__ import annotations
import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def validate(obj, schema: dict, path: str = "") -> list[str]:
    """Return a list of "<json-pointer>: message" errors; empty means valid."""
    where = path or "/"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](obj) for t in types):
            return [f"{where}: expected {'|'.join(types)}, got {type(obj).__name__}"]
    errors: list[str] = []
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{where}: {obj!r} not one of {schema['enum']}")
    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                errors.append(f"{path}/{key}: required")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, val in obj.items():
            if key in props:
                errors += validate(val, props[key], f"{path}/{key}")
            elif extra is False:
                errors.append(f"{path}/{key}: additional property not allowed")
            elif isinstance(extra, dict):
                errors += validate(val, extra, f"{path}/{key}")
    if isinstance(obj, list) and isinstance(schema.get("items"), dict):
        for i, val in enumerate(obj):
            errors += validate(val, schema["items"], f"{path}/{i}")
    return errors
