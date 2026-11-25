"""log.write (core_functions.py): a module writes an activity log entry
through the registry, into the file or database log the owner chose."""
import logging

import pytest

from contaautonomo.core.functions import InvalidArguments
from contaautonomo.core.module_manager import FileActivityLogger


@pytest.fixture
def file_log(loaded_modules, tmp_path, monkeypatch):
    log = FileActivityLogger(str(tmp_path))
    monkeypatch.setattr(loaded_modules.core, '_activity_logger', log)
    return log


def _write(loaded_modules, **arguments):
    return loaded_modules.functions.call('log.write', **arguments)


def test_the_core_provides_it_for_writing(loaded_modules):
    fn = loaded_modules.functions.get('log.write')
    assert loaded_modules.functions.provider('log.write') == 'core'
    assert fn.read_only is False and fn.assistant is False and fn.external is False
    assert not loaded_modules.functions.is_core_default('log.write')


def test_an_entry_shows_on_the_logs_page(loaded_modules, file_log, client):
    assert _write(loaded_modules, source='my_module', action='record_created',
                  details='Record #7 created') is None
    entry = file_log.get_entries(limit=1)[0]
    assert (entry['action'], entry['category'], entry['details']) == (
        'record_created', 'my_module', 'Record #7 created')
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/logs?category=my_module').get_data(as_text=True)
    assert 'record_created' in html and 'Record #7 created' in html


def test_category_and_no_details(loaded_modules, file_log):
    _write(loaded_modules, source='my_module', action='synced', category='backup')
    entry = file_log.get_entries(limit=1)[0]
    assert (entry['category'], entry['details']) == ('backup', None)


def test_the_database_log_gets_it_too(loaded_modules, monkeypatch):
    from contaautonomo.app import app, db
    from contaautonomo.core.module_manager import DbActivityLogger
    log = DbActivityLogger(db, app)
    log.clear()
    monkeypatch.setattr(loaded_modules.core, '_activity_logger', log)
    _write(loaded_modules, source='my_module', action='record_deleted', details='#3')
    entry = log.get_entries(limit=1)[0]
    assert (entry['action'], entry['category'], entry['details']) == (
        'record_deleted', 'my_module', '#3')
    log.clear()


def test_warning_and_error_also_go_to_the_server_log(loaded_modules, file_log, caplog):
    caplog.set_level(logging.INFO, logger='module.my_module')
    _write(loaded_modules, source='my_module', action='record_created', details='secret-ish')
    assert not caplog.records
    _write(loaded_modules, source='my_module', action='sync_failed', level='error',
           details='customer name')
    _write(loaded_modules, source='my_module', action='sync_slow', level='warning')
    assert [(r.name, r.levelno, r.getMessage()) for r in caplog.records] == [
        ('module.my_module', logging.ERROR, 'activity: sync_failed'),
        ('module.my_module', logging.WARNING, 'activity: sync_slow')]
    assert 'customer name' not in caplog.text          # details stay in the activity log
    assert len(file_log.get_entries()) == 3


@pytest.mark.parametrize('arguments, problem', [
    ({'action': 'x'}, 'source'),
    ({'source': 'my_module'}, 'action'),
    ({'source': 'My Module', 'action': 'x'}, 'source'),
    ({'source': 'my_module', 'action': 'x\nINFO forged'}, 'action'),
    ({'source': 'my_module', 'action': 'x', 'category': 'a-b'}, 'category'),
    ({'source': 'my_module', 'action': 'x', 'level': 'debug'}, 'level'),
    ({'source': 'my_module', 'action': 'x', 'details': 'd' * 2001}, 'details'),
])
def test_bad_arguments_are_refused_and_nothing_is_written(loaded_modules, file_log,
                                                          arguments, problem):
    with pytest.raises(InvalidArguments, match=problem):
        _write(loaded_modules, **arguments)
    assert file_log.get_entries() == []
