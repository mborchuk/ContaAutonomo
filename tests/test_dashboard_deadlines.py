"""B-3 — one source of tax deadlines: the Fiscal Calendar module."""
from contextlib import contextmanager
from datetime import date

import pytest
from flask import template_rendered


@pytest.fixture
def no_network(monkeypatch):
    import requests

    def offline(*_a, **_kw):
        raise requests.ConnectionError('tests run offline')
    monkeypatch.setattr(requests, 'get', offline)


@contextmanager
def _captured(app):
    seen = []

    def record(sender, template, context, **extra):
        seen.append((template.name, context))
    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def _dashboard(app, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    with _captured(app) as seen:
        resp = client.get('/')
    assert resp.status_code == 200
    ctx = next(c for name, c in seen if name == 'dashboard.html')
    return resp.get_data(as_text=True), ctx


def test_core_deadline_list_removed():
    import app as appmod
    assert not hasattr(appmod, '_get_upcoming_tax_deadlines')


def test_dashboard_uses_fiscal_calendar(loaded_modules, app, client, no_network):
    html, ctx = _dashboard(app, client)
    deadlines = ctx['tax_deadlines']
    assert deadlines is not None
    assert all({'form', 'period_label', 'window_end'} <= set(d) for d in deadlines)
    assert 'Enable the' not in html


def test_dashboard_hint_when_fiscal_calendar_disabled(loaded_modules, app, client,
                                                      no_network, monkeypatch):
    monkeypatch.delitem(loaded_modules.modules, 'fiscal_calendar')
    html, ctx = _dashboard(app, client)
    assert ctx['tax_deadlines'] is None
    assert 'Fiscal Calendar</a> module to see filing deadlines' in html


def test_dashboard_uses_any_provider_of_the_function(loaded_modules, app, client,
                                                     no_network, monkeypatch):
    from functions import Function, Param
    from module_manager import BaseModule

    entry = {'form': '999', 'period_label': 'Q1 2026', 'window_start': date(2026, 4, 1),
             'window_end': date(2026, 4, 20), 'state': 'upcoming', 'days_to_end': 9,
             'days_to_start': 1, 'verified': True}

    class OtherCalendar(BaseModule):
        module_id = 'other_calendar'
        name = 'Other calendar'

        def get_functions(self):
            return [Function('calendar.upcoming_deadlines', lambda limit=4, today=None: [entry],
                             'Deadlines.', params=(Param('limit', 'int'), Param('today', 'date')))]

    monkeypatch.delitem(loaded_modules.modules, 'fiscal_calendar')
    monkeypatch.setitem(loaded_modules.modules, 'other_calendar', OtherCalendar(loaded_modules.core))
    _html, ctx = _dashboard(app, client)
    assert ctx['tax_deadlines'] == [entry]


def test_upcoming_deadlines_limit(loaded_modules):
    items = loaded_modules.functions.call('calendar.upcoming_deadlines', limit=2,
                                          today='2026-03-01')
    assert len(items) == 2
    assert items[0]['window_end'] <= items[1]['window_end']


def test_calendar_functions(loaded_modules):
    functions = loaded_modules.functions
    assert functions.provider('calendar.deadline_for') == 'fiscal_calendar'
    q2 = functions.call('calendar.deadline_for', form='303', year=2026, quarter=2)
    assert q2['window_end'] == date(2026, 7, 20)
    renta = functions.call('calendar.deadline_for', form='100', year=2026)
    assert renta['window_end'] == date(2027, 6, 30)
    assert functions.call('calendar.deadline_for', form='999', year=2026) is None
    year = functions.call('calendar.deadlines_for_year', year=2026)
    assert {e['form'] for e in year} >= {'303', '130', '347', '100'}
    assert [e['window_end'] for e in year] == sorted(e['window_end'] for e in year)
    forms = functions.call('calendar.selected_forms')
    assert forms == sorted(forms) and isinstance(forms, list)
