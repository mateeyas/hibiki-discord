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
from hibiki_discord import (
    load_config,
    send_notification,
    fire_notification,
    anonymize_email,
)

load_config()  # reads hibiki-discord.toml

# Await the result (blocks the caller until sent)
await send_notification("user_signup", email="jane@example.com")

# Fire-and-forget (returns immediately, sends in the background)
fire_notification("subscription", plan="Pro", email="jane@example.com")
```

Values are sent exactly as given. Nothing is redacted automatically — wrap the
values you want anonymized:

```python
await send_notification("user_signup", email=anonymize_email(user.email))
```

## Configuration

### TOML config file

Each `[notifications.<name>]` section defines a notification type:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `webhook_url_env` | Yes | - | Name of the env var holding the Discord webhook URL |
| `message_template` | Yes | - | Python format string for the message |
| `username` | No | - | Display name for the Discord bot |
| `enabled` | No | `true` | Set to `false` to disable this notification type (a real boolean, not `"false"`) |
| `embed` | No | `false` | Send as a Discord embed instead of plain text |
| `embed_title` | No | - | Title for the embed |
| `embed_color` | No | Discord blurple | Embed colour, as `"#5865F2"` or an integer |
| `dedup_window` | No | `0` | Seconds identical messages collapse into one send; `0` disables |
| `max_per_minute` | No | `30` | Maximum sends to this webhook in any 60 second window |

### Config file location

By default, `load_config()` looks for `hibiki-discord.toml` in the current working directory. Override with:

- Pass a path: `load_config("/path/to/config.toml")`
- Set the `HIBIKI_DISCORD_CONFIG` env var: `export HIBIKI_DISCORD_CONFIG=/path/to/config.toml`

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HIBIKI_DISCORD_CONFIG` | `hibiki-discord.toml` | Path to the config file |
| `HIBIKI_DISCORD_DEDUP_WINDOW` | `0` | Default `dedup_window` for every notification type |
| `HIBIKI_DISCORD_MAX_PER_MINUTE` | `30` | Default `max_per_minute` for every notification type |

Per-notification TOML keys take precedence over the environment variables.
All of these are optional and have working defaults; upgrading requires no
configuration changes.

### Throttling

Discord rate limits webhooks at roughly 5 requests per 2 seconds and returns
429 beyond that. Without throttling, a caller stuck in a retry loop exceeds the
limit and loses notifications silently, because send failures are logged rather
than raised. Four mechanisms run on the send path:

**Retries.** 429 and 5xx responses are retried, honouring Discord's
`Retry-After` with exponential backoff. If Discord asks for a delay longer than
30 seconds the message is dropped rather than retried early, since sending
before the limit clears only extends it.

**Send budget.** `max_per_minute` caps how many webhook *requests* may go to one
webhook
in any 60 second window. The budget is keyed on the webhook URL, because that is
what Discord rate limits: notification types pointing at *different* webhooks
cannot shed each other's messages, but types sharing one webhook share its
budget, so a burst on a noisy type can shed a quiet one alongside it. Give a
notification type its own webhook where that matters. Messages beyond the budget
are dropped and counted, and the count is reported on the next successful send
("3 notifications dropped by the send budget."). They are dropped rather than
queued because queueing would need a background worker and its lifecycle. There
is nothing to start or shut down.

Retries count against the budget too. A send retried four times against a
failing webhook spends four slots, not one, so a Discord incident cannot quietly
multiply the request rate by four — which is the rate the budget exists to bound.

**Pacing.** The budget bounds the average over a minute; Discord's limit is on
the instant. Thirty sends fit a budget of thirty per minute whether they arrive
spread out or all at once, and all at once is exactly what trips the limit. So
sends to one webhook are spaced half a second apart: a batch loop firing
`fire_notification` for every row is spread out rather than shed. This means
`send_notification` can wait before sending — up to a minute on a saturated
webhook — so use `fire_notification` where the caller must not block. Beyond a
minute of pacing the notification is dropped and counted like any other
over-budget send. Pacing is not queueing: each caller waits its own turn on its
own task, and there is still nothing to start or drain.

**Deduplication, off by default.** Set `dedup_window` and identical messages —
same notification type, same rendered text — collapse into one send for that
many seconds, with the number collapsed reported on the next send for that
message ("142 further occurrences suppressed."). It is off by default because a
repeated business notification is usually a second real event, not a repeat:
two signups a second apart are two customers, not one message sent twice. Turn
it on for notification types where a repeat really is a repeat, such as an
incident or health-check alert.

A message that fails to send — or is cancelled mid-send, by a timeout or at
shutdown — does not open a dedup window and does not consume its suppression
counts, and occurrences collapsed into it while it was in flight are carried to
the next message, so a webhook outage cannot silence a notification.

> The same behaviour is implemented independently in
> [hibiki-logger](https://github.com/mateeyas/hibiki-logger). The two share no
> code, so a fix to throttling or embed formatting in one is usually worth
> applying to the other. The defaults differ deliberately: hibiki-logger
> deduplicates by traceback signature at 300 seconds, because a repeated log
> record is the same fault firing again.
>
> As of 3.0.0 they have genuinely diverged: pacing, charging retries to the send
> budget, and evicting dedup windows by what they still have to report are all
> here and not there. Worth backporting.

### Mentions

Every payload sets `allowed_mentions: {"parse": []}`, so Discord does not
resolve `@everyone`, `@here`, or role mentions in a message. Template values are
caller-supplied and often user-controlled — a signup email, a display name, a
free-text summary — and a value containing `@everyone` would otherwise ping the
whole channel on every notification.

This applies to mentions you put in a `message_template` deliberately, too:

```toml
message_template = "@here Payment processor unreachable"   # renders, does not ping
```

The text is unchanged and still renders as written; Discord is simply told not
to resolve it. There is no config key to opt back in.

### Embeds

Plain text is the default, so existing notifications render exactly as they did.
Set `embed = true` on a notification type to send it as a Discord embed instead:

```toml
[notifications.incident]
webhook_url_env = "INCIDENT_DISCORD_WEBHOOK_URL"
username = "Incident Bot"
message_template = "Payment processor unreachable"
embed = true
embed_title = "Incident"
embed_color = "#DC2626"
dedup_window = 300
```

The message goes in the description, truncated to Discord's 4096 character
limit, and suppression and drop counts go in the footer rather than the body. If
embed construction ever fails the notification still goes out as plain text,
because a notification that looks wrong beats no notification.

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

Send a notification. Looks up the config, resolves the webhook URL from env, formats the template, and sends. Returns `True` on success, and `False` when the notification is disabled, its webhook env var is unset, the send fails, or the message was collapsed or shed by [throttling](#throttling). Raises `ValueError` if the type is unknown or the template is missing.

### `fire_notification(notification_type, **template_vars) -> asyncio.Task[bool]`

Fire-and-forget variant of `send_notification`. Schedules the notification as a background task on the running event loop and returns immediately. Errors are logged instead of raised. The returned `asyncio.Task` can be awaited if you need the result, or simply ignored — the package keeps its own reference until the send finishes, so an ignored task is not garbage collected mid-flight.

### `send(webhook_url, message=None, username=None, embed=None, on_attempts=None) -> bool`

Low-level send. Posts a message directly to a Discord webhook URL, retrying on
429 and 5xx responses. Bypasses config, templates, and throttling. `on_attempts`
is called with the number of HTTP requests made once the send is done, for a
caller tracking a request budget of its own.

### `anonymize_email(email) -> str`

`john.doe@example.com` → `j***@example.com`. Returns anything without an `@`, or
with an empty local part, unchanged. Never applied automatically.

### `get_notification_config(notification_type) -> NotificationConfig | None`

Get the config for a specific notification type, or `None` if not configured.

## Troubleshooting

**Fewer notifications than expected** — check `dedup_window` for the notification
type. Identical messages collapse for that many seconds and are counted in the
next message for that text. Messages shed by `max_per_minute` are counted the
same way. Both counts appear in the message footer, so a collapsed or shed
message is still accounted for. The one exception is a webhook tracking more
than 512 distinct dedup signatures at once, where the oldest counts are
discarded to bound memory; that is logged as a warning naming how many were
lost.

**Notifications arrive late** — sends to one webhook are paced half a second
apart, so a burst is spread rather than dropped and the last of thirty goes out
about fifteen seconds after the first. `await send_notification(...)` waits its
turn; `fire_notification` returns immediately and waits in the background.

**Nothing arrives** — verify the webhook env var named by `webhook_url_env` is
set at runtime and `enabled` is not `false`. Both cases return `False` and log,
rather than raising.

## License

MIT
