"""Activity log entries carry a level and a source (module_manager.py): file
and database storage, entries written before levels existed, the Logs page
filter, BaseModule.log(), listeners, and the convention every module follows."""
import ast
import json
import logging
import pathlib
import re

import pytest

from contaautonomo.core.module_manager import (
    LOG_CATEGORIES, LOG_LEVELS, ActivityLogger, DbActivityLogger, FileActivityLogger)

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def file_log(loaded_modules, tmp_path, monkeypatch):
    log = FileActivityLogger(str(tmp_path))
    monkeypatch.setattr(loaded_modules.core, '_activity_logger', log)
    return log


def test_file_entries_keep_level_and_source_and_filter_by_level(file_log, loaded_modules):
    core = loaded_modules.core
    core.log_activity('settings_updated', 'settings')
    core.log_activity('sync_slow', 'system', level='warning', source='my_module')
    core.log_activity('sync_failed', 'system', level='error', source='my_module')
    entries = file_log.get_entries()
    assert [(e['action'], e['level'], e['source']) for e in entries] == [
        ('sync_failed', 'error', 'my_module'), ('sync_slow', 'warning', 'my_module'),
        ('settings_updated', 'info', 'core')]
    assert [e['action'] for e in file_log.get_entries(min_level='warning')] == [
        'sync_failed', 'sync_slow']
    assert [e['action'] for e in file_log.get_entries(min_level='error')] == ['sync_failed']


def test_an_unknown_level_is_stored_as_info(file_log, loaded_modules):
    loaded_modules.core.log_activity('x_done', 'system', level='verbose')
    assert file_log.get_entries()[0]['level'] == 'info'


def test_file_entries_written_before_levels_read_as_info(tmp_path):
    log = FileActivityLogger(str(tmp_path))
    with open(log._today_file(), 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'ts': '2026-01-01T10:00:00', 'action': 'login',
                             'cat': 'auth', 'details': None, 'user': None}) + '\n')
    entry = log.get_entries()[0]
    assert (entry['level'], entry['source']) == ('info', None)
    assert log.get_entries(min_level='warning') == []


def test_an_old_database_table_gets_the_new_columns(app):
    from sqlalchemy import inspect, text
    from contaautonomo.app import db
    with db.engine.connect() as conn:
        conn.execute(text('DROP TABLE IF EXISTS activity_log'))
        conn.execute(text('CREATE TABLE activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                          'timestamp TEXT NOT NULL, action TEXT NOT NULL, '
                          "category TEXT DEFAULT 'system', details TEXT, user TEXT)"))
        conn.execute(text("INSERT INTO activity_log (timestamp, action, category) "
                          "VALUES ('2026-01-01T10:00:00', 'login', 'auth')"))
        conn.commit()
    log = DbActivityLogger(db, app)
    with db.engine.connect() as conn:
        columns = {c['name'] for c in inspect(conn).get_columns('activity_log')}
    assert {'level', 'source'} <= columns
    log.log('backup_restored', 'backup', 'file.json', level='warning', source='backup')
    entries = log.get_entries()
    assert [(e['action'], e['level'], e['source']) for e in entries] == [
        ('backup_restored', 'warning', 'backup'), ('login', 'info', None)]
    assert [e['action'] for e in log.get_entries(min_level='warning')] == ['backup_restored']
    log.clear()


def test_the_logs_page_shows_and_filters_levels(file_log, loaded_modules, client):
    core = loaded_modules.core
    core.log_activity('quiet_thing_done', 'system')
    core.log_activity('sync_failed', 'system', 'details here', level='error', source='my_module')
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/logs').get_data(as_text=True)
    assert 'quiet_thing_done' in html and 'status-badge status-failed">error' in html
    assert 'log-source muted">my_module' in html
    html = client.get('/logs?level=warning').get_data(as_text=True)
    assert 'sync_failed' in html and 'quiet_thing_done' not in html
    assert 'value="warning" selected' in html
    assert client.get('/logs?page=abc&level=nonsense').status_code == 200


def test_module_helper_writes_through_log_write(file_log, loaded_modules):
    module = loaded_modules.modules['expenses']
    module.log('expense_created', {'id': 7, 'amount': 12.5}, category='expense')
    module.log('expense_import_failed', 'x' * 3000, category='expense', level='error')
    first, second = file_log.get_entries()[::-1]
    assert (first['action'], first['category'], first['source'], first['level']) == (
        'expense_created', 'expense', 'expenses', 'info')
    assert json.loads(first['details']) == {'id': 7, 'amount': 12.5}
    assert second['level'] == 'error' and len(second['details']) == 2000


def test_module_helper_never_raises(file_log, loaded_modules, caplog):
    module = loaded_modules.modules['expenses']
    with caplog.at_level(logging.ERROR, logger='module.expenses'):
        module.log('Not A Valid Action')
    assert file_log.get_entries() == []
    assert 'Could not write the activity log entry' in caplog.text


def test_a_logger_without_level_still_works(loaded_modules, monkeypatch):
    seen = []

    class OldLogger(ActivityLogger):
        def log(self, action, category='system', details=None, user=None):
            seen.append((action, category, details, user))

    monkeypatch.setattr(loaded_modules.core, '_activity_logger', OldLogger())
    loaded_modules.modules['expenses'].log('expense_created', 'a', category='expense')
    assert seen == [('expense_created', 'expense', 'a', None)]


def test_listeners_get_every_entry_and_a_failing_one_is_skipped(file_log, loaded_modules):
    core = loaded_modules.core
    got = []

    def broken(entry):
        raise RuntimeError('boom')

    core.add_activity_listener(broken)
    core.add_activity_listener(got.append)
    try:
        loaded_modules.modules['backup'].log('backup_restored', 'x.json', category='backup',
                                             level='warning')
    finally:
        core.remove_activity_listener(broken)
        core.remove_activity_listener(got.append)
    assert got == [{'action': 'backup_restored', 'category': 'backup', 'details': 'x.json',
                    'level': 'warning', 'source': 'backup'}]
    assert file_log.get_entries()[0]['action'] == 'backup_restored'


# --- The convention every bundled module follows ------------------------------

_NAME = re.compile(r'^[a-z][a-z0-9_]*$')


def _log_calls():
    for path in sorted((ROOT / 'modules').rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                yield path, node


def test_modules_write_the_activity_log_through_the_helper_only():
    direct = [f'{p.relative_to(ROOT)}:{n.lineno}' for p, n in _log_calls()
              if n.func.attr in ('log_activity', 'set_activity_logger')]
    assert direct == []


def test_every_module_entry_names_an_action_category_and_level():
    problems = []
    count = 0
    for path, node in _log_calls():
        receiver = node.func.value
        if not (node.func.attr == 'log' and isinstance(receiver, ast.Name)
                and receiver.id in ('self', 'module')):
            continue
        count += 1
        where = f'{path.relative_to(ROOT)}:{node.lineno}'
        action = node.args[0] if node.args else None
        if not (isinstance(action, ast.Constant) and _NAME.match(action.value or '')):
            problems.append(f'{where}: action must be a snake_case literal')
        keywords = {k.arg: k.value for k in node.keywords}
        category = keywords.get('category')
        if not (isinstance(category, ast.Constant) and category.value in LOG_CATEGORIES):
            problems.append(f'{where}: category must be one of LOG_CATEGORIES')
        level = keywords.get('level')
        if isinstance(level, ast.Constant) and level.value not in LOG_LEVELS:
            problems.append(f'{where}: unknown level {level.value!r}')
    assert count >= 35 and problems == []
