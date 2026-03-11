# Hibiki Discord - LLM Integration Guide

> Async Discord webhook notifications for business events. Config-driven, secrets stay in env vars.

## Package: `hibiki-discord`

- Install: `pip install hibiki-discord`
- Python: 3.11+
- Runtime dependency: `aiohttp>=3.8.0`
- Import: `from hibiki_discord import load_config, send_notification, fire_notification, send`
- Async: `send` and `send_notification` are coroutines. `fire_notification` is sync but requires a running event loop.

## Minimal working example

```python
from hibiki_discord import load_config, send_notification, fire_notification

# MUST call load_config() once at startup before any send
load_config()  # reads hibiki-discord.toml from cwd

# Awaited send (raises on misconfiguration)
success = await send_notification("user_signup", email="jane@example.com")

# Fire-and-forget (errors are logged, not raised)
fire_notification("subscription", plan="Pro", email="jane@example.com")
```

Corresponding `hibiki-discord.toml`:

```toml
[notifications.user_signup]
webhook_url_env = "SIGNUP_DISCORD_WEBHOOK_URL"
username = "Signup Bot"
message_template = "New user signed up: {email}"

[notifications.subscription]
webhook_url_env = "SUBSCRIPTION_DISCORD_WEBHOOK_URL"
username = "Billing Bot"
message_template = "New {plan} subscription by {email}"
```

Corresponding env vars (must be set at runtime):

```
SIGNUP_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
SUBSCRIPTION_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Rules

- MUST call `load_config()` or `load_config_from_dict()` before calling `send_notification` or `fire_notification`.
- TOML config stores env var **names**, never actual webhook URLs. Webhook URLs go in environment variables only.
- `message_template` uses Python `str.format()` syntax. Every `{placeholder}` in the template must be supplied as a keyword argument to `send_notification` / `fire_notification`.
- Any keyword argument with `"email"` in its key name (case-insensitive) is automatically anonymized before sending (e.g. `john.doe@example.com` → `j***.d***@example.com`). This applies to `send_notification` and `fire_notification`, NOT to the low-level `send`.
- `fire_notification` is the only non-coroutine. It calls `asyncio.create_task` internally, so an event loop must be running.
- Do NOT use the low-level `send()` for normal notifications. Use `send_notification` or `fire_notification` instead. `send()` bypasses config, templates, and anonymization.

## API reference

### `load_config(path=None) -> dict[str, NotificationConfig]`

Loads config from TOML. Call once at startup.

- `path`: file path. Falls back to env var `HIBIKI_DISCORD_CONFIG`, then `hibiki-discord.toml` in cwd.
- Raises: `FileNotFoundError` (missing file), `ValueError` (malformed config or missing `webhook_url_env`).

### `send_notification(notification_type: str, **template_vars) -> bool` — async

Sends a configured notification to Discord.

- Returns `True` on success.
- Returns `False` silently if notification is disabled (`enabled = false`) or webhook env var is unset.
- Raises `ValueError` if: type is unknown, template is missing, or a template variable is missing.

### `fire_notification(notification_type: str, **template_vars) -> asyncio.Task[bool]` — sync

Fire-and-forget wrapper around `send_notification`. Returns an `asyncio.Task`. Errors are logged, not raised. The task can be awaited if you need the result, or ignored.

### `send(webhook_url: str, message: str, username=None) -> bool` — async

Low-level: POSTs a message directly to a webhook URL. No config, no templates, no anonymization. Returns `True` on HTTP 204.

### `get_notification_config(notification_type: str) -> NotificationConfig | None`

Returns loaded config for a notification type, or `None`.

## TOML config format

Each notification is a `[notifications.<name>]` block:

- `webhook_url_env` (str, required): env var name holding the Discord webhook URL.
- `message_template` (str, required for send_notification): Python `str.format()` template.
- `username` (str, optional): display name for the webhook bot in Discord.
- `enabled` (bool, optional, default `true`): set to `false` to disable without removing.

## Programmatic config (no TOML file)

Import `load_config_from_dict` from `hibiki_discord.config` (not re-exported at top level):

```python
from hibiki_discord.config import load_config_from_dict

load_config_from_dict({
    "user_signup": {
        "webhook_url_env": "DISCORD_SIGNUP_WEBHOOK",
        "username": "Signup Bot",
        "message_template": "New user: {email}",
    },
})
```

Same key schema as the TOML format.

## Error handling

| Situation | `send_notification` behavior | `fire_notification` behavior |
| --- | --- | --- |
| Unknown notification type | Raises `ValueError` | Logs error, returns `False` via task |
| Missing `message_template` | Raises `ValueError` | Logs error, returns `False` via task |
| Missing template variable | Raises `ValueError` | Logs error, returns `False` via task |
| Webhook env var not set | Returns `False`, logs warning | Returns `False` via task, logs warning |
| Notification disabled | Returns `False` silently | Returns `False` via task silently |
| HTTP error from Discord | Returns `False`, logs error | Returns `False` via task, logs error |

## Test utilities

Available from `hibiki_discord.config` (not re-exported):

- `reset()`: clears all loaded configs. Call in test teardown.
- `get_all_configs() -> dict`: returns all loaded configs.
- `load_config_from_dict(...)`: configure without a TOML file (see above).

## Logging

Logger name: `hibiki_discord`. Configure with standard `logging` module.
