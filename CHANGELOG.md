# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Modelo 390 draft** (#108): annual VAT summary at `/tax-forms-draft/390/<year>`,
  each casilla the sum of the four quarterly 303 drafts; casillas checked
  against the AEAT 2025 instructions.
- **Year-keyed tax data** (#101): the fiscal calendar works for any year
  (weekend deadlines move to Monday); Modelo 303/130 boxes and RETA brackets
  are keyed by year and show "figures use the <year> table — verify" when an
  earlier table is used. Annual checklist in `CONTRIBUTING.md`.
- **IVA in report PDFs** (#98): IVA % and IVA (EUR) columns with totals in the
  income and expenses tables; reverse-charge rows marked `ISP` with the
  Art. 194 note.
- **Bank import expense suggestions** (#107): debit movements get ranked
  expense suggestions and a Link button (confirmation required).
- **Facturae XAdES-EPES signature** (#109): exports are signed with the PDF
  Signature certificate when one is configured (`.xsig`); unsigned otherwise.
- **Verifactu QR on invoice PDFs** (#110): every PDF of an issued invoice
  carries the AEAT QR (30 mm, level M, "QR tributario:"); official hash and
  QR URL formats in `modules/verifactu/aeat.py`; comparison with the AEAT
  specifications in `modules/verifactu/SPEC_COMPARISON.md`. Submission to AEAT
  is not available, so the QR uses the non-VERI*FACTU URL.
- **Lint and coverage** (#103): `ruff` (`make lint`), coverage in CI.
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
- **Invoice status only through lifecycle actions** (#91): new invoices are
  always drafts; the status field is gone from the forms and the API
  (`POST /invoices/<id>/issue`, `/mark-paid`); annulled invoices are locked.
- **Dashboard income** (#95) counts every invoice dated in the year except
  cancelled ones, like the tax drafts (`constants.counts_as_income`).
- **Dashboard tax deadlines** (#102) come from the Fiscal Calendar module; the
  core list is removed.
- **Reports year picker** (#94) lists the current year and every year with data.
- **Deprecated calls replaced** (#104): `utcnow()` and `Query.get()`; the test
  run has no warnings.
- Module comments rewritten in plain English (#105); tests for four modules
  that had none (#106).
- SECRET_KEY now required in production (hard fail), warns in dev.
- SQLite: WAL journal + `foreign_keys=ON`; SQLAlchemy `pool_pre_ping`/`pool_recycle`.
- `MAX_CONTENT_LENGTH` = 50 MB + friendly 413 handling.
- ECB exchange-rate feed cached in memory (4h TTL).
- Startup backup deferred ~60s off the boot path.
- GoogleDrive backend: exponential backoff + retry for transient errors.
- Module logging configured at import (works under gunicorn).
- `Content-Security-Policy-Report-Only` header added.
- Dockerfile: `apt-get upgrade` to clear base-image CVEs.

### Fixed
- Invoices in currencies other than EUR/USD are converted with their own rate,
  not the USD rate; a missing rate stops the save instead of using 1:1 (#97).
- The invoice view shows the invoice currency instead of "(USD)" (#96).
- The backup list no longer offers the live database for download, restore
  or delete (#92).

### Security
- **Module settings panels are auto-escaped and never show stored secrets**
  (#99): passwords, API keys and tokens are write-only fields; the API token
  is shown once after generation or rotation.
- **Service credentials encrypted at rest** (#100) with `CREDENTIALS_KEY` (or
  `instance/credentials.key`); existing values are encrypted on start-up.
- **Verifactu records signed with a dedicated key** (#93) — `VERIFACTU_KEY` or
  `instance/verifactu.key` — so rotating `SECRET_KEY` no longer breaks the
  chain; existing records are re-signed once.
- `delete_invoice` is POST-only + CSRF (was GET).
- Session cleared before populating identity (session-fixation defense).
- Failed logins recorded to the activity log.
- `secure_filename` empty-result guard.
- Hardened CSRF-error open-redirect to same-origin only.
- Log-injection sanitization on user-controlled values.
- `AUTH_CONFIG_PATH` env override; rate limits on backup/restore and AI parse.
