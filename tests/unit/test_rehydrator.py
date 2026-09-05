from datetime import datetime, timedelta, timezone

from vaultcompute.core.lineage import Lineage, Policy, VaultRecord
from vaultcompute.core.policy import SessionBoundPolicy
from vaultcompute.core.rehydrator import PLACEHOLDER_PROMPT, TOKEN_PATTERN, rehydrate
from vaultcompute.core.vault import MemoryTokenStore
from vaultcompute.ports.token_store import TokenStore


def _put(store: MemoryTokenStore, token: str, value, *, session="s", reveal: bool = True) -> None:
    now = datetime.now(tz=timezone.utc)
    store.put(
        VaultRecord(
            token=token,
            value=value,
            dtype="string" if isinstance(value, str) else "number",
            semantic_type=None,
            unit=None,
            session_id=session,
            created_at=now,
            ttl=now + timedelta(hours=1),
            lineage=Lineage(op="literal"),
            policy=Policy(reveal_to_frontend=reveal),
        )
    )


def test_regex_matches_valid_and_rejects_invalid():
    assert TOKEN_PATTERN.fullmatch(TokenStore.mint_token())
    assert TOKEN_PATTERN.fullmatch("⟦tok_deadbeefdeadbeef⟧")
    assert TOKEN_PATTERN.fullmatch("⟦tok_00000000⟧")  # the 8-hex tokens of older vaults
    assert not TOKEN_PATTERN.fullmatch("⟦tok_TOOSHORT⟧")
    assert not TOKEN_PATTERN.fullmatch("⟦tok_gggggggg⟧")  # 'g' not hex
    assert not TOKEN_PATTERN.fullmatch("⟦tok_deadbeefdead⟧")  # neither width
    assert not TOKEN_PATTERN.fullmatch("(tok_deadbeef)")


def test_placeholder_prompt_forbids_adaptive_probing():
    lowered = PLACEHOLDER_PROMPT.lower()
    assert "binary search" in lowered
    assert "deliberate errors" in lowered
    assert "token cloning" in lowered


def test_rehydrate_happy_path():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    out = rehydrate(
        "The winner is ⟦tok_00000001⟧.",
        "s",
        store,
        SessionBoundPolicy(),
    )
    assert out == "The winner is Alice."


def test_rehydrate_multiple_tokens_in_one_string():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    _put(store, "⟦tok_00000002⟧", 42)
    out = rehydrate(
        "⟦tok_00000001⟧ is number ⟦tok_00000002⟧.",
        "s",
        store,
        SessionBoundPolicy(),
    )
    assert out == "Alice is number 42."


def test_rehydrate_unknown_token_flagged():
    store = MemoryTokenStore()
    out = rehydrate("Where is ⟦tok_deadbeef⟧?", "s", store, SessionBoundPolicy())
    assert out == "Where is [unknown token]?"


def test_rehydrate_wrong_session_flagged_as_redacted():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "secret", session="other")
    out = rehydrate("value: ⟦tok_00000001⟧", "s", store, SessionBoundPolicy())
    assert out == "value: [redacted]"


def test_rehydrate_reveal_denied_flagged_as_redacted():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "secret", reveal=False)
    out = rehydrate("value: ⟦tok_00000001⟧", "s", store, SessionBoundPolicy())
    assert out == "value: [redacted]"


def test_rehydrate_no_tokens_passthrough():
    store = MemoryTokenStore()
    out = rehydrate("plain string, no tokens", "s", store, SessionBoundPolicy())
    assert out == "plain string, no tokens"


def test_rehydrate_does_not_match_similar_but_wrong_syntax():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    # Wrong brackets should not trigger substitution.
    out = rehydrate("(tok_00000001) and [tok_00000001]", "s", store, SessionBoundPolicy())
    assert out == "(tok_00000001) and [tok_00000001]"


def test_rehydrate_public_from_top_level_module():
    from vaultcompute import rehydrate as top_level_rehydrate

    assert top_level_rehydrate is rehydrate
