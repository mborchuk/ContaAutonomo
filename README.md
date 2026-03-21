# ContaAutónomo — Invoice & Finance Manager

A self-hosted web application for freelancers and small businesses to manage invoices, expenses, tax obligations, and financial reports. Built with Flask and a modular plugin architecture that adapts to different countries' tax systems.

## Features

- Invoice management with PDF generation and customizable templates
- Multi-currency support with ECB exchange rate conversion and 50+ currency symbols
- Customer and contractor management
- Configurable tax rates (VAT, income tax advance) per country
- Modular architecture — enable only what you need
- Password-protected access with encrypted backups
- Activity logging (file or database) with configurable retention
- Built-in task scheduler for periodic jobs
- Dashboard with financial overview, tax calculations, and exchange rates

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
| **AI Parser** | Parse invoice data from PDFs/images using AI providers (OpenAI, Anthropic, Google) |

## Quick Start

### Prerequisites

- Python 3.10+
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
| `SECRET_KEY` | `dev-secret-key-...` | Flask session secret. **Set in production.** |
| `DATABASE_URL` | `sqlite:///invoices.db` | SQLAlchemy database URI |
| `FLASK_DEBUG` | `0` | Set to `1` for debug mode |

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
├── auth.py / auth_routes.py    # Authentication
├── currency_converter.py       # ECB exchange rates + shared CURRENCY_SYMBOLS
├── repositories.py             # Repository pattern for DB queries
├── modules/                    # Dynamic modules
│   ├── ai_parser/              # AI-powered invoice parsing
│   ├── backup/                 # Backup & Restore
│   ├── documents/              # Document management
│   ├── expenses/               # Expense tracking
│   ├── external_storage/       # Pluggable storage: Local, S3, GCS, Google Drive
│   ├── invoice_attachments/    # Upload signed/scanned invoice PDFs
│   ├── pdf_signature/          # Visual + digital PDF signing
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

The module system supports country-specific tax overrides. Here's a minimal example:

```python
# modules/tax_mycountry/index.py
from module_manager import BaseModule

class TaxMyCountryModule(BaseModule):
    @property
    def module_id(self): return 'tax_mycountry'

    @property
    def name(self): return 'Tax MyCountry'

    @property
    def settings_tab(self): return 'general'

    def on_enable(self):
        """Set VAT rate when module is enabled."""
        from app import Settings
        s = Settings.query.first()
        if s:
            s.default_vat_rate = 20.0  # your country's VAT
            self._db.session.commit()

    def calculate_income_tax(self, context):
        """Override income tax calculation."""
        taxable = context.get('taxable_income', 0)
        tax = taxable * 0.20  # flat 20%
        return {
            'income_tax': tax,
            'irpf_breakdown': [{'bracket': 'Flat 20%', 'rate': 20, 'amount': taxable, 'tax': tax, 'active': True}],
            'label': 'Income Tax (20%)',
        }

    def calculate_vat(self, context):
        """Override VAT calculation."""
        invoices = context.get('invoices', [])
        rate = 0.20
        vat = sum(inv.amount_base * rate for inv in invoices
                  if inv.customer and inv.customer.tax_type == 'standard')
        return {'vat_collected': vat, 'vat_rate': rate, 'label': 'VAT'}
```

See `modules/tax_poland/` for a complete real-world example with progressive brackets, health insurance, and ZUS contributions.

## Production Deployment

1. Set a strong `SECRET_KEY` environment variable
2. Use Docker (`docker compose up -d`) or gunicorn behind a reverse proxy:
   ```bash
   gunicorn -w 2 -b 0.0.0.0:5000 app:app
   ```
3. Use a reverse proxy (nginx/Caddy) with HTTPS
4. Enable the Backup module with daily scheduling
5. Consider External Storage module for S3/GCS backups
6. Set `FLASK_DEBUG=0` (default)

## Technology Stack

- **Backend**: Flask + SQLAlchemy (SQLite)
- **PDF**: ReportLab (generation), pyHanko (digital signatures)
- **Security**: cryptography (AES-256 backup encryption)
- **Cloud Storage**: boto3 (S3), google-cloud-storage (GCS), google-api-python-client (Drive)
- **Exchange Rates**: ECB API with exchangerate-api fallback
- **AI Parsing**: OpenAI, Anthropic, Google Generative AI (optional)

## License

MIT
