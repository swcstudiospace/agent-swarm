"""Behaviour of the v2.1 A07 data lane tools that had only generated contract probes.

Covers data_down_present, data_destructive_ddl_scan, data_not_null_default_check, data_index_name_check,
data_fk_cycle_check, data_pii_tag_coverage, data_contract_field_compat and data_migration_version_order, called through
swarm.tool_registry.call_tool as A07. Rules come from the handlers (swarm/tools/a07_tools.py,
swarm/tools/batch_06_10.py, scripts/data_migration_check.py) and prompts/A07-data.md.
"""
from __future__ import annotations

import pytest

from swarm import tool_registry


def run(name: str, payload: dict, caller: str = "A07") -> dict:
    return tool_registry.call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result


TOOLS = (
    "data_down_present",
    "data_destructive_ddl_scan",
    "data_not_null_default_check",
    "data_index_name_check",
    "data_fk_cycle_check",
    "data_pii_tag_coverage",
    "data_contract_field_compat",
    "data_migration_version_order",
)

SAMPLE = {
    "data_down_present": {"sql": "def downgrade():\n    pass"},
    "data_destructive_ddl_scan": {"sql": "DROP TABLE t"},
    "data_not_null_default_check": {"sql": "ALTER TABLE t ADD COLUMN a int"},
    "data_index_name_check": {"sql": "CREATE INDEX idx_t_a ON t (a)", "prefix": "idx"},
    "data_fk_cycle_check": {"edges": [{"from": "a", "to": "b"}]},
    "data_pii_tag_coverage": {"columns": [{"name": "a", "pii": True, "tag": "x"}], "allowed_tags": ["x"]},
    "data_contract_field_compat": {"before": [], "after": []},
    "data_migration_version_order": {"versions": ["1"]},
}


@pytest.mark.parametrize("name", TOOLS)
def test_only_a07_may_call_and_unknown_fields_are_rejected(name):
    assert run(name, SAMPLE[name], "A01")["state"] == "UNAUTHORIZED"
    assert run(name, SAMPLE[name])["state"] == "SUCCESS"
    bad = run(name, {**SAMPLE[name], "surprise": 1})
    invalid(bad, "surprise")


@pytest.mark.parametrize("name", TOOLS)
def test_probes_follow_the_shared_contract(name):
    assert run(name, {**SAMPLE[name], "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert run(name, {**SAMPLE[name], "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    assert run(name, {**SAMPLE[name], "probe": "sleep"})["state"] == "TIMEOUT"


# --- data_down_present ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "def downgrade():\n    op.drop_table('t')",  # alembic
        "def down(self):\n    pass",
        "exports.down = function () {}",
        "export async function down(knex) {}",
        "-- migrate:up\nCREATE TABLE t (id int);\n-- migrate:down\nDROP TABLE t;",
        "-- +goose Up\nCREATE TABLE t (id int);\n-- +goose Down\nDROP TABLE t;",
        "operations = [migrations.RunSQL('CREATE TABLE t (id int)', reverse_sql='DROP TABLE t')]",
        "module.exports = { up: async () => {}, down: async () => {} }",
    ],
)
def test_down_present_recognises_down_markers(sql):
    assert run("data_down_present", {"sql": sql}) == {"state": "SUCCESS", "reversible": True}


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (id int);",
        "ALTER TABLE t ADD COLUMN a int;",
        "def upgrade():\n    op.add_column('t', sa.Column('a', sa.Integer))",
        "-- a countdown comment, nothing about direction\nSELECT 1;",
        "SELECT breakdown, markdown FROM t;",
    ],
)
def test_down_present_is_false_without_a_marker(sql):
    assert run("data_down_present", {"sql": sql}) == {"state": "SUCCESS", "reversible": False}


def test_down_present_marker_matching_is_case_insensitive():
    assert run("data_down_present", {"sql": "DEF DOWNGRADE():\n    pass"})["reversible"] is True
    assert run("data_down_present", {"sql": "-- MIGRATE:DOWN\nDROP TABLE t;"})["reversible"] is True


def test_down_present_never_looks_at_the_filesystem():
    """The handler passes the fixed name migration.py, so pair files next to the cwd cannot make a migration reversible."""
    assert run("data_down_present", {"sql": "CREATE TABLE t (id int);"})["reversible"] is False


def test_down_present_rejects_empty_and_missing_sql():
    invalid(run("data_down_present", {"sql": ""}), "sql")
    invalid(run("data_down_present", {}), "sql")
    invalid(run("data_down_present", {"sql": 5}), "sql")


def test_down_present_handles_large_input():
    big = "SELECT 1;\n" * 200_000 + "-- migrate:down\n"
    assert run("data_down_present", {"sql": big})["reversible"] is True
    assert run("data_down_present", {"sql": "SELECT 1;\n" * 200_000})["reversible"] is False


# --- data_destructive_ddl_scan -------------------------------------------------------


def scan(sql: str) -> dict:
    return run("data_destructive_ddl_scan", {"sql": sql})


def test_destructive_scan_success_shape_for_drop_table_among_safe_statements():
    result = scan("CREATE TABLE a (id int); DROP TABLE b; SELECT 1;")
    assert result == {
        "state": "SUCCESS",
        "destructive": True,
        "statement_count": 3,
        "destructive_count": 1,
        "kinds": ["DROP_TABLE"],
    }


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("DROP TABLE users", "DROP_TABLE"),
        ("drop table if exists users cascade", "DROP_TABLE"),
        ("DROP SCHEMA reporting CASCADE", "DROP_SCHEMA"),
        ("DROP DATABASE prod", "DROP_DATABASE"),
        ("TRUNCATE TABLE orders", "TRUNCATE"),
        ("truncate orders", "TRUNCATE"),
        ("ALTER TABLE orders DROP COLUMN note", "DROP_COLUMN"),
        ("ALTER TABLE orders DROP note", "DROP_COLUMN"),
        ("ALTER TABLE orders DROP COLUMN IF EXISTS note", "DROP_COLUMN"),
        ("ALTER TABLE orders ALTER COLUMN total TYPE bigint", "COLUMN_TYPE_CHANGE"),
        ("ALTER TABLE orders ALTER total SET DATA TYPE numeric(12,2)", "COLUMN_TYPE_CHANGE"),
    ],
)
def test_destructive_scan_classifies_each_destructive_kind(sql, kind):
    result = scan(sql)
    assert result["destructive"] is True
    assert result["kinds"] == [kind]
    assert result["destructive_count"] == 1 and result["statement_count"] == 1


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (id int)",
        "ALTER TABLE t ADD COLUMN a int",
        "ALTER TABLE t DROP CONSTRAINT fk_t_u",
        "ALTER TABLE t ALTER COLUMN a DROP DEFAULT",
        "ALTER TABLE t ALTER COLUMN a DROP NOT NULL",
        "ALTER TABLE t ALTER COLUMN a SET DEFAULT 0",
        "ALTER TABLE t RENAME COLUMN a TO b",
        "DROP INDEX idx_t_a",
        "DROP VIEW v",
        "DELETE FROM t WHERE id = 1",
        "SELECT 'a' FROM t",
    ],
)
def test_destructive_scan_leaves_non_destructive_statements_alone(sql):
    result = scan(sql)
    assert result["destructive"] is False and result["kinds"] == [] and result["destructive_count"] == 0
    assert result["statement_count"] == 1


def test_destructive_scan_counts_statements_and_reports_sorted_distinct_kinds():
    sql = "DROP TABLE a; DROP TABLE b; TRUNCATE c; ALTER TABLE d DROP COLUMN x; SELECT 1"
    result = scan(sql)
    assert result["statement_count"] == 5 and result["destructive_count"] == 4
    assert result["kinds"] == ["DROP_COLUMN", "DROP_TABLE", "TRUNCATE"]


def test_destructive_scan_one_statement_with_two_kinds_counts_once():
    result = scan("ALTER TABLE t DROP COLUMN a, ALTER COLUMN b TYPE text")
    assert result["destructive_count"] == 1
    assert result["kinds"] == ["COLUMN_TYPE_CHANGE", "DROP_COLUMN"]


def test_destructive_scan_ignores_commented_out_ddl_and_counts_only_real_statements():
    sql = "-- DROP TABLE users;\n/* TRUNCATE t; */\nSELECT 1;"
    result = scan(sql)
    assert result["destructive"] is False and result["statement_count"] == 1


def test_destructive_scan_trailing_semicolon_and_blank_statements_are_not_counted():
    result = scan(";; DROP TABLE a ;;\n;")
    assert result["statement_count"] == 1 and result["destructive_count"] == 1


def test_destructive_scan_whitespace_and_newlines_inside_a_statement_are_normalised():
    result = scan("ALTER\n  TABLE   t\n\tDROP\n COLUMN a")
    assert result["kinds"] == ["DROP_COLUMN"]


def test_destructive_scan_only_comments_is_invalid_and_names_the_field():
    invalid(scan("-- nothing here"), "sql")
    invalid(scan("/* nothing */ ;  ; "), "sql")
    invalid(scan("   \n "), "sql")


def test_destructive_scan_empty_missing_and_oversized_sql_are_rejected_by_the_schema():
    invalid(scan(""), "sql")
    invalid(run("data_destructive_ddl_scan", {}), "sql")
    invalid(scan("x" * 1_000_001), "sql")


def test_destructive_scan_handles_many_statements():
    sql = "SELECT 1;\n" * 20_000 + "DROP SCHEMA s;"
    result = scan(sql)
    assert result["statement_count"] == 20_001 and result["kinds"] == ["DROP_SCHEMA"]


def test_destructive_scan_quoted_column_drop_with_the_COLUMN_keyword_is_flagged():
    assert scan('ALTER TABLE t DROP COLUMN "Email"')["kinds"] == ["DROP_COLUMN"]


def test_destructive_scan_quoted_column_drop_without_the_COLUMN_keyword_is_flagged():
    """`ALTER TABLE t DROP "Email"` is valid Postgres (COLUMN is optional) and drops a column."""
    assert scan('ALTER TABLE t DROP "Email"')["kinds"] == ["DROP_COLUMN"]
    assert scan('ALTER TABLE t DROP CONSTRAINT x')["kinds"] == []
    assert scan('ALTER TABLE t DROP CONSTRAINT "x"')["kinds"] == []
    assert scan('DROP INDEX idx')["kinds"] == []
    assert scan('ALTER TABLE t DROP INDEX idx')["kinds"] == []


# --- data_not_null_default_check -----------------------------------------------------


def nn(sql: str) -> dict:
    return run("data_not_null_default_check", {"sql": sql})


def test_not_null_check_flags_not_null_without_default():
    assert nn("ALTER TABLE orders ADD COLUMN status text NOT NULL;") == {
        "state": "SUCCESS",
        "checked_columns": 1,
        "unsafe_columns": ["orders.status"],
        "unsafe_count": 1,
        "safe": False,
    }


def test_not_null_check_accepts_default_nullable_and_missing_column_keyword():
    sql = (
        "ALTER TABLE orders ADD COLUMN a int NOT NULL DEFAULT 0;"
        "ALTER TABLE orders ADD COLUMN b int;"
        "ALTER TABLE orders ADD c int DEFAULT 1;"
    )
    result = nn(sql)
    assert result["checked_columns"] == 3 and result["unsafe_columns"] == [] and result["safe"] is True


def test_not_null_check_primary_key_column_needs_a_default_too():
    assert nn("ALTER TABLE t ADD COLUMN id bigint PRIMARY KEY")["unsafe_columns"] == ["t.id"]
    assert nn("ALTER TABLE t ADD COLUMN id bigint PRIMARY KEY DEFAULT 1")["unsafe_columns"] == []


def test_not_null_check_multi_action_alter_reports_each_unsafe_column_with_its_table():
    sql = "ALTER TABLE public.users ADD COLUMN a int NOT NULL, ADD COLUMN b numeric(10,2) NOT NULL DEFAULT 0, ADD COLUMN c text NOT NULL;"
    result = nn(sql)
    assert result["checked_columns"] == 3
    assert result["unsafe_columns"] == ["public.users.a", "public.users.c"]
    assert result["unsafe_count"] == 2


def test_not_null_check_commas_inside_parentheses_do_not_split_a_column():
    result = nn("ALTER TABLE t ADD COLUMN price numeric(10, 2) NOT NULL DEFAULT 0")
    assert result["checked_columns"] == 1 and result["safe"] is True


def test_not_null_check_skips_table_constraints_and_non_add_actions():
    sql = (
        "ALTER TABLE t ADD CONSTRAINT ck CHECK (a > 0), ADD PRIMARY KEY (id), ADD UNIQUE (a), "
        "ADD FOREIGN KEY (b) REFERENCES u (id), ALTER COLUMN a SET NOT NULL, DROP COLUMN z;"
    )
    result = nn(sql)
    assert result["checked_columns"] == 0 and result["safe"] is True


def test_not_null_check_handles_if_exists_only_and_quoted_identifiers():
    result = nn('ALTER TABLE IF EXISTS ONLY "s"."t" ADD COLUMN IF NOT EXISTS "col" int NOT NULL;')
    assert result["checked_columns"] == 1
    assert result["unsafe_columns"] == ["s.t.col"]


def test_not_null_check_non_alter_statements_are_not_counted_but_sql_must_have_a_statement():
    result = nn("CREATE TABLE t (a int NOT NULL); SELECT 1;")
    assert result["checked_columns"] == 0 and result["safe"] is True
    invalid(nn("-- ALTER TABLE t ADD COLUMN a int NOT NULL;"), "sql")


def test_not_null_check_is_case_insensitive():
    assert nn("alter table t add column a int not null")["unsafe_columns"] == ["t.a"]
    assert nn("alter table t add column a int not null default 0")["unsafe_columns"] == []


def test_not_null_check_default_in_another_column_does_not_excuse_this_one():
    result = nn("ALTER TABLE t ADD COLUMN a int NOT NULL, ADD COLUMN b int DEFAULT 1")
    assert result["unsafe_columns"] == ["t.a"]


def test_not_null_check_schema_errors_name_the_field():
    invalid(run("data_not_null_default_check", {"sql": ""}), "sql")
    invalid(run("data_not_null_default_check", {}), "sql")
    invalid(run("data_not_null_default_check", {"sql": "x" * 1_000_001}), "sql")


def test_not_null_check_handles_many_columns():
    cols = ", ".join(f"ADD COLUMN c{i} int NOT NULL" for i in range(2000))
    result = nn(f"ALTER TABLE big {cols}")
    assert result["checked_columns"] == 2000 and result["unsafe_count"] == 2000


def test_not_null_check_string_literal_default_counts_as_a_default():
    result = nn("ALTER TABLE t ADD COLUMN a text NOT NULL DEFAULT 'x'")
    assert result["checked_columns"] == 1 and result["unsafe_columns"] == []


# --- data_index_name_check -----------------------------------------------------------


def idx(sql: str, prefix: str = "idx") -> dict:
    return run("data_index_name_check", {"sql": sql, "prefix": prefix})


def test_index_check_conforming_names_pass():
    result = idx("CREATE INDEX idx_orders_user_id ON orders (user_id); CREATE UNIQUE INDEX idx_users_email ON public.users (email);")
    assert result == {"state": "SUCCESS", "index_count": 2, "violations": [], "violation_count": 0, "conforming": True}


def test_index_check_wrong_prefix_reports_the_expected_stem():
    result = idx("CREATE INDEX orders_user_idx ON orders (user_id)")
    assert result["violations"] == ["orders_user_idx: must start with idx_orders_"]
    assert result["conforming"] is False and result["violation_count"] == 1


def test_index_check_name_must_have_a_suffix_after_the_stem():
    assert idx("CREATE INDEX idx_orders_ ON orders (a)")["violations"] == ["idx_orders_: must start with idx_orders_"]
    assert idx("CREATE INDEX idx_orders_a ON orders (a)")["violations"] == []


def test_index_check_uses_the_table_name_without_schema_for_the_stem():
    assert idx("CREATE INDEX idx_orders_a ON sales.orders (a)")["violations"] == []
    assert idx("CREATE INDEX idx_sales_orders_a ON sales.orders (a)")["violations"] == [
        "idx_sales_orders_a: must start with idx_orders_"
    ]


def test_index_check_uppercase_and_quoted_mixed_case_names_are_not_snake_case():
    result = idx('CREATE INDEX "Idx_Orders_A" ON orders (a)')
    assert result["violations"] == ["Idx_Orders_A: must be lowercase letters, digits and underscores; must start with idx_orders_"]


def test_index_check_name_longer_than_postgres_limit_is_reported():
    name = "idx_orders_" + "a" * 60
    assert len(name) > 63
    result = idx(f"CREATE INDEX {name} ON orders (a)")
    assert result["violations"] == [f"{name}: longer than 63 characters"]
    assert idx("CREATE INDEX idx_orders_" + "a" * (63 - len("idx_orders_")) + " ON orders (a)")["violations"] == []


def test_index_check_unnamed_index_is_a_violation():
    result = idx("CREATE INDEX ON orders (a); CREATE UNIQUE INDEX CONCURRENTLY ON orders (b)")
    assert result["index_count"] == 2
    assert result["violations"] == ["unnamed index: a name is required"] * 2


def test_index_check_accepts_concurrently_if_not_exists_only_and_unique_modifiers():
    sql = (
        "CREATE INDEX CONCURRENTLY idx_t_a ON t (a);"
        "CREATE INDEX IF NOT EXISTS idx_t_b ON ONLY t (b);"
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_t_c ON t (c);"
    )
    result = idx(sql)
    assert result["index_count"] == 3 and result["violations"] == []


def test_index_check_ignores_non_index_statements_and_reports_zero_indexes():
    result = idx("CREATE TABLE t (id int); DROP INDEX idx_t_a;")
    assert result["index_count"] == 0 and result["conforming"] is True


def test_index_check_prefix_is_validated():
    for bad in ("Idx", "1idx", "idx_", "a" * 17, "i-x"):
        result = run("data_index_name_check", {"sql": "CREATE INDEX idx_t_a ON t (a)", "prefix": bad})
        invalid(result, "prefix")
    assert idx("CREATE INDEX a1_t_x ON t (a)", prefix="a1")["violations"] == []
    assert idx("CREATE INDEX " + "p" * 16 + "_t_x ON t (a)", prefix="p" * 16)["violations"] == []


def test_index_check_empty_sql_and_comment_only_sql_name_the_field():
    invalid(run("data_index_name_check", {"sql": "", "prefix": "idx"}), "sql")
    invalid(idx("-- CREATE INDEX idx_t_a ON t (a);"), "sql")
    invalid(run("data_index_name_check", {"sql": "SELECT 1"}), "prefix")
    invalid(run("data_index_name_check", {"prefix": "idx"}), "sql")


def test_index_check_collects_one_violation_per_bad_index_in_order():
    sql = "CREATE INDEX a_b ON t (x); CREATE INDEX idx_t_ok ON t (x); CREATE INDEX idx_u_bad ON t (y);"
    result = idx(sql)
    assert result["violations"] == ["a_b: must start with idx_t_", "idx_u_bad: must start with idx_t_"]
    assert result["index_count"] == 3 and result["violation_count"] == 2


def test_index_check_handles_many_indexes():
    sql = "".join(f"CREATE INDEX idx_t_{i} ON t (c{i});" for i in range(5000))
    result = idx(sql)
    assert result["index_count"] == 5000 and result["conforming"] is True


# --- data_fk_cycle_check -------------------------------------------------------------


def edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"from": a, "to": b} for a, b in pairs]


def fk(*pairs: tuple[str, str], **extra) -> dict:
    return run("data_fk_cycle_check", {"edges": edges(*pairs), **extra})


def test_fk_cycle_acyclic_chain_has_no_cycle():
    assert fk(("order_items", "orders"), ("orders", "users")) == {
        "state": "SUCCESS",
        "edge_count": 2,
        "table_count": 3,
        "has_cycle": False,
        "cycle": [],
    }


def test_fk_cycle_two_table_cycle_is_reported_with_the_start_repeated():
    result = fk(("a", "b"), ("b", "a"))
    assert result["has_cycle"] is True and result["cycle"] == ["a", "b", "a"]


def test_fk_cycle_three_table_cycle_found_through_a_tail():
    result = fk(("x", "a"), ("a", "b"), ("b", "c"), ("c", "a"))
    assert result["has_cycle"] is True and result["cycle"] == ["a", "b", "c", "a"]
    assert result["table_count"] == 4 and result["edge_count"] == 4


def test_fk_cycle_diamond_is_not_a_cycle():
    result = fk(("d", "b"), ("d", "c"), ("b", "a"), ("c", "a"))
    assert result["has_cycle"] is False and result["table_count"] == 4


def test_fk_cycle_self_reference_is_a_cycle_by_default_and_allowed_on_request():
    default = fk(("employees", "employees"))
    assert default["has_cycle"] is True and default["cycle"] == ["employees", "employees"]
    allowed = fk(("employees", "employees"), allow_self_reference=True)
    assert allowed["has_cycle"] is False and allowed["cycle"] == [] and allowed["table_count"] == 1
    explicit_false = fk(("employees", "employees"), allow_self_reference=False)
    assert explicit_false["has_cycle"] is True


def test_fk_cycle_allowing_self_reference_still_finds_real_cycles():
    result = fk(("t", "t"), ("a", "b"), ("b", "a"), allow_self_reference=True)
    assert result["has_cycle"] is True and result["cycle"] == ["a", "b", "a"]


def test_fk_cycle_reports_the_alphabetically_first_self_reference():
    result = fk(("zeta", "zeta"), ("alpha", "alpha"))
    assert result["cycle"] == ["alpha", "alpha"]


def test_fk_cycle_duplicate_edges_are_counted_but_do_not_create_a_cycle():
    result = fk(("a", "b"), ("a", "b"), ("a", "b"))
    assert result["edge_count"] == 3 and result["table_count"] == 2 and result["has_cycle"] is False


def test_fk_cycle_disconnected_components_one_of_which_cycles():
    result = fk(("a", "b"), ("m", "n"), ("n", "o"), ("o", "m"))
    assert result["has_cycle"] is True and result["cycle"] == ["m", "n", "o", "m"]
    assert result["table_count"] == 5


def test_fk_cycle_empty_edges_and_malformed_edges_are_invalid_input():
    invalid(run("data_fk_cycle_check", {"edges": []}), "edges")
    invalid(run("data_fk_cycle_check", {}), "edges")
    assert run("data_fk_cycle_check", {"edges": [{"from": "a"}]})["state"] == "INVALID_INPUT"
    assert run("data_fk_cycle_check", {"edges": [{"from": "", "to": "a"}]})["state"] == "INVALID_INPUT"
    assert run("data_fk_cycle_check", {"edges": [{"from": "a", "to": "b", "extra": 1}]})["state"] == "INVALID_INPUT"
    invalid(run("data_fk_cycle_check", {"edges": edges(("a", "b")), "allow_self_reference": "yes"}), "allow_self_reference")


def test_fk_cycle_more_than_300_edges_is_rejected_and_300_long_chain_is_handled():
    too_many = edges(*[(f"t{i}", f"t{i + 1}") for i in range(301)])
    invalid(run("data_fk_cycle_check", {"edges": too_many}), "edges")
    chain = [(f"t{i:03d}", f"t{i + 1:03d}") for i in range(300)]
    assert fk(*chain)["has_cycle"] is False
    ring = [(f"t{i:03d}", f"t{(i + 1) % 300:03d}") for i in range(300)]
    result = fk(*ring)
    assert result["has_cycle"] is True and result["cycle"][0] == result["cycle"][-1] == "t000" and len(result["cycle"]) == 301


# --- data_pii_tag_coverage -----------------------------------------------------------


def col(name: str, pii: bool, tag: str = "") -> dict:
    return {"name": name, "pii": pii, "tag": tag}


def pii(columns: list[dict], allowed: list[str]) -> dict:
    return run("data_pii_tag_coverage", {"columns": columns, "allowed_tags": allowed})


def test_pii_coverage_full_coverage():
    result = pii([col("email", True, "PII-direct"), col("zip", True, "PII-indirect"), col("id", False)], ["PII-direct", "PII-indirect"])
    assert result == {
        "state": "SUCCESS",
        "column_count": 3,
        "pii_columns": 2,
        "tagged_pii_columns": 2,
        "untagged": [],
        "coverage": 1.0,
    }


def test_pii_coverage_partial_coverage_is_rounded_to_four_places_and_untagged_sorted():
    cols = [col("c", True, ""), col("a", True, "bogus"), col("b", True, "pii")]
    result = pii(cols, ["pii"])
    assert result["untagged"] == ["a", "c"]
    assert result["pii_columns"] == 3 and result["tagged_pii_columns"] == 1
    assert result["coverage"] == 0.3333
    two_of_three = pii([col("a", True, "pii"), col("b", True, "pii"), col("c", True, "")], ["pii"])
    assert two_of_three["coverage"] == 0.6667


def test_pii_coverage_no_pii_columns_is_full_coverage():
    result = pii([col("id", False), col("created_at", False, "ignored")], ["pii"])
    assert result["pii_columns"] == 0 and result["coverage"] == 1.0 and result["untagged"] == []


def test_pii_coverage_non_pii_columns_never_count_as_untagged():
    result = pii([col("id", False, ""), col("email", True, "pii")], ["pii"])
    assert result["untagged"] == [] and result["column_count"] == 2


def test_pii_coverage_tag_is_trimmed_before_comparison_but_case_is_exact():
    result = pii([col("a", True, "  pii  "), col("b", True, "PII")], ["pii"])
    assert result["untagged"] == ["b"] and result["tagged_pii_columns"] == 1


def test_pii_coverage_whitespace_only_tag_is_untagged():
    assert pii([col("a", True, "   ")], ["pii"])["untagged"] == ["a"]


def test_pii_coverage_duplicate_column_names_are_invalid():
    invalid(pii([col("a", True, "pii"), col("a", False)], ["pii"]), "columns")


def test_pii_coverage_allowed_tags_with_duplicates_still_work():
    assert pii([col("a", True, "x")], ["x", "x"])["coverage"] == 1.0


def test_pii_coverage_schema_errors_name_the_field():
    invalid(run("data_pii_tag_coverage", {"columns": [], "allowed_tags": ["x"]}), "columns")
    invalid(run("data_pii_tag_coverage", {"columns": [col("a", True, "x")], "allowed_tags": []}), "allowed_tags")
    invalid(run("data_pii_tag_coverage", {"columns": [col("a", True, "x")]}), "allowed_tags")
    invalid(run("data_pii_tag_coverage", {"allowed_tags": ["x"]}), "columns")
    assert run("data_pii_tag_coverage", {"columns": [{"name": "a", "pii": "yes", "tag": "x"}], "allowed_tags": ["x"]})["state"] == "INVALID_INPUT"
    assert run("data_pii_tag_coverage", {"columns": [{"name": "a", "pii": True}], "allowed_tags": ["x"]})["state"] == "INVALID_INPUT"


def test_pii_coverage_handles_the_maximum_500_columns_and_rejects_501():
    cols = [col(f"c{i}", True, "pii" if i % 2 else "") for i in range(500)]
    result = pii(cols, ["pii"])
    assert result["column_count"] == 500 and result["tagged_pii_columns"] == 250 and result["coverage"] == 0.5
    invalid(pii(cols + [col("extra", True, "pii")], ["pii"]), "columns")


# --- data_contract_field_compat ------------------------------------------------------


def field(name: str, type_: str = "string", required: bool = False) -> dict:
    return {"name": name, "type": type_, "required": required}


def compat(before: list[dict], after: list[dict]) -> dict:
    return run("data_contract_field_compat", {"before": before, "after": after})


def test_contract_compat_identical_field_sets_are_compatible():
    fields = [field("id", "int", True), field("note")]
    assert compat(fields, fields) == {
        "state": "SUCCESS",
        "compatible": True,
        "breaking": [],
        "additive": [],
        "breaking_count": 0,
        "additive_count": 0,
    }


def test_contract_compat_removed_field_is_breaking():
    result = compat([field("id"), field("old")], [field("id")])
    assert result["compatible"] is False and result["breaking"] == ["old: removed"] and result["breaking_count"] == 1


def test_contract_compat_new_optional_field_is_additive_and_new_required_field_is_breaking():
    result = compat([field("id")], [field("id"), field("extra"), field("must", required=True)])
    assert result["breaking"] == ["must: new required field"]
    assert result["additive"] == ["extra: new optional field"]
    assert result["compatible"] is False


def test_contract_compat_type_change_is_breaking_and_names_both_types():
    result = compat([field("qty", "int")], [field("qty", "string")])
    assert result["breaking"] == ["qty: type int -> string"]


def test_contract_compat_optional_to_required_is_breaking_and_required_to_optional_is_additive():
    tightened = compat([field("a")], [field("a", required=True)])
    assert tightened["breaking"] == ["a: became required"] and tightened["additive"] == []
    relaxed = compat([field("a", required=True)], [field("a")])
    assert relaxed["compatible"] is True and relaxed["additive"] == ["a: relaxed to optional"]


def test_contract_compat_type_change_and_tightening_on_one_field_report_both():
    result = compat([field("a", "int")], [field("a", "string", True)])
    assert result["breaking"] == ["a: type int -> string", "a: became required"]
    assert result["breaking_count"] == 2


def test_contract_compat_findings_are_sorted_by_field_name_regardless_of_input_order():
    before = [field("z"), field("m"), field("a")]
    after = [field("b", required=True)]
    result = compat(before, after)
    assert result["breaking"] == ["a: removed", "b: new required field", "m: removed", "z: removed"]


def test_contract_compat_type_comparison_is_case_sensitive_and_exact():
    assert compat([field("a", "String")], [field("a", "string")])["breaking"] == ["a: type String -> string"]


def test_contract_compat_empty_before_and_after_are_allowed():
    assert compat([], [])["compatible"] is True
    assert compat([], [field("a")])["additive"] == ["a: new optional field"]
    assert compat([field("a")], [])["breaking"] == ["a: removed"]


def test_contract_compat_duplicate_names_are_invalid_and_name_the_side():
    invalid(compat([field("a"), field("a", "int")], [field("a")]), "before")
    invalid(compat([field("a")], [field("a"), field("a")]), "after")


def test_contract_compat_schema_errors():
    invalid(run("data_contract_field_compat", {"before": []}), "after")
    invalid(run("data_contract_field_compat", {"after": []}), "before")
    assert run("data_contract_field_compat", {"before": [{"name": "a", "type": "x"}], "after": []})["state"] == "INVALID_INPUT"
    assert run("data_contract_field_compat", {"before": [{"name": "", "type": "x", "required": True}], "after": []})["state"] == "INVALID_INPUT"
    invalid(run("data_contract_field_compat", {"before": [field(f"f{i}") for i in range(501)], "after": []}), "before")


def test_contract_compat_handles_500_fields_each_side():
    before = [field(f"f{i}", "int", True) for i in range(500)]
    after = [field(f"f{i}", "int", i % 2 == 0) for i in range(500)]
    result = compat(before, after)
    assert result["compatible"] is True and result["additive_count"] == 250


# --- data_migration_version_order ----------------------------------------------------


def versions(*items: str) -> dict:
    return run("data_migration_version_order", {"versions": list(items)})


def test_version_order_ascending_versions_are_ordered_and_latest_is_the_last():
    assert versions("1", "2", "3") == {
        "state": "SUCCESS",
        "count": 3,
        "ordered": True,
        "duplicates": [],
        "out_of_order": [],
        "latest": "3",
    }


def test_version_order_compares_digit_runs_as_integers():
    result = versions("1", "2", "10")
    assert result["ordered"] is True and result["latest"] == "10"
    out = versions("10", "9")
    assert out["ordered"] is False and out["out_of_order"] == ["9"] and out["latest"] == "10"


def test_version_order_dotted_and_timestamp_versions():
    assert versions("1.0", "1.2", "1.10")["ordered"] is True
    result = versions("20240101120000", "20240102090000", "20231231000000")
    assert result["out_of_order"] == ["20231231000000"] and result["latest"] == "20240102090000"


def test_version_order_duplicates_are_reported_once_per_repeat_and_break_order():
    result = versions("1", "2", "2", "3", "1")
    assert result["duplicates"] == ["2", "1"] and result["out_of_order"] == []
    assert result["ordered"] is False and result["count"] == 5


def test_version_order_out_of_order_is_measured_against_the_highest_seen_so_far():
    result = versions("5", "9", "6", "7")
    assert result["out_of_order"] == ["6", "7"] and result["latest"] == "9"


def test_version_order_numeric_equivalence_counts_as_duplicate():
    """'01' and '1' have the same numeric key."""
    result = versions("1", "01")
    assert result["duplicates"] == ["01"] and result["ordered"] is False


def test_version_order_digits_sort_before_letters_and_letters_compare_as_text():
    assert versions("1", "a")["ordered"] is True
    assert versions("a", "1")["out_of_order"] == ["1"]
    assert versions("v1", "v2", "v10")["ordered"] is True


def test_version_order_single_version_is_ordered():
    result = versions("2024.08.31-add-orders")
    assert result["ordered"] is True and result["latest"] == "2024.08.31-add-orders" and result["count"] == 1


@pytest.mark.parametrize("bad", ["", " 1", "1 2", "_1", "-1", ".1", "1/2", "1;2", "é1"])
def test_version_order_invalid_version_ids_are_rejected(bad):
    result = run("data_migration_version_order", {"versions": ["1", bad]})
    assert result["state"] == "INVALID_INPUT"
    assert result["field"].startswith("versions")


def test_version_order_schema_limits():
    invalid(run("data_migration_version_order", {"versions": []}), "versions")
    invalid(run("data_migration_version_order", {}), "versions")
    invalid(run("data_migration_version_order", {"versions": [str(i) for i in range(501)]}), "versions")
    assert run("data_migration_version_order", {"versions": ["1" * 65]})["state"] == "INVALID_INPUT"
    assert run("data_migration_version_order", {"versions": ["1" * 64]})["state"] == "SUCCESS"


def test_version_order_handles_500_versions():
    result = versions(*[str(i) for i in range(1, 501)])
    assert result["ordered"] is True and result["latest"] == "500" and result["count"] == 500
    shuffled = versions(*[str(i) for i in range(500, 0, -1)])
    assert shuffled["ordered"] is False and len(shuffled["out_of_order"]) == 499 and shuffled["latest"] == "500"
