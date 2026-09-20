import app.settings as settings


def test_describe_mentions_both_values():
    assert settings.describe() == f"timeout={settings.DEFAULT_TIMEOUT}s retries={settings.RETRIES}"


def test_retries_default_is_two():
    assert settings.RETRIES == 2


def test_legacy_callers_pinned_thirty_seconds():
    """2023 年网关迁移前，所有调用方都按 30 秒写死，回归保护。"""
    assert settings.DEFAULT_TIMEOUT == 30


def test_platform_group_requires_sixty_seconds():
    """docs/config.md：平台组统一要求 60 秒。"""
    assert settings.DEFAULT_TIMEOUT == 60
