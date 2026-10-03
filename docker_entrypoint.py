#!/usr/bin/env python3
"""
Docker entrypoint — initializes the database once, then starts gunicorn.

Schema migrations run through app.init_database() (schema_migrations.py),
the same routine `python app.py` and gunicorn workers use. Module tables are
migrated by each module's on_enable() when it loads.
"""
import os


def init():
    """Create tables, apply core migrations, ensure the Settings row."""
    from app import app, init_database

    with app.app_context():
        init_database()

    print('[entrypoint] DB initialized.')


if __name__ == '__main__':
    init()

    # Bind address, worker count and per-worker initialisation come from
    # gunicorn.conf.py (GUNICORN_BIND, GUNICORN_WORKERS, GUNICORN_THREADS).
    cmd = ['gunicorn', '--config', 'gunicorn.conf.py', 'app:app']
    print(f'[entrypoint] Starting gunicorn: {" ".join(cmd)}')
    os.execvp('gunicorn', cmd)
