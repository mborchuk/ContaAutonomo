# Core & Modules Documentation

Technical reference for the Autónomos application architecture.

## Table of Contents

1. [Core Components](#core-components)
2. [Module System](#module-system)
3. [CoreServices API](#coreservices-api)
4. [Existing Modules](#existing-modules)
5. [Creating a Module](#creating-a-module)

---

## Core Components

### Models (app.py)

| Model | Table | Description |
|-------|-------|-------------|
| `Customer` | `customer` | Client information |
| `Invoice` | `invoice` | Invoice records |
| `InvoiceItem` | `invoice_item` | Line items for invoices |
| `Bank` | `bank` | Bank account details |
| `Settings` | `settings` | Application configuration (single row) |
| `Contractor` | `contractor` | Contractor/vendor info |

### Core Routes

| Route | Description |
|-------|-------------|
| `/` | Dashboard |
| `/invoices` | Invoice list with filters |
| `/create` | Create invoice (calls `module_manager.on_invoice_created` after commit) |
| `/edit/<id>` | Edit invoice (calls `module_manager.on_invoice_updated` after commit) |
| `/view/<id>` | View invoice (renders `module_manager.get_invoice_actions`) |
| `/generate-pdf/<id>` | Generate invoice PDF |
| `/settings` | Settings (all tabs) |
| `/logs` | System activity logs |
| `/scheduler` | Scheduled tasks overview |
| `/customers/*` | Customer CRUD |
| `/contractors/*` | Contractor CRUD |

### Activity Logging

Core provides two logger backends:

- `FileActivityLogger` — JSON-lines files, one per day in `logs/` directory
- `DbActivityLogger` — SQLite `activity_log` table

### Currency Symbols (`currency_converter.py`)

Shared registry of 50+ currency code → symbol mappings. All templates and modules should use this instead of local dicts:

```python
from currency_converter import get_currency_symbol, CURRENCY_SYMBOLS

sym = get_currency_symbol('PLN')   # → 'zł'
sym = get_currency_symbol('EUR')   # → '€'
sym = get_currency_symbol('XOF')   # → 'XOF' (fallback to code)
```

Configurable in Settings → General Settings → System Logs. Modules have two logging mechanisms:

- Activity log (user-visible): `self.core.log_activity(action, category, details)` — appears in System → Logs
- Python logger (console): `self.logger.info(...)` — named `module.<module_id>`, for developer diagnostics

### Task Scheduler

Built-in lightweight scheduler (`TaskScheduler`) runs a daemon thread checking every 30 seconds.

```python
# In module on_enable():
self.core.scheduler.add_job(
    job_id='my_module.daily_task',
    func=self._my_task,
    job_type='daily',       # or 'interval'
    time_str='03:00',       # for daily
    interval=3600,          # for interval (seconds)
    description='My daily task',
)
```

View registered tasks at System → Scheduled Tasks.

---

## Module System

### Lifecycle

```
App startup
  └─ ModuleManager.discover_modules()    # scan modules/ directory
  └─ ModuleManager.load_enabled_modules()
       └─ For each enabled module:
            ├─ register_models(db)       # create DB tables
            ├─ register_routes(app)      # register Blueprint
            ├─ register_template_filters(app)
            └─ on_enable()               # init, migrations, scheduler jobs
  └─ scheduler.start()                   # start background thread
```

### Module States

- Stored in `module_enabled` table
- Toggle in Settings → Modules
- Restart required for full effect

---

## CoreServices API

Every module receives `self.core` — a `CoreServices` instance.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `core.app` | `Flask` | Flask application |
| `core.db` | `SQLAlchemy` | Database instance |
| `core.app_path` | `str` | Application root path |
| `core.storage` | `FileStorageBackend` | Active file storage backend |
| `core.activity_logger` | `ActivityLogger` | Active logger instance |
| `core.scheduler` | `TaskScheduler` | Task scheduler |
| `core.module_manager` | `ModuleManager` | Module manager (access other modules' data via contracts) |
| `core.invoice_service` | `InvoiceService` | Safe API for reading/writing invoices (PAID = read-only) |
| `core.currency_service` | `CurrencyService` | Exchange rate API — get rates, convert amounts, register custom providers |

### Methods

| Method | Description |
|--------|-------------|
| `get_settings()` | Get `Settings` model instance |
| `get_upload_path(subfolder)` | Get/create upload directory, returns absolute path |
| `flash(message, category)` | Flash a message to the user |
| `login_required(f)` | Decorator to protect routes |
| `save_file(file_data, subfolder, filename)` | Save file via storage backend |
| `delete_file(storage_key)` | Delete file via storage backend |
| `send_file(storage_key, download_name)` | Send file as download response |
| `file_exists(storage_key)` | Check if file exists |
| `set_storage_backend(backend)` | Replace file storage (used by external_storage module) |
| `log_activity(action, category, details, user)` | Log an activity entry |
| `set_activity_logger(logger)` | Replace activity logger |
| `get_activity_log(limit, category, offset)` | Retrieve log entries |

### InvoiceService (`core.invoice_service`)

Safe, controlled API for modules to interact with invoices.

| Method | Description |
|--------|-------------|
| `get(invoice_id)` | Get invoice by ID |
| `get_all(**filters)` | Query invoices with filters |
| `get_by_number(invoice_number)` | Get invoice by number |
| `is_locked(invoice)` | True if PAID |
| `has_pdf(invoice_or_id)` | True if PDF exists on disk. Accepts Invoice object or int ID. |
| `get_pdf_path(invoice_or_id)` | Filesystem path to PDF or None. Accepts Invoice object or int ID. |
| `update(invoice_id, **fields)` | Update fields (raises `ValueError` if PAID) |
| `attach_pdf(invoice_or_id, file_data, filename)` | Save PDF, compute hash. Accepts Invoice object or int ID. Raises `ValueError` if PAID + sealed. Logs file size, hash, replaced/new status. |

Prefer passing Invoice objects instead of IDs to avoid unnecessary DB queries.

### CurrencyService (`core.currency_service`)

Exchange rate API for modules. Default provider: ECB (European Central Bank) with exchangerate-api fallback. Modules can register custom rate providers.

#### Rate Operations

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_rate(from_currency, to_currency, date_str)` | `str`, `str`, `str` | `(float, str)` | Get exchange rate between two currencies. Returns `(rate, actual_date)`. |
| `get_rates(currencies, base, date_str)` | `list`, `str`, `str` | `dict` | Get rates for multiple currencies relative to base. Returns `{code: rate}`. |
| `convert(amount, from_currency, to_currency, date_str)` | `float`, `str`, `str`, `str` | `(float, float, str)` | Convert amount. Returns `(converted_amount, rate, actual_date)`. |

#### Provider Management

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `register_provider(name, provider_fn)` | `str`, `callable` | `None` | Register a custom rate provider. `provider_fn(from_cur, to_cur, date_str)` must return `(rate, actual_date)` or `(None, None)`. |
| `unregister_provider(name)` | `str` | `None` | Remove a custom provider. |
| `set_active_provider(name)` | `str` or `None` | `None` | Switch to a custom provider. `None` reverts to default ECB. |
| `active_provider` | — | `str` or `None` | Name of the active provider. |
| `available_providers` | — | `list[str]` | List of registered provider names. |

#### Usage Example

```python
svc = self.core.currency_service

# Get a single rate
rate, actual_date = svc.get_rate('USD', 'EUR', '2026-03-17')

# Convert amount
eur_amount, rate, actual_date = svc.convert(1000, 'USD', 'EUR', '2026-03-17')

# Get multiple rates at once
rates = svc.get_rates(['USD', 'GBP', 'CZK'], base='EUR', date_str='2026-03-17')

# Register a custom provider (e.g. National Bank of Ukraine)
def nbu_rate(from_cur, to_cur, date_str):
    # fetch from NBU API...
    return rate, date_str

svc.register_provider('nbu', nbu_rate)
svc.set_active_provider('nbu')  # all get_rate() calls now use NBU first
svc.set_active_provider(None)   # revert to default ECB
```

---

## Existing Modules

### Expenses (`expenses`)

Track business expenses with file uploads, categories, and contractor linking.

- Routes: `/expenses/`, `/expenses/add`, `/expenses/edit/<id>`, `/expenses/delete/<id>`
- Models: `Expense` (extends core model)
- Nav: Expenses (with group support)
- Reports: contributes expense data to financial reports
- Settings: expense-related options in General tab

### Tax Management (`tax_management`)

Spanish tax forms and Social Security payment tracking.

- Routes: `/tax-forms/`, upload, download, delete, SS payment CRUD
- Models: `TaxForm`, `SSPayment` (extend core models)
- Nav: Tax Forms
- Dashboard: SS data for tax obligations panel
- Reports: SS payments section in financial reports
- Settings: `social_security_monthly` in General tab

### Documents (`documents`)

Document management system with categories, tags, multi-file attachments, and change history.

- Routes: `/documents/` (list), `/documents/create`, `/documents/edit/<id>`, `/documents/view/<id>`, `/documents/delete/<id>`, `/documents/duplicate/<id>`, `/documents/bulk`, `/documents/download/<id>`, `/documents/preview/<id>`, `/documents/file/<id>/download`, `/documents/file/<id>/preview`, `/documents/file/<id>/delete`, `/documents/file/<id>/sign`, `/documents/categories`
- Models: `Document`, `DocumentCategory`, `DocumentFile`, `DocumentConfig`, `DocumentHistory`
- Nav: All Documents, Categories (grouped under Documents dropdown)
- Features:
  - Multi-file attachments per document (PDF, JPG, PNG, DOC, XLSX)
  - Document detail page with full info, files, and change history timeline
  - New fields: reference number, counterparty, status (Active/Pending/Archived/Expired)
  - Change history: automatic tracking of all field changes, file additions/removals
  - Categories with custom colors, auto-add on first use
  - Tags: comma-separated, clickable for quick filtering (in list and detail views)
  - Sortable columns: Date, Name, Category, Amount (asc/desc)
  - Pagination: 50 documents per page
  - Bulk actions: select multiple documents → delete, set category, or add tag
  - Duplicate: create a copy of any document with all metadata and file references
  - Expiry tracking: documents with expiry dates shown with color-coded warnings
  - Cross-module integration: "Sign PDF" button uses `pdf_signature` module when enabled
  - Dashboard: expiry alerts panel (expiring within 30 days + already expired)
  - Reports: contributes "Documents with Amounts" section to financial reports
  - Dropdown action menu per document (⋮) with view files, edit, duplicate, delete
  - Row coloring by category (toggleable in Categories page)

### Backup & Restore (`backup`)

Full encrypted backups including DB dump (all tables via SQLAlchemy inspector) and uploaded files.

- Routes: `/backup/`, create, download, restore
- Models: `BackupConfig`
- Features: AES-256-CBC encryption (none/app password/custom), custom backup directory, S3 upload via external_storage, daily auto-backup via scheduler
- Settings: backup options in General tab (`settings_tab = 'general'`)
- Scheduler: registers `backup.daily` job at 03:00

### Reports (`reports`)

PDF financial reports collecting data dynamically from all enabled modules.

- Routes: `/reports/`, generate
- Nav: Reports
- User selects which sections to include via checkboxes (Income is always available from core; other sections come from enabled modules)
- Collects data via `module_manager.get_report_sections()`
- Known section types (`expenses`, `ss_payments`) have dedicated rendering; unknown sections render as generic tables
- Modules provide section metadata: `id`, `title`, `description`, `query_fn`, optional `columns` and `total_field`

### External Storage (`external_storage`)

Configurable file storage backend. When enabled, replaces the default local storage with the selected provider.

- Supported backends: Local (default), AWS S3, Google Cloud Storage, Google Drive
- Settings: backend selection + provider-specific config (bucket/folder, auth method, credentials)
- Replaces `core.storage` via `set_storage_backend()`
- Auto-migrates DB schema when upgrading from S3-only version
- Google Cloud Storage auth: Application Default Credentials (ADC) or Service Account JSON key file
- Google Drive auth: Service Account JSON key file (share target folder with SA email) or ADC
- Google Drive uses file IDs as storage keys (opaque strings, not paths)

### Invoice Attachments (`invoice_attachments`)

Upload ready-made invoice PDFs instead of generating them.

- Routes: `/invoice-attachments/attach/<id>` (POST)
- Uses `core.invoice_service` for safe PDF attachment with hash tracking
- Injects "📎 Attach Invoice" button on invoice view page via `get_invoice_actions()` (hidden for PAID invoices)
- Injects PDF upload field on create page via `get_create_form_html()`
- Injects PDF upload field on edit page via `get_edit_form_html(invoice)` (shows "Replace PDF" if PDF exists)
- Processes uploads after save via `on_invoice_created()` / `on_invoice_updated()`
- Detailed activity logging: attach/replace/reject/fail events with file name, size, hash

### PDF Signature (`pdf_signature`)

Visual and digital signing of invoice PDFs.

- Routes: `/pdf-signature/settings` (POST), `/pdf-signature/upload-file` (POST), `/pdf-signature/preview-signature` (GET)
- Models: `PDFSignatureConfig` (signature image path, PFX path, position, margins, enable flags)
- Features:
  - Visual signature: image overlay on PDF with configurable position (top-left/right, center-left/right, bottom-left/right), margins, and max width
  - Digital signature: X.509/PFX certificate signing via pyHanko
  - Auto-signs on PDF generation when enabled
  - Signature checkboxes on invoice create form (hidden when both types disabled in settings)
- Settings: dedicated "PDF Signature" tab with image upload, PFX upload, position controls
- Hooks: `on_invoice_created()` — auto-signs after PDF generation
- Files stored in `pdf_signature_files/` via `core.storage`

### AI Parser (`ai_parser`)

Parse invoice data from PDFs and images using AI providers.

- Routes: `/ai-parser/parse` (GET/POST), `/ai-parser/settings` (POST)
- Models: `AIParserConfig` (provider, API key, model)
- Supported providers: OpenAI (GPT-4), Anthropic (Claude), Google (Gemini)
- Settings: dedicated "AI Parser" tab with provider selection, API key, model config

### Invoice Designer (`invoice_designer`)

Visual UI for creating custom invoice PDF templates with configurable block positioning, colors, fonts, and labels.

- Routes: `/invoice-designer/` (list), `/invoice-designer/create`, `/invoice-designer/edit/<id>`, `/invoice-designer/delete/<id>`, `/invoice-designer/duplicate/<id>`, `/invoice-designer/preview/<id>`, `/invoice-designer/import` (POST), `/invoice-designer/export/<id>`
- Models: `InvoiceTemplate` (`invoice_template_config` table — name, config_json, logo_storage_key)
- Nav: Invoice Designer (grouped under Invoices dropdown)
- Features:
  - Grid-based layout: 5 zones (top, header, body, bottom, footer) × 3 columns (left, center, right)
  - 8 placeable blocks: logo, title, sender_info, recipient_info, invoice_meta, bank_details, notes, payment_terms
  - Each block assignable to any slot (e.g., `top-left`, `footer-right`, `hidden`)
  - Fine-tune X/Y offsets per block in points
  - 6 layout presets (Standard, Classic Right, Modern Center, Minimal Left, Compact Header, Bottom Bank)
  - Configurable: accent/text/header/page background colors, font, title size, layout style
  - Toggle sections: logo, bank details, notes, payment terms, due date, VAT breakdown, accent line, separator lines
  - Customizable labels for all invoice text (Invoice #, Bill To, Subtotal, etc.)
  - JSON import/export for sharing templates
  - Edit as JSON toggle with live sync to visual editor
  - Preview generates PDF with real settings data (sender, bank, customer from DB)
  - Templates appear in Settings → Invoice PDF Template dropdown with `🎨` prefix
  - `app.py` patched in 3 locations (create, download, preview) to handle `__designer__` template path

### Tax Poland IT (`tax_poland`)

Polish tax rules for IT freelancers (JDG/B2B).

- Models: `TaxPolandConfig` (tax_mode, zus_monthly)
- Settings: Tax Poland section in General tab (tax mode radio, ZUS monthly input)
- Tax hooks:
  - `calculate_income_tax()`: flat 19% PIT or progressive 12%/32% with 30,000 PLN tax-free + health insurance (4.9% flat / 9% progressive)
  - `calculate_vat()`: Polish 23% VAT
  - `get_tax_obligations()`: ZUS annual contribution in dashboard
- `on_enable()`: sets default VAT rate to 23%
- Example of a country-specific tax module — see source for implementation patterns

---

## Creating a Module

### Minimal Example

```
modules/my_module/
├── __init__.py
├── index.py
└── templates/
    └── my_page.html
```

```python
# modules/my_module/index.py
from module_manager import BaseModule
from flask import Blueprint, render_template

class MyModule(BaseModule):

    @property
    def module_id(self):
        return 'my_module'

    @property
    def name(self):
        return 'My Module'

    @property
    def description(self):
        return 'What this module does'

    @property
    def settings_tab(self):
        return 'general'  # or 'security'

    def register_models(self, db):
        self._db = db
        class MyRecord(db.Model):
            __tablename__ = 'mymod_record'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(200))
        self.MyRecord = MyRecord
        return {'MyRecord': MyRecord}

    def register_routes(self, app):
        bp = Blueprint('my_module', __name__,
                       template_folder='templates',
                       url_prefix='/my-module')
        module = self

        @bp.route('/')
        @self.core.login_required
        def index():
            items = module.MyRecord.query.all()
            return render_template('my_page.html', items=items)

        app.register_blueprint(bp)
```

### BaseModule Properties

| Property | Required | Default | Description |
|----------|----------|---------|-------------|
| `module_id` | Yes | — | Unique identifier matching directory name |
| `name` | Yes | — | Human-readable name |
| `description` | No | `''` | Shown in Modules settings |
| `version` | No | `'1.0.0'` | Semantic version |
| `nav_items` | No | `[]` | Navigation menu entries (supports `group` key for dropdown placement) |
| `settings_tab` | No | `'general'` | Which settings tab to place module settings in |

### Navigation Grouping

Module nav items support a `group` key to place them inside existing or new dropdown menus:

```python
@property
def nav_items(self):
    return [
        {'label': 'My Tool', 'endpoint': 'my_mod.index', 'icon': '🔧', 'group': 'Invoices'}
    ]
```

Core dropdown groups: `Invoices`, `System`. Any other group name creates a new dropdown.
Items without `group` appear as top-level links. The full menu is built by `ModuleManager.get_full_nav()`.

### BaseModule Methods

| Method | Description |
|--------|-------------|
| `register_models(db)` | Define DB models, return `{'Name': Class}` |
| `register_routes(app)` | Register Flask Blueprint |
| `on_enable()` | Called on load — migrations, scheduler registration |
| `on_disable()` | Called on disable |
| `get_settings_html(settings)` | Return HTML for settings tab |
| `save_settings(settings, form)` | Handle settings form POST |
| `get_dashboard_panels()` | Dashboard widget data |
| `get_report_sections()` | Report section generators |
| `get_invoice_actions(invoice)` | HTML snippets for invoice view actions bar |
| `get_create_form_html()` | HTML to inject into invoice create form |
| `get_edit_form_html(invoice)` | HTML to inject into invoice edit form |
| `on_invoice_created(invoice, request)` | Called after new invoice is committed |
| `on_invoice_updated(invoice, request)` | Called after existing invoice is committed |
| `get_invoice_templates()` | Invoice PDF templates provided by this module |
| `get_tax_obligations(context)` | Tax panel contributions |
| `calculate_income_tax(context)` | Override income tax calculation (first non-None wins) |
| `calculate_vat(context)` | Override VAT collection calculation (first non-None wins) |

### Built-in Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.core` | `CoreServices` | Core services interface |
| `self.logger` | `logging.Logger` | Python logger named `module.<module_id>` for console/debug output |

### Cross-Module Communication

Modules can access other enabled modules via `self.core.module_manager.modules`:

```python
# Check if another module is enabled and call its methods
pdf_sig = self.core.module_manager.modules.get('pdf_signature')
if pdf_sig:
    signed_bytes = pdf_sig._apply_visual_signature(pdf_bytes)
```

This pattern is used by the Documents module to offer PDF signing via the PDF Signature module.
Only access modules that are enabled — always check with `.get()` first.

See [modules/README.md](modules/README.md) for the full development guide with examples.
