import blindfold
from blindfold.session import BlindfoldSession


def test_top_level_public_api_is_explicit_and_importable():
    expected = {
        "PLACEHOLDER_PROMPT",
        "BlindfoldSession",
        "ProtectionError",
        "TableQueryCapability",
        "describe_config",
        "describe_schema",
        "rehydrate",
    }

    assert set(blindfold.__all__) == expected
    for name in expected:
        assert getattr(blindfold, name) is not None


def test_session_module_exports_only_the_mode_b_facade():
    import blindfold.session as session_module

    assert session_module.__all__ == ["BlindfoldSession"]
    assert session_module.BlindfoldSession is BlindfoldSession
