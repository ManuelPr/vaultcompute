"""Collective tokens: one placeholder for a whole list, queried by operations.

The property that matters most is the last section: no query may fail because
of the data. That is what stops the one-bit oracle that arbitrary Python opens,
and it is why this path runs no sandbox.
"""

from datetime import datetime, timedelta, timezone

import pytest

from vaultcompute.config import (
    VaultComputeConfig,
    ColumnConfig,
    SensitiveFieldConfig,
    TableConfig,
    ToolSchemaConfig,
    load_config,
    table_schemas_for,
)
from vaultcompute.core.lineage import Column, TableSchema
from vaultcompute.core.capabilities import TableQueryCapability
from vaultcompute.core.policy import SessionBoundPolicy
from vaultcompute.core.rehydrator import rehydrate
from vaultcompute.core.sqlite_store import SQLiteTokenStore
from vaultcompute.core.table import run_query
from vaultcompute.core.tokenizer import describe_tables, tokenize_result
from vaultcompute.core.vault import MemoryTokenStore
from vaultcompute.sandbox.subprocess_ import SubprocessSandbox
from vaultcompute.tools.vault_compute import handle_vault_compute
from vaultcompute.tools.vault_table import handle_vault_table

TTL = datetime.now(tz=timezone.utc) + timedelta(hours=1)
SESSION = "s"

ROWS = [
    {"name": "James Brown", "salary": 71000, "dept": "Eng"},
    {"name": "John Smith", "salary": 62000, "dept": "Eng"},
    {"name": "Emily Johnson", "salary": 55000, "dept": "Sales"},
]
SCHEMA = TableSchema(
    columns=(
        Column("name", "person_name"),
        Column("salary", "salary", "EUR/year"),
        Column("dept"),
    )
)


# --- the operations -------------------------------------------------------


def test_filter_sort_limit_select_compose():
    assert run_query(
        ROWS,
        SCHEMA,
        [
            {"op": "filter", "column": "dept", "cmp": "==", "value": "Eng"},
            {"op": "sort_by", "column": "salary", "desc": True},
            {"op": "limit", "n": 1},
            {"op": "select", "columns": ["name"]},
        ],
    ) == [{"name": "James Brown"}]


@pytest.mark.parametrize(
    "op, expected",
    [
        ({"op": "sum", "column": "salary"}, 188000),
        ({"op": "min", "column": "salary"}, 55000),
        ({"op": "max", "column": "salary"}, 71000),
        ({"op": "count"}, 3),
    ],
)
def test_value_operations(op, expected):
    assert run_query(ROWS, SCHEMA, [op]) == expected


def test_mean_is_a_number_not_a_row_list():
    assert run_query(ROWS, SCHEMA, [{"op": "mean", "column": "salary"}]) == pytest.approx(62666.67, abs=0.01)


def test_contains_matches_substrings():
    got = run_query(ROWS, SCHEMA, [{"op": "filter", "column": "name", "cmp": "contains", "value": "Johnson"}])
    assert [r["name"] for r in got] == ["Emily Johnson"]


def test_a_value_operation_must_come_last():
    with pytest.raises(ValueError, match="must be the last operation"):
        run_query(ROWS, SCHEMA, [{"op": "count"}, {"op": "limit", "n": 1}])


@pytest.mark.parametrize(
    "ops, message",
    [
        ([{"op": "filter", "column": "bonus", "cmp": "==", "value": 1}], "unknown column"),
        ([{"op": "select", "columns": ["bonus"]}], "unknown column"),
        ([{"op": "explode"}], "unknown operation"),
        ([{"op": "filter", "column": "salary", "cmp": "~=", "value": 1}], "unknown comparison"),
        ([{"op": "limit", "n": -1}], "non-negative integer"),
        ([{"op": "select", "columns": []}], "non-empty"),
    ],
)
def test_a_malformed_query_says_what_is_wrong(ops, message):
    with pytest.raises(ValueError, match=message):
        run_query(ROWS, SCHEMA, ops)


def test_the_error_names_the_columns_that_do_exist():
    with pytest.raises(ValueError) as ei:
        run_query(ROWS, SCHEMA, [{"op": "sum", "column": "bonus"}])
    assert "name, salary, dept" in str(ei.value)


# --- no query may fail because of the data --------------------------------
#
# This is the whole reason a fixed operation set is worth having. With
# arbitrary Python the model writes code whose *success* depends on a hidden
# value and reads one bit per call. Here every well-formed query succeeds, so
# there is no bit to read.

MIXED = [{"v": 1}, {"v": "two"}, {"v": None}, {"v": True}, {"v": {"nested": 1}}]
MIXED_SCHEMA = TableSchema(columns=(Column("v"),))


@pytest.mark.parametrize("cmp", ["<", "<=", ">", ">=", "==", "!=", "contains"])
def test_comparing_across_types_never_raises(cmp):
    # Python refuses to compare str with int. Raising here would leak a bit.
    run_query(MIXED, MIXED_SCHEMA, [{"op": "filter", "column": "v", "cmp": cmp, "value": 5}])
    run_query(MIXED, MIXED_SCHEMA, [{"op": "filter", "column": "v", "cmp": cmp, "value": "x"}])


def test_sorting_mixed_types_never_raises():
    assert len(run_query(MIXED, MIXED_SCHEMA, [{"op": "sort_by", "column": "v"}])) == len(MIXED)


def test_aggregating_a_column_with_no_numbers_returns_none_rather_than_raising():
    assert run_query([{"v": "a"}, {"v": None}], MIXED_SCHEMA, [{"op": "sum", "column": "v"}]) is None


def test_an_empty_result_is_an_answer_not_an_error():
    got = run_query(ROWS, SCHEMA, [{"op": "filter", "column": "salary", "cmp": ">", "value": 10**9}])
    assert got == []
    assert run_query(ROWS, SCHEMA, [{"op": "filter", "column": "dept", "cmp": "==", "value": "None"}, {"op": "count"}]) == 0


def test_rows_that_are_not_objects_do_not_raise():
    assert run_query([1, "two", None], MIXED_SCHEMA, [{"op": "sort_by", "column": "v"}]) is not None


# --- tokenizing a table ---------------------------------------------------


def test_a_declared_list_becomes_one_token():
    store = MemoryTokenStore()
    out = tokenize_result(
        {"employees": ROWS, "count": 3},
        "hr.list",
        [],
        store,
        SESSION,
        TTL,
        tables=[("$.employees", SCHEMA)],
    )
    assert isinstance(out["employees"], str)
    assert out["employees"].startswith("⟦tok_")
    assert out["count"] == 3
    records = store.find_by_session(SESSION)
    assert len(records) == 1, "one token for the whole table"
    assert records[0].dtype == "table"
    assert records[0].table == SCHEMA
    assert records[0].policy.can_be_input_to_query
    assert not records[0].policy.can_be_input_to_compute


def test_five_hundred_rows_still_mint_one_token():
    # The point of the feature: 500 rows x 5 fields used to mint 2,500 tokens,
    # which the model could not operate on.
    store = MemoryTokenStore()
    rows = [{"name": f"p{i}", "salary": 30000 + i, "dept": "Eng"} for i in range(500)]
    tokenize_result(
        {"employees": rows}, "hr.list", [], store, SESSION, TTL, tables=[("$.employees", SCHEMA)]
    )
    assert len(store.find_by_session(SESSION)) == 1


def test_a_declared_table_that_is_not_a_list_is_a_no_op():
    store = MemoryTokenStore()
    payload = {"employees": "unavailable"}
    out = tokenize_result(payload, "hr.list", [], store, SESSION, TTL, tables=[("$.employees", SCHEMA)])
    assert out == payload
    assert store.find_by_session(SESSION) == []


def test_no_row_value_survives_into_the_tokenized_output():
    import json

    store = MemoryTokenStore()
    out = tokenize_result(
        {"employees": ROWS}, "hr.list", [], store, SESSION, TTL, tables=[("$.employees", SCHEMA)]
    )
    rendered = json.dumps(out)
    assert "71000" not in rendered
    assert "James Brown" not in rendered


# --- the tool -------------------------------------------------------------


def _table_token(store):
    out = tokenize_result(
        {"employees": ROWS}, "hr.list", [], store, SESSION, TTL, tables=[("$.employees", SCHEMA)]
    )
    return out["employees"]


def _query(store, token, ops):
    return handle_vault_table(
        {"table": token, "ops": ops},
        store=store,
        policy=SessionBoundPolicy(),
        session_id=SESSION,
        ttl_seconds=3600,
    )


def test_the_tool_returns_a_placeholder_never_a_value():
    store = MemoryTokenStore()
    result = _query(store, _table_token(store), [{"op": "max", "column": "salary"}])
    assert result.startswith("⟦tok_")
    assert store.resolve(result) == 71000


def test_a_table_token_cannot_bypass_the_query_language_through_python():
    store = MemoryTokenStore()
    token = _table_token(store)
    with pytest.raises(ValueError, match="policy denied"):
        handle_vault_compute(
            {"code": f"result = resolve('{token}')", "inputs": [token]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id=SESSION,
            ttl_seconds=3600,
        )


def test_a_table_result_cannot_be_used_as_a_fresh_python_oracle_token():
    store = MemoryTokenStore()
    count = _query(
        store,
        _table_token(store),
        [
            {"op": "filter", "column": "salary", "cmp": ">", "value": 70000},
            {"op": "count"},
        ],
    )
    with pytest.raises(ValueError, match="policy denied"):
        handle_vault_compute(
            {"code": f"result = 1 / 0 if resolve('{count}') > 0 else 'ok'", "inputs": [count]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id=SESSION,
            ttl_seconds=3600,
        )


def test_mode_b_can_require_an_exact_trusted_side_capability():
    store = MemoryTokenStore()
    token = _table_token(store)
    ops = [{"op": "filter", "column": "salary", "cmp": ">", "value": 70000}]
    capability = TableQueryCapability.issue(
        session_id=SESSION,
        table_token=token,
        ops=ops,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=5),
    )
    result = handle_vault_table(
        {"table": token, "ops": ops},
        store=store,
        policy=SessionBoundPolicy(),
        session_id=SESSION,
        ttl_seconds=3600,
        capability=capability,
        require_capability=True,
    )
    assert store.resolve(result) == [ROWS[0]]

    changed = [{"op": "filter", "column": "salary", "cmp": ">", "value": 71000}]
    with pytest.raises(ValueError, match="not authorized"):
        handle_vault_table(
            {"table": token, "ops": changed},
            store=store,
            policy=SessionBoundPolicy(),
            session_id=SESSION,
            ttl_seconds=3600,
            capability=capability,
            require_capability=True,
        )


def test_a_row_result_can_be_queried_again():
    store = MemoryTokenStore()
    eng = _query(
        store, _table_token(store), [{"op": "filter", "column": "dept", "cmp": "==", "value": "Eng"}]
    )
    total = _query(store, eng, [{"op": "sum", "column": "salary"}])
    assert store.resolve(total) == 133000


def test_select_narrows_what_the_derived_token_can_be_asked():
    store = MemoryTokenStore()
    names = _query(store, _table_token(store), [{"op": "select", "columns": ["name"]}])
    assert store.get(names).table.column_names() == ("name",)
    with pytest.raises(ValueError, match="unknown column"):
        _query(store, names, [{"op": "sum", "column": "salary"}])


def test_a_scalar_result_is_not_a_table():
    store = MemoryTokenStore()
    total = _query(store, _table_token(store), [{"op": "sum", "column": "salary"}])
    assert store.get(total).table is None
    with pytest.raises(ValueError, match="not a table token"):
        _query(store, total, [{"op": "count"}])


def test_another_session_cannot_query_the_table():
    store = MemoryTokenStore()
    token = _table_token(store)
    with pytest.raises(ValueError, match="policy denied"):
        handle_vault_table(
            {"table": token, "ops": [{"op": "count"}]},
            store=store,
            policy=SessionBoundPolicy(),
            session_id="someone_else",
            ttl_seconds=3600,
        )


def test_an_unknown_table_token_is_refused():
    store = MemoryTokenStore()
    with pytest.raises(ValueError, match="unknown or expired"):
        _query(store, "⟦tok_deadbeef⟧", [{"op": "count"}])


def test_the_derived_token_inherits_the_shortest_ttl():
    store = MemoryTokenStore()
    token = _table_token(store)
    derived = _query(store, token, [{"op": "count"}])
    assert store.get(derived).ttl == store.get(token).ttl


# --- what the model is told ------------------------------------------------


def test_describe_tables_names_the_columns_and_the_tool():
    note = describe_tables([("$.employees", SCHEMA)])
    for expected in ("$.employees", "name", "salary", "EUR/year", "dept", "vault_table"):
        assert expected in note


def test_describe_tables_is_none_without_tables():
    assert describe_tables([]) is None


def test_the_description_carries_no_values():
    assert "71000" not in describe_tables([("$.employees", SCHEMA)])


# --- rendering -------------------------------------------------------------


def test_a_row_result_rehydrates_as_json_not_python_repr():
    store = MemoryTokenStore()
    rows = _query(store, _table_token(store), [{"op": "limit", "n": 1}, {"op": "select", "columns": ["name"]}])
    shown = rehydrate(f"top: {rows}", SESSION, store, SessionBoundPolicy())
    assert shown == 'top: [{"name": "James Brown"}]'
    assert "'" not in shown


# --- config ----------------------------------------------------------------


def test_tables_are_declared_per_tool(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            "schemas:\n  hr.list:\n    tables:\n      - path: $.employees\n"
            "        columns:\n          - name: salary\n            semantic_type: salary\n"
            "            unit: EUR/year\n          - name: dept\n",
        )
    )
    path, schema = table_schemas_for(cfg, "hr.list")[0]
    assert path == "$.employees"
    assert schema.column_names() == ("salary", "dept")


def test_a_table_needs_at_least_one_column():
    with pytest.raises(ValueError, match="at least one column"):
        TableConfig(path="$.employees", columns=[])


def test_duplicate_column_names_are_refused():
    with pytest.raises(ValueError, match="duplicate column"):
        TableConfig(path="$.e", columns=[ColumnConfig(name="a"), ColumnConfig(name="a")])


def test_a_table_and_a_field_inside_it_are_refused_as_overlapping():
    # Tokenizing both would hide the list and then walk into a placeholder.
    with pytest.raises(ValueError, match="overlap"):
        ToolSchemaConfig(
            sensitive_fields=[SensitiveFieldConfig(path="$.employees[*].salary")],
            tables=[TableConfig(path="$.employees", columns=[ColumnConfig(name="salary")])],
        )


def test_a_table_path_is_validated_like_any_other():
    with pytest.raises(ValueError, match="recursive descent"):
        TableConfig(path="$..employees", columns=[ColumnConfig(name="salary")])


# --- persistence -----------------------------------------------------------


def test_a_table_token_keeps_its_schema_across_processes(tmp_path):
    path = tmp_path / "vault.db"
    writer = SQLiteTokenStore(path)
    try:
        token = _table_token(writer)
    finally:
        writer.close()

    reader = SQLiteTokenStore(path)
    try:
        record = reader.get(token)
        assert record.table == SCHEMA
        assert reader.resolve(_query(reader, token, [{"op": "max", "column": "salary"}])) == 71000
    finally:
        reader.close()


def _write(tmp_path, body: str):
    p = tmp_path / "vaultcompute.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_a_host_briefing_describes_tables_too():
    # A config declaring only tables used to produce no briefing at all, so in
    # a host the model got a collective token with nothing said about it.
    from vaultcompute.config import describe_config

    cfg = VaultComputeConfig(
        schemas={
            "hr.list": ToolSchemaConfig(
                tables=[
                    TableConfig(
                        path="$.employees",
                        columns=[
                            ColumnConfig(name="salary", semantic_type="salary", unit="EUR/year"),
                            ColumnConfig(name="dept"),
                        ],
                    )
                ]
            )
        }
    )
    brief = describe_config(cfg)
    assert brief is not None
    for expected in ("hr.list", "$.employees", "vault_table", "salary", "EUR/year", "dept"):
        assert expected in brief
    assert "71000" not in brief
