from datetime import datetime, timedelta, timezone

import pytest

from blindfold.core.capabilities import TableQueryCapability


NOW = datetime.now(tz=timezone.utc)
OPS = [{"op": "filter", "column": "salary", "cmp": ">", "value": 70000}]


def _cap(**overrides):
    values = {
        "session_id": "s",
        "table_token": "⟦tok_00000001⟧",
        "ops": OPS,
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return TableQueryCapability.issue(**values)


def test_exact_authorized_query_is_accepted():
    _cap().authorize(session_id="s", table_token="⟦tok_00000001⟧", ops=OPS)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"session_id": "other", "table_token": "⟦tok_00000001⟧", "ops": OPS}, "session"),
        ({"session_id": "s", "table_token": "⟦tok_00000002⟧", "ops": OPS}, "another table"),
        (
            {
                "session_id": "s",
                "table_token": "⟦tok_00000001⟧",
                "ops": [{"op": "filter", "column": "salary", "cmp": ">", "value": 71000}],
            },
            "not authorized",
        ),
    ],
)
def test_agent_cannot_change_the_authorized_scope(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _cap().authorize(**kwargs)


def test_expired_capability_is_refused():
    cap = _cap(expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="expired"):
        cap.authorize(session_id="s", table_token="⟦tok_00000001⟧", ops=OPS)


def test_capability_copies_the_operation_semantics_at_issue_time():
    ops = [{"op": "filter", "column": "salary", "cmp": ">", "value": 70000}]
    cap = _cap(ops=ops)
    ops[0]["value"] = 71000
    with pytest.raises(ValueError, match="not authorized"):
        cap.authorize(session_id="s", table_token="⟦tok_00000001⟧", ops=ops)
