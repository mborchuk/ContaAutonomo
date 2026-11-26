# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Log levels and sources on System → Logs.** Every activity log entry now
  has a level (info, warning, error) and says who wrote it (the core or a
  module). The Logs page shows both and filters by *Warnings and errors* or
  *Errors*. Entries written before keep working and read as info; a database
  log gets the two new columns at start-up.
- **Every module writes the activity log the same way.** Modules call
  `self.log()`, which goes through `log.write`: actions in snake_case and the
  past tense, one list of categories (`LOG_CATEGORIES`), failures as
  warnings or errors. Some entries changed: `bank_import` is now
  `bank_statement_imported` (category `bank`), `ai_parse_success`
  `ai_parse_succeeded`, `verifactu_record` `verifactu_record_added`,
  `fiscal_deadline_reminder` `fiscal_deadline_reminded`, `demo_loaded`
  `demo_data_loaded`; API tokens are under `auth`, reports under `report`,
  tax filings and reminders under `tax`. A failed login and a failed PDF
  generation are warnings. Modules can react to entries with
  `core.add_activity_listener()`.
- **Server log.** An error caught in an `except` block is logged with its
  traceback (`.exception()`), and lint (ruff `G`, `LOG`, `TRY400`) keeps it
  that way.
- **`log.write`, a core function for writing the activity log.** A module
  writes an entry on System → Logs through the function registry
  (`source`, `action`, optional `details`, `category` and `level`), into
  the file or database log chosen in Settings, without importing the core.
  Source, action and category are checked (lower case letters, digits,
  `_`); a `warning` or `error` also goes to the server log, with the action
  only.
- **Yearly VAT return from 2027 (Real Decreto-ley 43/2026).** In Settings →
  Tax Forms the owner records that they file VAT yearly from a tax year
  (régimen especial de declaración anual, up to 85,000 EUR of volume of
  operations) and, optionally, each advance as the AEAT shows it. The new
  page Yearly VAT return adds up the year's quarterly drafts and deducts the
  three advances (20 April, 20 July, 20 October; a quarter of the previous
  year's result). The quarterly 303 pages say there is nothing to file, the
  Fiscal Calendar shows the advance debits and the yearly return instead of
  the quarterly 303 (new `filing_plan` capability), and Tax Provision counts
  the year so far less the advances debited. The AEAT form is not published
  yet: concepts, not box numbers, and no AEAT file.
- **IRPF: Real Decreto-ley 43/2026 figures, and the yearly IRPF figures in
  Tax Data.** For 2026 the gastos de difícil justificación are 10 % of the
  net with a 3,000 EUR cap (were 5 % and 2,000 EUR), counted from the
  fourth-quarter Modelo 130 on; the first three quarters keep the old
  figures, as filed. The percentage, the cap and the Modelo 130 rate are a
  table in Taxes → Tax Data (`es.irpf_figures`): the owner confirms them for
  a year or enters new values, and the IRPF Estimator, the Modelo 130 draft
  and the renta estimate use them. A year without its own figures warns on
  the IRPF pages until confirmed.
- **Modelo 130 from 2027 (Real Decreto-ley 43/2026).** The rate is 19 %
  instead of 20 % when the net turnover two years before was below 250,000
  EUR. No Modelo 130 is due when it was below 85,000 EUR and all of that
  year's payments were refunded in its renta. The owner can also record the
  option for payments the AEAT calculates and debits, with the amount the
  AEAT shows. The turnover comes from the invoices in the app (or the owner
  enters it) and the answers are in Settings → IRPF (España); the rates and
  limits are in Tax Data. The IRPF Estimator, the Modelo 130 draft, the
  renta estimate and Tax Provision follow it.
- **Documents: AI suggestions for fields and tags.** Off until the owner
  turns them on in Settings → Documents and chooses the provider (the AI
  Agent's choice or a named one; nothing is sent when it is not set up),
  the categories never sent and how many characters may go out. On a
  document's page the owner ticks the PDFs whose text may be sent; only that
  text and the category and tag names go to the AI. The checked suggestions
  (name, category, dates, amount, reference, counterparty, creator,
  language, description, tags) are applied one by one; every request is in
  the activity log and the document history. The provider is asked in the
  background: the page shows that it is waiting, cannot send the same
  request twice, and shows the suggestions or the problem when the answer
  arrives.
- **AI Agent: a caller may choose the provider.** `ai.complete_text` and
  `ai.text_status` take an optional `provider`, and `ai.list_providers`
  lists every provider with its model and whether it can answer now, so a
  module can let the owner choose where its data goes. Without `provider`
  nothing changes.
- **Documents: search in the text of PDFs.** A background job reads the
  text of each stored PDF through `pdf.extract_text` — existing files too —
  and indexes it with SQLite FTS5, so saving a document stays fast. The
  Documents search then also finds documents by words in their files —
  accents folded, words as prefixes — and shows where they matched. The
  list and the document page show a badge per document — *Text read*,
  *Text pending*, *Partly read*, *Scan: needs OCR*, *Text not readable* —
  and the document page the status of each PDF. Settings → Documents turns the job on or
  off, sets how often it runs (default every 5 minutes) and how many PDFs it
  reads per run (default 10), shows how many are waiting, and reads a batch
  at once with *Read now*. Backups leave the index out; a restore drops it
  and the next start rebuilds it.
- **PDF text through the registry.** The core provides `pdf.extract_text`:
  the text layer of a PDF, page by page, with pypdf (no OCR), and a status
  that tells a scan (`no_text_layer`), an encrypted or broken file apart.
  It is a *core default*: a module that declares the same function — a
  PyMuPDF or OCR module — replaces it without a conflict, and its card in
  Settings → Modules says so. The AI Parser's CLI agents read PDFs through
  it.
- **Documents: Dublin Core metadata (ISO 15836).** Every document has an
  identifier the app assigns and never changes (`DOC-2026-0001`, numbered
  within the year it was added; existing documents get one on the first
  start, oldest first), and new fields Creator, Language (ISO 639-1, Spanish
  by default for new documents) and Rights. The document page downloads its
  Dublin Core record as `oai_dc` XML; the API returns it at
  `GET /api/v1/m/documents/documents/<id>/dublin-core`, and the document
  endpoints include the new fields. Search also finds the identifier and the
  reference number.
- **Documents: file integrity and versions.** Each file stores its SHA-256
  fingerprint when it is uploaded; *Verify integrity* on the document page
  reads every file and earlier version back, compares, and records the result
  in the history (files stored before this get their fingerprint on the first
  check). *Replace* uploads a new version and keeps the earlier one, which
  stays downloadable; signing a PDF also keeps the unsigned version instead
  of overwriting it. A PDF that declares PDF/A (ISO 19005, archival PDF) in
  its metadata shows its level, e.g. PDF/A-2B; the claim is not validated.
- **Documents: records and retention (ISO 15489).** Each category can have a
  retention period in years (Documents → Categories; the owner sets them, the
  page lists common Spanish periods with their source). *Declare as record*
  on a document fixes it: its details and files can no longer be changed or
  deleted (notes can) until 31 December of the year of its date plus the
  period. Documents → Retention lists the records past that date — keep one
  longer or dispose of it — and those due in the next 12 months; a message
  in the bell says when a record is due. Every deletion and disposal is kept
  in a log on that page (identifier, name, reason, files with their SHA-256),
  also after the document is gone. Removing the record status needs a
  reason, kept in the history.

### Changed
- **Verifactu starts on 6 October 2028 for everyone.** Real Decreto-ley
  43/2026 (BOE-A-2026-24701) moves the Verifactu obligation of every
  taxpayer to the end of the B2B e-invoicing period for autónomos. The
  status page shows it in force and the 1 January 2027 and 1 July 2027
  dates of Real Decreto-ley 15/2025 as superseded.
- **The core is a package.** The 22 Python files of the repository root are
  now the `contaautonomo` package: `contaautonomo/app.py`, `core/` (module
  system, function registry, message center, invoice PDFs), `security/`
  (authentication, stored secrets, rate limits, upload checks) and `utils/`.
  The core's templates, static files and invoice templates moved into it;
  `docker_entrypoint.py` and `gunicorn.conf.py` into `deploy/`,
  `MODULES_DOCUMENTATION.md` into `docs/`, `demo_data.json` into the Backup
  module, the pytest settings into `pyproject.toml`. Runtime data —
  `instance/`, `logs/`, `backups/`, uploads — stays where it was (the
  repository root, `/app` in the image), so installations and Docker volumes
  are unchanged; `CONTAAUTONOMO_DATA_DIR` can move it. Start the development
  server with `python -m contaautonomo.app` and gunicorn with
  `gunicorn --config deploy/gunicorn.conf.py contaautonomo.app:app`; the
  Docker image does this itself.
- **`contaautonomo/app.py` is split.** The models are in `models.py`, the
  routes in `routes/` (invoices with their lifecycle, dashboard, customers,
  contractors, settings, messages, system), `db` in `extensions.py`; `app.py`
  keeps the configuration, security, the shared helpers and start-up
  (2,664 → about 500 lines). URLs and endpoint names are unchanged, and
  `from contaautonomo.app import Invoice, db` still works.
- **For module authors:** import the core by its new names, e.g.
  `from contaautonomo.core.module_manager import BaseModule`,
  `from contaautonomo.core.functions import Function, Param`. The old names
  (`module_manager`, `app`, `functions`, …) still work, as the same modules,
  with a `DeprecationWarning`; they go in the next major version
  (`docs/MODULES_DOCUMENTATION.md` → *Package Layout*).
- The test suite keeps its runtime data in a temporary folder; it no longer
  writes logs or generated invoice PDFs into the checkout.

### Fixed
- Documents: the edit form showed "None" in an empty description.
- Documents: deleting a document deleted the stored files its duplicate
  still used; a stored file is now deleted only when nothing uses it.

## [2.0.0] - 2026-11-20

### Upgrading to 2.0.0

- **From 1.7.0** nothing is needed before the upgrade: the database is updated
  on the first start (new tables such as `message` and `ai_agent_config`, and
  the column `invoice.pdf_generated`). Back up first and keep the same
  `SECRET_KEY`, `BACKUP_KEY`, `CREDENTIALS_KEY` and `VERIFACTU_KEY`.
- **From a release older than 1.7.0**, add `VERIFACTU_KEY` and `CREDENTIALS_KEY`
  to `.env` before the upgrade and start the first time with the same
  `SECRET_KEY`, so the Verifactu records are re-signed with the new key; from a
  release older than 1.6.0, also add `BACKUP_KEY` and run one Gunicorn worker.
- **New modules start switched off**: AI Agent, Ledger Assistant and Tax
  Provision (Settings → Modules). The Ledger Assistant needs the AI Agent to
  answer; when first enabled, the AI Agent copies the AI Parser's provider
  settings and keys once. CLI agents run only with `AI_AGENT_ALLOW_CLI=1` or
  `AI_PARSER_ALLOW_CLI=1`.
- **Tax Data** (Taxes → Tax Data) is a new module that is on by default: the
  yearly tax tables are checked and confirmed there, not in the code.
- **Dashboard**: the Document Alerts and Fiscal Deadlines panels are gone; their
  alerts and deadlines are messages in the 🔔 bell (top right).
- **Settings**: the report template moved from Settings → General to its own
  tab, Settings → Reports. The automatic-backup switch and retention stay in
  Settings → Backup. Both keep their values on upgrade.
- **Not yet validated against the official systems**: the AEAT import files
  (Modelo 303, 130, 349, 390, 347). Import each once in the AEAT Sede before
  filing with it.
- The REST API (`/api/v1`) is unchanged, except that the Modelo 303 draft
  reports output VAT in boxes `07`/`09` instead of `01`/`03`.

### Changes for module authors

These make 2.0.0 a major version; a module written for 1.x needs them:

- Yearly tax tables are the modules' own: the core's `year_tables.py`
  (`resolve`, `fallback_note`) is gone. A module keeps its tables keyed by
  year, declares them to Tax Data as `year_table` capabilities and reads the
  owner's confirmation with `taxdata.year_values`
  (`modules/tax_es_forms/yearly.py` is an example).
- Rules one module owns and another needs are registry functions, not core
  code: the reverse-charge region check moved from the core
  (`supplier_region.py`) to Spanish Tax Forms (`vat.reverse_charge_check`),
  and so did the output VAT of an invoice (`vat.invoice_lines`, which Reports
  calls; without it the report's income IVA cells show '–'). The core's
  `invoice_vat.py` is now `invoice_amounts.py` with only `to_cents`,
  `invoice_base_eur` and `customer_tax_type`; `invoice_output_vat` and
  `invoice_vat_line` are in `modules/tax_es_forms/output_vat.py`.
- `core.deployment_key(env_var, purpose, file_name)` takes the key file's
  name (under `instance/`, moved with `<env_var>_FILE`) instead of an
  `app.config` key: the core no longer defines `VERIFACTU_KEY_FILE` or
  `BACKUP_KEY_FILE`. The environment variables work as before.
- The core `Settings` model no longer has `report_template` (now Reports'
  `reports_config`), `auto_backup_enabled` and `daily_backup_retention_count`
  (now Backup's `backup_config`) or the unused `backup_retention_count`. An
  upgraded database keeps the old columns; the modules copy their values once.
  The report template is chosen in Settings → Reports.
- Modules do not import each other or reach into another module: they call
  registry functions (`self.core.functions.call(name, ...)`) and declare their
  own in `get_functions()`. The test suite refuses new imports.
- `ai.complete_text` and `ai.text_status` come from the AI Agent, no longer
  from the AI Parser.
- Invoice PDFs are built by the core function `invoice.render_pdf`; only
  templates listed by `get_invoice_templates()` are used, and a module template
  may carry its own `render` callable. The Invoice Designer reads module blocks
  through `invoice.list_blocks`.
- Navigation is a left sidebar: `nav_items` take `group` and `order`; a module
  can add a top-bar widget with `get_header_widgets()`.
- Alerts for the owner go to the message center through `messages.post`
  instead of dashboard panels.
- `calculate_income_tax` has no Spanish fallback in the core any more: the
  dashboard's built-in IRPF brackets are gone. Without a module that answers
  (the IRPF Estimator for Spain), the dashboard shows no income tax and says
  that no income tax module is enabled. The result keys are unchanged
  (`income_tax`, `label`, `irpf_breakdown`).

### Added
- **Reverse charge must match the supplier's country**: an expense marked
  reverse charge for "another EU country" whose supplier is outside the EU
  (for example a US company), the opposite, or either for a Spanish supplier
  gets a "Check" notice on the expense (a new deduction-check rule, LIVA arts.
  15, 69, 84.Uno.2º) and is listed on the Modelo 303 draft. The region comes
  from the supplier's country or its VAT number prefix; an unknown supplier
  gets no notice.
- **Message center**: a 🔔 bell in the top bar (right) shows the number of unread
  messages; opened, it lists them, each with **Mark as read**, which hides it
  without leaving the page. The **Messages** page (System → Messages) keeps the history:
  mark as read or unread, delete. Modules post with the core registry function
  `messages.post` (text with emoji and `[label](url)` links to app pages or
  external sites, an optional picture up to 64×64, a level, and a
  deduplication key so a read or deleted message does not come back);
  `messages.unread_count` gives the count. Each message has an id, and every
  post, read, unread and delete is in System → Logs, linked to the message.
- **Sidebar navigation**: the menu moved from the top bar to a left sidebar.
  Groups (Invoices, System, and groups that modules create) open and close in
  place and remember their state; the current page is highlighted and its
  group opens. Settings, a dark-mode switch and Logout sit at the bottom of
  the sidebar; the floating theme button is gone (the `D` shortcut still
  works). On phones and tablets the sidebar opens as a drawer from the menu
  button (Escape or the backdrop closes it; focus stays inside while open).
- **Top bar for module widgets**: `BaseModule.get_header_widgets()` lets a
  module place a widget such as search or notifications in the top bar, on
  the right (`position: 'end'`, default) or the left (`'start'`). A failing
  module is skipped; with no widget the bar is hidden on wide screens.
- **Navigation order**: a module's nav item may carry `order` (lower first) to
  sort it within its group or among the top-level links; items without it keep
  load order.
- **VAT deduction check on parsed expenses**: the AI Parser also reports the
  buyer's tax ID (`buyer_tax_id`). When a parsed document has Spanish VAT but
  does not show the owner's NIF, or shows another tax ID, the expense form warns
  next to "Deductible (IVA soportado)" that its VAT is normally not deductible
  (LIVA art. 97; RD 1619/2012, arts. 6–7) and how to record it. Nothing is
  changed automatically. Reverse-charge, foreign and 0 % VAT documents are not
  flagged.
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
  invoice output VAT Reports shows comes from Spanish Tax Forms through
  `vat.invoice_lines`. The OpenAPI document lists `/expenses` while
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
- **How Modelo 130 boxes 01, 02 and 05 are made up**: the Modelo 130 draft
  page shows box 01 by invoice (date, number, customer, amount in its own
  currency × the stored rate = EUR in cents, retention for box 06), box 02 by
  expense category (each category opens to its expenses:
  date, supplier, description, amount without VAT), each asset's
  depreciation of the year, Social Security with its source (payments
  recorded, or the Settings monthly amount × months) and the 5 % difícil
  justificación when it is applied; and box 05 by earlier quarter, from the
  filed return or estimated by the app, saying what to do in Tax Forms. It
  makes a comparison with the gestor's sales and expense ledgers line by line. Tax
  Forms warns when uploaded Modelo 130/303/349 PDFs have no boxes read yet
  (uploads made before boxes were read on upload) and names them; Read filed
  boxes reads them.
- **Tax Data module** (Taxes → Tax Data, on by default): the yearly tax
  tables — Modelo 303 and 130 boxes, 390 casillas, the 347 threshold, the
  RETA brackets and rate — with their state for a year, what each holds,
  numbered steps and links to the official AEAT, BOE and Seguridad Social
  pages. The owner confirms a table unchanged for a new year, or enters the
  new 347 threshold or RETA values, in the app; the pages use them for that
  year and stop warning (*"Figures for 2027 use the 2026 table"*, which now
  links there). For Modelo 303, 130 and 390 the app compares its box numbers
  with the official AEAT record layouts it ships and shows the result; a test
  does the same on every run. Modelo 390 and 347 have 2026 tables (390
  checked against the 2026 layout, 347 against RD 1065/2007 art. 33).
  CONTRIBUTING.md → Annual Tax Data Update is a step-by-step guide.

### Changed

### Changed
- **Ledger Assistant shows that it is thinking**: after **Ask**, the button
  reads "Thinking…" and cannot be pressed twice, a note with a spinner says the
  assistant is looking up records and asking the AI provider, a timer counts
  the seconds, and after a minute the note says a CLI agent can take a few
  minutes. The previous answer is dimmed until the new one arrives.
- **Invoice designer blocks through the registry**: the Invoice Designer reads
  the blocks other modules add to its layouts (the Verifactu QR) through the new
  core registry function `invoice.list_blocks` instead of calling the module
  manager directly. Modules still add blocks with `get_invoice_blocks()`; what
  the designer shows and prints is unchanged.
- **One code path for invoice PDFs**: the new-invoice PDF, the PDF download and
  the PDF preview each had their own copy of "pick the template from
  Settings, build the PDF, fall back to the default". They now call the core
  registry function `invoice.render_pdf` (`invoice_pdf.py`), and the Settings
  → General "Invoice PDF Template" list comes from `invoice.list_pdf_templates`;
  modules can call both. Only a listed template is used: a template name that
  is not in the list no longer loads `invoice_templates/<name>.py` from the
  path the name gives. The new-invoice PDF now also passes through the modules'
  PDF filters (the Verifactu QR is added to issued invoices only, so drafts look
  the same). A missing template on download shows the same warning for core
  and module templates.
- **Document expiry alerts moved to the message center**: the Documents module
  no longer draws a "Document Alerts" panel on the dashboard. A document that
  expires within 30 days, or expired in the last 30 days, gets one message in
  the top-bar bell with a link to it (a daily job at 07:30 and the dashboard
  post them). A read or deleted alert does not come back; a new expiry date
  gives a new one.
- **Parsed supplier picks the contractor**: an AI-parsed expense selected a
  contractor only when its name was exactly the document's, so most suppliers
  ended up in Notes ("Contractor: Media Markt Saturn S.A. (ESA82037292)"). The
  form now matches by VAT number first, then by name, ignoring case, accents,
  punctuation and legal forms (S.L., S.A.U., GmbH, …). It chooses a contractor
  only when exactly one matches, skips a name match whose VAT number differs,
  and otherwise names the supplier next to the field and on its own line in
  Notes.
- **Fiscal Calendar**: the "📅 Fiscal Deadlines" dashboard panel is gone.
  Upcoming filing windows (within 60 days, or open now) are messages in the
  🔔 message center, one per form and period with both dates, posted once by
  the daily job or when the dashboard opens. The T-14/T-7/T-1 closing
  reminders are messages too (info, warning, danger), besides the activity log
  and email.
- **Tax pages grouped under Taxes**: Tax Forms, Obligations, Tax Drafts, IRPF
  Estimator, RETA Advisor and Fiscal Calendar now sit together in the Taxes
  menu (they were spread over System, Taxes and the top level). Verifactu moved
  to Invoices. System keeps Logs, Scheduled Tasks and Bank Import.
- **Text questions moved from the AI Parser to the AI Agent**: the AI Parser
  no longer declares `ai.complete_text` and `ai.text_status` (nor runs CLI
  agents for text); it reads documents only, with its own provider settings.
  Enable the AI Agent for the Ledger Assistant — its page says so until then.
- **Modelo 303 draft uses the official box numbers**: output VAT at 21 % is
  in boxes **07/09**, as on the official form (the draft page showed it as
  01/03, which are the 4 % boxes; a filed return prints 21,00 next to [08]).
  The 303 and 390 draft pages, the AEAT file and the `tax.draft_303`
  function and `GET /api/v1/m/tax_es_forms/draft/303/…` use `07`/`09`; the 390
  casillas 05/06 and 99 sum them.

### Fixed
- **Modelo 303 AEAT file rejected by the Sede**: importing a 1T–3T file failed
  with "El fichero se rechazó" (E010170, E010180) because the two prorrata
  especial flags were blank; they take only 1 or 2 (E010171, E010181) and are
  now `2` (no). The rate casilla [169] was zero where the Sede admits only
  0,50 (E010541): the 2026 layout now carries that fixed rate like the other
  rate casillas. The generator refuses a blank in any numeric field, and
  tests check every numeric field of the 303, 130 and 390 files and every 303
  rate casilla. After these fixes a 3T 2026 file imported in the Sede
  with "No existen errores"; the Modelo 130 file of the same quarter
  imported without errors as it was.
- **Used invoice number**: creating an invoice with a number that already
  exists ended in an error page (`UNIQUE constraint failed: invoice.invoice_number`).
  The create and edit forms now say under the Invoice Number field, before
  sending, that the number is used and by which invoice (customer and date), and
  the browser blocks Submit until it changes. The server refuses a used number
  with the same message before it writes anything (no new customer is created).
  Spaces around the number are removed.
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
- **Tax figures add each invoice in cents**: an invoice converted from
  another currency stores its EUR amount with many decimals, and the tax
  figures added the raw values, so Modelo 130 box 01 came out one cent above
  the books. `invoice_amounts.to_cents` rounds every invoice amount to the cent (half
  up) before it is added: Modelo 303, 130, 390, 347, Reports and the IRPF
  Estimator.
- **Modelo 130 breakdown on narrow screens and in print**: the amounts
  column of "How boxes 02 and 05 are made up" was cut off and long lines lost
  their ends when the page was narrow or printed; lines now wrap and the
  amounts stay in view. Each expense line and the Social Security total are
  in cents, so the parts add up to box 02 exactly (a capped difícil
  justificación showed 2000.01).
- **"Writing a Country Tax Module" in the README**: the example failed when
  the module was enabled (it used `self._db`, which only a module with
  `register_models` has) and imported `app`. It now uses `self.core`, reads
  the customer saved at issue (`invoice_amounts.customer_tax_type`), shows
  `get_tax_obligations` and `get_field_labels`, lists the hooks with what the
  dashboard passes to them, and says which Spanish modules to turn off. A
  test runs the example from the README. The Polish module's `on_enable` uses
  the core the same way.

## [1.7.0] - 2026-11-12

### Added
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

### Changed
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

### Fixed
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

## [1.6.0] - 2026-10-04

Everything up to 1.6.0. Versions 1.6.1 to 1.6.3 have no entries of their own.

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

[Unreleased]: https://github.com/mborchuk/ContaAutonomo/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/mborchuk/ContaAutonomo/compare/v1.7.0...v2.0.0
[1.7.0]: https://github.com/mborchuk/ContaAutonomo/compare/v1.6.3...v1.7.0
[1.6.0]: https://github.com/mborchuk/ContaAutonomo/releases/tag/v1.6.0
