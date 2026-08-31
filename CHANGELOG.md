# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-09-01

### Security

- Every Discord webhook payload now sets `allowed_mentions: {"parse": []}`.
  Template values are caller-supplied and often user-controlled — a signup
  email, a display name, a free-text summary — and Discord resolves mentions in
  webhook `content`, so a value containing `@everyone`, `@here`, or a role
  mention pinged the whole channel on every notification. Embeds were never
  exposed: Discord does not resolve mentions inside them, so only the plain-text
  path carried this.

  **Behaviour change:** mentions placed in a `message_template`
  *deliberately* (e.g. `message_template = "@here Processor unreachable"`) stop
  pinging too. The text is unchanged and still renders as written; Discord is
  simply told not to resolve it. There is no config key to opt back in.

  This matches [hibiki-js](https://github.com/mateeyas/hibiki-js)
  (`packages/discord`), which has always set it, and lands in hibiki-logger 1.5.0.

### Changed

- **Breaking:** email anonymization is no longer automatic. `send_notification`
  and `fire_notification` previously rewrote any keyword argument whose name
  contained `email` (case-insensitive), on every notification type. Template
  values are now sent exactly as given. A magic substring match on argument
  names is wrong for a general-purpose library, and it silently changed data the
  caller never asked to change; this brings the package in line with
  [hibiki-js](https://github.com/mateeyas/hibiki-js), where anonymization has
  always been opt-in.

  Anonymization is now something you call. Audit every `send_notification` /
  `fire_notification` call passing an address you do not want in Discord --
  `grep -rn 'email' your-app/` -- and wrap the value:

  ```python
  from hibiki_discord import anonymize_email

  await send_notification("user_signup", email=anonymize_email(user.email))
  ```

  Nothing raises or warns if you miss one: the real address is sent. This is the
  only backwards-incompatible change in this release.
- `dedup_window` remains off by default, but for one reason rather than two: a
  repeated business notification is usually a second real event. The old
  rationale that anonymization renders distinct signups identically now applies
  only to templates the caller has chosen to anonymize.

### Fixed

- The send budget bounded the average over a minute but not the instant, so it
  did not prevent the 429s it exists to prevent: with the default
  `max_per_minute = 30`, thirty `fire_notification` calls in one loop all passed
  the budget and left as one burst, far over Discord's roughly 5 requests per
  2 seconds. Sends to a webhook are now spaced 0.5 seconds apart.

  **Behaviour change:** `send_notification` can now wait before sending — up to
  a minute on a saturated webhook — where it previously returned as fast as the
  request allowed. Nothing extra is dropped: a burst that fits the budget is
  spread rather than shed, and only pacing deeper than a minute sheds, counted
  like any other over-budget drop. Use `fire_notification` where the caller must
  not block.
- Retry attempts bypassed the send budget entirely. `check` reserved one slot
  per notification, but a send makes up to four HTTP requests, so during a
  Discord incident a budget of thirty a minute permitted up to a hundred and
  twenty requests while the throttle believed it had allowed thirty. Every
  request is now charged, retries included.
- The dedup table's hard cap evicted the oldest window first, which is the long
  dedup window most likely to be holding the largest undelivered suppression
  count — so a flood of distinct notifications silently discarded exactly the
  "N further occurrences suppressed" notes that matter most, against the
  README's promise that nothing disappears without a trace. Windows with nothing
  to report are now evicted first, and a count that must be dropped is logged.
  Relatedly, expiry was skipped once the table hit the cap, pinning it there for
  good: expired empty windows were never reclaimed and each send evicted a live
  one. Expiry now runs on every send.
- `fire_notification` returned a task nobody retained. asyncio keeps only a weak
  reference to a running task, so an ignored one could be garbage collected
  mid-flight and the notification never sent — while the docs explicitly said
  the task may be ignored. The package now holds a reference until the send
  finishes. The retry and pacing paths make a send long-lived enough for this to
  matter in practice.

### Added

- `send()` takes an optional `on_attempts` callback, called with the number of
  HTTP requests made once a send is done. It is how the notification path
  charges retries to the send budget, and is available to callers tracking a
  request budget of their own.
- `anonymize_email` is exported from the package root
  (`from hibiki_discord import anonymize_email`). It was previously importable
  only from `hibiki_discord.service`.

## [2.0.0] - 2026-08-30

### Added

- Retry with backoff on Discord `429` and `5xx` responses, honouring the
  `Retry-After` Discord returns. Rate-limited notifications are delayed rather
  than dropped; a delay beyond 30 seconds is not retried, since sending before
  the limit clears only extends it.
- Per-webhook send budget over a sliding 60 second window, configured with
  `HIBIKI_DISCORD_MAX_PER_MINUTE` or `max_per_minute` per notification type
  (default 30). Notifications beyond the budget are dropped and counted, and the
  count is reported in the next successful send.
- Optional deduplication of identical notifications, configured with
  `HIBIKI_DISCORD_DEDUP_WINDOW` or `dedup_window` per notification type.
  **Off by default**: a repeated business notification is usually a second real
  event, and email anonymization makes distinct signups render identically.
- Optional Discord embeds per notification type: `embed`, `embed_title`, and
  `embed_color`. Plain text remains the default. Suppression and drop counts go
  in the embed footer, and construction falls back to plain text if it fails.
- `send()` accepts an `embed` argument and treats HTTP `200` as success
  alongside `204`.

### Changed

- **Breaking:** `enabled` and `embed` must be real TOML booleans. A quoted
  `enabled = "false"` is a truthy string, so it previously left a notification
  type firing that the operator believed was switched off; it now raises
  `ValueError` at load time. Configs using quoted booleans — including a
  working `enabled = "true"` — need the quotes removed before upgrading. Check
  with `grep -n 'enabled\|embed' your-config.toml`. This is the only
  backwards-incompatible change in this release; everything else works
  unchanged on existing configs and defaults.
- `load_config()` and `load_config_from_dict()` now validate `dedup_window`,
  `max_per_minute`, and `embed_color`, raising `ValueError` at load time rather
  than failing at send time. Loading config also clears throttle state.

### Fixed

- `LLMGUIDE.md` no longer documents the pre-1.0.1 `j***.d***@example.com`
  anonymization form.

## [1.0.1] - 2026-05-13

### Fixed

- `anonymize_email` now consistently produces `<first letter>***@domain` for all
  email addresses. Previously, dotted local parts were anonymized per-segment
  (e.g. `j***.d***@example.com`) and short single-character segments were left
  unmodified.

## [1.0.0] - 2026-03-11

### Added

- TOML-based notification configuration (`hibiki-discord.toml`)
- Programmatic configuration via `load_config_from_dict()`
- Async Discord webhook delivery via `aiohttp`
- Automatic email anonymization in notification messages
- Per-notification enable/disable toggle
- Webhook URLs resolved from environment variables at send time
