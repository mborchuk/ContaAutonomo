"""System routes: health probe, installable-app manifest and service worker, logs, scheduled tasks, module toggle.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …). They import from `contaautonomo.web`, never from
`contaautonomo.app`, and read `state.module_manager` when a request runs."""
import os

from flask import (flash, redirect, render_template, request, url_for)

from contaautonomo import state
from contaautonomo.web import (app, db, logger, login_required, log_activity)
from contaautonomo.routes.messages import _message_log_link


# --- Health probe: checks DB + storage, for Docker/uptime monitors ---
@app.route('/health')
def health_check():
    from flask import jsonify
    from sqlalchemy import text
    checks = {'db': False, 'storage': False}
    try:
        db.session.execute(text('SELECT 1'))
        checks['db'] = True
    except Exception as e:
        # Probe only: DB unreachable is reported via the 503 below, not raised.
        logger.warning('health: db probe failed: %s', e)
    try:
        if state.module_manager is not None:
            state.module_manager.core.storage.exists('__health_probe__')
        checks['storage'] = True
    except Exception as e:
        # Probe only: storage backend unreachable is reported via the 503.
        logger.warning('health: storage probe failed: %s', e)
    return jsonify(checks), (200 if all(checks.values()) else 503)


# --- Installable app (G-8): manifest and service worker, no login needed ---
# Both are public and contain no user data. The share target and the capture
# shortcut are offered only while the Expenses module is loaded.
@app.route('/manifest.webmanifest')
def web_manifest():
    from flask import jsonify
    manifest = {
        'id': '/',
        'name': 'ContaAutónomo',
        'short_name': 'ContaAutónomo',
        'description': 'Invoices, expenses and Spanish tax estimates for an autónomo.',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#f1f5f9',
        'theme_color': '#4f46e5',
        'icons': [
            {'src': url_for('static', filename='icons/icon-192.png'),
             'sizes': '192x192', 'type': 'image/png'},
            {'src': url_for('static', filename='icons/icon-512.png'),
             'sizes': '512x512', 'type': 'image/png'},
            {'src': url_for('static', filename='icons/icon-maskable-512.png'),
             'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    }
    if state.module_manager is not None and state.module_manager.provider_of('expenses') is not None:
        manifest['shortcuts'] = [{'name': 'Capture a receipt', 'url': '/expenses/capture',
                                  'icons': [manifest['icons'][0]]}]
        manifest['share_target'] = {
            'action': '/expenses/capture/share',
            'method': 'POST',
            'enctype': 'multipart/form-data',
            'params': {'files': [{'name': 'receipt',
                                  'accept': ['image/*', '.jpg', '.jpeg', '.png']}]},
        }
    response = jsonify(manifest)
    response.mimetype = 'application/manifest+json'
    return response


@app.route('/sw.js')
def service_worker():
    # Served from the site root so its scope is the whole app; never cached by
    # the browser's HTTP cache, so an update reaches clients on the next load.
    from flask import send_from_directory
    response = send_from_directory(os.path.join(app.root_path, 'static', 'js'), 'sw.js',
                                   mimetype='text/javascript', max_age=0)
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


# ============================================================================
# SYSTEM LOGS
# ============================================================================

@app.route('/logs')
@login_required
def system_logs():
    """View system activity logs"""
    cat = request.args.get('category', '')
    search = request.args.get('search', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = 50

    logger = state.module_manager.core.activity_logger if state.module_manager else None
    entries = []
    categories = []
    if logger and hasattr(logger, 'get_entries'):
        entries = logger.get_entries(
            limit=per_page, offset=(page - 1) * per_page,
            category=cat or None,
            date_from=date_from or None,
            date_to=date_to or None,
            search=search or None)
        if hasattr(logger, 'get_categories'):
            categories = logger.get_categories()

    for entry in entries:
        entry['message_link'] = _message_log_link(entry)

    return render_template('logs.html',
                           entries=entries,
                           categories=categories,
                           filters={'category': cat, 'search': search,
                                    'date_from': date_from, 'date_to': date_to},
                           page=page, per_page=per_page)


@app.route('/scheduler')
@login_required
def scheduled_tasks():
    """View registered scheduled tasks"""
    jobs = []
    if state.module_manager:
        jobs = state.module_manager.core.scheduler.get_jobs()
    return render_template('scheduler.html', jobs=jobs)


# ============================================================================
# MODULE MANAGEMENT ROUTES
# ============================================================================

@app.route('/modules/toggle/<module_id>', methods=['POST'])
@login_required
def toggle_module(module_id):
    """Enable or disable a module"""
    if not state.module_manager:
        flash('Module system not initialized', 'danger')
        return redirect(url_for('settings') + '#modules')

    if module_id not in state.module_manager.discovered:
        flash('Unknown module.', 'danger')
        return redirect(url_for('settings') + '#modules')

    action = request.form.get('action', 'enable')
    if action == 'enable':
        enabled = state.module_manager.enable_module(module_id)
        for mid in enabled:
            log_activity('module_enabled', 'system', mid)
        also = [m for m in enabled if m != module_id]
        flash('Module enabled' + (f' together with the modules it needs: {", ".join(also)}' if also else '')
              + '. Please restart the application for changes to take effect.', 'success')
    else:
        needed_by = state.module_manager.disable_module(module_id)
        if needed_by:
            flash(f'Not disabled: needed by {", ".join(needed_by)}. Disable those first.', 'warning')
        else:
            log_activity('module_disabled', 'system', module_id)
            flash('Module disabled. Please restart the application for changes to take effect.', 'success')

    return redirect(url_for('settings') + '#modules')
