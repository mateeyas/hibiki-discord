"""
Hibiki Discord - Business Event Notification Service

Send Discord notifications for business events (signups, subscriptions, etc.)
using a simple TOML config file and environment variables for webhook URLs.

Usage:
    from hibiki_discord import load_config, send_notification

    load_config()  # reads hibiki-discord.toml

    await send_notification("user_signup", email="user@example.com")

    # Opt in to anonymization explicitly:
    # from hibiki_discord import anonymize_email
    # await send_notification("user_signup", email=anonymize_email(addr))
"""

__version__ = "3.0.0"

from .config import load_config, get_notification_config
from .service import send_notification, send, fire_notification, anonymize_email

__all__ = [
    "load_config",
    "get_notification_config",
    "send_notification",
    "fire_notification",
    "send",
    "anonymize_email",
]
