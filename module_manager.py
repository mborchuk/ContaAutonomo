#!/usr/bin/env python3
"""
Module Manager - Core module loading and management system.

Discovers, loads, and manages application modules.
Each module lives in modules/<name>/ with an index.py that defines a class
inheriting from BaseModule.
"""

import os
import logging

logger = logging.getLogger(__name__)


def _sanitize_log(value):
    """Strip control characters to prevent log injection."""
    if not isinstance(value, str):
        value = str(value)
    return value.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
import importlib
import importlib.util
from abc import ABC, abstractmethod


class BaseModule(ABC):
    """
    Base class for all modules. Every module must inherit from this
    and implement the required methods.
    """

    def __init__(self, core):
        """
        Initialize module with core services.

        Args:
            core: CoreServices instance providing db, app, settings access, etc.
        """
        self.core = core
        self.logger = logging.getLogger(f'module.{self.module_id}')

    @property
    @abstractmethod
    def module_id(self):
        """Unique module identifier (e.g., 'tax_management')"""
        pass

    @property
    @abstractmethod
    def name(self):
        """Human-readable module name (e.g., 'Tax Management')"""
        pass

    @property
    def description(self):
        """Module description"""
        return ''

    @property
    def version(self):
        """Module version"""
        return '0.1.0'

    @property
    def nav_items(self):
        """
        Navigation menu items. Return list of dicts:
        [{'label': 'Tax Forms', 'endpoint': 'tax_management.tax_forms_index', 'icon': '📋'}]
        """
        return []

    @property
    def settings_panels(self):
        """
        Settings panels to add. Return list of dicts:
        [{'tab_id': 'tax_settings', 'tab_label': 'Tax Settings', 'template': 'tax_management/settings.html'}]
        If empty, no settings tab is added.
        """
        return []

    def register_models(self, db):
        """
        Register database models. Called once during module loading.
        Models should be defined as classes inside the module and returned here.

        Returns:
            dict: {'ModelName': ModelClass, ...}
        """
        return {}

    def register_routes(self, app):
        """
        Register Flask routes. Use self.blueprint to define routes,
        then register the blueprint here.
        """
        pass

    def register_template_filters(self, app):
        """Register any Jinja2 template filters"""
        pass

    def on_enable(self):
        """Called when module is enabled. Use for DB table creation, etc."""
        pass

    def on_disable(self):
        """Called when module is disabled."""
        pass

    def get_dashboard_panels(self):
        """
        Return dashboard panel data if module contributes to dashboard.
        Returns:
            list of dicts: [{'template': 'tax_management/dashboard_panel.html', 'data': {...}, 'order': 10}]
        """
        return []

    def get_report_sections(self):
        """
        Return report section generators if module contributes to reports.
        Returns:
            list of dicts: [{'id': 'ss_payments', 'title': 'Social Security', 'generator': callable}]
        """
        return []

    def get_settings_html(self, settings):
        """
        Return HTML to inject into a settings tab.
        Called when rendering the settings page.

        Args:
            settings: The Settings model instance

        Returns:
            str or None: HTML string to inject, or None
        """
        return None

    @property
    def settings_tab(self):
        """
        Which settings tab this module's settings appear in.
        Return 'general', 'security', or any custom tab id.
        Default: 'general'
        """
        return 'general'

    def save_settings(self, settings, form):
        """
        Handle saving module-specific settings from the General Settings form.
        Called during settings POST.

        Args:
            settings: The Settings model instance
            form: The request.form data
        """
        pass

    def get_tax_obligations(self, context):
        """
        Contribute to the Tax Obligations panel on the dashboard.
        Called with context dict containing: current_year, income, base_currency,
        exchange_rates, settings.

        Returns:
            dict or None with keys:
                - summary_columns: list of {'label': str, 'value': float} for the top summary row
                - breakdown_rows: list of {'label': str, 'amount': float} for the tax breakdown table
                - notes: list of str for the notes section
                - deductions: float — amount to subtract from income for taxable_income
                - tax_total: float — total tax amount this module contributes
        """
        return None

    def calculate_income_tax(self, context):
        """
        Override the default income tax (IRPF) calculation.

        If a module returns a non-None result, it completely replaces the
        built-in Spanish IRPF progressive brackets. Only the first module
        that returns a result is used.

        Args:
            context: dict with keys:
                - taxable_income: float
                - settings: Settings model instance
                - base_currency: str (e.g. 'EUR')
                - currency_symbol: str (e.g. '€')

        Returns:
            dict or None. If dict, must contain:
                - income_tax: float — total income tax amount
                - irpf_breakdown: list of dicts with keys:
                    bracket (str), rate (number), amount (float),
                    tax (float), active (bool)
                - label: str — name for the tax (e.g. 'Income Tax (PIT)')
            Return None to use the default calculation.
        """
        return None

    def calculate_vat(self, context):
        """
        Override the default VAT collection calculation.

        If a module returns a non-None result, it replaces the built-in
        VAT calculation. Only the first module that returns a result is used.

        Args:
            context: dict with keys:
                - invoices: list of paid invoices for current year
                - settings: Settings model instance
                - base_currency: str

        Returns:
            dict or None. If dict, must contain:
                - vat_collected: float — total VAT collected
                - vat_rate: float — rate used (e.g. 0.21)
                - label: str — name for the tax (e.g. 'VAT', 'IVA', 'PTU')
            Return None to use the default calculation.
        """
        return None

    def get_invoice_actions(self, invoice):
        """
        Provide extra action buttons/forms for the invoice view page.
        Called by core when rendering an invoice.

        Args:
            invoice: Invoice model instance

        Returns:
            list of str: rendered HTML snippets to inject into the actions bar
        """
        return []

    def get_create_form_html(self):
        """
        Return HTML to inject into the invoice create form.
        Called when rendering the create invoice page.

        Returns:
            str or None: HTML string to inject before the submit button
        """
        return None

    def on_invoice_created(self, invoice, request):
        """
        Called after a new invoice is created and committed.
        Modules can process their custom form fields here.

        Args:
            invoice: the newly created Invoice instance (already committed)
            request: Flask request object (access form data and files)
        """
        pass

    def get_edit_form_html(self, invoice):
        """
        Return HTML to inject into the invoice edit form.
        Called when rendering the edit invoice page.

        Args:
            invoice: the Invoice being edited

        Returns:
            str or None: HTML string to inject before the submit button
        """
        return None

    def on_invoice_updated(self, invoice, request):
        """
        Called after an existing invoice is updated and committed.
        Modules can process their custom form fields here.

        Args:
            invoice: the updated Invoice instance (already committed)
            request: Flask request object (access form data and files)
        """
        pass

    def get_invoice_templates(self):
        """
        Return invoice PDF templates provided by this module.

        Returns:
            list[dict]: each dict has:
                - 'id': unique template identifier (used in Settings.invoice_template)
                - 'name': human-readable name (shown in dropdown)
                - 'path': absolute path to the .py template file
        """
        return []

    def get_field_labels(self):
        """
        Override UI labels for core fields.
        Country-specific modules can rename fields to match local terminology.

        Returns:
            dict: field_name -> label string, e.g. {'nie_number': 'PESEL'}
        """
        return {}


class FileStorageBackend:
    """
    Abstract file storage backend.
    Modules can replace the default backend via CoreServices.set_storage_backend().
    """

    def save(self, file_data, relative_path):
        """
        Save file data to storage.

        Args:
            file_data: file-like object (e.g., from request.files) or bytes
            relative_path: path relative to app root (e.g., 'documents_files/20260315_doc.pdf')

        Returns:
            str: the storage key/path used to retrieve the file later
        """
        raise NotImplementedError

    def delete(self, storage_key):
        """
        Delete a file from storage.

        Args:
            storage_key: the key returned by save()
        """
        raise NotImplementedError

    def get(self, storage_key):
        """
        Get file content.

        Args:
            storage_key: the key returned by save()

        Returns:
            tuple: (file_bytes, filename) or None
        """
        raise NotImplementedError

    def send(self, storage_key, download_name=None):
        """
        Send file as Flask response (for downloads).

        Args:
            storage_key: the key returned by save()
            download_name: filename for the download

        Returns:
            Flask response
        """
        raise NotImplementedError

    def exists(self, storage_key):
        """Check if file exists in storage"""
        raise NotImplementedError


class LocalStorageBackend(FileStorageBackend):
    """Default local filesystem storage"""

    def __init__(self, app_root):
        self.app_root = app_root

    def _full_path(self, relative_path):
        # Handle both relative and absolute paths (backward compatibility)
        if os.path.isabs(relative_path):
            return relative_path
        return os.path.join(self.app_root, relative_path)

    def save(self, file_data, relative_path):
        full_path = self._full_path(relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        if hasattr(file_data, 'save'):
            file_data.save(full_path)
        else:
            with open(full_path, 'wb') as f:
                f.write(file_data if isinstance(file_data, bytes) else file_data.read())
        return relative_path

    def delete(self, storage_key):
        full_path = self._full_path(storage_key)
        if os.path.exists(full_path):
            os.remove(full_path)

    def get(self, storage_key):
        full_path = self._full_path(storage_key)
        if not os.path.exists(full_path):
            return None
        with open(full_path, 'rb') as f:
            return f.read(), os.path.basename(storage_key)

    def send(self, storage_key, download_name=None):
        from flask import send_from_directory
        full = self._full_path(storage_key)
        directory = os.path.dirname(full)
        filename = os.path.basename(full)
        return send_from_directory(directory, filename, as_attachment=True,
                                   download_name=download_name or filename)

    def exists(self, storage_key):
        return os.path.exists(self._full_path(storage_key))


class ActivityLogger:
    """
    Base activity logger. Stores user/system activity entries.
    Modules can subclass or replace via CoreServices.set_activity_logger().
    """

    def log(self, action, category='system', details=None, user=None):
        """
        Log an activity entry.

        Args:
            action: short description, e.g. 'login', 'invoice_created'
            category: 'auth', 'invoice', 'expense', 'backup', 'settings', 'system', etc.
            details: optional extra info (str or dict)
            user: optional user identifier
        """
        pass

    def get_entries(self, limit=100, category=None, offset=0):
        """
        Retrieve log entries.

        Returns:
            list of dicts: [{'id', 'timestamp', 'action', 'category', 'details', 'user'}, ...]
        """
        return []

    def clear(self, before=None):
        """Clear log entries, optionally only those before a given datetime."""
        pass


class FileActivityLogger(ActivityLogger):
    """Default file-based activity logger: one JSON-lines file per day."""

    def __init__(self, log_dir):
        import json as _json
        self._json = _json
        self._log_dir = os.path.join(log_dir, 'logs')
        os.makedirs(self._log_dir, exist_ok=True)

    @property
    def log_dir(self):
        return self._log_dir

    @log_dir.setter
    def log_dir(self, path):
        self._log_dir = path
        os.makedirs(self._log_dir, exist_ok=True)

    def _today_file(self):
        from datetime import date
        return os.path.join(self._log_dir,
                            f'{date.today().isoformat()}.log')

    def log(self, action, category='system', details=None, user=None):
        try:
            from datetime import datetime
            det = details if isinstance(details, str) else (
                self._json.dumps(details, ensure_ascii=False)
                if details else None)
            entry = {
                'ts': datetime.utcnow().isoformat(),
                'action': action,
                'cat': category,
                'details': det,
                'user': user,
            }
            with open(self._today_file(), 'a', encoding='utf-8') as fh:
                fh.write(self._json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error('FileLogger write error: %s', e)

    def get_entries(self, limit=100, category=None, offset=0,
                    date_from=None, date_to=None, search=None):
        """Read entries from log files, newest first."""
        files = sorted(
            [f for f in os.listdir(self._log_dir) if f.endswith('.log')],
            reverse=True)
        if date_from:
            files = [f for f in files if f[:-4] >= date_from]
        if date_to:
            files = [f for f in files if f[:-4] <= date_to]
        entries = []
        skipped = 0
        for fname in files:
            if len(entries) >= limit:
                break
            fpath = os.path.join(self._log_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as fh:
                    lines = fh.read().strip().split('\n')
            except Exception:
                continue
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    e = self._json.loads(line)
                except Exception:
                    continue
                if category and e.get('cat') != category:
                    continue
                if search and search.lower() not in line.lower():
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                entries.append({
                    'timestamp': e.get('ts'),
                    'action': e.get('action'),
                    'category': e.get('cat', 'system'),
                    'details': e.get('details'),
                    'user': e.get('user'),
                })
                if len(entries) >= limit:
                    break
        return entries

    def get_categories(self):
        """Scan log files for unique categories."""
        cats = set()
        for fname in os.listdir(self._log_dir):
            if not fname.endswith('.log'):
                continue
            fpath = os.path.join(self._log_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as fh:
                    for line in fh:
                        if line.strip():
                            try:
                                cats.add(
                                    self._json.loads(line).get('cat', 'system'))
                            except (ValueError, KeyError):
                                continue
            except OSError:
                continue
        return sorted(cats)

    def cleanup(self, retention_days):
        """Delete log files older than retention_days. 0 = keep all."""
        if retention_days <= 0:
            return
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
        for fname in os.listdir(self._log_dir):
            if fname.endswith('.log') and fname[:-4] < cutoff:
                try:
                    os.remove(os.path.join(self._log_dir, fname))
                except OSError:
                    continue

    def clear(self, before=None):
        if before:
            cutoff = before.strftime('%Y-%m-%d')
            for fname in os.listdir(self._log_dir):
                if fname.endswith('.log') and fname[:-4] < cutoff:
                    try:
                        os.remove(os.path.join(self._log_dir, fname))
                    except OSError:
                        continue
        else:
            for fname in os.listdir(self._log_dir):
                if fname.endswith('.log'):
                    try:
                        os.remove(os.path.join(self._log_dir, fname))
                    except OSError:
                        continue


class DbActivityLogger(ActivityLogger):
    """Database-backed activity logger: stores entries in an activity_log table."""

    def __init__(self, db, app):
        self._db = db
        self._app = app
        self._json = __import__('json')
        self._ensure_table()

    def _ensure_table(self):
        from sqlalchemy import text
        with self._app.app_context():
            with self._db.engine.connect() as conn:
                conn.execute(text('''
                    CREATE TABLE IF NOT EXISTS activity_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        action TEXT NOT NULL,
                        category TEXT DEFAULT 'system',
                        details TEXT,
                        user TEXT
                    )
                '''))
                conn.commit()

    def log(self, action, category='system', details=None, user=None):
        try:
            from datetime import datetime
            from sqlalchemy import text
            det = details if isinstance(details, str) else (
                self._json.dumps(details, ensure_ascii=False) if details else None)
            with self._db.engine.connect() as conn:
                conn.execute(text(
                    'INSERT INTO activity_log (timestamp, action, category, details, user) '
                    'VALUES (:ts, :action, :cat, :det, :usr)'),
                    {'ts': datetime.utcnow().isoformat(), 'action': action,
                     'cat': category, 'det': det, 'usr': user})
                conn.commit()
        except Exception as e:
            logger.error('DbLogger log error: %s', e)

    def get_entries(self, limit=100, category=None, offset=0,
                    date_from=None, date_to=None, search=None):
        from sqlalchemy import text
        clauses = []
        params = {}
        if category:
            clauses.append('category = :cat')
            params['cat'] = category
        if date_from:
            clauses.append('timestamp >= :df')
            params['df'] = date_from
        if date_to:
            clauses.append('timestamp <= :dt')
            params['dt'] = date_to + 'T23:59:59' if date_to and 'T' not in date_to else date_to
        if search:
            clauses.append("(action LIKE :s OR details LIKE :s)")
            params['s'] = f'%{search}%'
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        sql = f'SELECT timestamp, action, category, details, user FROM activity_log{where} ORDER BY id DESC LIMIT :lim OFFSET :off'
        params['lim'] = limit
        params['off'] = offset
        entries = []
        try:
            with self._db.engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
            for r in rows:
                entries.append({
                    'timestamp': r[0], 'action': r[1],
                    'category': r[2] or 'system',
                    'details': r[3], 'user': r[4],
                })
        except Exception as e:
            logger.error('DbLogger get_entries error: %s', e)
        return entries

    def get_categories(self):
        from sqlalchemy import text
        try:
            with self._db.engine.connect() as conn:
                rows = conn.execute(text(
                    'SELECT DISTINCT category FROM activity_log ORDER BY category')).fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error('DbLogger get_categories error: %s', e)
            return []

    def cleanup(self, retention_days):
        if retention_days <= 0:
            return
        from datetime import date, timedelta
        from sqlalchemy import text
        cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
        try:
            with self._db.engine.connect() as conn:
                conn.execute(text('DELETE FROM activity_log WHERE timestamp < :c'), {'c': cutoff})
                conn.commit()
        except Exception as e:
            logger.error('DbLogger cleanup error: %s', e)

    def clear(self, before=None):
        from sqlalchemy import text
        try:
            with self._db.engine.connect() as conn:
                if before:
                    conn.execute(text('DELETE FROM activity_log WHERE timestamp < :b'),
                                 {'b': before.strftime('%Y-%m-%dT%H:%M:%S')})
                else:
                    conn.execute(text('DELETE FROM activity_log'))
                conn.commit()
        except Exception as e:
            logger.error('DbLogger clear error: %s', e)


class TaskScheduler:
    """
    Lightweight in-process task scheduler.

    Modules register periodic jobs via core.scheduler.add_job().
    The scheduler runs a single daemon thread that checks every 30 s
    which jobs are due and executes them inside an app context.

    Job types:
        'interval' – run every *interval* seconds
        'daily'    – run once per day at *time_str* (HH:MM, 24-h, local)
    """

    def __init__(self, app):
        self._app = app
        self._jobs = {}          # job_id -> dict
        self._lock = __import__('threading').Lock()
        self._running = False
        self._thread = None

    # ---- public API used by modules ----

    def add_job(self, job_id, func, job_type='interval',
                interval=3600, time_str='03:00', description=''):
        """
        Register a periodic job.

        Args:
            job_id:      unique string, e.g. 'backup.daily'
            func:        callable (no args) to execute
            job_type:    'interval' | 'daily'
            interval:    seconds between runs (for 'interval' type)
            time_str:    'HH:MM' local time (for 'daily' type)
            description: human-readable label
        """
        from datetime import datetime
        with self._lock:
            self._jobs[job_id] = {
                'func': func,
                'type': job_type,
                'interval': interval,
                'time_str': time_str,
                'description': description,
                'last_run': None,
                'next_run': self._calc_next(job_type, interval, time_str),
                'running': False,
                'last_error': None,
            }

    def remove_job(self, job_id):
        """Unregister a job."""
        with self._lock:
            self._jobs.pop(job_id, None)

    def get_jobs(self):
        """Return a snapshot list of registered jobs (safe for templates)."""
        with self._lock:
            out = []
            for jid, j in self._jobs.items():
                out.append({
                    'id': jid,
                    'description': j['description'],
                    'type': j['type'],
                    'interval': j['interval'],
                    'time_str': j['time_str'],
                    'last_run': j['last_run'].isoformat() if j['last_run'] else None,
                    'next_run': j['next_run'].isoformat() if j['next_run'] else None,
                    'running': j['running'],
                    'last_error': j['last_error'],
                })
            return out

    def start(self):
        """Start the scheduler background thread (idempotent)."""
        if self._running:
            return
        self._running = True
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='task-scheduler')
        self._thread.start()

    def stop(self):
        """Signal the scheduler to stop."""
        self._running = False

    # ---- internals ----

    def _calc_next(self, job_type, interval, time_str):
        from datetime import datetime, timedelta
        now = datetime.now()
        if job_type == 'daily':
            h, m = (int(x) for x in time_str.split(':'))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        else:
            return now + timedelta(seconds=interval)

    def _loop(self):
        import time as _time
        while self._running:
            self._tick()
            _time.sleep(30)

    def _tick(self):
        from datetime import datetime
        now = datetime.now()
        with self._lock:
            due = [(jid, j) for jid, j in self._jobs.items()
                   if j['next_run'] and j['next_run'] <= now and not j['running']]
        for jid, j in due:
            self._run_job(jid, j)

    def _run_job(self, jid, j):
        from datetime import datetime
        with self._lock:
            j['running'] = True
        try:
            with self._app.app_context():
                j['func']()
            j['last_error'] = None
        except Exception as e:
            j['last_error'] = str(e)
            logger.error('Scheduler job %s failed: %s', jid, e)
        finally:
            with self._lock:
                j['running'] = False
                j['last_run'] = datetime.now()
                j['next_run'] = self._calc_next(
                    j['type'], j['interval'], j['time_str'])


class CoreServices:
    """
    Services provided by the core to modules.
    This is the standardized interface modules use to interact with the core.
    """

    def __init__(self, app, db):
        self.app = app
        self.db = db
        self._settings_model = None
        self._module_enabled_model = None
        self._module_manager = None
        self._storage_backend = LocalStorageBackend(app.root_path)
        self._activity_logger = FileActivityLogger(app.root_path)
        self._scheduler = TaskScheduler(app)

    @property
    def module_manager(self):
        """Get the ModuleManager instance"""
        return self._module_manager

    @property
    def app_path(self):
        """Application root path"""
        return self.app.root_path

    def get_settings(self):
        """Get application settings"""
        if self._settings_model:
            return self._settings_model.query.first()
        return None

    def get_upload_path(self, subfolder):
        """
        Get a safe upload path for module files.
        Creates the directory if it doesn't exist.
        """
        path = os.path.join(self.app.root_path, subfolder)
        os.makedirs(path, exist_ok=True)
        return path

    def flash(self, message, category='info'):
        """Flash a message to the user"""
        from flask import flash
        flash(message, category)

    def login_required(self, f):
        """Decorator: require login for a route"""
        from app import login_required
        return login_required(f)

    # --- File Storage ---

    @property
    def storage(self):
        """Get the active file storage backend"""
        return self._storage_backend

    def set_storage_backend(self, backend):
        """
        Replace the file storage backend.
        Called by storage modules (e.g., external_storage) to override default local storage.

        Args:
            backend: FileStorageBackend instance
        """
        self._storage_backend = backend

    def save_file(self, file_data, subfolder, filename):
        """
        Save a file using the active storage backend.

        Args:
            file_data: file-like object or bytes
            subfolder: e.g., 'documents_files', 'expenses_files'
            filename: target filename (already sanitized)

        Returns:
            str: storage key for later retrieval
        """
        relative_path = os.path.join(subfolder, filename)
        return self._storage_backend.save(file_data, relative_path)

    def delete_file(self, storage_key):
        """Delete a file using the active storage backend"""
        if storage_key:
            self._storage_backend.delete(storage_key)

    def send_file(self, storage_key, download_name=None):
        """Send a file as download response using the active storage backend"""
        return self._storage_backend.send(storage_key, download_name)

    def file_exists(self, storage_key):
        """Check if a file exists in the active storage"""
        return self._storage_backend.exists(storage_key) if storage_key else False

    def preview_file(self, storage_key, filename=None):
        """
        Return a Flask Response that displays the file inline in the browser.
        Works with any storage backend (local, S3, GCS, Google Drive).

        Args:
            storage_key: the key returned by save_file / storage.save
            filename: optional original filename (used to detect MIME type)

        Returns:
            Flask Response or None if file not found
        """
        from flask import Response as _Response
        backend_name = type(self._storage_backend).__name__
        logger.info('[preview_file] key=%s, filename=%s, backend=%s',
                    storage_key, filename, backend_name)
        if not storage_key:
            logger.info('[preview_file] storage_key is empty/None')
            return None
        exists = self.file_exists(storage_key)
        logger.info('[preview_file] file_exists(%s) = %s', storage_key, exists)
        if not exists:
            return None
        result = self._storage_backend.get(storage_key)
        logger.info('[preview_file] backend.get() returned %s',
                    'data' if result else 'None')
        if not result:
            return None
        file_bytes, stored_name = result
        name = filename or stored_name or ''
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        mime_map = {
            'pdf': 'application/pdf',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xls': 'application/vnd.ms-excel',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        mime = mime_map.get(ext, 'application/octet-stream')
        logger.info('[preview_file] serving %d bytes as %s', len(file_bytes), mime)
        return _Response(file_bytes, mimetype=mime,
                         headers={'Content-Disposition': 'inline'})

    # --- Activity Logging ---

    @property
    def activity_logger(self):
        """Get the active activity logger"""
        return self._activity_logger

    def set_activity_logger(self, logger):
        """
        Replace or wrap the activity logger.
        Called by modules that want to extend logging (e.g. remote logging).

        Args:
            logger: ActivityLogger instance
        """
        self._activity_logger = logger

    def log_activity(self, action, category='system', details=None, user=None):
        """
        Log a user or system activity.

        Args:
            action: e.g. 'login', 'invoice_created', 'backup_created'
            category: 'auth', 'invoice', 'expense', 'backup', 'settings', 'system'
            details: optional str or dict with extra info
            user: optional user identifier
        """
        self._activity_logger.log(action, category, details, user)

    def get_activity_log(self, limit=100, category=None, offset=0):
        """Retrieve activity log entries"""
        return self._activity_logger.get_entries(limit, category, offset)

    # --- Task Scheduler ---

    @property
    def scheduler(self):
        """Get the task scheduler"""
        return self._scheduler

    # --- Invoice Service (safe API for modules) ---

    @property
    def invoice_service(self):
        """Get the invoice service for safe module access to invoices.

        Provides read/write operations with built-in protection:
        - PAID invoices cannot be modified or deleted
        - All mutations are logged
        - PDF attachment/replacement is supported

        Usage from modules:
            svc = self.core.invoice_service
            inv = svc.get(invoice_id)
            svc.attach_pdf(invoice_id, file_data, filename)
        """
        if not hasattr(self, '_invoice_service'):
            self._invoice_service = InvoiceService(self)
        return self._invoice_service

    # --- Currency Service (exchange rates API for modules) ---

    @property
    def currency_service(self):
        """Get the currency service for exchange rate operations.

        Provides:
        - get_rate(from, to, date) — single pair conversion rate
        - get_rates(currencies, base, date) — multiple rates at once
        - convert(amount, from, to, date) — convert an amount
        - register_provider(name, fn) — modules can add custom rate sources
        - set_active_provider(name) — switch to a custom provider

        Usage from modules:
            svc = self.core.currency_service
            rate, date = svc.get_rate('USD', 'EUR', '2026-03-17')
            amount_eur, rate, date = svc.convert(1000, 'USD', 'EUR', '2026-03-17')
        """
        if not hasattr(self, '_currency_service'):
            self._currency_service = CurrencyService(self)
        return self._currency_service


class CurrencyService:
    """Currency exchange rate API for modules.

    Wraps the built-in currency_converter.py and allows modules to register
    custom rate providers (e.g. a module that fetches rates from a bank API).

    Default provider: ECB (European Central Bank) with exchangerate-api fallback.

    Usage from modules:
        svc = self.core.currency_service

        # Get a single rate
        rate, actual_date = svc.get_rate('USD', 'EUR', '2026-03-17')

        # Get multiple rates
        rates = svc.get_rates(['USD', 'GBP', 'CZK'], base='EUR', date_str='2026-03-17')

        # Convert amount
        eur_amount = svc.convert(1000, 'USD', 'EUR', '2026-03-17')

        # Register a custom provider
        svc.register_provider('my_bank', my_rate_function)
        svc.set_active_provider('my_bank')
    """

    def __init__(self, core):
        self._core = core
        self._providers = {}
        self._active_provider = None  # None = use default (ECB)
        self._logger = logging.getLogger('currency_service')

    # --- Provider management ---

    def register_provider(self, name, provider_fn):
        """Register a custom exchange rate provider.

        Args:
            name: unique provider name (e.g. 'my_bank', 'nbu')
            provider_fn: callable(from_currency, to_currency, date_str) -> (rate, actual_date)
                         Must return (float, str) or (None, None) on failure.
                         date_str is 'YYYY-MM-DD'.
        """
        self._providers[name] = provider_fn
        self._logger.info('Registered currency provider: %s', name)
        self._core.log_activity(
            'currency_provider_registered', 'system',
            f'Currency provider registered: {name}')

    def unregister_provider(self, name):
        """Remove a custom provider."""
        self._providers.pop(name, None)
        if self._active_provider == name:
            self._active_provider = None
        self._logger.info('Unregistered currency provider: %s', name)

    def set_active_provider(self, name):
        """Set which provider to use. Pass None to revert to default (ECB).

        Args:
            name: provider name (must be registered) or None for default
        """
        if name is not None and name not in self._providers:
            raise ValueError(f'Unknown currency provider: {name}. '
                             f'Available: {list(self._providers.keys())}')
        self._active_provider = name
        label = name or 'default (ECB)'
        self._logger.info('Active currency provider set to: %s', label)

    @property
    def active_provider(self):
        """Name of the active provider, or None if using default."""
        return self._active_provider

    @property
    def available_providers(self):
        """List of registered provider names."""
        return list(self._providers.keys())

    # --- Rate fetching ---

    def get_rate(self, from_currency, to_currency, date_str):
        """Get exchange rate between two currencies.

        Tries the active custom provider first, falls back to default ECB.

        Args:
            from_currency: source currency code (e.g. 'USD')
            to_currency: target currency code (e.g. 'EUR')
            date_str: date in 'YYYY-MM-DD' format

        Returns:
            (rate, actual_date) — rate is float, actual_date is str 'YYYY-MM-DD'
            rate means: 1 unit of from_currency = rate units of to_currency
        """
        # Try custom provider
        if self._active_provider and self._active_provider in self._providers:
            try:
                rate, actual_date = self._providers[self._active_provider](
                    from_currency, to_currency, date_str)
                if rate is not None:
                    self._logger.debug(
                        '%s->%s on %s via %s: %s (date: %s)',
                        from_currency, to_currency, date_str,
                        self._active_provider, rate, actual_date)
                    return rate, actual_date
                self._logger.warning(
                    'Provider %s returned None for %s->%s on %s, falling back to default',
                    self._active_provider, from_currency, to_currency, date_str)
            except Exception as e:
                self._logger.error(
                    'Provider %s failed for %s->%s on %s: %s — falling back to default',
                    self._active_provider, from_currency, to_currency, date_str, e)

        # Default: use built-in currency_converter
        return self._default_rate(from_currency, to_currency, date_str)

    def get_rates(self, currencies, base='EUR', date_str=None):
        """Get exchange rates for multiple currencies relative to a base.

        Args:
            currencies: list of currency codes (e.g. ['USD', 'GBP', 'CZK'])
            base: base currency (default 'EUR')
            date_str: date in 'YYYY-MM-DD' (default: today)

        Returns:
            dict: {currency_code: rate} where rate is units of currency per 1 base
        """
        if date_str is None:
            from datetime import date
            date_str = date.today().strftime('%Y-%m-%d')

        from currency_converter import get_multiple_exchange_rates
        return get_multiple_exchange_rates(date_str, currencies, base_currency=base)

    def convert(self, amount, from_currency, to_currency, date_str):
        """Convert an amount between currencies.

        Args:
            amount: numeric amount to convert
            from_currency: source currency code
            to_currency: target currency code
            date_str: date in 'YYYY-MM-DD'

        Returns:
            (converted_amount, rate, actual_date)
        """
        if from_currency == to_currency:
            return amount, 1.0, date_str

        rate, actual_date = self.get_rate(from_currency, to_currency, date_str)
        return amount * rate, rate, actual_date

    # --- Default provider (ECB) ---

    def _default_rate(self, from_currency, to_currency, date_str):
        """Fetch rate using built-in ECB-based currency_converter."""
        from currency_converter import get_exchange_rate, get_multiple_exchange_rates

        if from_currency == to_currency:
            return 1.0, date_str

        # The built-in get_exchange_rate returns USD->EUR rate
        if from_currency == 'USD' and to_currency == 'EUR':
            return get_exchange_rate(date_str)

        if from_currency == 'EUR' and to_currency == 'USD':
            rate, actual_date = get_exchange_rate(date_str)
            return 1.0 / rate, actual_date

        # For arbitrary pairs, use get_multiple_exchange_rates
        # Get both currencies relative to EUR, then cross-calculate
        codes = set()
        if from_currency != 'EUR':
            codes.add(from_currency)
        if to_currency != 'EUR':
            codes.add(to_currency)
        rates = get_multiple_exchange_rates(date_str, list(codes), base_currency='EUR')

        # rates[X] = how many X per 1 EUR
        from_per_eur = rates.get(from_currency, 1.0) if from_currency != 'EUR' else 1.0
        to_per_eur = rates.get(to_currency, 1.0) if to_currency != 'EUR' else 1.0

        # 1 from_currency = (to_per_eur / from_per_eur) to_currency
        cross_rate = to_per_eur / from_per_eur if from_per_eur else 0.0
        return cross_rate, date_str


class InvoiceService:
    """Safe, controlled API for modules to interact with invoices.

    Rules enforced:
    - PAID invoices are read-only (no update, no delete, no PDF replace)
    - All write operations are logged via activity_logger
    - PDF files are stored in invoices_pdf/ with hash tracking
    """

    def __init__(self, core):
        self._core = core

    def _get_models(self):
        from app import Invoice
        return Invoice, self._core.db

    def get(self, invoice_id):
        """Get an invoice by ID. Returns None if not found."""
        Invoice, _ = self._get_models()
        return Invoice.query.get(invoice_id)

    def get_all(self, **filters):
        """Query invoices with optional filters (status, client_name, etc.)"""
        Invoice, _ = self._get_models()
        return Invoice.query.filter_by(**filters).all()

    def get_by_number(self, invoice_number):
        """Get an invoice by its invoice_number."""
        Invoice, _ = self._get_models()
        return Invoice.query.filter_by(invoice_number=invoice_number).first()

    def is_locked(self, invoice):
        """Check if invoice is locked (PAID status = read-only)."""
        return invoice and invoice.status == 'paid'

    def update(self, invoice_id, **fields):
        """Update invoice fields. Raises ValueError if invoice is PAID.

        Args:
            invoice_id: int
            **fields: column=value pairs to update

        Returns:
            Updated Invoice object

        Raises:
            ValueError: if invoice is paid or not found
        """
        Invoice, _ = self._get_models()
        invoice = Invoice.query.get(invoice_id)
        if not invoice:
            raise ValueError(f'Invoice #{invoice_id} not found')
        if self.is_locked(invoice):
            raise ValueError(
                f'Invoice #{invoice.invoice_number} is PAID and cannot be modified')

        for key, value in fields.items():
            if hasattr(invoice, key) and key not in ('id', 'pdf_hash'):
                setattr(invoice, key, value)
        self._core.db.session.commit()
        self._core.log_activity(
            'invoice_updated_by_module', 'invoice',
            f'Invoice #{invoice.invoice_number} updated by module | '
            f'fields: {list(fields.keys())} | '
            f'values: { {k: str(v)[:50] for k, v in fields.items()} }')
        return invoice

    def attach_pdf(self, invoice_or_id, file_data, original_filename=None):
        """Attach or replace the PDF file for an invoice.

        Saves the file via core.storage and updates pdf_hash + pdf_storage_key.
        Raises ValueError if invoice is PAID and already has a sealed PDF.

        Args:
            invoice_or_id: Invoice object or int (invoice ID)
            file_data: file-like object or bytes
            original_filename: optional, used for logging

        Returns:
            str: storage key for the saved PDF
        """
        import hashlib as _hashlib

        if isinstance(invoice_or_id, int):
            Invoice, _ = self._get_models()
            invoice = Invoice.query.get(invoice_or_id)
        else:
            invoice = invoice_or_id

        if not invoice:
            raise ValueError('Invoice not found')

        pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
        storage_key = getattr(invoice, 'pdf_storage_key', None)

        # PAID + existing PDF with hash = locked, cannot replace
        if self.is_locked(invoice) and invoice.pdf_hash:
            has_existing = False
            if storage_key:
                has_existing = self._core.storage.exists(storage_key)
            else:
                # Legacy: check local path
                legacy_path = os.path.join('invoices_pdf', pdf_filename)
                has_existing = self._core.storage.exists(legacy_path)
            if has_existing:
                raise ValueError(
                    f'Invoice #{invoice.invoice_number} is PAID with a sealed PDF. '
                    'Cannot replace.')

        # Read content
        if hasattr(file_data, 'read'):
            file_data.seek(0)
            content = file_data.read()
        else:
            content = file_data

        if not content:
            raise ValueError('PDF file is empty')

        # Check if replacing
        replaced = False
        if storage_key:
            replaced = self._core.storage.exists(storage_key)

        # Save via core.storage
        relative_path = os.path.join('invoices_pdf', pdf_filename)
        new_key = self._core.storage.save(content, relative_path)

        # Calculate and store hash + storage key
        pdf_hash = _hashlib.sha256(content).hexdigest()
        invoice.pdf_hash = pdf_hash
        if hasattr(invoice, 'pdf_storage_key'):
            invoice.pdf_storage_key = new_key
        self._core.db.session.commit()

        size_kb = len(content) / 1024
        action = 'replaced' if replaced else 'attached'
        self._core.log_activity(
            'invoice_pdf_attached', 'invoice',
            f'PDF {action} for invoice #{invoice.invoice_number} | '
            f'file={original_filename or pdf_filename} | '
            f'size={size_kb:.1f}KB | hash={pdf_hash[:12]}… | '
            f'key={new_key}')
        return new_key

    def get_pdf(self, invoice_or_id):
        """Get invoice PDF content via core.storage.

        Args:
            invoice_or_id: Invoice object or int (invoice ID)

        Returns:
            tuple (bytes, filename) or None
        """
        if isinstance(invoice_or_id, int):
            invoice = self.get(invoice_or_id)
        else:
            invoice = invoice_or_id
        if not invoice:
            return None

        key = self._resolve_storage_key(invoice)
        if not key:
            return None
        return self._core.storage.get(key)

    def send_pdf(self, invoice_or_id):
        """Send invoice PDF as download response via core.storage.

        Args:
            invoice_or_id: Invoice object or int (invoice ID)

        Returns:
            Flask response or None
        """
        if isinstance(invoice_or_id, int):
            invoice = self.get(invoice_or_id)
        else:
            invoice = invoice_or_id
        if not invoice:
            return None

        key = self._resolve_storage_key(invoice)
        if not key:
            return None
        pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
        return self._core.storage.send(key, download_name=pdf_filename)

    def get_pdf_path(self, invoice_or_id):
        """Check if invoice has a PDF and return its storage key, or None.

        Note: for backwards compatibility this method name is kept, but it
        now returns the storage key (which may be a local path or a remote ID).

        Args:
            invoice_or_id: Invoice object or int (invoice ID)
        """
        if isinstance(invoice_or_id, int):
            invoice = self.get(invoice_or_id)
        else:
            invoice = invoice_or_id
        if not invoice:
            return None

        key = self._resolve_storage_key(invoice)
        if key and self._core.storage.exists(key):
            return key
        return None

    def has_pdf(self, invoice_or_id):
        """Check if an invoice has a PDF file.

        Args:
            invoice_or_id: Invoice object or int (invoice ID)
        """
        return self.get_pdf_path(invoice_or_id) is not None

    def _resolve_storage_key(self, invoice):
        """Resolve the storage key for an invoice PDF.
        Uses pdf_storage_key if set, otherwise falls back to legacy path convention.
        """
        key = getattr(invoice, 'pdf_storage_key', None)
        if key:
            return key
        # Legacy fallback: convention-based local path
        pdf_filename = f'invoice_{invoice.invoice_number}.pdf'
        legacy_path = os.path.join('invoices_pdf', pdf_filename)
        if self._core.storage.exists(legacy_path):
            return legacy_path
        return None


class ModuleManager:
    """
    Discovers, loads, and manages modules.
    """

    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.core = CoreServices(app, db)
        self.core._module_manager = self
        self.modules = {}           # module_id -> module instance
        self.discovered = {}        # module_id -> module class
        self._enabled_cache = None

    def _get_module_enabled_model(self):
        """Get or create the ModuleEnabled model"""
        if self.core._module_enabled_model:
            return self.core._module_enabled_model

        # Define the model dynamically
        class ModuleEnabled(self.db.Model):
            __tablename__ = 'module_enabled'
            id = self.db.Column(self.db.Integer, primary_key=True)
            module_id = self.db.Column(self.db.String(100), unique=True, nullable=False)
            enabled = self.db.Column(self.db.Boolean, default=False)

            def __repr__(self):
                return f'<ModuleEnabled {self.module_id}: {self.enabled}>'

        self.core._module_enabled_model = ModuleEnabled
        return ModuleEnabled

    def discover_modules(self):
        """Scan modules/ directory for available modules"""
        modules_dir = os.path.join(self.app.root_path, 'modules')
        if not os.path.exists(modules_dir):
            os.makedirs(modules_dir)
            return

        for item in os.listdir(modules_dir):
            module_path = os.path.join(modules_dir, item)
            index_path = os.path.join(module_path, 'index.py')

            if os.path.isdir(module_path) and os.path.exists(index_path):
                try:
                    spec = importlib.util.spec_from_file_location(
                        f'modules.{item}.index', index_path
                    )
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)

                    # Find the BaseModule subclass
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (isinstance(attr, type) and
                            issubclass(attr, BaseModule) and
                            attr is not BaseModule):
                            self.discovered[item] = attr
                            break
                except Exception as e:
                    logger.error("Error discovering module '%s': %s",
                                 _sanitize_log(item), e)

    def init_db(self):
        """Create the module_enabled table if it doesn't exist"""
        ModuleEnabled = self._get_module_enabled_model()
        with self.app.app_context():
            # Create only the module_enabled table
            ModuleEnabled.__table__.create(self.db.engine, checkfirst=True)

    def is_enabled(self, module_id):
        """Check if a module is enabled"""
        ModuleEnabled = self._get_module_enabled_model()
        record = ModuleEnabled.query.filter_by(module_id=module_id).first()
        return record.enabled if record else False

    def get_all_module_states(self):
        """Get list of all discovered modules with their enabled state"""
        result = []
        for mod_id, mod_class in self.discovered.items():
            # Instantiate temporarily to read metadata
            instance = mod_class(self.core)
            result.append({
                'module_id': mod_id,
                'name': instance.name,
                'description': instance.description,
                'version': instance.version,
                'enabled': self.is_enabled(mod_id)
            })
        return result

    def enable_module(self, module_id):
        """Enable a module"""
        ModuleEnabled = self._get_module_enabled_model()
        record = ModuleEnabled.query.filter_by(module_id=module_id).first()
        if record:
            record.enabled = True
        else:
            record = ModuleEnabled(module_id=module_id, enabled=True)
            self.db.session.add(record)
        self.db.session.commit()

        # Load the module if discovered
        if module_id in self.discovered and module_id not in self.modules:
            self._load_module(module_id)

    def disable_module(self, module_id):
        """Disable a module"""
        ModuleEnabled = self._get_module_enabled_model()
        record = ModuleEnabled.query.filter_by(module_id=module_id).first()
        if record:
            record.enabled = False
            self.db.session.commit()

        # Remove from active modules
        if module_id in self.modules:
            self.modules[module_id].on_disable()
            del self.modules[module_id]

    def load_enabled_modules(self):
        """Load all enabled modules"""
        for mod_id in self.discovered:
            if self.is_enabled(mod_id):
                self._load_module(mod_id)

    def _load_module(self, module_id):
        """Load and initialize a single module"""
        if module_id not in self.discovered:
            return

        try:
            mod_class = self.discovered[module_id]
            instance = mod_class(self.core)

            # Register models
            models = instance.register_models(self.db)
            if models:
                # Create tables for module models
                with self.app.app_context():
                    for model_class in models.values():
                        model_class.__table__.create(self.db.engine, checkfirst=True)

            # Register routes
            instance.register_routes(self.app)

            # Register template filters
            instance.register_template_filters(self.app)

            # Call on_enable
            instance.on_enable()

            self.modules[module_id] = instance
            logger.info("Loaded module: %s v%s",
                       _sanitize_log(instance.name),
                       _sanitize_log(instance.version))

        except Exception as e:
            logger.error("Error loading module '%s': %s",
                         _sanitize_log(module_id), e)

    def get_nav_items(self):
        """Get navigation items from all active modules"""
        items = []
        for mod in self.modules.values():
            items.extend(mod.nav_items)
        return items

    def get_grouped_nav_items(self):
        """Get nav items split into ungrouped list and grouped dict.
        Returns (ungrouped_list, groups_dict).
        groups_dict preserves insertion order; core groups come first."""
        ungrouped = []
        groups = {}
        for item in self.get_nav_items():
            g = item.get('group')
            if g:
                groups.setdefault(g, []).append(item)
            else:
                ungrouped.append(item)
        return ungrouped, groups

    def get_full_nav(self):
        """Build the complete navigation structure.

        Returns a list of nav entries, each is either:
          {'type': 'link', 'label': ..., 'endpoint': ..., 'icon': ...}
          {'type': 'dropdown', 'label': ..., 'items': [{'label':..., 'endpoint':..., 'icon':...}, ...]}

        Core menus (Invoices, System) are defined here with their
        hardcoded items.  Module nav_items with a matching ``group``
        are appended into the corresponding dropdown.  Modules that
        define a ``group`` not present in core menus get their own
        dropdown.  Ungrouped module items become top-level links.
        """
        ungrouped, groups = self.get_grouped_nav_items()

        # Core menu definitions — order matters
        core = [
            {
                'type': 'dropdown',
                'label': 'Invoices',
                'items': [
                    {'label': 'All Invoices', 'endpoint': 'index'},
                    {'label': 'Create Invoice', 'endpoint': 'create_invoice'},
                ],
            },
            # placeholder — ungrouped module links will be inserted here
            '__ungrouped__',
            # placeholder — extra module groups inserted here
            '__extra_groups__',
            {
                'type': 'dropdown',
                'label': 'System',
                'items': [
                    {'label': 'Logs', 'endpoint': 'system_logs'},
                    {'label': 'Scheduled Tasks', 'endpoint': 'scheduled_tasks'},
                ],
            },
            {'type': 'link', 'label': 'Settings', 'endpoint': 'settings'},
        ]

        # Inject module items into core dropdowns
        seen_groups = set()
        for entry in core:
            if isinstance(entry, dict) and entry.get('type') == 'dropdown':
                label = entry['label']
                if label in groups:
                    for item in groups[label]:
                        entry['items'].append({
                            'label': item.get('label', ''),
                            'endpoint': item.get('endpoint', ''),
                            'icon': item.get('icon', ''),
                        })
                    seen_groups.add(label)

        # Build final list, expanding placeholders
        result = []
        for entry in core:
            if entry == '__ungrouped__':
                for item in ungrouped:
                    result.append({
                        'type': 'link',
                        'label': item.get('label', ''),
                        'endpoint': item.get('endpoint', ''),
                        'icon': item.get('icon', ''),
                    })
            elif entry == '__extra_groups__':
                for gname, gitems in groups.items():
                    if gname not in seen_groups:
                        result.append({
                            'type': 'dropdown',
                            'label': gname,
                            'items': [{'label': i.get('label', ''),
                                       'endpoint': i.get('endpoint', ''),
                                       'icon': i.get('icon', '')} for i in gitems],
                        })
            else:
                result.append(entry)

        return result

    def get_settings_panels(self):
        """Get settings panels from all active modules"""
        panels = []
        for mod in self.modules.values():
            panels.extend(mod.settings_panels)
        return panels

    def get_dashboard_panels(self):
        """Get dashboard panels from all active modules"""
        panels = []
        for mod in self.modules.values():
            panels.extend(mod.get_dashboard_panels())
        panels.sort(key=lambda p: p.get('order', 99))
        return panels

    def get_report_sections(self):
        """Get report sections from all active modules"""
        sections = []
        for mod in self.modules.values():
            sections.extend(mod.get_report_sections())
        return sections

    def get_settings_html(self, settings):
        """Get settings HTML panels from all active modules"""
        panels = []
        for mod in self.modules.values():
            html = mod.get_settings_html(settings)
            if html:
                tab_info = mod.settings_tab
                if isinstance(tab_info, dict):
                    tab_id = tab_info.get('id', 'general')
                    tab_label = tab_info.get('label', tab_id)
                else:
                    tab_id = tab_info
                    tab_label = tab_info
                panels.append({
                    'module_id': mod.module_id,
                    'html': html,
                    'tab': tab_id,
                    'tab_label': tab_label,
                })
        return panels

    def save_settings(self, settings, form):
        """Let only modules whose tab matches the submitted tab handle settings save"""
        active_tab = form.get('_settings_tab', '')
        for mod in self.modules.values():
            tab = getattr(mod, 'settings_tab', None)
            if callable(tab):
                tab = tab()
            # Normalize: dict tabs use 'id' key
            if isinstance(tab, dict):
                tab_id = tab.get('id', 'general')
            else:
                tab_id = tab or 'general'

            if tab_id == active_tab:
                mod.save_settings(settings, form)

    def get_tax_obligations(self, context):
        """Collect tax obligation contributions from all active modules"""
        results = []
        for mod in self.modules.values():
            result = mod.get_tax_obligations(context)
            if result:
                results.append(result)
        return results

    def calculate_income_tax(self, context):
        """Ask modules to override income tax calculation. First non-None wins."""
        for mod in self.modules.values():
            try:
                result = mod.calculate_income_tax(context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error("Module '%s' calculate_income_tax error: %s",
                             _sanitize_log(mod.module_id), e)
        return None

    def calculate_vat(self, context):
        """Ask modules to override VAT calculation. First non-None wins."""
        for mod in self.modules.values():
            try:
                result = mod.calculate_vat(context)
                if result is not None:
                    return result
            except Exception as e:
                logger.error("Module '%s' calculate_vat error: %s",
                             _sanitize_log(mod.module_id), e)
        return None

    def get_invoice_actions(self, invoice):
        """Collect invoice action buttons/forms from all active modules"""
        actions = []
        for mod in self.modules.values():
            actions.extend(mod.get_invoice_actions(invoice))
        return actions

    def get_create_form_html(self):
        """Collect create-form HTML snippets from all active modules"""
        parts = []
        for mod in self.modules.values():
            html = mod.get_create_form_html()
            if html:
                parts.append(html)
        return parts

    def on_invoice_created(self, invoice, request):
        """Notify all active modules that an invoice was created"""
        for mod in self.modules.values():
            try:
                mod.on_invoice_created(invoice, request)
            except Exception as e:
                logger.error("Module '%s' on_invoice_created error: %s",
                             _sanitize_log(mod.module_id), e)

    def get_edit_form_html(self, invoice):
        """Collect edit-form HTML snippets from all active modules"""
        parts = []
        for mod in self.modules.values():
            html = mod.get_edit_form_html(invoice)
            if html:
                parts.append(html)
        return parts

    def on_invoice_updated(self, invoice, request):
        """Notify all active modules that an invoice was updated"""
        for mod in self.modules.values():
            try:
                mod.on_invoice_updated(invoice, request)
            except Exception as e:
                logger.error("Module '%s' on_invoice_updated error: %s",
                             _sanitize_log(mod.module_id), e)

    def get_invoice_templates(self):
        """Collect invoice PDF templates from core + all active modules.

        Returns:
            list[dict]: each dict has 'id', 'name', 'path'
        """
        # Core templates from invoice_templates/ directory
        templates = []
        core_dir = os.path.join(self.app.root_path, 'invoice_templates')
        if os.path.isdir(core_dir):
            for fname in sorted(os.listdir(core_dir)):
                if fname.endswith('.py') and not fname.startswith('_'):
                    tid = fname[:-3]  # strip .py
                    # Derive human name: default_template -> Default Template
                    name = tid.replace('_', ' ').title()
                    templates.append({
                        'id': tid,
                        'name': name,
                        'path': os.path.join(core_dir, fname),
                    })

        # Module templates
        for mod in self.modules.values():
            try:
                for tpl in mod.get_invoice_templates():
                    templates.append(tpl)
            except Exception as e:
                logger.error("Module '%s' get_invoice_templates error: %s",
                             _sanitize_log(mod.module_id), e)
        return templates

    def get_field_labels(self):
        """Collect field label overrides from all active modules.
        Later modules overwrite earlier ones."""
        labels = {}
        for mod in self.modules.values():
            try:
                labels.update(mod.get_field_labels())
            except Exception as e:
                logger.error("Module '%s' get_field_labels error: %s",
                             _sanitize_log(mod.module_id), e)
        return labels
