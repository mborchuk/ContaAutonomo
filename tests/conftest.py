"""Pytest fixtures: a Flask app + in-memory SQLite DB.

DATABASE_URL and FLASK_DEBUG are set *before* importing app so the app binds to
an in-memory database and boots in dev mode (no SECRET_KEY hard-fail).
"""
import os

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('FLASK_DEBUG', '1')

import pytest

from app import app as flask_app, db as _db  # noqa: E402


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
