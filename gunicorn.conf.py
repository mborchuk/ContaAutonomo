"""
gunicorn settings for ContaAutónomo.

gunicorn reads ./gunicorn.conf.py automatically, so `gunicorn app:app` from
the repository root (or /app in the image) picks this file up; from anywhere
else pass `-c /path/to/gunicorn.conf.py`.

Each worker initialises the app once after loading it (post_worker_init →
app.bootstrap()): database migrations, modules, scheduler. Only one process
per deployment runs scheduled jobs (file lock in instance/scheduler.lock).

Defaults: one worker with threads. Rate-limit counters, login lockout and
caches live in process memory, so a single worker keeps them consistent;
with GUNICORN_WORKERS above 1 each worker counts separately.
"""
import os

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5000')
workers = int(os.environ.get('GUNICORN_WORKERS', '1'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = 120
accesslog = '-'
errorlog = '-'


def post_worker_init(worker):
    from app import bootstrap
    bootstrap()


def worker_exit(server, worker):
    from app import shutdown
    shutdown()
