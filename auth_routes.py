#!/usr/bin/env python3
"""
Authentication Routes Module
Handles login, logout, setup, and password management
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from auth import auth_manager
import logging

logger = logging.getLogger(__name__)

# Create Blueprint
auth_bp = Blueprint('auth', __name__)


def login_required(f):
    """Decorator to require login for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def _store_encryption_token(password):
    """Derive and store an encryption token in session (never the raw password)."""
    try:
        key = auth_manager.get_encryption_key(password)
        session['_enc_token'] = key.decode('utf-8') if isinstance(key, bytes) else key
    except Exception:
        session.pop('_enc_token', None)


def get_encryption_password():
    """Get encryption key from session for backup operations.

    Returns the derived encryption key, or None if not available.
    Modules that need encryption should call this instead of session['_password'].
    """
    return session.get('_enc_token')


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-time setup page"""
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

            session['authenticated'] = True
            session.permanent = True
            _store_encryption_token(password)

            flash('Account created successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error('Setup error: %s', e)
            flash('Error setting up account. Check server logs.', 'danger')
            return render_template('setup.html')

    return render_template('setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if auth_manager.is_first_run():
        return redirect(url_for('auth.setup'))

    if session.get('authenticated'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        password = request.form.get('password')

        if auth_manager.verify_password(password):
            session['authenticated'] = True
            session.permanent = True
            _store_encryption_token(password)

            flash('Login successful!', 'success')
            try:
                from app import log_activity
                log_activity('login', 'auth')
            except Exception:
                pass  # log_activity may not be available during early init
            return redirect(url_for('dashboard'))
        else:
            logger.warning('Failed login attempt from %s',
                           request.remote_addr.replace('\n', '').replace('\r', ''))
            flash('Invalid password.', 'danger')

    return render_template('login.html')


# Apply rate limiting if available (deferred to avoid circular import)
def _apply_rate_limits():
    """Apply rate limits after app is fully initialized."""
    global login, setup
    try:
        from app import limiter
        if limiter:
            login = limiter.limit('5/minute')(login)
            setup = limiter.limit('3/minute')(setup)
    except (ImportError, RuntimeError, AttributeError):
        pass  # limiter not available or app not ready yet


@auth_bp.route('/logout')
def logout():
    """Logout"""
    try:
        from app import log_activity
        log_activity('logout', 'auth')
    except Exception:
        pass  # log_activity may not be available during early init
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/security', methods=['GET', 'POST'])
@login_required
def security():
    """Security settings page"""
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
                _store_encryption_token(new_password)

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
