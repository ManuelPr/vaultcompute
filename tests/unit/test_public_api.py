import vaultcompute
from vaultcompute.session import VaultComputeSession


def test_top_level_public_api_is_explicit_and_importable():
    expected = {
        "PLACEHOLDER_PROMPT",
        "VaultComputeSession",
        "ProtectionError",
        "TableQueryCapability",
        "describe_config",
        "describe_schema",
        "rehydrate",
    }

    assert set(vaultcompute.__all__) == expected
    for name in expected:
        assert getattr(vaultcompute, name) is not None


def test_session_module_exports_only_the_mode_b_facade():
    import vaultcompute.session as session_module

    assert session_module.__all__ == ["VaultComputeSession"]
    assert session_module.VaultComputeSession is VaultComputeSession
