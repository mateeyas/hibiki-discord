# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-30

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

### Fixed

- `LLMGUIDE.md` no longer documents the pre-1.0.1 `j***.d***@example.com`
  anonymization form.

### Changed

- `load_config()` and `load_config_from_dict()` now validate `dedup_window`,
  `max_per_minute`, and `embed_color`, raising `ValueError` at load time rather
  than failing at send time. Loading config also clears throttle state.
- **Breaking:** `enabled` and `embed` must be real TOML booleans. A quoted
  `enabled = "false"` is a truthy string, so it previously left a notification
  type firing that the operator believed was switched off; it now raises
  `ValueError` at load time. Configs using quoted booleans — including a
  working `enabled = "true"` — need the quotes removed before upgrading. Check
  with `grep -n 'enabled\|embed' your-config.toml`.

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
