import asyncio
import aiohttp
import logging
from typing import Optional

from .config import get_notification_config
from .embeds import build_footer, build_notification_embed, truncate_end
from .throttle import get_throttle

logger = logging.getLogger("hibiki_discord")

# Discord caps `content` at 2000 characters.
CONTENT_LIMIT = 2000

# 429 and 5xx responses are retried. The ceiling keeps a wedged webhook
# from holding the calling task open indefinitely.
MAX_SEND_ATTEMPTS = 4
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 10

_OK = "ok"
_RETRY = "retry"
_FAIL = "fail"


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


def _parse_retry_after(response, payload: Optional[dict]) -> Optional[float]:
    """Extract the retry delay Discord asks for, in seconds.

    Discord sends `Retry-After` as a header and also `retry_after` in the
    JSON body. Either may be absent; the body value is authoritative when
    both are present.
    """
    if payload:
        value = payload.get("retry_after")
        if value is not None:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                pass

    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except (TypeError, ValueError):
            pass
    return None


def _backoff_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Delay before the next attempt.

    Discord's own figure is honoured exactly when it gives one. It is not
    clamped to MAX_BACKOFF_SECONDS: retrying sooner than asked is what
    escalates a soft rate limit into a longer ban. Callers check the delay
    against the cap and give up rather than retry early.
    """
    if retry_after is not None:
        return max(retry_after, 0.0)
    return min(BASE_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)


async def _attempt_send(webhook_url: str, payload: dict):
    """Make one webhook request.

    Returns (outcome, retry_after). The caller owns the waiting, so the
    session and response are always closed before any backoff begins.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                status = response.status

                if status in (200, 204):
                    return _OK, None

                if status == 429:
                    try:
                        body = await response.json(content_type=None)
                    except Exception:
                        body = None
                    logger.warning("Discord rate limited the webhook")
                    return _RETRY, _parse_retry_after(response, body)

                if 500 <= status < 600:
                    logger.warning("Discord returned status %s", status)
                    return _RETRY, None

                # 4xx other than 429 will not succeed on retry.
                logger.error("Discord webhook returned status %s", status)
                return _FAIL, None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Error sending Discord message: %s", e)
        return _RETRY, None


async def send(
    webhook_url: str,
    message: Optional[str] = None,
    username: Optional[str] = None,
    embed: Optional[dict] = None,
) -> bool:
    """
    Send a message to a Discord webhook.

    Retries on 429 and 5xx responses, honouring Discord's `Retry-After`
    with exponential backoff between attempts. Returns False rather than
    raising, so a failing webhook never breaks the caller.

    Args:
        webhook_url: Discord webhook URL
        message: The message to send (optional when an embed is given)
        username: Optional display name for the webhook bot
        embed: Optional Discord embed object sent alongside the message

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not webhook_url:
        logger.warning("Discord webhook URL is empty")
        return False

    if not message and not embed:
        logger.warning("Discord message has neither content nor embed")
        return False

    payload: dict = {}
    if message:
        payload["content"] = truncate_end(message, CONTENT_LIMIT)
    if embed:
        payload["embeds"] = [embed]
    if username:
        payload["username"] = username

    for attempt in range(MAX_SEND_ATTEMPTS):
        outcome, retry_after = await _attempt_send(webhook_url, payload)

        if outcome == _OK:
            return True
        if outcome == _FAIL:
            return False

        if attempt + 1 >= MAX_SEND_ATTEMPTS:
            logger.error("Discord send failed and retries are exhausted")
            return False

        delay = _backoff_delay(attempt, retry_after)
        if delay > MAX_BACKOFF_SECONDS:
            # Retrying before Discord is ready would only deepen the limit.
            logger.error(
                "Discord asked to wait %.0fs, beyond the %.0fs cap; "
                "dropping message",
                delay,
                MAX_BACKOFF_SECONDS,
            )
            return False

        logger.warning("Discord send failed; retrying in %.2fs", delay)
        # Outside the session context, so nothing is held open while waiting.
        await asyncio.sleep(delay)

    return False


def _with_footer(message: str, suppressed_count: int, dropped_count: int) -> str:
    """Append the suppression note to a plain-text message.

    Nothing is appended when there is nothing to report, so a notification
    sent on a quiet channel renders exactly as it always did.

    The body is trimmed to leave room for the note rather than letting the
    whole string be truncated at CONTENT_LIMIT, which would cut the note
    off the end -- losing precisely the line saying this one message
    stands for many.
    """
    footer = build_footer(suppressed_count, dropped_count)
    if not footer:
        return message
    note = f"\n_{footer}_"
    body = truncate_end(message, max(CONTENT_LIMIT - len(note), 0))
    return f"{body}{note}"


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

    Identical notifications are deduplicated and sends are capped by a per
    webhook budget; see hibiki_discord.throttle. A notification collapsed
    or shed by the throttle returns False without sending, and is reported
    as a count on a later send.

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

    throttle = get_throttle()
    decision = throttle.check(
        notification_type=notification_type,
        message=message,
        webhook_url=webhook_url,
        dedup_window=config.resolve_dedup_window(),
        max_per_minute=config.resolve_max_per_minute(),
    )
    if not decision.send:
        logger.debug(
            "Notification '%s' throttled and not sent", notification_type
        )
        return False

    try:
        delivered = await _deliver(
            config=config,
            message=message,
            webhook_url=webhook_url,
            suppressed_count=decision.suppressed,
            dropped_count=decision.dropped,
        )
    except BaseException:
        # BaseException so cancellation is covered too: a send cancelled
        # mid-backoff, by a timeout or at shutdown, reached Discord no more
        # than a failed one did.
        throttle.record_failure(decision)
        raise

    if not delivered:
        # Nothing reached Discord, so this must not suppress the next
        # occurrence of the same notification.
        throttle.record_failure(decision)

    return delivered


async def _deliver(
    config,
    message: str,
    webhook_url: str,
    suppressed_count: int = 0,
    dropped_count: int = 0,
) -> bool:
    """Render a notification and hand it to the webhook.

    Plain text is the default. Notification types that set `embed = true`
    are sent as an embed, falling back to plain text if embed construction
    fails, because a notification that looks wrong beats no notification.
    """
    if config.embed:
        try:
            embed = build_notification_embed(
                message=message,
                title=config.embed_title,
                color=config.embed_color,
                suppressed_count=suppressed_count,
                dropped_count=dropped_count,
            )
        except Exception:
            logger.exception(
                "Failed to build Discord embed; falling back to plain text"
            )
        else:
            return await send(
                webhook_url=webhook_url,
                username=config.username,
                embed=embed,
            )

    return await send(
        webhook_url=webhook_url,
        message=_with_footer(message, suppressed_count, dropped_count),
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
