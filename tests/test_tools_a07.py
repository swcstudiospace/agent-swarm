"""Behaviour of the v2.2 A07 data lane tools: data_lock_risk_scan and data_explain_regression_check.

The handlers are pure: every result is computed from the payload. Rules come from prompts/A07-data.md
(decision_logic 2 and 5, operating procedure step 5) and agents/A07-data.md (requires_lock_timeout_s).
"""
import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tools.a07_tools import (
    data_explain_regression_check,
    data_lock_risk_scan,
    data_reversible_pair_check,
    data_row_count_reconcile,
)

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a07-data.tools.json"


def kinds(result):
    return [(r["kind"], r["statement_index"]) for r in result["risks"]]


# --- data_lock_risk_scan -------------------------------------------------------------


def test_lock_scan_flags_create_index_without_concurrently():
    result = data_lock_risk_scan({"sql": "CREATE INDEX idx_orders_user ON orders (user_id);"})
    assert result["state"] == "SUCCESS"
    assert kinds(result) == [("create-index-blocking", 1)]
    assert result["risk_count"] == 1 and result["lock_timeout_required"] is True


def test_lock_scan_accepts_create_index_concurrently_and_unique():
    sql = "CREATE INDEX CONCURRENTLY idx_a ON t (a); CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_b ON t (b);"
    result = data_lock_risk_scan({"sql": sql})
    assert result["risks"] == [] and result["lock_timeout_required"] is False
    unique = data_lock_risk_scan({"sql": "CREATE UNIQUE INDEX ux_b ON t (b)"})
    assert kinds(unique) == [("create-index-blocking", 1)]


def test_lock_scan_foreign_key_and_check_need_not_valid():
    sql = (
        "ALTER TABLE orders ADD CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users (id);\n"
        "ALTER TABLE orders ADD CONSTRAINT fk_user2 FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;\n"
        "ALTER TABLE orders ADD CONSTRAINT ck_total CHECK (total >= 0);\n"
        "ALTER TABLE orders ADD CONSTRAINT ck_total2 CHECK (total >= 0) NOT VALID;"
    )
    result = data_lock_risk_scan({"sql": sql})
    assert kinds(result) == [("add-foreign-key-validating", 1), ("add-check-validating", 3)]
    assert result["statement_count"] == 4


def test_lock_scan_set_not_null_and_multi_action_alter():
    sql = "ALTER TABLE t ALTER COLUMN a SET NOT NULL, ADD CONSTRAINT ck CHECK (b > 0), ADD COLUMN c int;"
    result = data_lock_risk_scan({"sql": sql})
    assert kinds(result) == [("set-not-null-scan", 1), ("add-check-validating", 1)]
    assert result["risk_count"] == 2


def test_lock_scan_unique_constraint_is_safe_only_with_prebuilt_index():
    risky = data_lock_risk_scan({"sql": "ALTER TABLE t ADD CONSTRAINT u UNIQUE (a);"})
    safe = data_lock_risk_scan({"sql": "ALTER TABLE t ADD CONSTRAINT u UNIQUE USING INDEX ux_t_a;"})
    assert kinds(risky) == [("add-unique-index-build", 1)]
    assert safe["risks"] == []


def test_lock_scan_comments_are_ignored_and_indexes_count_statements_after_comments():
    sql = "-- CREATE INDEX nope ON t (a);\n/* ALTER TABLE t ALTER COLUMN a SET NOT NULL; */\nSELECT 1;\nCREATE INDEX i ON t (a);"
    result = data_lock_risk_scan({"sql": sql})
    assert result["statement_count"] == 2
    assert kinds(result) == [("create-index-blocking", 2)]


def test_lock_scan_declared_timeout_from_payload_or_sql():
    sql = "CREATE INDEX i ON t (a);"
    assert data_lock_risk_scan({"sql": sql})["lock_timeout_declared"] is False
    assert data_lock_risk_scan({"sql": sql, "lock_timeout_s": 0})["lock_timeout_declared"] is False
    assert data_lock_risk_scan({"sql": sql, "lock_timeout_s": 5})["lock_timeout_declared"] is True
    set_sql = "SET lock_timeout = '5s'; " + sql
    assert data_lock_risk_scan({"sql": set_sql})["lock_timeout_declared"] is True
    assert data_lock_risk_scan({"sql": "SET LOCAL lock_timeout TO 0; " + sql})["lock_timeout_declared"] is False


def test_lock_scan_additive_column_only_has_no_risk():
    result = data_lock_risk_scan({"sql": "ALTER TABLE orders ADD COLUMN note text DEFAULT '';"})
    assert result == {
        "state": "SUCCESS",
        "statement_count": 1,
        "risks": [],
        "risk_count": 0,
        "lock_timeout_required": False,
        "lock_timeout_declared": False,
    }


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment", "/* nothing */ ;"])
def test_lock_scan_empty_sql_is_invalid_and_names_the_field(sql):
    result = data_lock_risk_scan({"sql": sql})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "sql"


def test_lock_scan_large_input_is_scanned_without_error():
    sql = "CREATE INDEX CONCURRENTLY i ON t (a);\n" * 5000 + "ALTER TABLE t ALTER COLUMN a SET NOT NULL;"
    result = data_lock_risk_scan({"sql": sql})
    assert result["statement_count"] == 5001
    assert kinds(result) == [("set-not-null-scan", 5001)]


def test_lock_scan_probes_follow_the_shared_contract():
    base = {"sql": "CREATE INDEX i ON t (a);"}
    assert data_lock_risk_scan({**base, "probe": "partial"})["state"] == "PARTIAL_SUCCESS"
    with pytest.raises(OSError):
        data_lock_risk_scan({**base, "probe": "dependency"})
    assert data_lock_risk_scan({**base, "probe": "sleep"})["state"] == "TIMEOUT"


# --- data_lock_risk_scan: RV-D fix-ups ----------------------------------------------

INDEX = "CREATE INDEX i ON t (a);"


def declared(sql):
    return data_lock_risk_scan({"sql": sql})["lock_timeout_declared"]


def test_lock_scan_timeout_set_after_the_ddl_does_not_cover_it():
    """RV-D 1: `CREATE INDEX i ON t (a); SET lock_timeout='5s';` gave lock_timeout_declared True."""
    assert declared(INDEX + " SET lock_timeout='5s';") is False
    assert declared("SET lock_timeout='5s'; " + INDEX) is True


def test_lock_scan_set_local_ends_at_commit_or_rollback():
    """RV-D 1: `BEGIN; SET LOCAL lock_timeout='5s'; COMMIT; CREATE INDEX ...` gave True."""
    assert declared("BEGIN; SET LOCAL lock_timeout='5s'; COMMIT; " + INDEX) is False
    assert declared("BEGIN; SET LOCAL lock_timeout='5s'; ROLLBACK; " + INDEX) is False
    assert declared("BEGIN; SET LOCAL lock_timeout='5s'; " + INDEX + " COMMIT;") is True
    assert declared("SET LOCAL lock_timeout='5s'; " + INDEX) is True


def test_lock_scan_reset_or_zero_or_default_clears_the_timeout():
    """RV-D 1: `SET lock_timeout='5s'; RESET lock_timeout; CREATE INDEX ...` gave True."""
    for undo in ("RESET lock_timeout;", "SET lock_timeout = 0;", "SET lock_timeout TO DEFAULT;", "RESET ALL;", "SET lock_timeout='0s';"):
        assert declared("SET lock_timeout='5s'; " + undo + " " + INDEX) is False, undo


def test_lock_scan_rollback_undoes_a_session_set_made_inside_the_transaction():
    assert declared("BEGIN; SET lock_timeout='5s'; ROLLBACK; " + INDEX) is False
    assert declared("BEGIN; SET lock_timeout='5s'; COMMIT; " + INDEX) is True
    assert declared("SET lock_timeout='5s'; BEGIN; ROLLBACK; " + INDEX) is True
    assert declared("BEGIN; SELECT set_config('lock_timeout', '5s', true); COMMIT; " + INDEX) is False
    assert declared("SELECT set_config('lock_timeout', '5s', false); " + INDEX) is True


def test_lock_scan_every_risky_statement_needs_a_timeout_in_effect():
    both = "SET lock_timeout='5s'; CREATE INDEX a ON t (a); RESET lock_timeout; CREATE INDEX b ON t (b);"
    assert declared(both) is False
    assert declared("SET lock_timeout='5s'; CREATE INDEX a ON t (a); CREATE INDEX b ON t (b);") is True
    assert data_lock_risk_scan({"sql": both, "lock_timeout_s": 5})["lock_timeout_declared"] is True


@pytest.mark.parametrize("value", ["'1.5s'", "'1h'", "'500us'", "'1min'", "'1d'", "'2 s'", "250", "'250ms'"])
def test_lock_scan_recognises_postgres_timeout_value_forms(value):
    """RV-D 6: '1.5s', '1h', '500us' were not recognised."""
    assert declared(f"SET lock_timeout = {value}; " + INDEX) is True


@pytest.mark.parametrize("value", ["0", "'0'", "'0ms'", "'0.0s'", "DEFAULT"])
def test_lock_scan_zero_timeout_values_are_not_a_timeout(value):
    assert declared(f"SET lock_timeout TO {value}; " + INDEX) is False


def test_lock_scan_do_block_bodies_are_scanned_under_the_outer_statement():
    """RV-D 2: `DO $$ BEGIN ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES c(id); END $$;` gave risks []."""
    sql = "DO $$ BEGIN ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES c(id); END $$;"
    assert kinds(data_lock_risk_scan({"sql": sql})) == [("add-foreign-key-validating", 1)]
    idempotent = (
        "SELECT 1;\nDO $body$\nBEGIN\n  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk') THEN\n"
        "    ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES c(id);\n  ELSE\n    CREATE INDEX i ON o (c);\n  END IF;\n"
        "EXCEPTION WHEN duplicate_object THEN\n  ALTER TABLE o ALTER COLUMN c SET NOT NULL;\nEND\n$body$ LANGUAGE plpgsql;"
    )
    result = data_lock_risk_scan({"sql": idempotent})
    assert result["statement_count"] == 2
    assert kinds(result) == [
        ("add-foreign-key-validating", 2), ("create-index-blocking", 2), ("set-not-null-scan", 2),
    ]
    safe = "DO $$ BEGIN ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES c(id) NOT VALID; END $$;"
    assert data_lock_risk_scan({"sql": safe})["risks"] == []


def test_lock_scan_do_body_as_string_and_execute_format_are_scanned():
    quoted = "DO 'BEGIN CREATE INDEX i ON t (a); END';"
    assert kinds(data_lock_risk_scan({"sql": quoted})) == [("create-index-blocking", 1)]
    dynamic = "DO $$ BEGIN EXECUTE format('CREATE INDEX %I ON %I (a)', 'i', 't'); END $$;"
    assert kinds(data_lock_risk_scan({"sql": dynamic})) == [("create-index-blocking", 1)]
    assert declared("DO $$ BEGIN SET LOCAL lock_timeout = '5s'; CREATE INDEX i ON t (a); END $$;") is True


def test_lock_scan_function_bodies_are_not_run_by_the_migration_and_not_scanned():
    sql = "CREATE FUNCTION f() RETURNS void AS $$ BEGIN CREATE INDEX i ON t (a); END $$ LANGUAGE plpgsql;"
    assert data_lock_risk_scan({"sql": sql})["risks"] == []


def test_lock_scan_column_level_unique_primary_key_and_references():
    """RV-D 3: `ALTER TABLE o ADD COLUMN code text UNIQUE;` gave []."""
    unique = data_lock_risk_scan({"sql": "ALTER TABLE o ADD COLUMN code text UNIQUE;"})
    assert kinds(unique) == [("add-unique-index-build", 1)]
    pk = data_lock_risk_scan({"sql": "ALTER TABLE o ADD COLUMN id bigint PRIMARY KEY, ADD COLUMN u text CONSTRAINT uq UNIQUE;"})
    assert kinds(pk) == [("add-unique-index-build", 1)]
    fk = data_lock_risk_scan({"sql": "ALTER TABLE o ADD COLUMN c int REFERENCES c(id) UNIQUE;"})
    assert kinds(fk) == [("add-unique-index-build", 1), ("add-foreign-key-validating", 1)]
    plain = data_lock_risk_scan({"sql": "ALTER TABLE o ADD COLUMN c int DEFAULT 0, ADD COLUMN u text DEFAULT 'UNIQUE';"})
    assert plain["risks"] == []


def test_lock_scan_if_exists_before_only_is_parsed():
    """RV-D 4: `ALTER TABLE IF EXISTS ONLY public.o ADD CONSTRAINT fk ...` gave []."""
    sql = "ALTER TABLE IF EXISTS ONLY public.o ADD CONSTRAINT fk FOREIGN KEY (c) REFERENCES c(id);"
    assert kinds(data_lock_risk_scan({"sql": sql})) == [("add-foreign-key-validating", 1)]
    assert kinds(data_lock_risk_scan({"sql": "ALTER TABLE ONLY o ADD CONSTRAINT u UNIQUE (a);"})) == [("add-unique-index-build", 1)]


def test_lock_scan_quoted_identifiers_with_any_characters():
    """RV-D 6: `ADD CONSTRAINT "fk-o-c" FOREIGN KEY ...` and `ALTER COLUMN "Created At" SET NOT NULL` gave []."""
    fk = data_lock_risk_scan({"sql": 'ALTER TABLE "my table" ADD CONSTRAINT "fk-o-c" FOREIGN KEY (c) REFERENCES c(id);'})
    assert kinds(fk) == [("add-foreign-key-validating", 1)]
    nn = data_lock_risk_scan({"sql": 'ALTER TABLE o ALTER COLUMN "Created At" SET NOT NULL;'})
    assert kinds(nn) == [("set-not-null-scan", 1)]
    semicolon = data_lock_risk_scan({"sql": 'ALTER TABLE "a;b" ADD CONSTRAINT c CHECK (x > 0);'})
    assert kinds(semicolon) == [("add-check-validating", 1)] and semicolon["statement_count"] == 1


def test_lock_scan_keywords_and_comment_markers_inside_strings_are_inert():
    """RV-D 7: `CHECK (s <> 'NOT VALID')` was treated as NOT VALID; `'--x'` hid a following CREATE INDEX on the same line."""
    check = data_lock_risk_scan({"sql": "ALTER TABLE t ADD CONSTRAINT c CHECK (s <> 'NOT VALID');"})
    assert kinds(check) == [("add-check-validating", 1)]
    dashes = data_lock_risk_scan({"sql": "SELECT '--x'; CREATE INDEX i ON t (a);"})
    assert kinds(dashes) == [("create-index-blocking", 2)]
    semicolon = data_lock_risk_scan({"sql": "INSERT INTO t VALUES ('a;b'); CREATE INDEX i ON t (a);"})
    assert semicolon["statement_count"] == 2 and kinds(semicolon) == [("create-index-blocking", 2)]
    escaped = data_lock_risk_scan({"sql": "SELECT E'it\\'s;'; CREATE INDEX i ON t (a);"})
    assert kinds(escaped) == [("create-index-blocking", 2)]
    nested = data_lock_risk_scan({"sql": "/* a /* b */ CREATE INDEX hidden ON t (a); */ CREATE INDEX i ON t (a);"})
    assert nested["statement_count"] == 1


def test_lock_scan_megabyte_input_stays_well_inside_the_tool_timeout():
    import time

    sql = ("ALTER TABLE t ADD COLUMN c text DEFAULT 'x'; -- note\n" * 20000)[:1_000_000]
    started = time.perf_counter()
    result = data_lock_risk_scan({"sql": sql + "\nCREATE INDEX i ON t (a);"})
    assert time.perf_counter() - started < 2.0
    assert result["risk_count"] == 1


def test_lock_scan_unterminated_literals_do_not_hang_or_raise():
    for sql in ("SELECT 'abc", "SELECT $$abc", "SELECT \"abc", "SELECT E'abc\\", "/* open", "DO $$ BEGIN CREATE INDEX i ON t (a);"):
        assert data_lock_risk_scan({"sql": sql})["state"] in {"SUCCESS", "INVALID_INPUT"}


# --- data_row_count_reconcile / data_reversible_pair_check ---------------------------


def test_row_count_total_loss_to_zero_is_not_reported_as_reconciled():
    """Skill reviewers: before=1000, after=0, delta=-1000 (a dropped table reads 0 rows) was always `reconciled: true`."""
    result = data_row_count_reconcile({"before": 1000, "after": 0, "delta": -1000})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "after"
    assert "dropped" in result["message"]


def test_row_count_ordinary_cases_are_unchanged():
    assert data_row_count_reconcile({"before": 1000, "after": 1250, "delta": 250})["reconciled"] is True
    assert data_row_count_reconcile({"before": 1000, "after": 400, "delta": -600})["reconciled"] is True
    assert data_row_count_reconcile({"before": 1000, "after": 3, "delta": -1000})["drift"] == 3
    assert data_row_count_reconcile({"before": 1000, "after": 0, "delta": -999})["reconciled"] is False
    assert data_row_count_reconcile({"before": 0, "after": 0, "delta": 0})["reconciled"] is True
    assert data_row_count_reconcile({"before": 5, "after": 0, "delta": -9})["state"] == "INVALID_INPUT"


def test_pair_check_ok_is_false_when_migration_files_are_not_covered():
    """Skill reviewers: `ok` ignored `unrecognized` files, so an alembic or prisma layout reported ok true."""
    names = ["versions/3f2a_add_users.py", "versions/9c1b_add_email.py"]
    result = data_reversible_pair_check({"filenames": names})
    assert result["recognized_count"] == 0 and result["unrecognized"] == sorted(names) and result["ok"] is False
    prisma = data_reversible_pair_check({"filenames": ["prisma/migrations/20240101_init/migration.sql"]})
    assert prisma["ok"] is False


def test_pair_check_ok_ignores_non_migration_files_and_flyway_repeatables():
    result = data_reversible_pair_check({"filenames": ["V1__a.sql", "U1__a.sql", "R__views.sql", "db/R__more.sql", "README.md", "notes.txt"]})
    assert result["ok"] is True and result["paired"] == ["V1__a"]
    gap = data_reversible_pair_check({"filenames": ["V1__a.sql", "README.md"]})
    assert gap["ok"] is False and gap["missing_reverse"] == ["V1__a"]


# --- data_explain_regression_check ---------------------------------------------------


def q(qid, baseline, shadow):
    return {"id": qid, "baseline": baseline, "shadow": shadow}


def test_explain_exactly_twenty_percent_passes():
    result = data_explain_regression_check({"queries": [q("a", 100, 120)]})
    assert result["state"] == "SUCCESS"
    assert result["regressions"] == [] and result["within_budget"] is True
    assert result["max_pct"] == 20.0 and result["checked"] == 1


def test_explain_just_over_twenty_percent_fails():
    result = data_explain_regression_check({"queries": [q("a", 10000, 12001)]})
    assert result["within_budget"] is False
    assert result["regressions"] == [{"id": "a", "pct": 20.01}]
    assert result["max_pct"] == 20.01


def test_explain_float_boundary_is_exact():
    # 0.1 + 0.2 style inputs must not tip over the boundary through binary rounding.
    assert data_explain_regression_check({"queries": [q("a", 0.1, 0.12)]})["within_budget"] is True
    assert data_explain_regression_check({"queries": [q("a", 3.3, 3.96)]})["within_budget"] is True


def test_explain_improvements_are_negative_and_within_budget():
    result = data_explain_regression_check({"queries": [q("a", 200, 100), q("b", 50, 50)]})
    assert result["within_budget"] is True
    assert result["max_pct"] == 0.0
    only_better = data_explain_regression_check({"queries": [q("a", 200, 100)]})
    assert only_better["max_pct"] == -50.0 and only_better["regressions"] == []


def test_explain_reports_every_regression_and_the_worst():
    result = data_explain_regression_check(
        {"queries": [q("slow", 100, 150), q("fine", 100, 105), q("worse", 100, 300)]}
    )
    assert [r["id"] for r in result["regressions"]] == ["slow", "worse"]
    assert result["max_pct"] == 200.0 and result["checked"] == 3


def test_explain_custom_threshold():
    queries = [q("a", 100, 110)]
    assert data_explain_regression_check({"queries": queries, "threshold_pct": 5})["within_budget"] is False
    assert data_explain_regression_check({"queries": queries, "threshold_pct": 10})["within_budget"] is True
    assert data_explain_regression_check({"queries": [q("a", 100, 101)], "threshold_pct": 0})["within_budget"] is False


def test_explain_zero_baseline_is_invalid_and_names_the_field():
    result = data_explain_regression_check({"queries": [q("a", 100, 100), q("b", 0, 5)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "queries[1].baseline"


def test_explain_duplicate_ids_are_invalid():
    result = data_explain_regression_check({"queries": [q("a", 1, 1), q("a", 2, 2)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "queries[1].id"


def test_explain_shadow_zero_is_a_full_improvement():
    result = data_explain_regression_check({"queries": [q("a", 40, 0)]})
    assert result["max_pct"] == -100.0 and result["within_budget"] is True


def test_explain_handles_many_queries():
    queries = [q(f"q{i}", 100, 100) for i in range(500)]
    result = data_explain_regression_check({"queries": queries})
    assert result["checked"] == 500 and result["within_budget"] is True


def test_explain_probes_follow_the_shared_contract():
    base = {"queries": [q("a", 1, 1)]}
    assert data_explain_regression_check({**base, "probe": "partial"})["state"] == "PARTIAL_SUCCESS"
    with pytest.raises(OSError):
        data_explain_regression_check({**base, "probe": "dependency"})
    assert data_explain_regression_check({**base, "probe": "sleep"})["state"] == "TIMEOUT"


# --- registry fragment ---------------------------------------------------------------


def test_fragment_entries_validate_when_added_to_the_registry(tmp_path):
    """Passes before the intake merge (fragment present, entries absent) and after (entries merged)."""
    document = tool_registry.load_document()
    merged = copy.deepcopy(document)
    agent = next(a for a in merged["agents"] if a["id"] == "A07")
    present = {t["name"] for a in merged["agents"] for t in a.get("registered_tools", [])}
    names = ["data_lock_risk_scan", "data_explain_regression_check"]
    if FRAGMENT.exists():
        entries = json.loads(FRAGMENT.read_text(encoding="utf-8"))
        assert [e["name"] for e in entries] == names
        for entry in entries:
            assert entry["owner"] == "A07" and entry["permitted_callers"] == ["A07"]
            assert entry["side_effect_tier"] == "READ" and "|" not in entry["purpose"]
            if entry["name"] not in present:
                agent["registered_tools"].append(copy.deepcopy(entry))
    else:
        assert set(names) <= present, "fragment merged away but the entries are not in agents.json"
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(merged), encoding="utf-8")
    assert tool_registry.validate_registry(path) == []
