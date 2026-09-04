"""Adversarial checks for the Mode B privacy boundaries."""

from datetime import datetime, timedelta, timezone

import pytest

from blindfold import BlindfoldSession, TableQueryCapability
from blindfold.config import BlindfoldConfig
from blindfold.errors import ProtectionError


def _session(session_id: str = "owner", **kwargs) -> BlindfoldSession:
    config = BlindfoldConfig.model_validate(
        {
            "schemas": {
                "get_employee": {
                    "sensitive_fields": [{"path": "$.salary"}]
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
    return BlindfoldSession(config, session_id=session_id, **kwargs)


def test_raw_tool_value_never_enters_the_model_visible_stream():
    session = _session()
    model_visible = [session.model_instructions]

    protected = session.call_protected_tool(
        "get_employee", lambda: {"name": "Ada", "salary": 71000}
    )
    model_visible.append(str(protected))
    model_answer = f"Ada earns {protected['salary']}."
    model_visible.append(model_answer)

    assert "71000" not in "\n".join(model_visible)
    assert session.render_final_answer(model_answer) == "Ada earns 71000."


def test_shape_drift_stops_before_any_result_is_sent_to_the_model():
    session = _session()
    model_visible = [session.model_instructions]

    with pytest.raises(ProtectionError, match="missing"):
        protected = session.call_protected_tool(
            "get_employee", lambda: {"name": "Ada", "compensation": 71000}
        )
        model_visible.append(str(protected))

    assert "71000" not in "\n".join(model_visible)


def test_adaptive_threshold_attack_cannot_reuse_exact_authority():
    session = _session()
    protected = session.protect_tool_result(
        "list_employees", {"employees": [{"name": "Ada", "salary": 71000}]}
    )
    table = protected["employees"]
    authorized_ops = [
        {"op": "filter", "column": "salary", "cmp": ">", "value": 70000},
        {"op": "count"},
    ]
    capability = TableQueryCapability.issue(
        session_id=session.session_id,
        table_token=table,
        ops=authorized_ops,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=1),
    )

    authorized_result = session.execute_authorized_query(
        {"table": table, "ops": authorized_ops}, capability=capability
    )
    assert session.render_final_answer(authorized_result) == "1"

    for threshold in (70500, 70750, 70875, 70937, 70968):
        attack = {
            "table": table,
            "ops": [
                {
                    "op": "filter",
                    "column": "salary",
                    "cmp": ">",
                    "value": threshold,
                },
                {"op": "count"},
            ],
        }
        with pytest.raises(ValueError, match="not authorized"):
            session.execute_authorized_query(attack, capability=capability)


def test_forged_and_foreign_placeholders_do_not_reveal_data():
    owner = _session()
    protected = owner.protect_tool_result("get_employee", {"salary": 71000})
    attacker = _session("attacker", store=owner.store, policy=owner.policy)

    assert attacker.render_final_answer(protected["salary"]) == "[redacted]"
    assert (
        owner.render_final_answer("⟦tok_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa⟧")
        == "[unknown token]"
    )
