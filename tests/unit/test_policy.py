from datetime import datetime, timedelta, timezone

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.policy import SessionBoundPolicy
from blindfold.ports.policy import DetokenizeContext

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _rec(
    *, session: str = "s", reveal: bool = True, compute: bool = True, query: bool = True
) -> VaultRecord:
    return VaultRecord(
        token="⟦tok_00000001⟧",
        value="v",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id=session,
        created_at=NOW,
        ttl=NOW + timedelta(hours=1),
        lineage=Lineage(op="literal"),
        policy=Policy(
            reveal_to_frontend=reveal,
            can_be_input_to_compute=compute,
            can_be_input_to_query=query,
        ),
    )


def test_can_reveal_matching_session_and_permissive_policy():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="s"), _rec()) is True


def test_can_reveal_denied_across_sessions():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="other"), _rec(session="s")) is False


def test_can_reveal_denied_by_record_policy():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="s"), _rec(reveal=False)) is False


def test_can_compute_matching_session_and_permissive_policy():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="s"), _rec()) is True


def test_can_compute_denied_across_sessions():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="other"), _rec(session="s")) is False


def test_can_compute_denied_by_record_policy():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="s"), _rec(compute=False)) is False


def test_query_permission_is_independent_from_arbitrary_compute():
    p = SessionBoundPolicy()
    record = _rec(compute=False, query=True)
    assert not p.can_compute(DetokenizeContext(session_id="s"), record)
    assert p.can_query(DetokenizeContext(session_id="s"), record)


def test_can_query_is_session_bound_and_honors_record_policy():
    p = SessionBoundPolicy()
    assert not p.can_query(DetokenizeContext(session_id="other"), _rec())
    assert not p.can_query(DetokenizeContext(session_id="s"), _rec(query=False))
