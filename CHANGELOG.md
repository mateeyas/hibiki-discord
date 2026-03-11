# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-11

### Added

- TOML-based notification configuration (`hibiki-discord.toml`)
- Programmatic configuration via `load_config_from_dict()`
- Async Discord webhook delivery via `aiohttp`
- Automatic email anonymization in notification messages
- Per-notification enable/disable toggle
- Webhook URLs resolved from environment variables at send time
