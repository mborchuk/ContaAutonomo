#!/usr/bin/env python3
"""
Docker entrypoint — initializes the database once, then starts gunicorn.

Schema migrations run through contaautonomo.app.init_database()
(contaautonomo/core/schema_migrations.py), the same routine
`python -m contaautonomo.app` and gunicorn workers use. Module tables are
migrated by each module's on_enable() when it loads. Run from the repository
root (`/app` in the image) with it on PYTHONPATH.
"""
import os


def init():
    """Create tables, apply core migrations, ensure the Settings row."""
    from contaautonomo.app import app, init_database

    with app.app_context():
        init_database()

    print('[entrypoint] DB initialized.')


if __name__ == '__main__':
    init()

    # Bind address, worker count and per-worker initialisation come from
    # deploy/gunicorn.conf.py (GUNICORN_BIND, GUNICORN_WORKERS, GUNICORN_THREADS).
    config = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gunicorn.conf.py')
    cmd = ['gunicorn', '--config', config, 'contaautonomo.app:app']
    print(f'[entrypoint] Starting gunicorn: {" ".join(cmd)}')
    os.execvp('gunicorn', cmd)
