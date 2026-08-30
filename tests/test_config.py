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


class TestThrottleSettings:
    def test_defaults(self):
        load_config_from_dict({"a": {"webhook_url_env": "W", "message_template": "A"}})
        cfg = get_notification_config("a")
        assert cfg.resolve_dedup_window() == 0
        assert cfg.resolve_max_per_minute() == 30

    def test_env_vars_override_defaults(self, monkeypatch):
        monkeypatch.setenv("HIBIKI_DISCORD_DEDUP_WINDOW", "120")
        monkeypatch.setenv("HIBIKI_DISCORD_MAX_PER_MINUTE", "5")
        load_config_from_dict({"a": {"webhook_url_env": "W", "message_template": "A"}})
        cfg = get_notification_config("a")
        assert cfg.resolve_dedup_window() == 120
        assert cfg.resolve_max_per_minute() == 5

    def test_per_type_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("HIBIKI_DISCORD_MAX_PER_MINUTE", "5")
        load_config_from_dict({
            "a": {"webhook_url_env": "W", "message_template": "A", "max_per_minute": 2}
        })
        assert get_notification_config("a").resolve_max_per_minute() == 2

    def test_unusable_env_var_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("HIBIKI_DISCORD_MAX_PER_MINUTE", "not-a-number")
        load_config_from_dict({"a": {"webhook_url_env": "W", "message_template": "A"}})
        assert get_notification_config("a").resolve_max_per_minute() == 30

    def test_rejects_a_negative_dedup_window(self):
        with pytest.raises(ValueError, match="dedup_window"):
            load_config_from_dict({
                "a": {"webhook_url_env": "W", "dedup_window": -1}
            })

    def test_rejects_a_zero_send_budget(self):
        """There is no 'send nothing' setting; use enabled = false."""
        with pytest.raises(ValueError, match="max_per_minute"):
            load_config_from_dict({
                "a": {"webhook_url_env": "W", "max_per_minute": 0}
            })


class TestEnabledFlag:
    def test_rejects_a_non_boolean_enabled_flag(self):
        """A quoted "false" is truthy, so it would keep the type firing."""
        with pytest.raises(ValueError, match="enabled"):
            load_config_from_dict({
                "a": {"webhook_url_env": "W", "enabled": "false"}
            })

    def test_real_booleans_still_work(self):
        load_config_from_dict({
            "on": {"webhook_url_env": "W", "enabled": True},
            "off": {"webhook_url_env": "W", "enabled": False},
        })
        assert get_notification_config("on").enabled is True
        assert get_notification_config("off").enabled is False


class TestEmbedSettings:
    def test_embed_defaults_to_false(self):
        load_config_from_dict({"a": {"webhook_url_env": "W", "message_template": "A"}})
        assert get_notification_config("a").embed is False

    def test_embed_options_are_parsed(self):
        load_config_from_dict({
            "a": {
                "webhook_url_env": "W",
                "message_template": "A",
                "embed": True,
                "embed_title": "Signup",
                "embed_color": "#5865F2",
            }
        })
        cfg = get_notification_config("a")
        assert cfg.embed is True
        assert cfg.embed_title == "Signup"
        assert cfg.embed_color == 0x5865F2

    def test_rejects_a_non_boolean_embed_flag(self):
        """A truthy string such as "false" would otherwise switch embeds on."""
        with pytest.raises(ValueError, match="embed"):
            load_config_from_dict({
                "a": {"webhook_url_env": "W", "embed": "false"}
            })

    def test_bad_colour_names_the_notification(self):
        with pytest.raises(ValueError, match="'a'"):
            load_config_from_dict({
                "a": {"webhook_url_env": "W", "embed_color": "chartreuse"}
            })
