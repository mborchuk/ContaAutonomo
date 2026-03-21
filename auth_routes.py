#!/usr/bin/env python3
"""
Authentication Routes Module
Handles login, logout, setup, and password management
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from auth import auth_manager

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


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-time setup page"""
    # If already setup, redirect to login
    if not auth_manager.is_first_run():
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Validate
        if not password or len(password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
            return render_template('setup.html')

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('setup.html')

        try:
            # Setup password
            auth_manager.setup_password(password)

            # Auto-login
            session['authenticated'] = True
            session['_password'] = password  # Store password for encryption
            session.permanent = True

            flash('Account created successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash(f'Error setting up account: {str(e)}', 'danger')
            return render_template('setup.html')

    return render_template('setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    # If first run, redirect to setup
    if auth_manager.is_first_run():
        return redirect(url_for('auth.setup'))

    # If already logged in, redirect to dashboard
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        password = request.form.get('password')

        if auth_manager.verify_password(password):
            session['authenticated'] = True
            session.permanent = True

            # Store password in session for encryption operations
            # Note: In production, consider using a more secure method
            session['_password'] = password

            flash('Login successful!', 'success')
            # Log activity
            from app import log_activity
            log_activity('login', 'auth')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid password', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    """Logout"""
    from app import log_activity
    log_activity('logout', 'auth')
    session.clear()
    flash('You have been logged out', 'success')
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

            # Validate
            if not current_password or not new_password:
                flash('All fields are required', 'danger')
                return render_template('security.html')

            if len(new_password) < 8:
                flash('New password must be at least 8 characters long', 'danger')
                return render_template('security.html')

            if new_password != confirm_password:
                flash('New passwords do not match', 'danger')
                return render_template('security.html')

            try:
                auth_manager.change_password(current_password, new_password)

                # Update session password
                session['_password'] = new_password

                flash('Password changed successfully!', 'success')
                try:
                    from app import log_activity
                    log_activity('password_changed', 'auth')
                except Exception:
                    pass
            except Exception as e:
                flash(f'Error changing password: {str(e)}', 'danger')

    return render_template('security.html')
