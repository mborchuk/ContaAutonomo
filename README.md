# ContaAutónomo — Invoice & Finance Manager

A self-hosted web application for freelancers and small businesses to manage invoices, expenses, tax obligations, and financial reports. Built with Flask and a modular plugin architecture that adapts to different countries' tax systems.

## Features

- Invoice management with PDF generation and customizable templates
- Invoice lifecycle: draft → issued → paid, with per-series sequential numbering,
  a fiscal snapshot frozen at issue, rectificative invoices, and annulment
- Expense tracking with VAT breakdown (net / rate / amount, deductibility)
- Multi-currency support with ECB exchange rate conversion and 50+ currency symbols
- Customer and contractor management
- Configurable tax rates (VAT, income tax advance) per country
- Configurable payment methods per invoice
- Message center: a bell in the top bar with unread messages from the app and its modules (mark as read in place) and a Messages page with the history, every change in the activity log
- Modular architecture — enable only what you need
- Password-protected access with pluggable auth providers (OAuth, SAML)
- CSRF protection, rate limiting, and security headers
- Encrypted backups with scheduling
- Activity logging (file or database) with configurable retention
- Built-in task scheduler for periodic jobs
- Dashboard with financial overview, tax calculations, exchange rates, and clickable invoice rows
- Invoice signature verification — detects digital signatures in uploaded PDFs

### Modules (enable/disable in Settings → Modules)

| Module | Description |
|--------|-------------|
| **Expenses** | Track business expenses with file uploads, categories, and contractor linking |
| **Tax Management** | Spanish tax forms (Modelos 349, 303, 130, 390, 100) and Social Security payments |
| **Tax Poland (IT)** | Polish tax rules for IT freelancers (JDG/B2B) — flat 19% or progressive 12%/32%, VAT 23%, ZUS |
| **Documents** | General document storage and management |
| **Backup & Restore** | Full encrypted backups (DB + files), daily scheduling, S3 support |
| **Reports** | PDF financial reports with dynamic section selection from enabled modules |
| **External Storage** | Pluggable file storage: Local, AWS S3, Google Cloud Storage, Google Drive |
| **Invoice Attachments** | Upload signed/scanned invoice PDFs with SHA-256 hash tracking |
| **PDF Signature** | Visual (image overlay) and digital (X.509/PFX) signing of invoice PDFs |
| **PDF Verify** | Detect digital signatures in PDFs — shows signer info, clickable badge on documents and invoices |
| **Invoice Comments** | Internal comments/notes on invoices (not visible on PDF) |
| **Invoice Designer** | Visual editor for custom invoice PDF templates with grid layout and presets |
| **AI Agent** | Connection between modules and AI providers (OpenAI, Anthropic, Claude Code CLI, Kiro CLI): modules ask through it by function name and never see an API key |
| **AI Parser** | Parse invoice and expense-receipt data from PDFs/images using AI providers (OpenAI, Anthropic, Google) |
| **Fiscal Calendar** | Spanish AEAT filing deadlines and configurable reminders as messages in the 🔔 message center |
| **Modelo 303/130 Drafts** | Box-level quarterly IVA/IRPF draft calculator (estimate, not tax advice) |
| **RETA Advisor** | Contribution-bracket projection and year-end regularization forecast |
| **Tax Provision** | Dashboard total of the tax owed so far (IVA, Modelo 130, renta, RETA), each part from its own module (estimate, not tax advice) |
| **Recurring Invoices** | Invoice templates on a monthly/quarterly cadence that generate drafts automatically |
| **Invoice Email** | Send invoice PDFs via SMTP with send history and opt-in overdue reminders |
| **Verifactu** | Tamper-evident, hash-chained billing records on issue/annul (RD 1007/2023 compliance assist) |
| **Bank Import** | Norma 43 / CSV statement import with match suggestions and one-click reconciliation |
| **E-Invoice (Facturae)** | Facturae 3.2.2 XML export for issued invoices with a readiness checklist |
| **Ledger Assistant** | Opt-in, read-only questions over invoices, expenses and tax drafts through the AI Agent; sends only the records needed and lists them (not tax advice) |

## Quick Start

### Prerequisites

- Python 3.14 (the version of the Docker image and CI)
- pip

### Installation

```bash
git clone git@github.com:mborchuk/ContaAutonomo.git
cd ContaAutonomo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
# Development
FLASK_DEBUG=1 python app.py

# Production
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") python app.py
```

Open http://127.0.0.1:5000 — first launch will prompt you to create a password.

### Docker

```bash
# Quick start
docker compose up -d

# With a proper secret key
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") docker compose up -d
```

Data is persisted in Docker volumes (`app_data`, `app_backups`, etc.).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | *(random)* | Flask session secret. **Required in production.** |
| `DATABASE_URL` | `sqlite:///invoices.db` | SQLAlchemy database URI |
| `FLASK_DEBUG` | `0` | Set to `1` for debug mode |
| `FORCE_HTTPS` | `0` | Set to `1` to enable HSTS and secure cookies |

**Install on a phone.** Served over HTTPS (for example behind a reverse proxy),
the app can be installed: Chrome on Android → menu → *Install app*; Safari on
iOS → Share → *Add to Home Screen*. The installed app opens a receipt capture
page from its shortcut (`/expenses/capture`), and on Android it appears in the
share menu so a photo can be sent straight to a new expense. The service worker
caches nothing.

## Configuration

### Tax Rates

Tax rates are configurable in Settings → General Settings → Tax Rates:

- **VAT / IVA Rate** — applied to invoices (e.g. 21% Spain, 23% Poland, 20% UK)
- **Quarterly Tax Advance Rate** — applied to income for quarterly tax calculations (e.g. 20% Modelo 130 in Spain)

Modules can override these calculations. For example, enabling the Tax Poland module automatically sets VAT to 23% and provides Polish PIT brackets.

### Multi-Currency

The app supports any currency. In Settings → General:

1. Enter tracked currencies as comma-separated codes (e.g. `USD,EUR,GBP,PLN`)
2. Select your base currency from the dropdown (populated dynamically from tracked currencies)
3. Currency symbols are resolved automatically for 50+ currencies; unknown codes display as-is

### Dashboard

Toggle dashboard panels in Settings → General:
- Currency & Holidays panel (exchange rates, converter, upcoming holidays)
- Tax Obligations panel (income tax, VAT, social security)

## Project Structure

```
ContaAutonomo/
├── app.py                      # Core application (models, routes, settings)
├── module_manager.py           # Module system (BaseModule, CoreServices, TaskScheduler)
├── auth.py / auth_routes.py    # Authentication (pluggable providers)
├── currency_converter.py       # ECB exchange rates + shared CURRENCY_SYMBOLS
├── modules/                    # Dynamic modules
│   ├── ai_agent/               # Bridge between modules and AI providers
│   ├── ai_parser/              # AI-powered invoice parsing
│   ├── backup/                 # Backup & Restore
│   ├── documents/              # Document management
│   ├── expenses/               # Expense tracking
│   ├── external_storage/       # Pluggable storage: Local, S3, GCS, Google Drive
│   ├── invoice_attachments/    # Upload signed/scanned invoice PDFs
│   ├── invoice_comments/       # Internal invoice comments
│   ├── invoice_designer/       # Visual invoice template editor
│   ├── pdf_signature/          # Visual + digital PDF signing
│   ├── pdf_verify/             # PDF signature detection & display
│   ├── reports/                # Financial reports
│   ├── tax_management/         # Spanish tax forms & SS payments
│   └── tax_poland/             # Polish tax rules (JDG/B2B IT)
├── invoice_templates/          # Invoice PDF templates (pluggable)
├── templates/                  # Core Jinja2 templates
├── instance/                   # SQLite DB + auth config (gitignored)
├── logs/                       # Activity log files (gitignored)
└── backups/                    # Encrypted backups (gitignored)
```

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Core (app.py)                       │
│  Models: Invoice, Customer, Bank, Settings, ...       │
│  Routes: Dashboard, Invoices, Settings, Auth          │
├──────────────────────────────────────────────────────┤
│           ModuleManager (module_manager.py)            │
│  CoreServices: DB, Storage, Logger, Scheduler,        │
│  InvoiceService, CurrencyService                      │
├──────────┬──────────┬──────────┬─────────────────────┤
│ expenses │ backup   │ reports  │ tax_poland | ...     │
└──────────┴──────────┴──────────┴─────────────────────┘
```

Modules are discovered at startup from the `modules/` directory. Each module:
- Defines models, routes, and templates independently
- Accesses core services through `CoreServices` interface
- Can contribute to dashboard, reports, settings, and navigation
- Can override tax calculations for different countries
- Can register periodic tasks via the built-in scheduler

See [MODULES_DOCUMENTATION.md](MODULES_DOCUMENTATION.md) for the full API reference and [modules/README.md](modules/README.md) for the module development guide.

## Writing a Country Tax Module

A country tax module replaces the dashboard's tax estimates (income tax, VAT,
the Tax Obligations panel) with another country's rules. It files nothing and
does not change the Spanish forms: for another country, also turn off the
Spanish modules in Settings → Modules (Spanish Tax Forms, IRPF Estimator, RETA
Advisor, Tax Provision, Fiscal Calendar, Verifactu, E-invoice). A minimal
example:

```python
# modules/tax_mycountry/index.py
from invoice_vat import customer_tax_type
from module_manager import BaseModule

VAT_RATE = 20.0  # standard VAT of the country, %


class TaxMyCountryModule(BaseModule):
    """Dashboard tax estimates for MyCountry."""

    @property
    def module_id(self):
        return 'tax_mycountry'

    @property
    def name(self):
        return 'Tax MyCountry'

    @property
    def description(self):
        return 'MyCountry income tax and VAT on the dashboard.'

    def on_enable(self):
        """Use the country's VAT rate for new invoices."""
        settings = self.core.get_settings()
        if settings:
            settings.default_vat_rate = VAT_RATE
            self.core.db.session.commit()

    def calculate_income_tax(self, context):
        """Replace the built-in income tax estimate: a flat 20 %."""
        taxable = context['taxable_income']
        tax = round(max(0.0, taxable) * 0.20, 2)
        return {
            'income_tax': tax,
            'irpf_breakdown': [{'bracket': 'Flat 20%', 'rate': 20, 'amount': taxable,
                                'tax': tax, 'active': True}],
            'label': 'Income Tax (20%)',
        }

    def calculate_vat(self, context):
        """Replace the VAT estimate: VAT on invoices to customers who pay it.
        customer_tax_type() reads the customer saved when the invoice was
        issued, so later edits to the customer do not change past invoices."""
        rate = VAT_RATE / 100
        vat = sum(inv.amount_base * rate for inv in context['invoices']
                  if customer_tax_type(inv) == 'standard')
        return {'vat_collected': round(vat, 2), 'vat_rate': rate, 'label': 'VAT'}

    def get_tax_obligations(self, context):
        """Add a social contribution to the Tax Obligations panel."""
        yearly = 12 * 250.0  # for example 250 a month
        return {
            'summary_columns': [{'label': 'Social contribution', 'value': yearly}],
            'breakdown_rows': [{'label': 'Social contribution (250 × 12)', 'amount': yearly}],
            'notes': ['Estimate, not tax advice.'],
            'deductions': yearly,  # subtracted from income before income tax
            'tax_total': yearly,
        }

    def get_field_labels(self):
        """Settings field names in the country's terms."""
        return {'vat_number': 'VAT number', 'default_irpf_rate': 'Income tax advance (%)'}
```

The hooks a tax module uses (full list: [MODULES_DOCUMENTATION.md](MODULES_DOCUMENTATION.md)):

| Hook | What the core does with it | Context |
|---|---|---|
| `calculate_income_tax(context)` | Dashboard income tax; the first module that returns a result replaces the built-in Spanish brackets | `taxable_income` (income minus the modules' `deductions`), `settings`, `base_currency`, `currency_symbol` |
| `calculate_vat(context)` | Dashboard VAT; the first module that returns a result is used | `invoices`: every invoice dated this year except cancelled ones, each with `amount_base` in the base currency; `settings`, `base_currency` |
| `get_tax_obligations(context)` | Adds summary columns, breakdown rows and notes to the Tax Obligations panel; `deductions` are subtracted from income before income tax, `tax_total` is added to the taxes | `current_year`, `base_currency`, `exchange_rates`, `settings`, `vat_collected`, `currency_symbol` |
| `get_field_labels()` | Renames Settings fields | — |
| `on_enable()` | One-time setup when the module is switched on | — |
| `nav_items`, `register_routes(app)` | Pages of the module; put the menu item in the Taxes group: `{'label', 'endpoint', 'icon', 'group': 'Taxes', 'order'}` | — |
| `get_functions()` | Figures other modules can use by name, as the Spanish modules offer `tax.*` | — |

Rules: use the core through `self.core` (`get_settings()`, `db`, `functions`,
`storage`) rather than importing `app`; never import another module (a test
fails on that); keep the module's styles in its own templates. `modules/tax_poland/` is a complete
example with progressive brackets, health insurance and ZUS contributions.

## Production Deployment

1. Set a strong `SECRET_KEY` environment variable
2. Use Docker (`docker compose up -d`) or gunicorn behind a reverse proxy:
   ```bash
   gunicorn app:app        # from the repository root; reads gunicorn.conf.py
   ```
   `gunicorn.conf.py` initialises each worker (migrations, modules, scheduler)
   and defaults to one worker with 4 threads (`GUNICORN_WORKERS`,
   `GUNICORN_THREADS`, `GUNICORN_BIND`). Only one process runs scheduled jobs
   (lock file `instance/scheduler.lock`, override with `SCHEDULER_LOCK_PATH`).
3. Use a reverse proxy (nginx/Caddy) with HTTPS
4. Keep the backup key safe: the Backup module (enabled by default) encrypts
   automatic daily backups with `BACKUP_KEY` (generated by `make setup`) or,
   if unset, the key file `instance/backup.key`. Store a copy in a password
   manager — encrypted backups cannot be restored without it
5. Keep the credentials key safe: SMTP, storage, AI and currency provider
   credentials, backup and certificate passwords and the API token are stored
   encrypted with `CREDENTIALS_KEY` (generated by `make setup`) or, if unset,
   the key file `instance/credentials.key`. A database copy or backup alone
   does not reveal them. If the key is lost, re-enter the credentials in
   Settings
6. Consider External Storage module for S3/GCS backups
7. Set `FLASK_DEBUG=0` (default)

## Technology Stack

- **Backend**: Flask + SQLAlchemy (SQLite)
- **PDF**: ReportLab (generation), pypdf (manipulation), pyHanko (digital signatures)
- **Security**: Flask-WTF (CSRF), Flask-Limiter (rate limiting), cryptography (AES-256 backup encryption)
- **Cloud Storage**: boto3 (S3), google-cloud-storage (GCS), google-api-python-client (Drive)
- **Exchange Rates**: ECB API with Frankfurter and exchangerate-api fallbacks
- **AI Parsing**: OpenAI, Anthropic, Google Generative AI (optional)
- **Signature Verification**: asn1crypto (PKCS#7/CMS parsing)

## License

MIT
