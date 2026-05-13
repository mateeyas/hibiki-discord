import asyncio
import aiohttp
import logging
from typing import Optional

from .config import get_notification_config

logger = logging.getLogger("hibiki_discord")


def anonymize_email(email: str) -> str:
    """
    Anonymize an email address while keeping it identifiable.

    The local part is always replaced with its first character followed by
    three asterisks, regardless of length or punctuation.

    Examples:
        john.doe@example.com -> j***@example.com
        user@domain.com -> u***@domain.com
        a.b@test.com -> a***@test.com
    """
    if not email or "@" not in email:
        return email

    try:
        local_part, domain = email.split("@", 1)

        if not local_part:
            return email

        return f"{local_part[0]}***@{domain}"

    except Exception:
        return email


async def send(
    webhook_url: str,
    message: str,
    username: Optional[str] = None,
) -> bool:
    """
    Send a message to a Discord webhook.

    Args:
        webhook_url: Discord webhook URL
        message: The message to send
        username: Optional display name for the webhook bot

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not webhook_url:
        logger.warning("Discord webhook URL is empty")
        return False

    payload = {"content": message}
    if username:
        payload["username"] = username

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 204:
                    return True
                else:
                    logger.error(
                        "Discord webhook returned status %s", response.status
                    )
                    return False
    except Exception as e:
        logger.exception("Error sending Discord message: %s", e)
        return False


async def send_notification(
    notification_type: str,
    **template_vars,
) -> bool:
    """
    Send a notification for a configured notification type.

    Looks up the notification config, resolves the webhook URL from the
    environment, formats the message template with the provided variables,
    and sends to Discord.

    Email values in template_vars are automatically anonymized.

    Args:
        notification_type: The notification type name (must match a key in the config).
        **template_vars: Variables to substitute in the message template.

    Returns:
        True if the message was sent successfully, False otherwise.

    Raises:
        ValueError: If the notification type is not configured or has no message template.
    """
    config = get_notification_config(notification_type)
    if config is None:
        raise ValueError(f"Unknown notification type: '{notification_type}'")

    if not config.enabled:
        logger.debug("Notification '%s' is disabled", notification_type)
        return False

    webhook_url = config.resolve_webhook_url()
    if not webhook_url:
        logger.warning(
            "Env var '%s' for notification '%s' is not set",
            config.webhook_url_env,
            notification_type,
        )
        return False

    if config.message_template is None:
        raise ValueError(
            f"Notification '{notification_type}' has no message_template"
        )

    sanitized_vars = dict(template_vars)
    for key, value in sanitized_vars.items():
        if "email" in key.lower() and isinstance(value, str):
            sanitized_vars[key] = anonymize_email(value)

    try:
        message = config.message_template.format(**sanitized_vars)
    except KeyError as e:
        raise ValueError(
            f"Missing template variable {e} for notification '{notification_type}'"
        ) from e

    return await send(
        webhook_url=webhook_url,
        message=message,
        username=config.username,
    )


def fire_notification(
    notification_type: str,
    **template_vars,
) -> asyncio.Task[bool]:
    """
    Send a notification as a background task (fire-and-forget).

    Schedules send_notification on the running event loop and returns
    immediately. The returned Task can be awaited or ignored. Errors are
    logged rather than raised to the caller.

    Args:
        notification_type: The notification type name (must match a key in the config).
        **template_vars: Variables to substitute in the message template.

    Returns:
        The asyncio.Task wrapping the send. Await it only if you need the result.
    """

    async def _wrapper() -> bool:
        try:
            return await send_notification(notification_type, **template_vars)
        except Exception as e:
            logger.exception(
                "Background notification '%s' failed: %s", notification_type, e
            )
            return False

    return asyncio.create_task(_wrapper())
