"""A07 DATA lane: pure checks over migration SQL, foreign-key edges, field sets, versions, and row counts.

Mission: give the Data Engineer repeatable evidence for migration safety (destructive DDL,
NOT NULL additions), naming, foreign-key ordering, PII tagging, contract compatibility,
row-count reconciliation, migration ordering, and up/down pairing. All tools are READ tier.

Tools:
  data_destructive_ddl_scan     classify destructive DDL statements in SQL text
  data_not_null_default_check   ADD COLUMN NOT NULL or PRIMARY KEY without DEFAULT
  data_index_name_check         CREATE INDEX names against prefix_table_suffix
  data_fk_cycle_check           cycle in supplied foreign-key edges
  data_pii_tag_coverage         share of PII columns carrying an allowed tag
  data_contract_field_compat    breaking vs additive changes between two field sets
  data_row_count_reconcile      before + delta against observed after
  data_migration_version_order  unique and strictly increasing version ids
  data_reversible_pair_check    up/down and Flyway V/U file pairing by name

SQL is split on ';' after comment removal; string literals containing ';' are not parsed.
"""
from __future__ import annotations

import re

from swarm.tools.support import respond

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _statements(sql: str) -> list[str]:
    cleaned = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))
    return [" ".join(part.split()) for part in cleaned.split(";") if part.strip()]


def _no_statements() -> dict:
    return {"state": "INVALID_INPUT", "field": "sql", "message": "no SQL statements after removing comments"}


def _top_level_parts(body: str) -> list[str]:
    """Split on commas that sit outside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


# --- data_destructive_ddl_scan -------------------------------------------------------

_DESTRUCTIVE = (
    ("DROP_TABLE", re.compile(r"^DROP\s+TABLE\b")),
    ("DROP_SCHEMA", re.compile(r"^DROP\s+SCHEMA\b")),
    ("DROP_DATABASE", re.compile(r"^DROP\s+DATABASE\b")),
    ("TRUNCATE", re.compile(r"^TRUNCATE\b")),
    (
        "DROP_COLUMN",
        re.compile(
            r"^ALTER\s+TABLE\b.*\bDROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?"
            r"(?!(?:CONSTRAINT|DEFAULT|NOT\s+NULL|IDENTITY|EXPRESSION|PRIMARY|FOREIGN|UNIQUE|CHECK|INDEX|EXCLUDE)\b)\w"
        ),
    ),
    (
        "COLUMN_TYPE_CHANGE",
        re.compile(r"^ALTER\s+TABLE\b.*\bALTER\s+(?:COLUMN\s+)?\S+\s+(?:SET\s+DATA\s+)?TYPE\b"),
    ),
)


def _destructive_kinds(statement: str) -> list[str]:
    upper = statement.upper()
    return [kind for kind, pattern in _DESTRUCTIVE if pattern.search(upper)]


def data_destructive_ddl_scan(payload: dict) -> dict:
    def work() -> dict:
        statements = _statements(payload["sql"])
        if not statements:
            return _no_statements()
        kinds: set[str] = set()
        flagged = 0
        for statement in statements:
            found = _destructive_kinds(statement)
            if found:
                flagged += 1
                kinds.update(found)
        return {
            "state": "SUCCESS",
            "destructive": flagged > 0,
            "statement_count": len(statements),
            "destructive_count": flagged,
            "kinds": sorted(kinds),
        }

    return respond(payload, work)


# --- data_not_null_default_check -----------------------------------------------------

_ALTER_TABLE = re.compile(r'^ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?([\w."]+)\s+(.*)$', re.I)
_ADD_COLUMN = re.compile(r'^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?([\w"]+)\s+(.*)$', re.I)
_NOT_COLUMN_WORDS = frozenset({"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "EXCLUDE", "INDEX", "KEY"})
_NEEDS_VALUE = re.compile(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", re.I)
_HAS_DEFAULT = re.compile(r"\bDEFAULT\b", re.I)


def data_not_null_default_check(payload: dict) -> dict:
    def work() -> dict:
        statements = _statements(payload["sql"])
        if not statements:
            return _no_statements()
        checked = 0
        unsafe: list[str] = []
        for statement in statements:
            alter = _ALTER_TABLE.match(statement)
            if not alter:
                continue
            table = alter.group(1).replace('"', "")
            for part in _top_level_parts(alter.group(2)):
                add = _ADD_COLUMN.match(part)
                if not add:
                    continue
                column = add.group(1).replace('"', "")
                if column.upper() in _NOT_COLUMN_WORDS:
                    continue
                checked += 1
                definition = add.group(2)
                if _NEEDS_VALUE.search(definition) and not _HAS_DEFAULT.search(definition):
                    unsafe.append(f"{table}.{column}")
        return {
            "state": "SUCCESS",
            "checked_columns": checked,
            "unsafe_columns": unsafe,
            "unsafe_count": len(unsafe),
            "safe": not unsafe,
        }

    return respond(payload, work)


# --- data_index_name_check -----------------------------------------------------------

_CREATE_INDEX = re.compile(
    r'^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?([\w"]+)\s+ON\s+(?:ONLY\s+)?([\w."]+)',
    re.I,
)
_ANY_INDEX = re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b", re.I)
_NOT_INDEX_NAMES = frozenset({"CONCURRENTLY", "IF", "ON", "ONLY"})
_PREFIX = re.compile(r"[a-z][a-z0-9]{0,15}")
_SNAKE = re.compile(r"[a-z0-9_]+")
_MAX_IDENTIFIER = 63


def data_index_name_check(payload: dict) -> dict:
    def work() -> dict:
        prefix = payload["prefix"]
        if not _PREFIX.fullmatch(prefix):
            return {
                "state": "INVALID_INPUT",
                "field": "prefix",
                "message": "prefix must start with a lowercase letter and hold at most 16 lowercase letters or digits",
            }
        statements = _statements(payload["sql"])
        if not statements:
            return _no_statements()
        indexes = [statement for statement in statements if _ANY_INDEX.match(statement)]
        violations: list[str] = []
        for statement in indexes:
            named = _CREATE_INDEX.match(statement)
            if not named or named.group(1).upper() in _NOT_INDEX_NAMES:
                violations.append("unnamed index: a name is required")
                continue
            name = named.group(1).replace('"', "")
            table = named.group(2).replace('"', "").rsplit(".", 1)[-1]
            reasons: list[str] = []
            if not _SNAKE.fullmatch(name):
                reasons.append("must be lowercase letters, digits and underscores")
            if len(name) > _MAX_IDENTIFIER:
                reasons.append(f"longer than {_MAX_IDENTIFIER} characters")
            stem = f"{prefix}_{table}_"
            if not name.startswith(stem) or len(name) == len(stem):
                reasons.append(f"must start with {stem}")
            if reasons:
                violations.append(f"{name}: {'; '.join(reasons)}")
        return {
            "state": "SUCCESS",
            "index_count": len(indexes),
            "violations": violations,
            "violation_count": len(violations),
            "conforming": not violations,
        }

    return respond(payload, work)


# --- data_fk_cycle_check -------------------------------------------------------------


def _find_cycle(graph: dict[str, set[str]]) -> list[str]:
    """Depth-first search over sorted nodes. Returns one cycle with its start repeated at the end, or []."""
    unseen, active, done = 0, 1, 2
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = active
        path.append(node)
        for nxt in sorted(graph[node]):
            seen = state.get(nxt, unseen)
            if seen == active:
                return path[path.index(nxt):] + [nxt]
            if seen == unseen:
                found = visit(nxt)
                if found:
                    return found
        path.pop()
        state[node] = done
        return []

    for node in sorted(graph):
        if state.get(node, unseen) == unseen:
            found = visit(node)
            if found:
                return found
    return []


def data_fk_cycle_check(payload: dict) -> dict:
    def work() -> dict:
        edges = payload["edges"]
        allow_self = payload.get("allow_self_reference", False)
        nodes = {edge["from"] for edge in edges} | {edge["to"] for edge in edges}
        graph: dict[str, set[str]] = {node: set() for node in nodes}
        for edge in edges:
            if edge["from"] != edge["to"]:
                graph[edge["from"]].add(edge["to"])
        self_refs = sorted({edge["from"] for edge in edges if edge["from"] == edge["to"]})
        if self_refs and not allow_self:
            cycle = [self_refs[0], self_refs[0]]
        else:
            cycle = _find_cycle(graph)
        return {
            "state": "SUCCESS",
            "edge_count": len(edges),
            "table_count": len(nodes),
            "has_cycle": bool(cycle),
            "cycle": cycle,
        }

    return respond(payload, work)


# --- data_pii_tag_coverage -----------------------------------------------------------


def data_pii_tag_coverage(payload: dict) -> dict:
    def work() -> dict:
        columns = payload["columns"]
        names = [column["name"] for column in columns]
        if len(set(names)) != len(names):
            return {"state": "INVALID_INPUT", "field": "columns", "message": "column names must be unique"}
        allowed = set(payload["allowed_tags"])
        pii = [column for column in columns if column["pii"]]
        untagged = sorted(column["name"] for column in pii if column["tag"].strip() not in allowed)
        tagged = len(pii) - len(untagged)
        coverage = round(tagged / len(pii), 4) if pii else 1.0
        return {
            "state": "SUCCESS",
            "column_count": len(columns),
            "pii_columns": len(pii),
            "tagged_pii_columns": tagged,
            "untagged": untagged,
            "coverage": coverage,
        }

    return respond(payload, work)


# --- data_contract_field_compat ------------------------------------------------------


def data_contract_field_compat(payload: dict) -> dict:
    def work() -> dict:
        indexes: dict[str, dict[str, dict]] = {}
        for label in ("before", "after"):
            index = {field["name"]: field for field in payload[label]}
            if len(index) != len(payload[label]):
                return {"state": "INVALID_INPUT", "field": label, "message": "field names must be unique"}
            indexes[label] = index
        old, new = indexes["before"], indexes["after"]
        breaking: list[str] = []
        additive: list[str] = []
        for name in sorted(old.keys() | new.keys()):
            if name not in new:
                breaking.append(f"{name}: removed")
            elif name not in old:
                if new[name]["required"]:
                    breaking.append(f"{name}: new required field")
                else:
                    additive.append(f"{name}: new optional field")
            else:
                was, now = old[name], new[name]
                if was["type"] != now["type"]:
                    breaking.append(f"{name}: type {was['type']} -> {now['type']}")
                if not was["required"] and now["required"]:
                    breaking.append(f"{name}: became required")
                elif was["required"] and not now["required"]:
                    additive.append(f"{name}: relaxed to optional")
        return {
            "state": "SUCCESS",
            "compatible": not breaking,
            "breaking": breaking,
            "additive": additive,
            "breaking_count": len(breaking),
            "additive_count": len(additive),
        }

    return respond(payload, work)


# --- data_row_count_reconcile --------------------------------------------------------


def data_row_count_reconcile(payload: dict) -> dict:
    def work() -> dict:
        before, after, delta = payload["before"], payload["after"], payload["delta"]
        expected = before + delta
        if expected < 0:
            return {"state": "INVALID_INPUT", "field": "delta", "message": "delta would make the row count negative"}
        drift = after - expected
        return {
            "state": "SUCCESS",
            "expected_after": expected,
            "observed_change": after - before,
            "drift": drift,
            "reconciled": drift == 0,
        }

    return respond(payload, work)


# --- data_migration_version_order ----------------------------------------------------

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _version_key(version: str) -> tuple:
    """Natural order: digit runs compare as integers, other runs as text."""
    return tuple(
        (0, int(token), "") if token.isdigit() else (1, 0, token)
        for token in re.findall(r"\d+|\D+", version)
    )


def data_migration_version_order(payload: dict) -> dict:
    def work() -> dict:
        versions = payload["versions"]
        for version in versions:
            if not _VERSION.fullmatch(version):
                return {
                    "state": "INVALID_INPUT",
                    "field": "versions",
                    "message": f"invalid version id {version!r}; use letters, digits, '.', '_' or '-'",
                }
        duplicates: list[str] = []
        out_of_order: list[str] = []
        seen: set[tuple] = set()
        highest: tuple | None = None
        for version in versions:
            key = _version_key(version)
            if key in seen:
                duplicates.append(version)
            elif highest is not None and key < highest:
                out_of_order.append(version)
            seen.add(key)
            highest = key if highest is None else max(highest, key)
        return {
            "state": "SUCCESS",
            "count": len(versions),
            "ordered": not duplicates and not out_of_order,
            "duplicates": duplicates,
            "out_of_order": out_of_order,
            "latest": max(versions, key=_version_key),
        }

    return respond(payload, work)


# --- data_reversible_pair_check ------------------------------------------------------

_UP_DOWN = re.compile(r"(.+)\.(up|down)\.sql")
_FLYWAY = re.compile(r"([VU])([0-9][^_]*)__(.+)\.sql")


def _pair_key(filename: str) -> tuple[str, str, str] | None:
    """Return (direction, family, display) for a recognised migration file, else None."""
    name = filename.rsplit("/", 1)[-1]
    up_down = _UP_DOWN.fullmatch(name)
    if up_down:
        direction = "forward" if up_down.group(2) == "up" else "reverse"
        return direction, "up-down", up_down.group(1)
    flyway = _FLYWAY.fullmatch(name)
    if flyway:
        direction = "forward" if flyway.group(1) == "V" else "reverse"
        return direction, "flyway", f"V{flyway.group(2)}__{flyway.group(3)}"
    return None


def data_reversible_pair_check(payload: dict) -> dict:
    def work() -> dict:
        filenames = payload["filenames"]
        forward: set[tuple[str, str]] = set()
        reverse: set[tuple[str, str]] = set()
        unrecognized: list[str] = []
        for filename in filenames:
            parsed = _pair_key(filename)
            if parsed is None:
                unrecognized.append(filename)
                continue
            direction, family, display = parsed
            (forward if direction == "forward" else reverse).add((family, display))
        return {
            "state": "SUCCESS",
            "file_count": len(filenames),
            "recognized_count": len(forward) + len(reverse),
            "paired": sorted(key[1] for key in forward & reverse),
            "missing_reverse": sorted(key[1] for key in forward - reverse),
            "orphan_reverse": sorted(key[1] for key in reverse - forward),
            "unrecognized": sorted(unrecognized),
            "ok": forward == reverse,
        }

    return respond(payload, work)
