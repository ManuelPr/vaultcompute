import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from vaultcompute import TableQueryCapability, VaultComputeSession
from vaultcompute.config import VaultComputeConfig

sys.stdout.reconfigure(encoding="utf-8")


def list_employees():
    return {"employees": [
        {"name": "Manuel", "salary": 62000},
        {"name": "Andrea", "salary": 71000},
    ]}


config = VaultComputeConfig.model_validate({
    "schemas": {
        "list_employees": {
            "tables": [{
                "path": "$.employees",
                "columns": [{"name": "name"}, {"name": "salary"}],
            }]
        }
    }
})
session = VaultComputeSession(config, session_id=uuid4().hex)
protected = session.call_protected_tool("list_employees", list_employees)
print("Tool result for the model:", json.dumps(protected, ensure_ascii=False))

# The trusted application authorizes this exact request: highest-paid employee.
ops = [
    {"op": "sort_by", "column": "salary", "desc": True},
    {"op": "limit", "n": 1},
    {"op": "select", "columns": ["name", "salary"]},
]
capability = TableQueryCapability.issue(
    session_id=session.session_id,
    table_token=protected["employees"],
    ops=ops,
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
)

# Scripted stand-in for the model's proposed vault_table call.
proposed_query = {"table": protected["employees"], "ops": ops}
result_token = session.execute_authorized_query(
    proposed_query, capability=capability,
)
model_answer = f"Highest-paid employee: {result_token}"
print("Model answer:", model_answer)
print("User sees:", session.render_final_answer(model_answer))
