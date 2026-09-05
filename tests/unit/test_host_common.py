import pytest

from vaultcompute.hosts.common import ProtectionError, session_id


def test_host_session_id_is_required():
    for event in ({}, {"session_id": None}, {"session_id": ""}, {"session_id": 123}):
        with pytest.raises(ProtectionError, match="session_id"):
            session_id(event)


def test_host_session_id_is_not_replaced_with_a_shared_fallback():
    assert session_id({"session_id": "session-a"}) == "session-a"
