# Release Notes

## v1.0.1 — Initial Public Release

Self-hosted invoicing and tax management app for freelancers and small businesses.

### Core

- Invoice CRUD with multi-line items, PDF generation, and hash integrity tracking
- Multi-currency support — ECB exchange rates with fallback to exchangerate-api
- Configurable invoice PDF templates (Default Classic, Modern Blue) — dynamically discovered
- Customer management with tax types (non-EU, EU B2B, standard IVA)
- Contractor and bank account management
- Dashboard: quarterly income tables, tax brackets, exchange rates, upcoming holidays and tax deadlines
- Configurable tax rates: VAT and quarterly tax advance rates editable in Settings → General → Tax Rates
- Dynamic Base Currency dropdown built from Tracked Currencies (50+ currency symbols supported)
- Authentication with setup wizard, password change, encrypted config
- Activity logging — file-based (JSON-lines) or database-backed, configurable retention
- Task scheduler — lightweight daemon for periodic jobs (daily backups, cleanup)
- Settings: independent tab-based architecture — each tab saves without affecting others

### Module System

- Dynamic discovery from `modules/` directory, enable/disable via Settings → Modules
- `CoreServices` API for modules: DB, Storage, Logger, Scheduler, InvoiceService, CurrencyService
- `BaseModule` hooks for models, routes, settings, dashboard, reports, invoices, and tax calculations
- Tax hooks: `calculate_income_tax(context)`, `calculate_vat(context)`, `get_tax_obligations(context)` — enables country-specific tax modules
- `InvoiceService` — safe API for modules to read/write invoices (PAID = read-only, PDF attach with hash)
- `CurrencyService` — exchange rate API with pluggable providers
- All file operations routed through `core.storage` — works with local, S3, GCS, and Google Drive

### Modules

- **Expenses**: Track business expenses with file uploads, categories, contractor linking
- **Tax Management**: Spanish tax forms (Modelos 130, 303, 349, 390), Social Security tracking
- **Tax Poland (IT)**: Polish tax rules for JDG/B2B — flat 19% or progressive 12%/32%, VAT 23%, ZUS
- **Documents**: General document storage and management
- **Reports**: PDF financial reports with selectable sections from any enabled module
- **Backup & Restore**: Full encrypted backups (AES-256-CBC), manual and scheduled, S3 support
- **External Storage**: Local, AWS S3, Google Cloud Storage, Google Drive backends
- **Invoice Attachments**: Upload ready-made invoice PDFs with SHA-256 hash tracking
- **PDF Signature**: Visual (image overlay) and digital (X.509/PFX) signing of invoice PDFs
- **AI Parser**: Parse invoice data from PDFs/images using AI providers (OpenAI, Anthropic, Google)

### Infrastructure

- Docker + docker-compose with gunicorn, persistent volumes
- Auto-migration for schema changes (new columns added on startup)
- SQLite by default, supports PostgreSQL/MySQL via `DATABASE_URL`

### Getting Started

```bash
git clone git@github.com:mborchuk/ContaAutonomo.git
cd ContaAutonomo
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 — first launch prompts for password setup.
