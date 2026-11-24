"""Message center pages: the top-bar bell (context processor), System → Messages, mark read or unread, delete.

Routes register on the core app under their own endpoint names
(`url_for('dashboard')`, …); `core_app.module_manager` is read when a request
runs, so it is always the current one."""

from flask import (flash, redirect, render_template, request, session, url_for)

import contaautonomo.app as core_app
from contaautonomo.app import (app, db, logger, login_required)


# ============================================================================
# MESSAGES (message center: top-bar bell + history page; message_center.py)
# ============================================================================

def _message_center():
    return getattr(core_app.module_manager.core, 'message_center', None) if core_app.module_manager else None


@app.context_processor
def inject_message_bell():
    """Unread messages for the top-bar bell, on every page of a signed-in
    owner. A failure leaves the bell empty instead of breaking the page."""
    center = _message_center()
    if center is None or not session.get('authenticated'):
        return {'message_bell': None}
    try:
        return {'message_bell': {'count': center.unread_count(), 'items': center.unread()}}
    except Exception:
        logger.warning('Message bell could not be loaded', exc_info=True)
        db.session.rollback()
        return {'message_bell': None}


@app.template_filter('message_html')
def message_html_filter(text):
    from contaautonomo.core.message_center import render_text
    return render_text(text)


def _message_done(message):
    """Answer a message action: JSON for the bell's script, else back to the
    Messages page, keeping its `show` filter and `page` from the form. No
    address is taken from the request, so nothing can redirect elsewhere."""
    center = _message_center()
    if request.accept_mimetypes.best == 'application/json' or request.is_json:
        if message is None:
            return {'error': 'Message not found'}, 404
        return {'id': message.id, 'read': message.read_at is not None,
                'unread_count': center.unread_count()}
    if message is None:
        flash('That message does not exist or was deleted.', 'warning')
    show = 'unread' if request.form.get('show') == 'unread' else None
    try:
        page = max(1, int(request.form.get('page', 1)))
    except ValueError:
        page = 1
    return redirect(url_for('messages_index', show=show, page=page if page > 1 else None))


@app.route('/messages')
@login_required
def messages_index():
    """Message history: every message not deleted, newest first."""
    center = _message_center()
    show = 'unread' if request.args.get('show') == 'unread' else 'all'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    rows, total = center.history(unread_only=(show == 'unread'), page=page) if center else ([], 0)
    return render_template('messages.html', messages=rows, total=total, show=show,
                           page=page, per_page=50)


@app.route('/messages/<int:message_id>/read', methods=['POST'])
@login_required
def messages_mark_read(message_id):
    return _message_done(_message_center().mark_read(message_id))


@app.route('/messages/<int:message_id>/unread', methods=['POST'])
@login_required
def messages_mark_unread(message_id):
    return _message_done(_message_center().mark_unread(message_id))


@app.route('/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def messages_delete(message_id):
    message = _message_center().delete(message_id)
    if message is not None and not (request.accept_mimetypes.best == 'application/json'):
        flash(f'Message #{message.id} deleted.', 'success')
    return _message_done(message)


@app.route('/messages/read-all', methods=['POST'])
@login_required
def messages_mark_all_read():
    count = _message_center().mark_all_read()
    if request.accept_mimetypes.best == 'application/json':
        return {'marked': count, 'unread_count': 0}
    flash(f'{count} message(s) marked as read.', 'success')
    return redirect(url_for('messages_index'))


def _message_log_link(entry):
    """URL of the message an activity-log entry is about (category
    `messages`, details holding its id), else None."""
    if entry.get('category') != 'messages' or not entry.get('details'):
        return None
    import json as _json
    try:
        details = _json.loads(entry['details']) if isinstance(entry['details'], str) else entry['details']
        message_id = int(details['id'])
    except (ValueError, TypeError, KeyError):
        return None
    return {'id': message_id,
            'url': url_for('messages_index', _anchor=f'message-{message_id}')}
