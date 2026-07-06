"""Pytest fixtures: a Flask app + in-memory SQLite DB.

DATABASE_URL and FLASK_DEBUG are set *before* importing app so the app binds to
an in-memory database and boots in dev mode (no SECRET_KEY hard-fail).
"""
import os

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('FLASK_DEBUG', '1')

import pytest

from app import app as flask_app, db as _db  # noqa: E402
from module_manager import ModuleManager  # noqa: E402


@pytest.fixture
def app():
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    with flask_app.app_context():
        _db.create_all()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def loaded_modules(_loaded_modules_session):
    """Function-scoped wrapper: other tests' `app` fixture drops all tables on
    teardown, so re-create them (idempotent) before each loaded-modules test."""
    _db.create_all()
    return _loaded_modules_session


@pytest.fixture(scope='session')
def _loaded_modules_session():
    """One shared ModuleManager for the whole session.

    Building more than one ModuleManager redefines the singleton
    `module_enabled` table in the shared metadata and errors, so every test that
    needs loaded modules goes through this single instance. Enables the modules
    the suite exercises (expenses, tax_es_forms).
    """
    import sys
    from app import Settings
    appmod = sys.modules['app']

    ctx = flask_app.app_context()
    ctx.push()
    _db.create_all()

    mm = ModuleManager(flask_app, _db)
    mm.core._settings_model = Settings
    mm.init_db()
    mm.discover_modules()

    enabled_model = mm._get_module_enabled_model()
    for module_id in ('expenses', 'tax_es_forms', 'fiscal_calendar',
                      'recurring_invoices', 'reta_advisor', 'invoice_email'):
        if not enabled_model.query.filter_by(module_id=module_id).first():
            _db.session.add(enabled_model(module_id=module_id, enabled=True))
    _db.session.commit()
    mm.load_enabled_modules()

    appmod.module_manager = mm
    flask_app.jinja_env.globals['module_manager'] = mm
    flask_app.jinja_env.globals.setdefault('app_version', 'test')

    yield mm

    _db.session.remove()
    ctx.pop()
