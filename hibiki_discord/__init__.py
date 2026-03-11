"""
Hibiki Discord - Business Event Notification Service

Send Discord notifications for business events (signups, subscriptions, etc.)
using a simple TOML config file and environment variables for webhook URLs.

Usage:
    from hibiki_discord import load_config, send_notification

    load_config()  # reads hibiki-discord.toml

    await send_notification("user_signup", email="user@example.com")
"""

__version__ = "1.0.0"

from .config import load_config, get_notification_config
from .service import send_notification, send, fire_notification

__all__ = [
    "load_config",
    "get_notification_config",
    "send_notification",
    "fire_notification",
    "send",
]
