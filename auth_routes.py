#!/usr/bin/env python3
"""
Authentication Routes Module

Supports pluggable auth providers. The login page renders forms/buttons
for each registered provider. On POST, the selected provider handles
authentication.
"""

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session)
from functools import wraps
from auth import auth_manager, auth_service
import logging

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def _store_session_identity(identity):
    """Store auth identity in session after successful login."""
    session['authenticated'] = True
    session['auth_provider'] = identity.get('provider', 'password')
    session['auth_identity'] = {
        'name': identity.get('name', ''),
        'email': identity.get('email', ''),
        'avatar_url': identity.get('avatar_url', ''),
        'provider': identity.get('provider', 'password'),
    }
    session.permanent = True

    # Store encryption token if password-based
    if identity.get('provider') == 'password':
        try:
            password = request.form.get('password', '')
            if password:
                key = auth_manager.get_encryption_key(password)
                session['_enc_token'] = key.decode('utf-8') if isinstance(key, bytes) else key
        except Exception:
            session.pop('_enc_token', None)


def get_encryption_password():
    """Get encryption key from session for backup operations."""
    return session.get('_enc_token') or session.get('_password')


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-time setup page (password-based)."""
    if not auth_manager.is_first_run():
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not password or len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
            return render_template('setup.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('setup.html')

        try:
            auth_manager.setup_password(password)
            _store_session_identity({'name': 'Admin', 'provider': 'password'})

            # Store encryption token
            try:
                key = auth_manager.get_encryption_key(password)
                session['_enc_token'] = key.decode('utf-8') if isinstance(key, bytes) else key
            except Exception:
                pass  # encryption token not critical for setup

            flash('Account created successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error('Setup error: %s', e)
            flash('Error setting up account. Check server logs.', 'danger')
            return render_template('setup.html')

    return render_template('setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page — supports multiple auth providers."""
    if auth_manager.is_first_run():
        return redirect(url_for('auth.setup'))

    if session.get('authenticated'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Determine which provider to use
        provider_id = request.form.get('auth_provider', 'password')
        result = auth_service.authenticate(provider_id, request)

        if result.redirect_url:
            # OAuth flow — redirect to external IdP
            return redirect(result.redirect_url)

        if result.success:
            _store_session_identity(result.identity)

            # Notify modules
            _notify_modules_authenticated(result.identity)

            flash('Login successful!', 'success')
            try:
                from app import log_activity
                log_activity('login', 'auth',
                             f'provider={result.identity.get("provider", "?")}')
            except Exception:
                pass  # log_activity may not be available during early init
            return redirect(url_for('dashboard'))
        else:
            logger.warning('Failed login attempt from %s via %s',
                           request.remote_addr.replace('\n', '').replace('\r', ''),
                           provider_id)
            flash(result.error or 'Authentication failed.', 'danger')

    # Render login page with all available providers
    providers = auth_service.available_providers()
    return render_template('login.html', auth_providers=providers)


@auth_bp.route('/logout')
def logout():
    """Logout — notify providers and modules."""
    try:
        from app import log_activity
        log_activity('logout', 'auth')
    except Exception:
        pass  # log_activity may not be available during early init

    auth_service.on_logout(session)
    _notify_modules_logout()
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/security', methods=['GET', 'POST'])
@login_required
def security():
    """Security settings page (password change)."""
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password or not new_password:
                flash('All fields are required.', 'danger')
                return render_template('security.html')

            if len(new_password) < 8:
                flash('New password must be at least 8 characters long.', 'danger')
                return render_template('security.html')

            if new_password != confirm_password:
                flash('New passwords do not match.', 'danger')
                return render_template('security.html')

            try:
                auth_manager.change_password(current_password, new_password)

                # Update encryption token
                try:
                    key = auth_manager.get_encryption_key(new_password)
                    session['_enc_token'] = key.decode('utf-8') if isinstance(key, bytes) else key
                except Exception:
                    pass  # encryption token update not critical

                flash('Password changed successfully!', 'success')
                try:
                    from app import log_activity
                    log_activity('password_changed', 'auth')
                except Exception:
                    pass  # logging failure should not block password change
            except Exception as e:
                logger.error('Password change error: %s', e)
                flash('Error changing password. Check current password.', 'danger')

    return render_template('security.html')


def _notify_modules_authenticated(identity):
    """Notify all modules that a user authenticated."""
    try:
        from app import module_manager
        if module_manager:
            for mod in module_manager.modules.values():
                try:
                    mod.on_user_authenticated(identity)
                except Exception as e:
                    logger.debug('Module %s on_user_authenticated error: %s',
                                 mod.module_id, e)
    except ImportError:
        pass  # app not ready yet


def _notify_modules_logout():
    """Notify all modules that a user logged out."""
    try:
        from app import module_manager
        if module_manager:
            for mod in module_manager.modules.values():
                try:
                    mod.on_user_logout()
                except Exception as e:
                    logger.debug('Module %s on_user_logout error: %s',
                                 mod.module_id, e)
    except ImportError:
        pass  # app not ready yet
