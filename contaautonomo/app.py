#!/usr/bin/env python3
"""
The ContaAutónomo app, put together: the Flask app and its security
(`web.py`), the models (`models.py`), the routes (`routes/`, one file per
area) and start-up (`init_database`, `init_module_manager`, `bootstrap`,
`shutdown`). Gunicorn serves `contaautonomo.app:app`; the development server
is `python -m contaautonomo.app`.

Modules and tests import from here — `from contaautonomo.app import Invoice,
db` — so the names they use are re-exported (`__all__`). The module manager
lives in `state.module_manager`; reading `contaautonomo.app.module_manager`
still works.
"""
import importlib
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime

# When started with `python -m contaautonomo.app`, this module is '__main__'.
# Register it under its import name as well, *before* any
# `from contaautonomo.app import ...` runs (here or in plugin modules /
# InvoiceService). Otherwise that import re-executes this file as a separate
# module with a second SQLAlchemy()/db, and ORM calls made through the import
# fail with "current Flask app is not registered with this 'SQLAlchemy'
# instance". Aliasing keeps a single module/db.
if __name__ == '__main__':
    sys.modules.setdefault('contaautonomo.app', sys.modules['__main__'])

from contaautonomo import state
from contaautonomo.web import (  # noqa: F401 — re-exported
    APP_VERSION, _apply_currency_provider, _apply_log_settings, app, csrf, db, limiter,
    log_activity, logger,
)
from contaautonomo.security.login import login_required  # noqa: F401 — re-exported
from contaautonomo.models import (  # noqa: F401 — re-exported
    Bank, Contractor, Customer, Document, Expense, Invoice, InvoiceItem, Message,
    Settings, SSPayment, TaxForm, find_customer_by_name,
)
from contaautonomo.security.secret_box import encrypt_existing
importlib.import_module('contaautonomo.routes')   # registers the routes on app
from contaautonomo.routes.invoices import (  # noqa: E402,F401 — re-exported
    NoExchangeRate, _invoice_locked, _issue_invoice, _mark_invoice_paid, invoice_amounts,
)
from contaautonomo.routes.messages import _message_log_link  # noqa: E402,F401 — re-exported

__all__ = [
    'app', 'db', 'csrf', 'limiter', 'logger', 'login_required', 'log_activity', 'APP_VERSION',
    'Bank', 'Contractor', 'Customer', 'Document', 'Expense', 'Invoice', 'InvoiceItem',
    'Message', 'Settings', 'SSPayment', 'TaxForm', 'find_customer_by_name',
    'NoExchangeRate', 'invoice_amounts', '_invoice_locked', '_issue_invoice',
    '_mark_invoice_paid', '_message_log_link',
    'init_database', 'init_module_manager', 'bootstrap', 'shutdown',
]


def __getattr__(name):
    if name == 'module_manager':   # 2.x code reads contaautonomo.app.module_manager
        return state.module_manager
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


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
    from contaautonomo.core.module_manager import ModuleManager

    module_manager = ModuleManager(app, db)
    state.module_manager = module_manager
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


def bootstrap():
    """Prepare this process to serve: database, modules, scheduler, settings.

    Called once per process by every serving entry point — `python app.py`
    and each gunicorn worker (post_worker_init in gunicorn.conf.py) — and a
    no-op on repeated calls. Importing app.py alone initialises nothing, so
    tests and tools can import it safely.
    """
    if state.module_manager is not None:
        return state.module_manager
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
    if state.module_manager is not None:
        state.module_manager.core.scheduler.stop()


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
