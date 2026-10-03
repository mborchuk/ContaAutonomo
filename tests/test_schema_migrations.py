"""Core schema migrations: one idempotent path for every entry point.

The fixtures in tests/fixtures/ are the DDL that the v1.0.0 (oldest release)
and v1.4.0 (last release before the F2 invoice columns) entrypoints created.
They hold no data; the rows inserted below are synthetic.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures'
RELEASE_SCHEMAS = ['schema_v1_0_0.sql', 'schema_v1_4_0.sql']


def _core_tables():
    import app as core
    models = (core.Settings, core.Customer, core.Bank, core.Contractor,
              core.Invoice, core.InvoiceItem, core.Expense, core.TaxForm,
              core.Document, core.SSPayment)
    # Mapper columns, not table columns: modules extend some core tables
    # (extend_existing=True) with columns they migrate themselves.
    return {m.__table__.name: {c.name for c in m.__mapper__.columns}
            for m in models}


def _columns(db_path, table):
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _release_db(tmp_path, schema):
    db_path = tmp_path / 'release.db'
    with sqlite3.connect(db_path) as conn:
        conn.executescript((FIXTURES / schema).read_text())
        conn.execute("INSERT INTO settings (id, base_currency) VALUES (1, 'EUR')")
        conn.execute(
            "INSERT INTO invoice (invoice_number, client_name, amount_usd, "
            "amount_eur, exchange_rate, invoice_date, status) VALUES "
            "('T-0001', 'ACME Synthetic SL', 100, 100, 1, '2026-01-15', 'draft')")
    return db_path


def _assert_current_schema(db_path):
    for table, expected in _core_tables().items():
        missing = expected - _columns(db_path, table)
        assert not missing, f'{table} is missing {sorted(missing)}'


@pytest.mark.parametrize('schema', RELEASE_SCHEMAS)
def test_run_migrations_upgrades_release_schema(app, tmp_path, schema):
    from app import db
    from schema_migrations import run_migrations

    db_path = _release_db(tmp_path, schema)
    engine = create_engine(f'sqlite:///{db_path}')
    try:
        assert run_migrations(engine, db.metadata), 'expected columns to be added'
        _assert_current_schema(db_path)
        # Idempotent: a second run changes nothing.
        assert run_migrations(engine, db.metadata) == []
    finally:
        engine.dispose()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            'SELECT invoice_number, series FROM invoice').fetchall() == [('T-0001', None)]


def test_run_migrations_fresh_database(app, tmp_path):
    from app import db
    from schema_migrations import run_migrations

    db_path = tmp_path / 'fresh.db'
    engine = create_engine(f'sqlite:///{db_path}')
    try:
        assert run_migrations(engine, db.metadata) == []
        _assert_current_schema(db_path)
    finally:
        engine.dispose()


def test_core_columns_cover_every_model_column_missing_from_oldest_release(app):
    """A new core model column must be listed in CORE_COLUMNS."""
    from schema_migrations import CORE_COLUMNS

    oldest = sqlite3.connect(':memory:')
    oldest.executescript((FIXTURES / RELEASE_SCHEMAS[0]).read_text())
    for table, expected in _core_tables().items():
        have = {r[1] for r in oldest.execute(f'PRAGMA table_info("{table}")')}
        listed = {name for name, _ in CORE_COLUMNS.get(table, [])}
        assert expected - have <= listed, (
            f'{table}: add {sorted(expected - have - listed)} to CORE_COLUMNS')


@pytest.mark.parametrize('schema', RELEASE_SCHEMAS)
@pytest.mark.parametrize('workers', [None, '2'])
def test_docker_entrypoint_upgrades_release_schema(tmp_path, schema, workers):
    """The container start path migrates an existing database (pitfall 1.1)."""
    db_path = _release_db(tmp_path, schema)
    env = {k: v for k, v in os.environ.items() if k != 'GUNICORN_WORKERS'}
    env.update(SECRET_KEY='test-only-not-secret', FLASK_DEBUG='0',
               DATABASE_URL=f'sqlite:///{db_path}',
               AUTH_CONFIG_PATH=str(tmp_path / 'auth_config.json'))
    if workers:
        env['GUNICORN_WORKERS'] = workers
    result = subprocess.run(
        [sys.executable, '-c', 'import docker_entrypoint; docker_entrypoint.init()'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert 'Auto-init failed' not in result.stderr
    _assert_current_schema(db_path)
