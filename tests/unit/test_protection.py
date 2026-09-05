from datetime import datetime, timedelta, timezone

import pytest

from vaultcompute.core.lineage import Column, TableSchema
from vaultcompute.core.protection import protect_result, protect_results
from vaultcompute.core.tokenizer import SchemaField
from vaultcompute.core.vault import MemoryTokenStore
from vaultcompute.errors import ProtectionError


TTL = datetime.now(tz=timezone.utc) + timedelta(hours=1)


def _protect(results, *, fields=None, tables=None, required_tables=None):
    store = MemoryTokenStore()
    protected = protect_results(
        results,
        source_name="tool",
        fields=fields or [],
        tables=tables or [],
        required_table_paths=required_tables or set(),
        store=store,
        session_id="s",
        ttl=TTL,
    )
    return protected, store


def test_required_fields_are_checked_before_any_value_is_stored():
    store = MemoryTokenStore()
    with pytest.raises(ProtectionError, match=r"missing.*\$\.iban"):
        protect_result(
            {"salary": 71000},
            source_name="tool",
            fields=[SchemaField("$.salary", required=True), SchemaField("$.iban", required=True)],
            tables=[],
            required_table_paths=set(),
            store=store,
            session_id="s",
            ttl=TTL,
        )
    assert store.find_by_session("s") == []


def test_optional_field_may_be_absent():
    protected, _store = _protect(
        [{"salary": 71000}],
        fields=[SchemaField("$.salary", required=True), SchemaField("$.bonus")],
    )
    assert protected[0]["salary"] != 71000
    assert "bonus" not in protected[0]


def test_required_paths_may_be_split_across_several_result_parts():
    protected, _store = _protect(
        [{"salary": 71000}, {"iban": "IT00X"}],
        fields=[SchemaField("$.salary", required=True), SchemaField("$.iban", required=True)],
    )
    assert protected[0]["salary"] != 71000
    assert protected[1]["iban"] != "IT00X"


def test_a_declared_table_must_be_a_list_even_when_optional():
    schema = TableSchema(columns=(Column("salary"),))
    with pytest.raises(ProtectionError, match="did not contain lists"):
        _protect([{"employees": "not a table"}], tables=[("$.employees", schema)])
