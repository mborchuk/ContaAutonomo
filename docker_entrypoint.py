#!/usr/bin/env python3
"""
Docker entrypoint — initializes DB, runs migrations, loads modules,
then starts gunicorn.

All schema migrations are handled by app.py and module on_enable() hooks.
This entrypoint just triggers initialization and starts the server.
"""
import os


def init():
    """Run DB migrations. Module init happens automatically on app import."""
    from app import app, db, Settings
    from sqlalchemy import inspect as sa_inspect, text

    with app.app_context():
        db.create_all()

        # Auto-migrate settings columns
        inspector = sa_inspect(db.engine)
        if 'settings' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('settings')]
            migrations = [
                ('social_security_monthly', "FLOAT DEFAULT 0.0"),
                ('log_path', "VARCHAR(500) DEFAULT ''"),
                ('log_retention_days', 'INTEGER DEFAULT 30'),
                ('log_use_external_storage', 'BOOLEAN DEFAULT 0'),
                ('log_storage', "VARCHAR(10) DEFAULT 'file'"),
            ]
            for col, typedef in migrations:
                if col not in columns:
                    with db.engine.connect() as conn:
                        conn.execute(text(
                            f'ALTER TABLE settings ADD COLUMN {col} {typedef}'))
                        conn.commit()

        # Auto-migrate invoice columns
        if 'invoice' in inspector.get_table_names():
            inv_columns = [c['name'] for c in inspector.get_columns('invoice')]
            if 'pdf_storage_key' not in inv_columns:
                with db.engine.connect() as conn:
                    conn.execute(text(
                        'ALTER TABLE invoice ADD COLUMN pdf_storage_key VARCHAR(500)'))
                    conn.commit()

        # Ensure default Settings row exists (first run)
        s = Settings.query.first()
        if not s:
            s = Settings(
                tracked_currencies='USD,EUR,GBP,CZK',
                base_currency='EUR',
                default_currency='EUR',
                default_vat_rate=21.0,
                default_irpf_rate=20.0,
            )
            db.session.add(s)
            db.session.commit()
            print('[entrypoint] Created default Settings row (first run)')

    print('[entrypoint] DB initialized.')


if __name__ == '__main__':
    init()

    workers = os.environ.get('GUNICORN_WORKERS', '2')
    bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5000')

    cmd = [
        'gunicorn',
        'app:app',
        '--bind', bind,
        '--workers', workers,
        '--timeout', '120',
        '--access-logfile', '-',
        '--error-logfile', '-',
    ]

    print(f'[entrypoint] Starting gunicorn: {" ".join(cmd)}')
    os.execvp('gunicorn', cmd)
