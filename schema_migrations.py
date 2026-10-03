"""
Core schema migrations — the one routine every entry point runs.

`python app.py`, `docker_entrypoint.py` and gunicorn workers all call
`run_migrations()` (through `app.init_database()`), so a core column added
here reaches every installation, whatever way it is started.

There is no migration framework. `create_all()` builds missing tables with
their current columns; existing tables only gain the columns listed in
`CORE_COLUMNS`. Rules for a new core column:

- add it to the model in app.py AND to `CORE_COLUMNS` below;
- additive only: nullable, or with a DEFAULT (SQLite cannot add a NOT NULL
  column without one); never rename or drop;
- tests/test_schema_migrations.py fails if a core model column is missing
  after upgrading the oldest supported schema.

Module tables keep their own migrations in the module's `on_enable()`.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

# Columns added to core tables after their table first shipped, oldest first.
# Type definitions match the ALTER statements that shipped before this file
# existed, so upgraded and fresh databases end up with the same defaults.
CORE_COLUMNS = {
    'settings': [
        ('social_security_monthly', 'FLOAT DEFAULT 0.0'),
        ('log_path', "VARCHAR(500) DEFAULT ''"),
        ('log_retention_days', 'INTEGER DEFAULT 30'),
        ('log_use_external_storage', 'BOOLEAN DEFAULT 0'),
        ('log_storage', "VARCHAR(10) DEFAULT 'file'"),
        ('default_vat_rate', 'FLOAT DEFAULT 21.0'),
        ('default_irpf_rate', 'FLOAT DEFAULT 20.0'),
        ('currency_provider', "VARCHAR(50) DEFAULT 'ecb'"),
        ('currency_provider_api_key', "VARCHAR(200) DEFAULT ''"),
        ('payment_methods',
         "TEXT DEFAULT 'Bank Transfer,PayPal,Credit Card,Cash,Crypto'"),
    ],
    'invoice': [
        ('pdf_storage_key', 'VARCHAR(500)'),
        # F2 — invoice lifecycle: series/sequence, rectificative links,
        # fiscal snapshot frozen at issue time.
        ('series', 'VARCHAR(20)'),
        ('sequence_number', 'INTEGER'),
        ('issued_at', 'DATETIME'),
        ('rectifies_invoice_id', 'INTEGER'),
        ('rectification_type', 'VARCHAR(20)'),
        ('snap_vat_rate', 'FLOAT'),
        ('snap_vat_amount', 'FLOAT'),
        ('snap_taxable_base', 'FLOAT'),
        ('snap_customer', 'TEXT'),
        # IRPF retención withheld by Spanish B2B clients (tax_es_irpf).
        ('irpf_retention_pct', 'FLOAT DEFAULT 0'),
        ('irpf_retention_amount', 'FLOAT DEFAULT 0'),
    ],
    'invoice_item': [
        # F2-D4 — per-line VAT rate.
        ('vat_rate', 'FLOAT'),
    ],
    'expense': [
        # F4 — VAT breakdown. No defaults on purpose: NULL on legacy rows
        # means "VAT unknown" (same definitions as the expenses module).
        ('net_amount', 'FLOAT'),
        ('vat_rate', 'FLOAT'),
        ('vat_amount', 'FLOAT'),
        ('deductible', 'BOOLEAN'),
        ('deductible_pct', 'FLOAT'),
    ],
}


def run_migrations(engine, metadata):
    """Bring the database up to the current core schema. Idempotent.

    Safe to run concurrently from several processes: a column another process
    added in the meantime is skipped. Returns the list of (table, column)
    pairs this call added.
    """
    metadata.create_all(engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added = []
    for table, columns in CORE_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        for name, typedef in columns:
            if name in existing:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE {table} ADD COLUMN {name} {typedef}'))
            except OperationalError as e:
                if 'duplicate column name' not in str(e).lower():
                    raise
                continue  # added by a concurrent process
            added.append((table, name))
            logger.info('Schema migration: added %s.%s', table, name)
    return added
