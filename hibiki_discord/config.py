import os
import tomllib
from typing import Optional

from .embeds import parse_color

_notifications: dict = {}

# Seconds during which identical notifications collapse into one send.
# Off by default, unlike hibiki-logger, which dedups at 300 seconds: there
# a repeat is the same fault firing again, whereas a repeated business
# notification is usually a second real event -- two signups a second
# apart are two customers, not one message sent twice. Enable it per
# notification type, or globally with HIBIKI_DISCORD_DEDUP_WINDOW, for
# types where a repeat really is a repeat.
DEFAULT_DEDUP_WINDOW = 0

# Webhook send budget over a sliding 60 second window. This bounds the
# average only; Discord's roughly 5 requests per 2 seconds is a limit on
# the instant, which hibiki_discord.throttle bounds separately by pacing
# sends that would otherwise leave together.
DEFAULT_MAX_PER_MINUTE = 30


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer env var, falling back on anything unusable.

    `minimum` is 1 by default but 0 where zero is a meaningful setting, so
    an operator can switch a stage off rather than silently receiving the
    default they were trying to override.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


class NotificationConfig:
    """Configuration for a single notification type."""

    def __init__(
        self,
        webhook_url_env: str,
        username: Optional[str] = None,
        message_template: Optional[str] = None,
        enabled: bool = True,
        embed: bool = False,
        embed_title: Optional[str] = None,
        embed_color: Optional[int] = None,
        dedup_window: Optional[int] = None,
        max_per_minute: Optional[int] = None,
    ):
        self.webhook_url_env = webhook_url_env
        self.username = username
        self.message_template = message_template
        self.enabled = enabled
        self.embed = embed
        self.embed_title = embed_title
        self.embed_color = embed_color
        self.dedup_window = dedup_window
        self.max_per_minute = max_per_minute

    def resolve_webhook_url(self) -> Optional[str]:
        """Resolve the webhook URL from the environment variable at call time."""
        return os.environ.get(self.webhook_url_env)

    def resolve_dedup_window(self) -> int:
        """Dedup window in seconds: per-type override, env var, or default."""
        if self.dedup_window is not None:
            return self.dedup_window
        return _int_env("HIBIKI_DISCORD_DEDUP_WINDOW", DEFAULT_DEDUP_WINDOW, minimum=0)

    def resolve_max_per_minute(self) -> int:
        """Send budget per 60 seconds: per-type override, env var, or default."""
        if self.max_per_minute is not None:
            return self.max_per_minute
        return _int_env("HIBIKI_DISCORD_MAX_PER_MINUTE", DEFAULT_MAX_PER_MINUTE)


def _build_config(name: str, cfg: dict) -> NotificationConfig:
    """Validate one notification block and build its config.

    Bad values raise here rather than at send time, so a typo surfaces at
    startup instead of silently changing how an alert is delivered.
    """
    if "webhook_url_env" not in cfg:
        raise ValueError(f"Notification '{name}' is missing 'webhook_url_env'")

    def _optional_int(key: str, minimum: int) -> Optional[int]:
        value = cfg.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(
                f"Notification '{name}' has an invalid '{key}': {value!r} "
                f"(expected an integer >= {minimum})"
            )
        return value

    def _bool(key: str, default: bool) -> bool:
        value = cfg.get(key, default)
        if not isinstance(value, bool):
            # A quoted "false" is a truthy string, so an unchecked value
            # here would invert what the operator asked for: a notification
            # they believe is switched off would keep firing, silently.
            raise ValueError(
                f"Notification '{name}' has an invalid '{key}': {value!r} "
                f"(expected true or false)"
            )
        return value

    try:
        embed_color = parse_color(cfg.get("embed_color"))
    except ValueError as e:
        raise ValueError(f"Notification '{name}': {e}") from None

    return NotificationConfig(
        webhook_url_env=cfg["webhook_url_env"],
        username=cfg.get("username"),
        message_template=cfg.get("message_template"),
        enabled=_bool("enabled", True),
        embed=_bool("embed", False),
        embed_title=cfg.get("embed_title"),
        embed_color=embed_color,
        dedup_window=_optional_int("dedup_window", 0),
        max_per_minute=_optional_int("max_per_minute", 1),
    )


def _reset_throttle_state() -> None:
    """Drop throttle state so reloaded config starts from a clean slate.

    Imported here rather than at module scope because throttle state is
    only relevant once something is sent.
    """
    from .throttle import reset_throttle

    reset_throttle()


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

    parsed = {}
    for name, cfg in notifications_data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Notification '{name}' must be a table")
        parsed[name] = _build_config(name, cfg)

    _notifications = parsed
    _reset_throttle_state()

    return _notifications


def load_config_from_dict(notifications: dict) -> dict:
    """
    Load notification config from a dictionary (programmatic alternative to TOML).

    Args:
        notifications: Dict of notification type name -> config dict.
            Each config dict must have "webhook_url_env" and optionally
            "username", "message_template", "enabled", "embed",
            "embed_title", "embed_color", "dedup_window", "max_per_minute".

    Returns:
        Dict of notification type name -> NotificationConfig.
    """
    global _notifications

    parsed = {}
    for name, cfg in notifications.items():
        parsed[name] = _build_config(name, cfg)

    _notifications = parsed
    _reset_throttle_state()

    return _notifications


def get_notification_config(notification_type: str) -> Optional[NotificationConfig]:
    """Get the config for a notification type, or None if not configured."""
    return _notifications.get(notification_type)


def get_all_configs() -> dict:
    """Get all loaded notification configs."""
    return dict(_notifications)


def reset():
    """Clear all loaded configs and throttle state. Intended for testing."""
    global _notifications
    _notifications = {}
    _reset_throttle_state()
