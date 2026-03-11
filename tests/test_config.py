import os
import pytest
import tempfile

from hibiki_discord.config import (
    load_config,
    load_config_from_dict,
    get_notification_config,
    get_all_configs,
    reset,
    NotificationConfig,
)


@pytest.fixture(autouse=True)
def clean_config():
    reset()
    yield
    reset()


def _write_toml(content: str) -> str:
    """Write TOML content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False)
    f.write(content)
    f.close()
    return f.name


class TestLoadConfig:
    def test_loads_valid_toml(self):
        path = _write_toml("""
[notifications.user_signup]
webhook_url_env = "DISCORD_SIGNUP_WEBHOOK"
username = "Signup Bot"
message_template = "New user: {email}"
""")
        try:
            result = load_config(path)
            assert "user_signup" in result
            cfg = result["user_signup"]
            assert isinstance(cfg, NotificationConfig)
            assert cfg.webhook_url_env == "DISCORD_SIGNUP_WEBHOOK"
            assert cfg.username == "Signup Bot"
            assert cfg.message_template == "New user: {email}"
            assert cfg.enabled is True
        finally:
            os.unlink(path)

    def test_loads_multiple_notifications(self):
        path = _write_toml("""
[notifications.signup]
webhook_url_env = "WEBHOOK_A"
message_template = "Signup: {email}"

[notifications.deploy]
webhook_url_env = "WEBHOOK_B"
message_template = "Deployed {service}"
enabled = false
""")
        try:
            result = load_config(path)
            assert len(result) == 2
            assert result["deploy"].enabled is False
        finally:
            os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.toml")

    def test_missing_webhook_url_env_raises(self):
        path = _write_toml("""
[notifications.bad]
username = "Bot"
message_template = "Hello"
""")
        try:
            with pytest.raises(ValueError, match="missing 'webhook_url_env'"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_empty_notifications_section(self):
        path = _write_toml("""
[notifications]
""")
        try:
            result = load_config(path)
            assert result == {}
        finally:
            os.unlink(path)

    def test_respects_hibiki_discord_config_env(self, monkeypatch):
        path = _write_toml("""
[notifications.test]
webhook_url_env = "TEST_WEBHOOK"
message_template = "Hello"
""")
        try:
            monkeypatch.setenv("HIBIKI_DISCORD_CONFIG", path)
            result = load_config()
            assert "test" in result
        finally:
            os.unlink(path)


class TestLoadConfigFromDict:
    def test_loads_from_dict(self):
        result = load_config_from_dict({
            "signup": {
                "webhook_url_env": "WEBHOOK_A",
                "username": "Bot",
                "message_template": "Hello {name}",
            }
        })
        assert "signup" in result
        assert result["signup"].webhook_url_env == "WEBHOOK_A"

    def test_missing_webhook_url_env_raises(self):
        with pytest.raises(ValueError, match="missing 'webhook_url_env'"):
            load_config_from_dict({"bad": {"username": "Bot"}})


class TestNotificationConfig:
    def test_resolve_webhook_url(self, monkeypatch):
        monkeypatch.setenv("MY_WEBHOOK", "https://discord.com/api/webhooks/test")
        cfg = NotificationConfig(webhook_url_env="MY_WEBHOOK")
        assert cfg.resolve_webhook_url() == "https://discord.com/api/webhooks/test"

    def test_resolve_webhook_url_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_WEBHOOK", raising=False)
        cfg = NotificationConfig(webhook_url_env="MISSING_WEBHOOK")
        assert cfg.resolve_webhook_url() is None


class TestGetNotificationConfig:
    def test_returns_none_for_unknown(self):
        assert get_notification_config("nonexistent") is None

    def test_returns_config_after_load(self):
        load_config_from_dict({
            "test": {
                "webhook_url_env": "WEBHOOK",
                "message_template": "Hi",
            }
        })
        cfg = get_notification_config("test")
        assert cfg is not None
        assert cfg.webhook_url_env == "WEBHOOK"


class TestGetAllConfigs:
    def test_returns_empty_before_load(self):
        assert get_all_configs() == {}

    def test_returns_all_after_load(self):
        load_config_from_dict({
            "a": {"webhook_url_env": "W1", "message_template": "A"},
            "b": {"webhook_url_env": "W2", "message_template": "B"},
        })
        all_configs = get_all_configs()
        assert len(all_configs) == 2
        assert "a" in all_configs
        assert "b" in all_configs
