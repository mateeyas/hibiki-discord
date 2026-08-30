"""
Discord embed construction for notifications.

Discord enforces hard limits on embed payloads and rejects the whole
message if any of them is exceeded:

* 4096 characters for the description
* 1024 characters per field value
* 6000 characters across the entire embed

Everything here is pure and synchronous so it can be unit tested without
touching the network. Construction never raises for oversized or unusual
input; callers still guard against unexpected failures and fall back to
plain text, because a notification that looks wrong beats no notification.

Embeds are opt-in per notification type (``embed = true`` in the TOML
config). Plain text stays the default so existing notifications render
exactly as they did before.
"""

from datetime import datetime, timezone
from typing import Optional, Union

DESCRIPTION_LIMIT = 4096
TITLE_LIMIT = 256
FOOTER_LIMIT = 2048
TOTAL_LIMIT = 6000

# Discord blurple, for notifications that set no colour of their own.
DEFAULT_COLOR = 0x5865F2

# Discord rejects colours outside a 24-bit RGB integer.
MAX_COLOR = 0xFFFFFF


def parse_color(value: Union[int, str, None]) -> Optional[int]:
    """Parse a config colour into the integer Discord expects.

    Accepts an integer (``5793266``), or a hex string with or without the
    leading hash (``"#5865F2"``). Returns None when unset.

    Raises:
        ValueError: If the value cannot be read as a colour, so a typo in
            the config surfaces at load time rather than at send time.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(f"Invalid embed colour: {value!r}")

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().lstrip("#")
        try:
            parsed = int(text, 16)
        except ValueError:
            raise ValueError(f"Invalid embed colour: {value!r}") from None
    else:
        raise ValueError(f"Invalid embed colour: {value!r}")

    if not 0 <= parsed <= MAX_COLOR:
        raise ValueError(
            f"Embed colour {value!r} is outside the range 0x000000-0xFFFFFF"
        )
    return parsed


def truncate_end(text: str, limit: int) -> str:
    """Truncate text to limit, marking that it was cut."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    suffix = "..."
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)] + suffix


def build_footer(suppressed_count: int = 0, dropped_count: int = 0) -> Optional[str]:
    """Render the footer note for suppressed and dropped notifications.

    Returns None when there is nothing to report, so the footer can be
    omitted rather than rendered empty.
    """
    parts = []
    if suppressed_count > 0:
        noun = "occurrence" if suppressed_count == 1 else "occurrences"
        parts.append(f"{suppressed_count} further {noun} suppressed.")
    if dropped_count > 0:
        noun = "notification" if dropped_count == 1 else "notifications"
        parts.append(f"{dropped_count} {noun} dropped by the send budget.")
    if not parts:
        return None
    return " ".join(parts)


def build_notification_embed(
    message: str,
    title: Optional[str] = None,
    color: Optional[int] = None,
    suppressed_count: int = 0,
    dropped_count: int = 0,
    timestamp: Optional[datetime] = None,
) -> dict:
    """Build a Discord embed for a notification, within Discord's limits."""
    embed: dict = {
        "color": color if color is not None else DEFAULT_COLOR,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }

    if title:
        embed["title"] = truncate_end(str(title), TITLE_LIMIT)

    footer = build_footer(suppressed_count, dropped_count)
    if footer:
        embed["footer"] = {"text": truncate_end(footer, FOOTER_LIMIT)}

    # The description is the only unbounded part of the payload, so it
    # absorbs whatever budget the fixed parts leave behind.
    fixed_length = len(embed.get("title") or "") + len(
        (embed.get("footer") or {}).get("text") or ""
    )
    budget = min(DESCRIPTION_LIMIT, max(TOTAL_LIMIT - fixed_length, 0))
    embed["description"] = truncate_end(str(message or ""), budget)

    return embed
