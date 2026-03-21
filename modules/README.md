# Module Development Guide

Complete guide for creating modules for ContaAutónomo.

## Table of Contents

1. [Architecture](#architecture)
2. [Quick Start](#quick-start)
3. [Module Structure](#module-structure)
4. [BaseModule API Reference](#basemodule-api-reference)
5. [CoreServices API Reference](#coreservices-api-reference)
6. [InvoiceService API Reference](#invoiceservice-api-reference)
7. [Invoice Actions Integration](#invoice-actions-integration)
8. [Invoice Form Integration](#invoice-form-integration)
9. [Database Models](#database-models)
10. [Routes & Templates](#routes--templates)
11. [Navigation Menu](#navigation-menu)
12. [Settings Integration](#settings-integration)
13. [Task Scheduler Integration](#task-scheduler-integration)
14. [Activity Logging](#activity-logging)
15. [Dashboard Integration](#dashboard-integration)
16. [Report Integration](#report-integration)
17. [Module Lifecycle](#module-lifecycle)
18. [Examples](#examples)
19. [Best Practices](#best-practices)
20. [Troubleshooting](#troubleshooting)

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Core (app.py)                       │
│  Models: Invoice, Customer, Bank, Settings, etc.      │
│  Routes: Dashboard, Invoices, Settings, Auth, Logs    │
│  Services: DB, Auth, Currency                         │
├──────────────────────────────────────────────────────┤
│            ModuleManager (module_manager.py)           │
│  CoreServices: DB, Storage, Logger, Scheduler         │
│  discover_modules() → load_enabled_modules()          │
├────────────┬────────────┬────────────┬───────────────┤
│  expenses  │  backup    │ your_mod   │    ...        │
│  module    │  module    │ module     │               │
└────────────┴────────────┴────────────┴───────────────┘
```

### How it works

1. On app startup, `ModuleManager` scans `modules/` directory
2. Each subfolder with `index.py` containing a `BaseModule` subclass is discovered
3. Module states (enabled/disabled) are stored in `module_enabled` DB table
4. Enabled modules get loaded: models registered, routes added, nav items injected
5. Users toggle modules in Settings → Modules tab

---

## Quick Start

Create a minimal module in 3 steps:

### Step 1: Create directory

```
modules/
└── my_module/
    ├── __init__.py          # Empty or "# My Module"
    ├── index.py             # Module definition (required)
    └── templates/           # Jinja2 templates (optional)
        └── my_page.html
```

### Step 2: Define module class in `index.py`

```python
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
    def de
ml')

        app.register_blueprint(bp)
```

### Step 3: Enable

Restart the app → Settings → Modules → Enable → Restart again.

---

## Module Structure

```
modules/
└── my_module/
    ├── __init__.py              # Required (can be empty)
    ├── index.py                 # Required: contains BaseModule subclass
    ├── templates/               # Optional: Jinja2 templates
    │   ├── my_list.html
    │   └── my_form.html
    └── static/                  # Optional: CSS, JS, images
        └── style.css
```

### Rules

- Directory name = module identifier used internally
- `index.py` must contain exactly ONE class that inherits from `BaseModule`
- Templates must extend `base.html` for consistent layout
- `__init__.py` is required (Python package requirement)

---

## BaseModule API Reference

Every module must inherit from `BaseModule` and implement required properties.

### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `module_id` | `str` | Unique identifier. Must match directory name. Example: `'expenses'` |
| `name` | `str` | Human-readable name shown in UI. Example: `'Expense Tracker'` |

### Optional Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `description` | `str` | `''` | Shown in Settings → Modules |
| `version` | `str` | `'1.0.0'` | Semantic version |
| `nav_items` | `list[dict]` | `[]` | Navigation menu entries |
| `settings_tab` | `str` | `'general'` | Which settings tab to place module settings in (`'general'` or `'security'`) |
| `settings_panels` | `list[dict]` | `[]` | Settings tab panels |

### Optional Methods

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `register_models(db)` | SQLAlchemy db | `dict` | Define DB models, return `{'Name': Class}` |
| `register_routes(app)` | Flask app | `None` | Register Blueprint with routes |
| `register_template_filters(app)` | Flask app | `None` | Add Jinja2 filters |
| `on_enable()` | — | `None` | Called when module is enabled |
| `on_disable()` | — | `None` | Called when module is disabled |
| `get_dashboard_panels()` | — | `list[dict]` | Dashboard widget data |
| `get_report_sections()` | — | `list[dict]` | Report section generators |
| `get_invoice_actions(invoice)` | `Invoice` | `list[str]` | HTML snippets for invoice view actions bar |
| `get_create_form_html()` | — | `str` or `None` | HTML to inject into invoice create form |
| `get_edit_form_html(invoice)` | `Invoice` | `str` or `None` | HTML to inject into invoice edit form |
| `on_invoice_created(invoice, request)` | `Invoice`, `Request` | `None` | Called after new invoice is committed |
| `on_invoice_updated(invoice, request)` | `Invoice`, `Request` | `None` | Called after existing invoice is committed |
| `get_invoice_templates()` | — | `list[dict]` | Invoice PDF templates provided by this module |
| `get_tax_obligations(context)` | `dict` | `dict` or `None` | Tax obligation data for dashboard |
| `get_settings_html(settings)` | `Settings` | `str` or `None` | HTML to inject into settings tab |
| `save_settings(settings, form)` | `Settings`, form | `None` | Handle saving module settings |

### Built-in Instance Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.core` | `CoreServices` | Core services interface (DB, storage, logger, scheduler, etc.) |
| `self.logger` | `logging.Logger` | Python logger named `module.<module_id>` (e.g. `module.invoice_attachments`). Use for console/file logging. |

---

## CoreServices API Reference

Every module receives a `CoreServices` instance as `self.core`. This is the standardized
interface for interacting with the application core.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `core.app` | `Flask` | Flask application instance |
| `core.db` | `SQLAlchemy` | Database instance |
| `core.app_path` | `str` | Application root directory path |
| `core.storage` | `FileStorageBackend` | Active file storage backend |
| `core.activity_logger` | `ActivityLogger` | Active activity logger |
| `core.scheduler` | `TaskScheduler` | Task scheduler for periodic jobs |
| `core.module_manager` | `ModuleManager` | Module manager instance (access other modules' data via contracts) |
| `core.invoice_service` | `InvoiceService` | Safe API for reading/writing invoices (PAID = read-only) |

### Methods

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `core.get_settings()` | — | `Settings` or `None` | Get app settings object |
| `core.get_upload_path(subfolder)` | `str` | `str` | Get/create upload directory. Returns absolute path. |
| `core.flash(message, category)` | `str`, `str` | `None` | Flash a message. Categories: `'success'`, `'danger'`, `'info'` |
| `core.login_required(f)` | function | function | Decorator to protect routes |
| `core.save_file(file_data, subfolder, filename)` | — | `str` | Save file via storage backend, returns storage key |
| `core.delete_file(storage_key)` | `str` | `None` | Delete file via storage backend |
| `core.send_file(storage_key, download_name)` | `str`, `str` | Response | Send file as download |
| `core.file_exists(storage_key)` | `str` | `bool` | Check if file exists |
| `core.log_activity(action, category, details, user)` | `str`, ... | `None` | Log an activity entry |
| `core.set_storage_backend(backend)` | backend | `None` | Replace file storage backend |
| `core.set_activity_logger(logger)` | logger | `None` | Replace activity logger |

### Usage Examples

```python
# Get settings
settings = self.core.get_settings()
base_currency = settings.base_currency if settings else 'EUR'

# Create upload directory
upload_dir = self.core.get_upload_path('my_module_files')
# Returns: /path/to/app/my_module_files/ (created if missing)

# Protect a route
@bp.route('/secret')
@self.core.login_required
def secret_page():
    return 'Protected content'
```

---

## InvoiceService API Reference

Modules can safely interact with invoices via `self.core.invoice_service`.
This service enforces business rules (PAID invoices are read-only) and logs all mutations.

### Read Operations

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get(invoice_id)` | `int` | `Invoice` or `None` | Get invoice by ID |
| `get_all(**filters)` | keyword args | `list[Invoice]` | Query invoices with filters (e.g. `status='paid'`) |
| `get_by_number(invoice_number)` | `str` | `Invoice` or `None` | Get invoice by number |
| `is_locked(invoice)` | `Invoice` | `bool` | True if invoice status is `'paid'` |
| `has_pdf(invoice_or_id)` | `Invoice` or `int` | `bool` | True if PDF file exists on disk |
| `get_pdf_path(invoice_or_id)` | `Invoice` or `int` | `str` or `None` | Filesystem path to PDF, or None |

### Write Operations

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `update(invoice_id, **fields)` | `int`, kwargs | `Invoice` | Update fields. Raises `ValueError` if PAID. |
| `attach_pdf(invoice_or_id, file_data, original_filename)` | `Invoice` or `int`, file, `str` | `str` (path) | Save PDF to `invoices_pdf/`, compute SHA-256 hash. Raises `ValueError` if PAID + sealed PDF exists. |

### Protection Rules

- **PAID invoices cannot be modified** — `update()` raises `ValueError`
- **PAID invoices with existing PDF + hash cannot have PDF replaced** — `attach_pdf()` raises `ValueError`
- All write operations are logged via `core.activity_logger` with detailed info (file size, hash, replaced/new)
- Fields `id` and `pdf_hash` cannot be set via `update()`

### Usage Example

```python
svc = self.core.invoice_service

# Read
invoice = svc.get(42)
all_paid = svc.get_all(status='paid')
has_file = svc.has_pdf(invoice)       # pass object — avoids extra DB query
has_file = svc.has_pdf(42)            # or pass ID

# Write (raises ValueError if PAID)
svc.update(42, description='Updated description')

# Attach PDF — pass object when you already have it
svc.attach_pdf(invoice, uploaded_file, 'invoice.pdf')
svc.attach_pdf(42, uploaded_file, 'invoice.pdf')  # or by ID
```

---

## Invoice Actions Integration

Modules can inject action buttons into the invoice view page by implementing
`get_invoice_actions()`. The core collects HTML snippets from all enabled modules
and renders them in the actions bar on `view.html`.

### How it works

1. Core renders `view.html` and calls `module_manager.get_invoice_actions(invoice)`
2. Each enabled module's `get_invoice_actions(invoice)` returns a list of HTML strings
3. HTML snippets are rendered with `|safe` filter in the actions bar

### Implementing invoice actions

```python
def get_invoice_actions(self, invoice):
    """Return action buttons for the invoice view page.

    Args:
        invoice: Invoice model instance (has .id, .status, .invoice_number, etc.)

    Returns:
        list[str]: rendered HTML snippets (forms, buttons, links)
    """
    is_locked = self.core.invoice_service.is_locked(invoice)
    html = render_template(
        'my_action_button.html',
        invoice=invoice,
        is_locked=is_locked,
    )
    return [html]
```

### Template example

```html
<!-- modules/my_module/templates/my_action_button.html -->
{% if not is_locked %}
<form method="POST" action="{{ url_for('my_module.do_action', id=invoice.id) }}"
      style="display: inline-flex;">
    <button type="submit" class="btn btn-primary">🔧 My Action</button>
</form>
{% endif %}
```

### Guidelines

- Always check `is_locked` before showing write actions on PAID invoices
- Use inline styles or scoped CSS — the actions bar uses `display: flex; gap: 8px`
- Return an empty list `[]` if the module has no actions for this invoice
- Templates are found via the blueprint's `template_folder` registration

---

## Invoice Form Integration

Modules can inject custom fields into the invoice create and edit forms, and
process those fields after the invoice is saved.

### How it works

1. Core renders `create.html` / `edit.html` and calls `module_manager.get_create_form_html()` / `module_manager.get_edit_form_html(invoice)`
2. Each enabled module returns an HTML string (or `None`) to inject before the submit button
3. After the invoice is committed, core calls `module_manager.on_invoice_created(invoice, request)` / `module_manager.on_invoice_updated(invoice, request)`
4. Modules process their custom fields from `request.form` or `request.files`

### Create form injection

```python
def get_create_form_html(self):
    """Return HTML to inject into the create invoice form."""
    return '''
    <div class="form-group">
        <label for="my_field">My Custom Field</label>
        <input type="text" id="my_field" name="my_field">
    </div>
    '''

def on_invoice_created(self, invoice, request):
    """Process custom fields after invoice creation."""
    value = request.form.get('my_field')
    if value:
        self.logger.info('Custom field value: %s for invoice #%s', value, invoice.invoice_number)
```

### Edit form injection

```python
def get_edit_form_html(self, invoice):
    """Return HTML to inject into the edit invoice form.
    Has access to the invoice being edited for context-aware rendering.
    """
    current_value = self._get_stored_value(invoice.id)
    return f'''
    <div class="form-group">
        <label for="my_field">My Custom Field</label>
        <input type="text" id="my_field" name="my_field" value="{current_value or ''}">
    </div>
    '''

def on_invoice_updated(self, invoice, request):
    """Process custom fields after invoice update."""
    value = request.form.get('my_field')
    if value:
        self.logger.info('Updated custom field: %s for invoice #%s', value, invoice.invoice_number)
```

### File uploads

If your module injects `<input type="file">` fields, the core form already has
`enctype="multipart/form-data"` on both create and edit pages. Access files via
`request.files.get('field_name')`.

```python
def get_create_form_html(self):
    return '''
    <div class="form-group">
        <label for="my_file">📎 Upload File</label>
        <input type="file" id="my_file" name="my_file" accept=".pdf">
    </div>
    '''

def on_invoice_created(self, invoice, request):
    file = request.files.get('my_file')
    if file and file.filename:
        file.seek(0)  # Always seek(0) before reading
        content = file.read()
        # process content...
```

---

## Database Models

### Defining New Models

Models are defined inside `register_models()` and returned as a dict.
The ModuleManager will create tables automatically (`checkfirst=True`).

```python
def register_models(self, db):
    self._db = db

    class MyRecord(db.Model):
        __tablename__ = 'my_module_record'    # Use unique prefix!
        __table_args__ = {'extend_existing': True}
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(200), nullable=False)
        amount = db.Column(db.Float, default=0.0)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

    self.MyRecord = MyRecord
    return {'MyRecord': MyRecord}  # Tables will be auto-created
```

### Using Core Models

To query models defined in core (app.py), use `extend_existing=True`
and define only the columns you need:

```python
class Expense(db.Model):
    __tablename__ = 'expense'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    # ... only columns you need to query

self.Expense = Expense
return {}  # Return empty — table already exists in core
```

### Important Rules

- Always use `__table_args__ = {'extend_existing': True}`
- Use a unique `__tablename__` prefix for new tables (e.g., `mymod_`)
- Store `self._db = db` for later use in business logic
- All DB operations go through `self._db.session`

### CRUD Example

```python
# Create
record = self.MyRecord(title='Test', amount=100.0)
self._db.session.add(record)
self._db.session.commit()

# Read
all_records = self.MyRecord.query.all()
one_record = self.MyRecord.query.get_or_404(id)
filtered = self.MyRecord.query.filter_by(title='Test').first()

# Update
record.amount = 200.0
self._db.session.commit()

# Delete
self._db.session.delete(record)
self._db.session.commit()
```

---

## Routes & Templates

### Registering Routes

Always use Flask Blueprints with a unique `url_prefix`:

```python
def register_routes(self, app):
    bp = Blueprint(
        'my_module',              # Blueprint name (unique)
        __name__,
        template_folder='templates',  # Relative to index.py
        url_prefix='/my-module'       # URL prefix (unique)
    )

    login_required = self.core.login_required
    module = self  # Capture self for closures

    @bp.route('/')
    @login_required
    def index():
        items = module.MyRecord.query.all()
        return render_template('my_list.html', items=items)

    @bp.route('/create', methods=['GET', 'POST'])
    @login_required
    def create():
        if request.method == 'POST':
            # handle form
            return redirect(url_for('my_module.index'))
        return render_template('my_form.html')

    app.register_blueprint(bp)
```

### Template Rules

Templates must extend `base.html`:

```html
{% extends "base.html" %}

{% block title %}My Page{% endblock %}

{% block content %}
<h1>My Module Page</h1>

<!-- Use blueprint-prefixed url_for -->
<a href="{{ url_for('my_module.create') }}">Create New</a>

<!-- Link to core routes (no prefix) -->
<a href="{{ url_for('settings') }}">Settings</a>
<a href="{{ url_for('dashboard') }}">Dashboard</a>

{% endblock %}
```

### URL Generation

Always use blueprint prefix in `url_for()`:

```python
# In Python
url_for('my_module.index')        # → /my-module/
url_for('my_module.create')       # → /my-module/create
url_for('my_module.edit', id=5)   # → /my-module/edit/5

# In Jinja2 templates
{{ url_for('my_module.index') }}
```

For core routes, no prefix needed:
```python
url_for('dashboard')    # → /dashboard
url_for('settings')     # → /settings
```

---

## Navigation Menu

Add items to the main navigation bar:

```python
@property
def nav_items(self):
    return [
        {
            'label': 'My Page',                    # Menu text
            'endpoint': 'my_module.index',          # Blueprint endpoint
            'icon': '📊'                            # Optional emoji icon
        },
        # Multiple items supported
        {
            'label': 'Reports',
            'endpoint': 'my_module.reports',
            'icon': '📈'
        }
    ]
```

Nav items appear between core items (Invoices, Create Invoice) and (Documents, Reports, Settings).
Order follows module discovery order.

---

## Settings Integration

Modules inject settings HTML into a specific tab via `get_settings_html()` and `settings_tab`:

```python
@property
def settings_tab(self):
    return 'general'  # 'general' (default) or 'security'

def get_settings_html(self, settings):
    return '''
    <h3>My Module Settings</h3>
    <div class="form-group">
        <label>My Option</label>
        <input type="text" name="my_option" value="...">
    </div>
    '''

def save_settings(self, settings, form):
    if 'my_option' in form:
        # save to your model or settings
        pass
```

---

## Task Scheduler Integration

Register periodic tasks in `on_enable()`:

```python
def on_enable(self):
    self.core.scheduler.add_job(
        job_id='my_module.cleanup',
        func=self._cleanup,
        job_type='daily',       # or 'interval'
        time_str='04:00',       # for daily type
        interval=3600,          # for interval type (seconds)
        description='Daily cleanup',
    )

def on_disable(self):
    self.core.scheduler.remove_job('my_module.cleanup')

def _cleanup(self):
    # runs inside app_context automatically
    pass
```

View all registered tasks at System → Scheduled Tasks.

---

## Activity Logging

Modules have two logging mechanisms:

### Activity Log (user-visible, System → Logs)

```python
self.core.log_activity('record_created', 'my_module', f'Record #{id} created by user')
```

Categories: `'auth'`, `'invoice'`, `'expense'`, `'backup'`, `'settings'`, `'system'`, or any custom string.

### Python Logger (console/file, for debugging)

Every module automatically gets `self.logger` — a Python `logging.Logger` named `module.<module_id>`:

```python
self.logger.debug('Processing item %d', item_id)
self.logger.info('Invoice #%s PDF attached: %s', inv.invoice_number, path)
self.logger.warning('Non-PDF file rejected: %s', filename)
self.logger.error('Failed to save: %s', error)
```

Output goes to console (and log files if configured). Use `self.logger` for developer-facing
diagnostics and `self.core.log_activity()` for user-facing audit trail.

---

## Dashboard Integration

Provide data for dashboard panels:

```python
def get_dashboard_panels(self):
    # Query your data
    total = self.MyRecord.query.count()

    return [{
        'id': 'my_dashboard_widget',
        'data': {
            'total_records': total,
            'status': 'active'
        },
        'order': 20  # Lower = appears first
    }]
```

Dashboard panels are accessible in the core dashboard template via `module_manager.get_dashboard_panels()`.

---

## Invoice Template Integration

Modules can provide custom invoice PDF templates. The core auto-discovers templates
from `invoice_templates/` and collects additional templates from enabled modules.
All templates appear in Settings → General → Invoice PDF Template dropdown.

### Registering a template

```python
def get_invoice_templates(self):
    """Return invoice PDF templates provided by this module."""
    return [{
        'id': 'my_custom_invoice',           # unique ID (used in Settings.invoice_template)
        'name': 'My Custom Invoice Layout',  # shown in dropdown
        'path': os.path.join(os.path.dirname(__file__), 'templates', 'my_custom_invoice.py'),
    }]
```

### Template file contract

The template `.py` file must define a `generate_invoice_pdf` function:

```python
def generate_invoice_pdf(invoice, customer, settings):
    """
    Args:
        invoice: Invoice object (.invoice_number, .invoice_date, .amount_usd, .amount_eur,
                 .currency, .items, .notes, .bank, .customer, .status, .pdf_hash)
        customer: Customer object or None (.name, .vat_number, .address, .city, .country, .tax_type)
        settings: Settings object (.business_name, .owner_name, .vat_number, .nie_number, .address, etc.)

    Returns:
        io.BytesIO buffer containing the generated PDF
    """
```

Optionally accept a `Bank` parameter for querying the default bank:

```python
def generate_invoice_pdf(invoice, customer, settings, Bank):
```

The core detects the signature via `inspect.signature` and passes `Bank` only if the parameter exists.

---

## Report Integration

The Reports module collects data from **any** enabled module that implements
`get_report_sections()`. There is **no direct dependency** between Reports and
any specific data module (e.g. Expenses). The coupling is purely through a
data contract — the format of dicts returned by `query_fn`.

### How it works

1. Reports module calls `core.module_manager.get_report_sections()`
2. Every enabled module that overrides `get_report_sections()` contributes sections
3. Reports matches sections by `id` and calls `query_fn(start_date, end_date)`
4. The returned data is passed to the report template for PDF rendering

This means you can replace the built-in Expenses module with your own — as long
as it returns sections with the same `id` and data format, Reports will use it
automatically.

### Registering a report section

```python
def get_report_sections(self):
    return [{
        'id': 'expenses',           # Section identifier (see known IDs below)
        'title': 'Expenses',        # Human-readable title
        'query_fn': self._get_data  # callable(start_date, end_date) -> list[dict]
    }]
```

### Known section IDs and their data contracts

#### `expenses`

Used by report type `expenses` and `combined`. `query_fn` must return:

```python
[{
    'expense_date': '15/03/2026',       # str, DD/MM/YYYY
    'invoice_number': 'INV-001',        # str
    'contractor_name': 'Acme Corp',     # str
    'category': 'Software',            # str
    'description': 'Monthly license',   # str
    'amount_eur': 99.00                 # float, amount in EUR
}]
```

#### `ss_payments`

Social Security payments. `query_fn` must return:

```python
[{
    'payment_date': '01/03/2026',       # str, DD/MM/YYYY
    'description': 'Monthly SS quota',  # str
    'amount': 300.00                    # float, EUR
}]
```

### Adding a custom section

Any module can add its own section to the report. The report template
renders unknown sections as generic tables automatically.

```python
def get_report_sections(self):
    return [{
        'id': 'adjusted_income',
        'title': 'Adjusted Income (Module X)',
        'query_fn': self._calc_adjusted_income,
        # Optional: explicit column definitions
        'columns': [
            {'key': 'period', 'label': 'Period', 'width': 4},
            {'key': 'original', 'label': 'Original (EUR)', 'width': 4},
            {'key': 'adjusted', 'label': 'Adjusted (EUR)', 'width': 4},
            {'key': 'difference', 'label': 'Difference (EUR)', 'width': 4},
        ],
        # Optional: which field to sum for a TOTAL row
        'total_field': 'adjusted',
    }]

def _calc_adjusted_income(self, start_date, end_date):
    return [
        {'period': 'Q1', 'original': 5000.0, 'adjusted': 4500.0, 'difference': -500.0},
        {'period': 'Q2', 'original': 6000.0, 'adjusted': 5800.0, 'difference': -200.0},
    ]
```

If `columns` is omitted, the template auto-detects columns from dict keys.
If `total_field` is provided, a TOTAL row is appended with the sum of that field.

---

## Module Lifecycle

```
1. App starts
   └── ModuleManager.discover_modules()
       └── Scans modules/ directory
       └── Imports index.py, finds BaseModule subclass

2. ModuleManager.load_enabled_modules()
   └── For each enabled module:
       ├── Instantiate: module = ModuleClass(core_services)
       ├── register_models(db) → creates DB tables
       ├── register_routes(app) → registers Blueprint
       ├── register_template_filters(app)
       └── on_enable()

3. App running
   └── Module routes handle requests
   └── Module nav items shown in menu
   └── Module dashboard/report integrations active

4. Module disabled (via Settings)
   └── on_disable() called
   └── Module removed from active modules
   └── Nav items disappear
   └── Routes still registered (need restart to fully remove)
```

### Enable/Disable Flow

- Enabling: takes effect immediately (module loaded at runtime)
- Disabling: nav items removed immediately, full cleanup on restart
- DB tables are never dropped on disable (data preserved)

---

## Examples

### Example 1: Simple Notes Module

```python
# modules/notes/index.py
from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime

class NotesModule(BaseModule):

    @property
    def module_id(self):
        return 'notes'

    @property
    def name(self):
        return 'Notes'

    @property
    def description(self):
        return 'Simple note-taking module'

    @property
    def nav_items(self):
        return [{'label': 'Notes', 'endpoint': 'notes.notes_list'}]

    def register_models(self, db):
        self._db = db

        class Note(db.Model):
            __tablename__ = 'notes_note'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            title = db.Column(db.String(200), nullable=False)
            content = db.Column(db.Text)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.Note = Note
        return {'Note': Note}

    def register_routes(self, app):
        bp = Blueprint('notes', __name__,
                       template_folder='templates',
                       url_prefix='/notes')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def notes_list():
            notes = module.Note.query.order_by(
                module.Note.created_at.desc()
            ).all()
            return render_template('notes_list.html', notes=notes)

        @bp.route('/add', methods=['POST'])
        @login_required
        def notes_add():
            note = module.Note(
                title=request.form['title'],
                content=request.form.get('content', '')
            )
            module._db.session.add(note)
            module._db.session.commit()
            flash('Note added!', 'success')
            return redirect(url_for('notes.notes_list'))

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def notes_delete(id):
            note = module.Note.query.get_or_404(id)
            module._db.session.delete(note)
            module._db.session.commit()
            flash('Note deleted!', 'success')
            return redirect(url_for('notes.notes_list'))

        app.register_blueprint(bp)
```

### Example 2: Module with File Uploads

```python
def _upload_file(self):
    file = request.files.get('file')
    if file and file.filename:
        upload_dir = self.core.get_upload_path('my_module_files')
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = f"{timestamp}_{filename}"
        file.save(os.path.join(upload_dir, safe_name))
        return safe_name
    return None
```

---

## Best Practices

1. **Table naming**: Prefix table names with module id: `mymod_records`, `mymod_settings`
2. **Blueprint naming**: Use `module_id` as blueprint name
3. **URL prefix**: Use kebab-case: `/my-module/`
4. **Always use `extend_existing=True`** on all model definitions
5. **Capture `self` as `module`** in route closures (Python scoping)
6. **Use `self.core.login_required`** — don't import from app directly
7. **Handle errors gracefully**: wrap DB operations in try/except, rollback on failure
8. **Don't modify core models**: only read from them via `extend_existing`
9. **Templates extend `base.html`**: ensures consistent navigation and styling
10. **Use `url_for('blueprint.endpoint')`**: never hardcode URLs
11. **Never `from app import ...`** in modules — use `self.core` for all core access. Direct imports from `app` cause circular import issues where values may be `None`.
12. **No ORM relationships across modules**: models defined with `extend_existing=True` don't inherit relationships from core. Use explicit queries (e.g. `Contractor.query.get(expense.contractor_id)`) instead of `expense.contractor`.
13. **Use `self.core.db` for DB session** — never import `db` from `app`. Use `self.core.db.session.commit()` in service code. Importing `db` from `app` can cause "Flask app not registered with this SQLAlchemy instance" errors.
14. **Always `file.seek(0)` before reading** uploaded files — the stream position may be at the end if Flask or another handler already read it.
15. **Use `self.logger`** for console/debug logging and `self.core.log_activity()` for user-visible audit trail. Both are available automatically.
16. **Pass invoice objects, not IDs** to `InvoiceService` methods when you already have the object — avoids unnecessary DB queries and SQLAlchemy context issues.

---

## Troubleshooting

### Module not appearing in Settings → Modules

- Check `modules/<name>/index.py` exists
- Verify class inherits from `BaseModule`
- Check console for `[ModuleManager] Error discovering module` messages
- Ensure `__init__.py` exists in module directory

### "Could not build url for endpoint"

- Use blueprint prefix: `url_for('my_module.my_route')` not `url_for('my_route')`
- Check blueprint name matches in `Blueprint('my_module', ...)` and `url_for('my_module.xxx')`

### Template not found

- Ensure `template_folder='templates'` in Blueprint constructor
- Template file must be in `modules/<name>/templates/`
- If core has a template with same name, delete the core one

### Database table not created

- Return model dict from `register_models()`: `return {'MyModel': MyModel}`
- Check `__tablename__` is set
- Check console for errors during module loading

### Module enabled but routes not working

- Restart the application after enabling
- Check console for `[ModuleManager] Loaded module: ...` message
- Verify Blueprint `url_prefix` doesn't conflict with existing routes

### "Flask app is not registered with this SQLAlchemy instance"

- Do NOT import `db` from `app` in module code or services
- Use `self.core.db` (or `self._core.db` in services) for all DB session operations
- Pass invoice/model objects directly to service methods instead of IDs when possible
- This error typically occurs when `from app import db` creates a reference to a different SQLAlchemy context

### File upload returns empty content

- Always call `file.seek(0)` before `file.read()` — the stream position may already be at the end
- Check that the form has `enctype="multipart/form-data"` (automatic for create/edit when modules inject form HTML)
