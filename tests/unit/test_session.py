from datetime import datetime, timedelta, timezone

import pytest

from blindfold import BlindfoldSession
from blindfold.config import BlindfoldConfig
from blindfold.core.capabilities import TableQueryCapability
from blindfold.core.vault import MemoryTokenStore
from blindfold.errors import ProtectionError


def _config() -> BlindfoldConfig:
    return BlindfoldConfig.model_validate(
        {
            "schemas": {
                "get_employee": {
                    "sensitive_fields": [
                        {"path": "$.salary", "semantic_type": "salary"},
                        {"path": "$.bonus", "required": False},
                    ]
                },
                "list_employees": {
                    "tables": [
                        {
                            "path": "$.employees",
                            "columns": [{"name": "name"}, {"name": "salary"}],
                        }
                    ]
                },
            }
        }
    )


def test_session_protects_and_renders_a_tool_result():
    session = BlindfoldSession(_config(), session_id="s")

    protected = session.protect_tool_result(
        "get_employee", {"name": "Ada", "salary": 71000}
    )

    assert protected["name"] == "Ada"
    assert protected["salary"] != 71000
    assert session.render_final_answer(f"Salary: {protected['salary']}") == "Salary: 71000"


def test_session_fails_closed_for_unknown_tool_or_missing_required_path():
    store = MemoryTokenStore()
    session = BlindfoldSession(_config(), session_id="s", store=store)

    with pytest.raises(ProtectionError, match="no protected schema"):
        session.protect_tool_result("unconfigured", {"salary": 71000})
    with pytest.raises(ProtectionError, match=r"missing.*\$\.salary"):
        session.protect_tool_result("get_employee", {"compensation": 71000})

    assert store.find_by_session("s") == []


def test_optional_path_may_be_absent():
    session = BlindfoldSession(_config(), session_id="s")
    protected = session.protect_tool_result("get_employee", {"salary": 71000})
    assert "bonus" not in protected


def test_declared_table_must_be_present_and_a_list():
    session = BlindfoldSession(_config(), session_id="s")

    with pytest.raises(ProtectionError, match="missing required"):
        session.protect_tool_result("list_employees", {})
    with pytest.raises(ProtectionError, match="did not contain lists"):
        session.protect_tool_result("list_employees", {"employees": "raw secret"})


def test_call_protected_tool_returns_only_the_protected_result():
    session = BlindfoldSession(_config(), session_id="s")

    protected = session.call_protected_tool(
        "get_employee", lambda employee_id: {"id": employee_id, "salary": 71000}, 4
    )

    assert protected["id"] == 4
    assert protected["salary"] != 71000


def test_call_protected_tool_propagates_failure_without_minting_tokens():
    store = MemoryTokenStore()
    session = BlindfoldSession(_config(), session_id="s", store=store)

    def failing_tool():
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError, match="tool failed"):
        session.call_protected_tool("get_employee", failing_tool)

    assert store.find_by_session("s") == []


async def test_call_protected_tool_async_returns_only_the_protected_result():
    session = BlindfoldSession(_config(), session_id="s")

    async def get_employee(employee_id):
        return {"id": employee_id, "salary": 71000}

    protected = await session.call_protected_tool_async(
        "get_employee", get_employee, 4
    )

    assert protected["id"] == 4
    assert protected["salary"] != 71000


def test_authorized_query_requires_and_enforces_exact_capability():
    session = BlindfoldSession(_config(), session_id="s")
    protected = session.protect_tool_result(
        "list_employees",
        {"employees": [{"name": "Ada", "salary": 71000}]},
    )
    query = {"table": protected["employees"], "ops": [{"op": "count"}]}
    capability = TableQueryCapability.issue(
        session_id="s",
        table_token=query["table"],
        ops=query["ops"],
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=1),
    )

    result_token = session.execute_authorized_query(query, capability=capability)
    assert session.render_final_answer(result_token) == "1"

    with pytest.raises(ValueError, match="not authorized"):
        session.execute_authorized_query(
            {"table": query["table"], "ops": [{"op": "limit", "n": 1}]},
            capability=capability,
        )


@pytest.mark.parametrize(
    ("query_change", "message"),
    [
        ({"ops": [{"op": "filter", "column": "salary", "cmp": ">", "value": 71000}]}, "not authorized"),
        ({"ops": [{"op": "filter", "column": "salary", "cmp": "<", "value": 70000}]}, "not authorized"),
        ({"table": "⟦tok_00000000000000000000000000000000⟧"}, "another table"),
    ],
)
def test_capability_rejects_adaptive_or_cross_table_queries(query_change, message):
    session = BlindfoldSession(_config(), session_id="s")
    protected = session.protect_tool_result(
        "list_employees",
        {"employees": [{"name": "Ada", "salary": 71000}]},
    )
    authorized = {
        "table": protected["employees"],
        "ops": [
            {"op": "filter", "column": "salary", "cmp": ">", "value": 70000}
        ],
    }
    capability = TableQueryCapability.issue(
        session_id="s",
        table_token=authorized["table"],
        ops=authorized["ops"],
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=1),
    )
    proposed = {**authorized, **query_change}

    with pytest.raises(ValueError, match=message):
        session.execute_authorized_query(proposed, capability=capability)


def test_capability_rejects_another_session_and_expiry():
    owner = BlindfoldSession(_config(), session_id="owner")
    protected = owner.protect_tool_result(
        "list_employees", {"employees": [{"name": "Ada", "salary": 71000}]}
    )
    query = {"table": protected["employees"], "ops": [{"op": "count"}]}

    other_session_capability = TableQueryCapability.issue(
        session_id="owner",
        table_token=query["table"],
        ops=query["ops"],
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=1),
    )
    other = BlindfoldSession(
        _config(), session_id="other", store=owner.store, policy=owner.policy
    )
    with pytest.raises(ValueError, match="another session"):
        other.execute_authorized_query(query, capability=other_session_capability)

    expired = TableQueryCapability.issue(
        session_id="owner",
        table_token=query["table"],
        ops=query["ops"],
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="expired"):
        owner.execute_authorized_query(query, capability=expired)


def test_rendering_unknown_and_cross_session_tokens_never_reveals_values():
    owner = BlindfoldSession(_config(), session_id="owner")
    protected = owner.protect_tool_result(
        "get_employee", {"name": "Ada", "salary": 71000}
    )
    other = BlindfoldSession(
        _config(), session_id="other", store=owner.store, policy=owner.policy
    )

    assert other.render_final_answer(protected["salary"]) == "[redacted]"
    assert (
        owner.render_final_answer("⟦tok_00000000000000000000000000000000⟧")
        == "[unknown token]"
    )


def test_reusing_a_capability_cannot_change_what_it_authorizes():
    session = BlindfoldSession(_config(), session_id="s")
    protected = session.protect_tool_result(
        "list_employees", {"employees": [{"name": "Ada", "salary": 71000}]}
    )
    query = {"table": protected["employees"], "ops": [{"op": "count"}]}
    capability = TableQueryCapability.issue(
        session_id="s",
        table_token=query["table"],
        ops=query["ops"],
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=1),
    )

    first = session.execute_authorized_query(query, capability=capability)
    second = session.execute_authorized_query(query, capability=capability)

    assert session.render_final_answer(first) == "1"
    assert session.render_final_answer(second) == "1"
    with pytest.raises(ValueError, match="not authorized"):
        session.execute_authorized_query(
            {"table": query["table"], "ops": [{"op": "limit", "n": 1}]},
            capability=capability,
        )


def test_model_instructions_are_owned_by_the_session():
    session = BlindfoldSession(_config(), session_id="s")
    assert "get_employee" in session.model_instructions
    assert "never" in session.model_instructions.lower()
