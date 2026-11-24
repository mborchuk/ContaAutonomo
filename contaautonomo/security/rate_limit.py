"""
The application's rate limiter.

Created unbound so route modules (auth_routes.py, modules) can decorate their
views at definition time; app.py binds it with `limiter.init_app(app)`.
Flask-Limiter enforces a decorated limit inside the wrapper it returns, so
the decorator must sit under the route decorator — wrapping a view after
its blueprint is registered has no effect.

Counters live in RATELIMIT_STORAGE_URI (default memory://, per process).
With more than one gunicorn worker, point it at a shared store supported by
Flask-Limiter (for example redis://host:6379, which needs the redis package).
"""
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    default_limits=[],
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
)
