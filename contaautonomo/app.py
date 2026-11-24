#!/usr/bin/env python3
"""
The ContaAutónomo Flask app: configuration, security (CSRF, rate limits,
headers, sessions), error handlers, the helpers every route uses
(`login_required`, `log_activity`), and start-up (`init_database`,
`init_module_manager`, `bootstrap`, `shutdown`).

The models are in `models.py` and the routes in `routes/`, one file per area;
both are imported here, so `from contaautonomo.app import Invoice, db` keeps
working. Start the development server with `python -m contaautonomo.app`.
"""

from flask import Flask, request, redirect, url_for, flash, session
from datetime import datetime
from functools import wraps
from contextlib import contextmanager
import os
import sys
import hashlib

# When started with `python -m contaautonomo.app`, this module is '__main__'.
# Register it under its import name as well, *before* any
# `from contaautonomo.app import ...` runs (here or in plugin modules /
# InvoiceService). Otherwise that import re-executes this file as a separate
# module with a second SQLAlchemy()/db, and ORM calls made through the import
# fail with "current Flask app is not registered with this 'SQLAlchemy'
# instance". Aliasing keeps a single module/db.
if __name__ == '__main__':
    sys.modules.setdefault('contaautonomo.app', sys.modules['__main__'])

from contaautonomo import paths

import logging
import logging.config


class _RequestIdFilter(logging.Filter):
    """Inject the current request id into log records for cross-worker tracing.
    Falls back to '-' outside a request context."""
    def filter(self, record):
        try:
            from flask import g, has_request_context
            record.request_id = (getattr(g, 'request_id', '-')
                                 if has_request_context() else '-')
        except Exception:
            record.request_id = '-'
        return True


# Module-level logging config: applies whether started via
# `python app.py` or gunicorn — basicConfig in __main__ has no effect under
# gunicorn, so configure here at import time instead.
logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {'request_id': {'()': _RequestIdFilter}},
    'formatters': {
        'default': {'format': '%(asctime)s [%(name)s] [%(request_id)s] %(levelname)s: %(message)s',
                    'datefmt': '%Y-%m-%d %H:%M:%S'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'default',
                    'filters': ['request_id']},
    },
    'root': {'level': 'INFO', 'handlers': ['console']},
})

logger = logging.getLogger(__name__)


# Templates, static files and invoice templates sit in this package
# (root_path); runtime data — instance/, logs/, uploads — in the data folder
# (paths.py), which is the repository root unless CONTAAUTONOMO_DATA_DIR says
# otherwise. A relative sqlite:/// URL resolves under instance/ there.
app = Flask(__name__, instance_path=paths.instance_dir())
APP_VERSION = '2'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///invoices.db')

# --- SECRET_KEY: require in production, warn in dev ---
_secret = os.environ.get('SECRET_KEY', '')
_is_dev = (os.environ.get('FLASK_DEBUG') == '1'
           or os.environ.get('FLASK_ENV') == 'development')
if not _secret:
    if _is_dev:
        _secret = 'dev-only-insecure-key-' + hashlib.sha256(os.urandom(16)).hexdigest()
        logger.warning('SECRET_KEY not set — using random dev key. Sessions will reset on restart.')
    else:
        # Fail hard in production: a random per-restart key silently invalidates
        # every session on each container restart.
        raise RuntimeError(
            "SECRET_KEY environment variable is required in production. "
            "Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
app.config['SECRET_KEY'] = _secret

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Scheduler leader lock: one process per deployment runs scheduled jobs.
app.config['SCHEDULER_LOCK_PATH'] = os.environ.get(
    'SCHEDULER_LOCK_PATH', os.path.join(app.instance_path, 'scheduler.lock'))
# Start-up lock: worker processes initialise the database one at a time.
app.config['INIT_LOCK_PATH'] = os.path.join(
    os.path.dirname(app.config['SCHEDULER_LOCK_PATH']), 'init.lock')
# Key file for stored credentials (secret_box.py), used when CREDENTIALS_KEY is
# not set. Modules name their own key files through core.deployment_key().
app.config['CREDENTIALS_KEY_FILE'] = os.environ.get(
    'CREDENTIALS_KEY_FILE', os.path.join(app.instance_path, 'credentials.key'))

from contaautonomo.utils.constants import (
    MAX_CONTENT_LENGTH_BYTES,
    SESSION_LIFETIME_SECONDS,
)
from contaautonomo.security.secret_box import encrypt_existing

# --- Upload size cap: reject oversized bodies before they fill disk ---
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH_BYTES

# --- SQLAlchemy engine options: safe on SQLite, sane for Postgres ---
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,   # verify a connection before use (avoids stale-conn errors)
    'pool_recycle': 300,     # recycle connections every 5 minutes
}

# --- Session security ---
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
_force_https = os.environ.get('FORCE_HTTPS', '').lower() in ('1', 'true', 'yes')
app.config['SESSION_COOKIE_SECURE'] = _force_https
app.config['PERMANENT_SESSION_LIFETIME'] = SESSION_LIFETIME_SECONDS

from contaautonomo.extensions import db  # noqa: E402

db.init_app(app)

# --- SQLite pragmas: WAL for concurrent reads, enforce foreign keys.
# Listen on the generic Engine (works at import time, no app context needed); the
# isinstance check means it only touches SQLite connections. ---
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _SAEngine
import sqlite3 as _sqlite3


@_sa_event.listens_for(_SAEngine, 'connect')
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    if isinstance(dbapi_conn, _sqlite3.Connection):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA foreign_keys=ON')
        cur.close()

# --- CSRF protection ---
try:
    from flask_wtf.csrf import CSRFProtect, CSRFError
    csrf = CSRFProtect(app)
except ImportError:
    csrf = None
    logger.warning('flask-wtf not installed — CSRF protection disabled')

# --- Rate limiting ---
from contaautonomo.security.rate_limit import limiter
limiter.init_app(app)

# --- Client address behind a reverse proxy ---
# With TRUSTED_PROXY_COUNT=N, trust the last N X-Forwarded-For / -Proto hops
# so rate limits and login lockout see the client, not the proxy. Leave 0
# when the app is reached directly: the headers can then be forged.
_trusted_proxies = int(os.environ.get('TRUSTED_PROXY_COUNT', '0') or 0)
if _trusted_proxies > 0:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_trusted_proxies,
                            x_proto=_trusted_proxies)

# --- Optional Sentry error tracking (env-gated) ---
_sentry_dsn = os.environ.get('SENTRY_DSN')
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(dsn=_sentry_dsn, integrations=[FlaskIntegration()])
        logger.info('Sentry error tracking enabled')
    except ImportError:
        logger.warning('SENTRY_DSN set but sentry-sdk not installed — skipping')


# --- Request correlation id (for tracing across interleaved worker logs) ---
@app.before_request
def _assign_request_id():
    import uuid
    from flask import g
    g.request_id = uuid.uuid4().hex[:8]


@app.after_request
def _add_request_id_header(response):
    from flask import g
    rid = getattr(g, 'request_id', None)
    if rid:
        response.headers['X-Request-ID'] = rid
    return response


# --- Security headers ---
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP in report-only first: the app emits inline styles/scripts and
    # module-generated HTML, so report violations before enforcing. Tighten and
    # switch to 'Content-Security-Policy' after auditing reports.
    response.headers['Content-Security-Policy-Report-Only'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none';"
    )
    if _force_https:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# --- Error handlers ---
@app.errorhandler(404)
def page_not_found(e):
    flash('Page not found', 'danger')
    return redirect(url_for('dashboard'))


@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    flash('Internal server error', 'danger')
    return redirect(url_for('dashboard'))


@app.errorhandler(413)
def request_entity_too_large(e):
    # friendly response instead of a stack trace when MAX_CONTENT_LENGTH
    # is exceeded. JSON for the API, flash+redirect for the web UI.
    if request.path.startswith('/api/'):
        from flask import jsonify
        return jsonify(error='payload_too_large',
                       message='Request body too large (max 50 MB)'), 413
    flash('File too large. Maximum size is 50 MB.', 'danger')
    # Redirect to a fixed internal page — never request.referrer — to avoid an
    # open redirect via a crafted Referer header.
    return redirect(url_for('dashboard'))


from jinja2.exceptions import TemplateNotFound
@app.errorhandler(TemplateNotFound)
def template_not_found(e):
    flash('Page not found', 'danger')
    return redirect(url_for('dashboard'))

# CSRF error handler
try:
    from flask_wtf.csrf import CSRFError
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash('Session expired or invalid request. Please try again.', 'danger')
        # Redirect to a fixed internal page — never to request.referrer — so a
        # crafted Referer can't drive an open redirect.
        return redirect(url_for('dashboard'))
except ImportError:
    pass  # flask-wtf not installed — CSRF error handler not registered

# Module manager: set by init_module_manager(); routes read core_app.module_manager.
module_manager = None


def log_activity(action, category='system', details=None):
    """Log user/system activity via core logger (safe to call before init)"""
    if module_manager:
        module_manager.core.log_activity(action, category, details)


# Login required decorator
def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function



# --- Models (models.py), re-exported: modules and tests import them from here.
from contaautonomo.models import (  # noqa: E402,F401
    Bank, Contractor, Customer, Document, Expense, Invoice, InvoiceItem, Message,
    Settings, SSPayment, TaxForm, find_customer_by_name,
)


# ============================================================================
# BLUEPRINTS REGISTRATION
# ============================================================================

# Register Auth Blueprint (must be first)
from contaautonomo.security.auth_routes import auth_bp
app.register_blueprint(auth_bp)

# Exempt auth routes from CSRF (login/setup have no session to protect)
if csrf:
    csrf.exempt(auth_bp)

# Expenses, reports, tax forms, documents and backups are modules
# (modules/<id>/), registered by the module manager.


# ============================================================================
# MODULE MANAGER INITIALIZATION
# ============================================================================

def init_database():
    """Create tables, apply core schema migrations and ensure the Settings row.

    Idempotent; every entry point (python app.py, docker_entrypoint.py,
    gunicorn workers) calls it before loading modules. Needs an app context.
    """
    from contaautonomo.core.schema_migrations import run_migrations
    run_migrations(db.engine, db.metadata)
    encrypt_existing(db, 'settings', ['currency_provider_api_key'])

    if not Settings.query.first():
        db.session.add(Settings(
            tracked_currencies='USD,EUR,GBP,CZK',
            base_currency='EUR',
            default_currency='EUR',
            default_vat_rate=21.0,
            default_irpf_rate=20.0,
        ))
        db.session.commit()
        logging.getLogger(__name__).info('Created default Settings row (first run)')


def init_module_manager():
    """Initialize the module manager, discover and load enabled modules"""
    global module_manager
    from contaautonomo.core.module_manager import ModuleManager

    module_manager = ModuleManager(app, db)
    module_manager.core._settings_model = Settings
    module_manager.register_core_functions(Invoice=Invoice, Customer=Customer, Message=Message,
                                           Bank=Bank)
    module_manager.init_db()
    module_manager.discover_modules()
    module_manager.load_enabled_modules()

    # Start the task scheduler (only in the main worker, not the reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        module_manager.core.scheduler.start()

    # Make module_manager and version available in templates
    app.jinja_env.globals['module_manager'] = module_manager
    app.jinja_env.globals['app_version'] = APP_VERSION
    app.jinja_env.globals['current_year'] = datetime.now().year

    # Make auth_service available in templates
    from contaautonomo.security.auth import auth_service
    app.jinja_env.globals['auth_service'] = auth_service

    import json as _json
    app.jinja_env.filters['from_json'] = lambda s: _json.loads(s) if s else {}

    return module_manager


def _apply_log_settings(s, mgr):
    """Apply log path / retention / storage type from Settings to the core logger."""
    from contaautonomo.core.module_manager import FileActivityLogger, DbActivityLogger
    storage = getattr(s, 'log_storage', 'file') or 'file'
    current = mgr.core.activity_logger

    if storage == 'db':
        if not isinstance(current, DbActivityLogger):
            mgr.core.set_activity_logger(DbActivityLogger(db, app))
    else:
        if not isinstance(current, FileActivityLogger):
            mgr.core.set_activity_logger(FileActivityLogger(paths.data_dir()))

    logger = mgr.core.activity_logger
    if hasattr(logger, 'cleanup') and s.log_retention_days is not None:
        logger.cleanup(s.log_retention_days)

    # Register daily log cleanup job
    retention = s.log_retention_days if s.log_retention_days else 0
    if retention > 0:
        def _log_cleanup():
            mgr.core.activity_logger.cleanup(retention)
        mgr.core.scheduler.add_job(
            job_id='core.log_cleanup',
            func=_log_cleanup,
            job_type='daily',
            time_str='04:00',
            description=f'Log cleanup (retain {retention} days)',
        )
    else:
        mgr.core.scheduler.remove_job('core.log_cleanup')


def _apply_currency_provider(s, mgr):
    """Apply currency provider from Settings to CurrencyService."""
    if not s or not mgr:
        return
    from contaautonomo.utils.currency_converter import make_frankfurter_provider, \
        make_open_exchange_rates_provider, make_fixer_provider
    provider_name = getattr(s, 'currency_provider', 'ecb') or 'ecb'
    api_key = getattr(s, 'currency_provider_api_key', '') or ''
    svc = mgr.core.currency_service

    # Register built-in providers that need factory functions
    if provider_name == 'frankfurter':
        svc.register_provider('frankfurter', make_frankfurter_provider())
        svc.set_active_provider('frankfurter')
    elif provider_name == 'open_exchange_rates' and api_key:
        svc.register_provider('open_exchange_rates', make_open_exchange_rates_provider(api_key))
        svc.set_active_provider('open_exchange_rates')
    elif provider_name == 'fixer' and api_key:
        svc.register_provider('fixer', make_fixer_provider(api_key))
        svc.set_active_provider('fixer')
    else:
        # Default ECB or unknown — reset to default
        svc.set_active_provider(None)


def bootstrap():
    """Prepare this process to serve: database, modules, scheduler, settings.

    Called once per process by every serving entry point — `python app.py`
    and each gunicorn worker (post_worker_init in gunicorn.conf.py) — and a
    no-op on repeated calls. Importing app.py alone initialises nothing, so
    tests and tools can import it safely.
    """
    if module_manager is not None:
        return module_manager
    # Workers start in parallel: one at a time creates tables and runs
    # migrations, or two of them race on CREATE TABLE in a fresh database.
    with _init_lock(), app.app_context():
        init_database()
        mgr = init_module_manager()
        s = Settings.query.first()
        if s:
            _apply_log_settings(s, mgr)
            _apply_currency_provider(s, mgr)
    return mgr


@contextmanager
def _init_lock():
    """Exclusive, blocking file lock for process start-up (no-op without flock)."""
    try:
        import fcntl
    except ImportError:  # Windows: single-process deployments only
        yield
        return
    path = app.config['INIT_LOCK_PATH']
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def shutdown():
    """Stop the scheduler so no new job starts while the process exits."""
    if module_manager is not None:
        module_manager.core.scheduler.stop()


# --- Routes (routes/), registered once the helpers above exist; the invoice
# lifecycle helpers are re-exported for modules and tests. ---
from contaautonomo import routes  # noqa: E402,F401
from contaautonomo.routes.invoices import (  # noqa: E402,F401
    NoExchangeRate, _invoice_locked, _issue_invoice, _mark_invoice_paid, invoice_amounts,
)
from contaautonomo.routes.messages import _message_log_link  # noqa: E402,F401


if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    app.logger.setLevel(logging.INFO)

    bootstrap()

    # Graceful shutdown: on SIGTERM/SIGINT stop the scheduler so an
    # in-flight job (e.g. a backup) isn't killed mid-write, then exit.
    import signal

    def _graceful_shutdown(signum, _frame):
        logger.info('Shutdown signal %s received — stopping scheduler', signum)
        try:
            shutdown()
        except Exception as e:
            logger.warning('Scheduler stop during shutdown failed: %s', e)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '5000')),
            debug=os.environ.get('FLASK_DEBUG', '1') == '1')
