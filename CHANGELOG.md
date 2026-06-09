# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **REST/JSON API** (`modules/api/`) under `/api/v1`: token auth (`X-API-Token`),
  OpenAPI discovery, invoices CRUD, customers, dashboard summary, rates, health.
  Per-module endpoint hook `get_api_routes()`; endpoints for expenses,
  tax_management, documents, ai_parser, and report generation.
- Core `/health` endpoint (DB + storage probe).
- Request correlation id (`X-Request-ID`) + log format integration.
- Optional Sentry error tracking (env-gated `SENTRY_DSN`).
- Scheduler run history (in-memory, last 10 runs per job).
- pytest harness (`tests/`) + CI workflow.
- `constants.py`, `.env.example`, `Makefile`, `CHANGELOG.md`.

### Changed
- SECRET_KEY now required in production (hard fail), warns in dev.
- SQLite: WAL journal + `foreign_keys=ON`; SQLAlchemy `pool_pre_ping`/`pool_recycle`.
- `MAX_CONTENT_LENGTH` = 50 MB + friendly 413 handling.
- ECB exchange-rate feed cached in memory (4h TTL).
- Startup backup deferred ~60s off the boot path.
- GoogleDrive backend: exponential backoff + retry for transient errors.
- Module logging configured at import (works under gunicorn).
- `Content-Security-Policy-Report-Only` header added.
- Dockerfile: `apt-get upgrade` to clear base-image CVEs.

### Security
- `delete_invoice` is POST-only + CSRF (was GET).
- Session cleared before populating identity (session-fixation defense).
- Failed logins recorded to the activity log.
- `secure_filename` empty-result guard.
- Hardened CSRF-error open-redirect to same-origin only.
- Log-injection sanitization on user-controlled values.
- `AUTH_CONFIG_PATH` env override; rate limits on backup/restore and AI parse.
