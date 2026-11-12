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
    real = loaded_modules.provider_of
    monkeypatch.setattr(loaded_modules, 'provider_of',
                        lambda role: None if role == 'fiscal_calendar' else real(role))
    html, ctx = _dashboard(app, client)
    assert ctx['tax_deadlines'] is None
    assert 'Fiscal Calendar</a> module to see filing deadlines' in html


def test_upcoming_deadlines_limit(loaded_modules):
    fc = loaded_modules.modules['fiscal_calendar']
    items = fc.upcoming_deadlines(limit=2, today=date(2026, 3, 1))
    assert len(items) == 2
    assert items[0]['window_end'] <= items[1]['window_end']
