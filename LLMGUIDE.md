# Hibiki Discord - LLM Integration Guide

> Async Discord webhook notifications for business events. Config-driven, secrets stay in env vars.

## Package: `hibiki-discord`

- Install: `pip install hibiki-discord`
- Python: 3.11+
- Runtime dependency: `aiohttp>=3.8.0`
- Import: `from hibiki_discord import load_config, send_notification, fire_notification, send, anonymize_email`
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

# Nothing is redacted automatically; redact at the call site if you want it
await send_notification("user_signup", email=anonymize_email(user.email))
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
- Nothing is redacted automatically. Template values are sent exactly as given. Wrap the ones you want anonymized: `send_notification("user_signup", email=anonymize_email(user.email))`. Prior to 3.0.0 any keyword argument with `"email"` in its key name was rewritten for you; it no longer is.
- `fire_notification` is the only non-coroutine. It calls `asyncio.create_task` internally, so an event loop must be running.
- Do NOT use the low-level `send()` for normal notifications. Use `send_notification` or `fire_notification` instead. `send()` bypasses config, templates, and throttling.
- A send that fails or is cancelled does not open a dedup window, and occurrences collapsed into it while it was in flight are carried to the next send, so a webhook outage cannot silence a notification.
- Sends are capped at `max_per_minute` per webhook URL (default 30, sliding 60 second window), counting every HTTP request including retries. Messages beyond the cap are dropped, counted, and the count is reported in the next successful send. Nothing is queued; there is no worker to start or stop.
- Sends to one webhook are spaced 0.5s apart, because the budget bounds the average over a minute while Discord's limit is on the instant. A burst is paced, not dropped, so `send_notification` can wait before sending — up to 60s on a saturated webhook, beyond which it is dropped and counted. Use `fire_notification` where the caller must not block.
- Deduplication is OFF by default (`dedup_window = 0`). Enable it per notification type only where a repeated message is genuinely a repeat: two signups a second apart are two customers, not one message sent twice. Note that anonymized values collapse (`alice@x.com` and `amir@x.com` both become `a***@x.com`), so dedup on an anonymized template merges distinct customers.
- Plain text is the default rendering. Embeds are opt-in per notification type with `embed = true`.
- Every payload sets `allowed_mentions: {"parse": []}`, so `@everyone`, `@here` and role mentions never resolve — including ones written into a `message_template` deliberately. Template values are caller-supplied and often user-controlled, so a value containing `@everyone` would otherwise ping the whole channel. There is no opt-out; do NOT tell a user to add a config key for it.

## API reference

### `load_config(path=None) -> dict[str, NotificationConfig]`

Loads config from TOML. Call once at startup.

- `path`: file path. Falls back to env var `HIBIKI_DISCORD_CONFIG`, then `hibiki-discord.toml` in cwd.
- Raises: `FileNotFoundError` (missing file), `ValueError` (malformed config or missing `webhook_url_env`).

### `send_notification(notification_type: str, **template_vars) -> bool` — async

Sends a configured notification to Discord.

- Returns `True` on success.
- Returns `False` silently if notification is disabled (`enabled = false`), the webhook env var is unset, or the message was collapsed by dedup / shed by the send budget.
- Raises `ValueError` if: type is unknown, template is missing, or a template variable is missing.

### `fire_notification(notification_type: str, **template_vars) -> asyncio.Task[bool]` — sync

Fire-and-forget wrapper around `send_notification`. Returns an `asyncio.Task`. Errors are logged, not raised. The task can be awaited if you need the result, or ignored — the package retains it until the send finishes, so it is not garbage collected mid-flight.

### `send(webhook_url: str, message=None, username=None, embed=None, on_attempts=None) -> bool` — async

Low-level: POSTs a message directly to a webhook URL. No config, no templates, no throttling. Optional `on_attempts` callback receives the number of HTTP requests made. Returns `True` on HTTP 200 or 204. Retries on 429 and 5xx, honouring Discord's `Retry-After` with exponential backoff (4 attempts, 30 second cap). At least one of `message` or `embed` is required.

### `anonymize_email(email: str) -> str` — sync

`john.doe@example.com` → `j***@example.com`. Returns anything without an `@`, or with an empty local part, unchanged. Never applied automatically; call it on the values you want redacted.

### `get_notification_config(notification_type: str) -> NotificationConfig | None`

Returns loaded config for a notification type, or `None`.

## TOML config format

Each notification is a `[notifications.<name>]` block:

- `webhook_url_env` (str, required): env var name holding the Discord webhook URL.
- `message_template` (str, required for send_notification): Python `str.format()` template.
- `username` (str, optional): display name for the webhook bot in Discord.
- `enabled` (bool, optional, default `true`): set to `false` to disable without removing.
- `embed` (bool, optional, default `false`): render as a Discord embed.
- `embed_title` (str, optional): embed title.
- `embed_color` (str or int, optional, default Discord blurple): `"#5865F2"` or an integer.
- `dedup_window` (int, optional, default `0`): seconds identical messages collapse into one send; `0` disables.
- `max_per_minute` (int, optional, default `30`, minimum `1`): sends allowed to this webhook per 60 seconds.

`enabled` and `embed` must be real TOML booleans, not quoted strings: a quoted `"false"` is truthy and would leave the type enabled. Invalid `enabled`, `embed`, `dedup_window`, `max_per_minute`, or `embed_color` values raise `ValueError` at load time.

## Environment variables

- `HIBIKI_DISCORD_CONFIG`: path to the TOML config file.
- `HIBIKI_DISCORD_DEDUP_WINDOW` (default `0`): default `dedup_window` for all types.
- `HIBIKI_DISCORD_MAX_PER_MINUTE` (default `30`): default `max_per_minute` for all types.

Per-notification TOML keys take precedence over these.

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
| Rate limited (429) or 5xx | Retries with backoff, then `False` | Same, via task |
| Collapsed by dedup or shed by budget | Returns `False`, count reported in a later send | Same, via task |

## Test utilities

Available from `hibiki_discord.config` (not re-exported):

- `reset()`: clears all loaded configs and throttle state. Call in test teardown, otherwise one test's sends throttle the next one's.
- `get_all_configs() -> dict`: returns all loaded configs.
- `load_config_from_dict(...)`: configure without a TOML file (see above).

## Throttling and embeds

Implemented independently in
[hibiki-logger](https://github.com/mateeyas/hibiki-logger) with the same
behaviour and config names; the two share no code. `hibiki_discord.throttle`
holds the dedup and rate-limit state (a dict of windows plus a list of send
timestamps per webhook, checked synchronously on the send path).
`hibiki_discord.embeds` builds embeds within Discord's limits and never raises.

## Logging

Logger name: `hibiki_discord`. Configure with standard `logging` module.
