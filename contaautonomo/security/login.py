"""The login check every protected route uses, core and modules alike
(`core.login_required`). It reads only the session, so it lives apart from
the app and the module manager can offer it without importing the app."""
from functools import wraps

from flask import redirect, session, url_for


def login_required(f):
    """Decorator: send a visitor who is not logged in to the login page."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function
