# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **AI Agent** (`ai_agent`), the bridge between modules and AI providers:
  OpenAI and Anthropic over their APIs, Claude Code CLI and Kiro CLI as
  programs on the server (run without any tool; off unless
  `AI_AGENT_ALLOW_CLI=1`). It declares `ai.complete_text` and
  `ai.text_status` and has no features of its own; the Ledger Assistant asks
  through it and never sees an API key. Settings → AI Agent holds the
  provider, the encrypted keys and the models, with "Refresh list" and "Check
  connection". On first enable it copies the provider, models and keys the
  AI Parser has stored, once; `AI_PARSER_*` CLI variables are used when the
  `AI_AGENT_*` ones are not set.
- **Invoice templates render themselves**: a module template in
  `get_invoice_templates()` may carry a `render(invoice, customer, settings)`
  callable instead of a file `path`. The Invoice Designer uses it, so the core
  no longer imports the designer or reads its table with SQL when it creates,
  downloads or previews an invoice PDF; a test refuses any core import of a
  module.
- **No module imports another module**: the API declares `api.check_token`,
  which Reports' API route uses instead of the module's `verify_token`; the
  invoice output-VAT rule Reports shares with Spanish Tax Forms moved to the
  core (`invoice_vat.py`). The OpenAPI document lists `/expenses` while
  something provides `ledger.list_expenses`.
- **PDF signing on the function registry**: PDF Signature declares
  `pdf.sign_invoice` and `pdf.signing_certificate`, PDF Verify
  `pdf.extract_signatures`. The core invoice route, E-invoice, the document
  form and PDF Signature's badge call them instead of reading another module's
  model or configuration, or importing its code.
- **Tax data on the function registry**: Tax Management declares
  `tax.filed_boxes` and `ss.payments_total`, the IRPF Estimator
  `tax.modelo130_boxes`, `tax.modelo100_result` and `tax.equipment_rule`, the
  RETA Advisor `reta.projection`. Spanish Tax Forms, the IRPF Estimator, the
  RETA Advisor and Tax Provision call them by name instead of using another
  module's methods or its `SSPayment` model, and their dependencies name these
  functions instead of modules, so a replacement that declares them needs no
  `provides`. Registry parameters may be `float`.
- **Fiscal calendar on the function registry**: the Fiscal Calendar declares
  `calendar.upcoming_deadlines`, `calendar.deadline_for`,
  `calendar.deadlines_for_year` and `calendar.selected_forms`; the dashboard,
  Tax Provision, Tax Obligations and the Modelo 347 draft call them instead of
  importing the calendar data or using the module's `interface`, so another
  calendar module can replace it. With no calendar enabled the obligations page
  lists no rows and says to enable the Fiscal Calendar (it used to read the
  bundled dataset directly).
- **Modules on the function registry** (#147): Expenses declares
  `ledger.list_expenses`, Spanish Tax Forms `tax.draft_303` and
  `tax.draft_130`, the AI Parser `ai.parse_document`, `ai.complete_text` and
  `ai.text_status`. The Ledger Assistant names no module any more: it builds
  its catalogue from the functions offered to the assistant, reads through the
  registry, asks through `ai.complete_text` (it no longer handles API keys),
  and depends on `ledger.list_invoices` instead of the `api` module. The
  expense form shows AI parsing when something provides
  `ai.parse_document` and posts to its route. `GET /api/v1/expenses` returns
  the VAT fields too. A test refuses new imports between modules.
- **Function registry** (#146): named functions that the core and modules
  declare — description and code together, in the module's own folder
  (`get_functions()` returns `functions.Function`) — and that other code calls
  by name through `core.functions`, with the arguments checked against the
  declaration. A module, also one from outside this repository, replaces
  another by declaring the same names. The first provider of a name owns it
  (core, then modules in load order); a later claim is not used and shows as a
  warning on that module's card in Settings → Modules. Dependencies may name a
  function. The core provides `ledger.list_invoices` and
  `ledger.list_customers`, which `GET /api/v1/invoices` and `/customers` now
  call. `ApiError` moved to the core (`api_errors.py`).
- **AI Parser CLI agents** (#145): Claude Code CLI and Kiro CLI as parse providers,
  using the CLI installed and logged in on the server. Off unless the server
  sets `AI_PARSER_ALLOW_CLI=1`; each run is read-only in a temporary
  directory with only the upload. Kiro's model list comes from
  `kiro-cli --list-models`.
- **Tax provision panel** (#135): new Tax Provision module; dashboard total of
  the tax owed so far — IVA and Modelo 130 of the open quarter, renta for the
  year to date, RETA regularización — each from its own module, with links and
  filing dates; missing parts are named.
- **What-if simulator** (#137): `/irpf-es/simulator` changes income, expenses,
  comunidad and RETA base and shows the effect on IRPF, the remaining 130
  payments, the regularización and net income; nothing is saved.
- **Modelo 347 check** (#136): customers and suppliers above 3,005.06 EUR with
  the quarterly split and the exclusions explained; February window in the
  fiscal calendar.
- **AEAT import files** (#141): download the 303, 130, 349, 390 and 347 drafts
  in the official record layouts for the Sede (not validated by the AEAT).
- **Deduction checks on expenses** (#138): notices for home-office supplies,
  dietas limits and vehicle costs, each with its legal source; dismissable per
  expense; the expense is never changed.
- **Ledger Assistant** (#140): opt-in, read-only questions over invoices,
  expenses and drafts with the AI Parser's provider; only the records needed
  are sent and every answer lists them. `GET /api/v1/invoices` and
  `/expenses` accept date filters.
- **Installable app and receipt capture** (#139): web app manifest, a service
  worker that caches nothing, `/expenses/capture`, and an Android share target
  for receipt photos.
- **Verifactu and e-invoicing dates** (#134): obligation dates with their BOE
  source on the Verifactu page; the October 2028 date is shown as announced,
  not yet in force.
- **Modelo 390 draft** (#125): annual VAT summary at `/tax-forms-draft/390/<year>`,
  each casilla the sum of the four quarterly 303 drafts; casillas checked
  against the AEAT 2025 instructions.
- **Year-keyed tax data** (#118): the fiscal calendar works for any year
  (weekend deadlines move to Monday); Modelo 303/130 boxes and RETA brackets
  are keyed by year and show "figures use the <year> table — verify" when an
  earlier table is used. Annual checklist in `CONTRIBUTING.md`.
- **IVA in report PDFs** (#115): IVA % and IVA (EUR) columns with totals in the
  income and expenses tables; reverse-charge rows marked `ISP` with the
  Art. 194 note.
- **Bank import expense suggestions** (#124): debit movements get ranked
  expense suggestions and a Link button (confirmation required).
- **Facturae XAdES-EPES signature** (#126): exports are signed with the PDF
  Signature certificate when one is configured (`.xsig`); unsigned otherwise.
- **Verifactu QR on invoice PDFs** (#127): every PDF of an issued invoice
  carries the AEAT QR (30 mm, level M, "QR tributario:"); official hash and
  QR URL formats in `modules/verifactu/aeat.py`; comparison with the AEAT
  specifications in `modules/verifactu/SPEC_COMPARISON.md`. Submission to AEAT
  is not available, so the QR uses the non-VERI*FACTU URL.
- **Lint and coverage** (#120): `ruff` (`make lint`), coverage in CI.
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
- **Text questions moved from the AI Parser to the AI Agent**: the AI Parser
  no longer declares `ai.complete_text` and `ai.text_status` (nor runs CLI
  agents for text); it reads documents only, with its own provider settings.
  Enable the AI Agent for the Ledger Assistant — its page says so until then.
- **Invoice status only through lifecycle actions** (#91): new invoices are
  always drafts; the status field is gone from the forms and the API
  (`POST /invoices/<id>/issue`, `/mark-paid`); annulled invoices are locked.
- **Dashboard income** (#112) counts every invoice dated in the year except
  cancelled ones, like the tax drafts (`constants.counts_as_income`).
- **Dashboard tax deadlines** (#119) come from the Fiscal Calendar module; the
  core list is removed.
- **Reports year picker** (#94) lists the current year and every year with data.
- **Deprecated calls replaced** (#121): `utcnow()` and `Query.get()`; the test
  run has no warnings.
- Module comments rewritten in plain English (#122); tests for four modules
  that had none (#123).
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
- **Expense category defaults after an AI parse**: the parsed category was set
  without the change event, so its defaults were never applied — a parsed fuel
  receipt ("Vehicle") kept "Deductible %" at 100 instead of 50. The prefill now
  applies them as choosing the category by hand does: the deductible %, and the
  category's VAT rate only when the document gave no VAT data (parsed VAT
  figures are never recomputed).
- **Ledger Assistant with a CLI agent**: with Claude Code CLI or Kiro CLI
  selected in the AI Parser, the assistant said the provider "cannot answer
  questions". The AI Parser now answers `ai.complete_text` with the selected
  CLI agent too — no tools, an empty temporary directory, the message on
  stdin. Only Google Document AI cannot answer text.
- **AI Parser PDF support** (#144): PDFs are sent as documents (Anthropic `document`
  block, OpenAI `file` part) instead of as images, which the providers
  rejected; the file type comes from its bytes, and GIF and WebP are accepted.
  Prompts now cover the Meals, Vehicle and Social Security categories, the
  supplier's VAT number and country, and reverse charge; a file that is not
  an invoice or receipt returns 422. The module is split into `catalog.py`,
  `media.py`, `prompts.py` and `providers.py`.
- **AI Parser models** (#143): parsing with Anthropic failed with 404 because the
  default `claude-sonnet-4-20250514` is retired. Defaults are now
  `claude-sonnet-5-5` and `gpt-6.1-terra`; stored settings on a retired Claude
  model move to the default on start-up. Settings → AI Parser lists the models
  the API key can use ("Refresh list", from the vendors' `/v1/models`). OpenAI
  requests send `max_completion_tokens` and no `temperature`, which current
  models require. Provider errors say what to fix (model, key, rate limit).
- Invoices in currencies other than EUR/USD are converted with their own rate,
  not the USD rate; a missing rate stops the save instead of using 1:1 (#114).
- The invoice view shows the invoice currency instead of "(USD)" (#113).
- The backup list no longer offers the live database for download, restore
  or delete (#92).

### Security
- **Module settings panels are auto-escaped and never show stored secrets**
  (#116): passwords, API keys and tokens are write-only fields; the API token
  is shown once after generation or rotation.
- **Service credentials encrypted at rest** (#117) with `CREDENTIALS_KEY` (or
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
