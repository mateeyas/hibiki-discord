# Hibiki Discord

A lean Discord notification service for business events. Define notification types in a TOML config file, store webhook URLs in environment variables, and send notifications with one function call.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub](https://img.shields.io/badge/GitHub-mateeyas%2Fhibiki--discord-181717?logo=github)](https://github.com/mateeyas/hibiki-discord)

## Installation

```bash
pip install hibiki-discord
```

## Quick start

**1. Create a config file** (`hibiki-discord.toml` in your project root):

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

`webhook_url_env` is the **name** of the environment variable that holds the webhook URL. The TOML file contains no secrets and is safe to commit.

**2. Set your webhook URLs** as environment variables (e.g. in your `.env` file or deployment config):

- `SIGNUP_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
- `SUBSCRIPTION_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`

**3. Send notifications:**

```python
from hibiki_discord import load_config, send_notification, fire_notification

load_config()  # reads hibiki-discord.toml

# Await the result (blocks the caller until sent)
await send_notification("user_signup", email="jane@example.com")

# Fire-and-forget (returns immediately, sends in the background)
fire_notification("subscription", plan="Pro", email="jane@example.com")
```

Emails are automatically anonymized in messages (e.g. `j***@example.com`).

## Configuration

### TOML config file

Each `[notifications.<name>]` section defines a notification type:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `webhook_url_env` | Yes | - | Name of the env var holding the Discord webhook URL |
| `message_template` | Yes | - | Python format string for the message |
| `username` | No | - | Display name for the Discord bot |
| `enabled` | No | `true` | Set to `false` to disable this notification type |

### Config file location

By default, `load_config()` looks for `hibiki-discord.toml` in the current working directory. Override with:

- Pass a path: `load_config("/path/to/config.toml")`
- Set the `HIBIKI_DISCORD_CONFIG` env var: `export HIBIKI_DISCORD_CONFIG=/path/to/config.toml`

### Programmatic configuration

Skip the TOML file and configure in Python:

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

## API reference

### `load_config(path=None) -> dict`

Load notification config from a TOML file. Returns a dict of notification type name to config.

### `send_notification(notification_type, **template_vars) -> bool`

Send a notification. Looks up the config, resolves the webhook URL from env, formats the template, and sends. Returns `True` on success. Raises `ValueError` if the type is unknown or the template is missing.

### `fire_notification(notification_type, **template_vars) -> asyncio.Task[bool]`

Fire-and-forget variant of `send_notification`. Schedules the notification as a background task on the running event loop and returns immediately. Errors are logged instead of raised. The returned `asyncio.Task` can be awaited if you need the result, or simply ignored.

### `send(webhook_url, message, username=None) -> bool`

Low-level send. Posts a message directly to a Discord webhook URL.

### `get_notification_config(notification_type) -> NotificationConfig | None`

Get the config for a specific notification type, or `None` if not configured.

## License

MIT
