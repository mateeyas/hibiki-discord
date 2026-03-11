import os
import tomllib
from typing import Optional

_notifications: dict = {}


class NotificationConfig:
    """Configuration for a single notification type."""

    def __init__(
        self,
        webhook_url_env: str,
        username: Optional[str] = None,
        message_template: Optional[str] = None,
        enabled: bool = True,
    ):
        self.webhook_url_env = webhook_url_env
        self.username = username
        self.message_template = message_template
        self.enabled = enabled

    def resolve_webhook_url(self) -> Optional[str]:
        """Resolve the webhook URL from the environment variable at call time."""
        return os.environ.get(self.webhook_url_env)


def load_config(path: Optional[str] = None) -> dict:
    """
    Load notification config from a TOML file.

    Args:
        path: Path to the TOML config file. Defaults to the
            HIBIKI_DISCORD_CONFIG env var, or "hibiki-discord.toml"
            in the current working directory.

    Returns:
        Dict of notification type name -> NotificationConfig.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file is malformed.
    """
    global _notifications

    if path is None:
        path = os.environ.get("HIBIKI_DISCORD_CONFIG", "hibiki-discord.toml")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    notifications_data = data.get("notifications", {})
    if not isinstance(notifications_data, dict):
        raise ValueError("'notifications' must be a table in the TOML config")

    _notifications = {}
    for name, cfg in notifications_data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Notification '{name}' must be a table")
        if "webhook_url_env" not in cfg:
            raise ValueError(f"Notification '{name}' is missing 'webhook_url_env'")

        _notifications[name] = NotificationConfig(
            webhook_url_env=cfg["webhook_url_env"],
            username=cfg.get("username"),
            message_template=cfg.get("message_template"),
            enabled=cfg.get("enabled", True),
        )

    return _notifications


def load_config_from_dict(notifications: dict) -> dict:
    """
    Load notification config from a dictionary (programmatic alternative to TOML).

    Args:
        notifications: Dict of notification type name -> config dict.
            Each config dict must have "webhook_url_env" and optionally
            "username", "message_template", "enabled".

    Returns:
        Dict of notification type name -> NotificationConfig.
    """
    global _notifications

    _notifications = {}
    for name, cfg in notifications.items():
        if "webhook_url_env" not in cfg:
            raise ValueError(f"Notification '{name}' is missing 'webhook_url_env'")

        _notifications[name] = NotificationConfig(
            webhook_url_env=cfg["webhook_url_env"],
            username=cfg.get("username"),
            message_template=cfg.get("message_template"),
            enabled=cfg.get("enabled", True),
        )

    return _notifications


def get_notification_config(notification_type: str) -> Optional[NotificationConfig]:
    """Get the config for a notification type, or None if not configured."""
    return _notifications.get(notification_type)


def get_all_configs() -> dict:
    """Get all loaded notification configs."""
    return dict(_notifications)


def reset():
    """Clear all loaded configs. Intended for testing."""
    global _notifications
    _notifications = {}
