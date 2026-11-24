"""The Flask app object and what every route needs: configuration, security
(CSRF, rate limits, headers, sessions, trusted proxies), error handlers,
`db`, `login_required`, `log_activity` and the settings helpers.

It imports neither the models nor the routes: `contaautonomo.app` puts the
app together (models, routes, start-up) on top of it, and the route files in
`routes/` import from here, so there is no import cycle.
"""
from flask import Flask, request, redirect, url_for, flash
import os
import hashlib


from contaautonomo import state
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

# Named after the app module, as before the split, so log lines keep their name.
logger = logging.getLogger('contaautonomo.app')


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

def log_activity(action, category='system', details=None):
    """Log user/system activity via core logger (safe to call before init)"""
    if state.module_manager:
        state.module_manager.core.log_activity(action, category, details)


# Login required decorator (security/login.py; modules use core.login_required)
from contaautonomo.security.login import login_required  # noqa: E402,F401 — routes import it from here



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
