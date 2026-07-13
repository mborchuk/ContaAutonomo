# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Invoice lifecycle (F2)**: `draft → issued → paid` states with issued-invoice
  immutability. Issuing assigns a per-series sequential number (e.g. `2026/0001`)
  and freezes a fiscal snapshot (VAT rate/amount, taxable base, customer fiscal
  identity) so later customer or settings edits never change an issued invoice.
  New actions: Issue, Mark paid, Rectify (linked rectificative draft), Annul
  (retained, excluded from income). New module hooks `on_invoice_issued`,
  `on_invoice_rectified`, `on_invoice_annulled` (issue-blocking: a raising hook
  aborts the transition). **Cutover**: existing invoices keep their historical
  numbers and semantics; legacy `pending` stays freely editable, new invoices
  default to `draft`.
- **Expense VAT breakdown (F4)**: `net_amount`, `vat_rate`, `vat_amount`,
  `deductible`, `deductible_pct` on expenses, with form auto-calculation,
  per-category defaults (editable in Settings → Expenses), and surfacing in
  lists, reports, the API, and the tax panel.
- **Fiscal calendar** (`modules/fiscal_calendar/`, F3): Spanish AEAT deadline
  dataset (local data, versioned), dashboard panel, form selection in settings,
  idempotent T-14/T-7/T-1 reminders via the `notify` capability.
- **Modelo 303/130 draft calculator** (`modules/tax_es_forms/`, F5):
  box-level quarterly drafts computed from invoices (fiscal snapshot) and
  expense VAT data, with copy-to-clipboard, print/PDF, dashboard panel, and
  REST endpoints. Framed as an estimate, not tax advice.
- **RETA quota advisor** (`modules/reta_advisor/`, F6): projects the
  contribution bracket from real income/expenses and forecasts the year-end
  regularization (estimate only; bracket table versioned).
- **Recurring invoices** (`modules/recurring_invoices/`, F7): invoice templates
  with monthly/quarterly cadence that generate **drafts** on schedule
  (idempotent catch-up, never auto-issued), "Make recurring" invoice action,
  dashboard nudge.
- **Invoice email** (`modules/invoice_email/`, F8): SMTP settings tab, send
  panel on the invoice view with placeholders and send history, opt-in overdue
  payment reminders (due +3/+10 days), and an email `notify` capability.
- **Verifactu** (`modules/verifactu/`, F1): append-only, hash-chained billing
  records written transactionally on invoice issue/annul, chain-verification
  page, NDJSON export, AEAT-style QR on the invoice view. Record format is a
  versioned draft pending verification against the RD 1007/2023 annexes;
  VERI*FACTU submission is scaffolded and gated on AEAT test access.
- **Tax filing workflow (F10)**: obligations view crossing the fiscal calendar
  with recorded filings (pending/filed/paid/overdue), record-filing action,
  per-form yearly totals, filings report section, overdue count on the
  dashboard tax panel. `tax_form` gains `status`, `amount`, `filed_date`,
  `payment_date`.
- **AI receipt parsing on the expense form (F11)**: one upload prefills the
  form (including the VAT breakdown) and attaches the file as the receipt;
  mobile camera capture supported.
- **Bank import & reconciliation** (`modules/bank_import/`, F12): Norma 43
  (AEB43) and mapped-CSV import with dedup and batch undo, ranked match
  suggestions, one-click confirm that marks the invoice paid through the
  normal lifecycle transition.
- **E-invoicing Phase 1** (`modules/einvoice/`, F9): unsigned Facturae 3.2.2
  export for issued invoices with a per-invoice readiness checklist. XAdES
  signing is a documented follow-up (no XAdES library in the dependency set).
- **Module-load smoke test (F13)**: every module in `modules/` must discover
  and instantiate; suite now covers tax math, lifecycle, and all new modules.
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
