"""A-4 — one income rule: every invoice dated in the year except cancelled."""
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from flask import template_rendered

from constants import counts_as_income


class _Inv:
    def __init__(self, status):
        self.status = status


@pytest.mark.parametrize('status,expected', [
    ('draft', True), ('issued', True), ('pending', True), ('paid', True),
    ('cancelled', False),
])
def test_counts_as_income(status, expected):
    assert counts_as_income(_Inv(status)) is expected


@contextmanager
def _captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))
    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


@pytest.fixture
def no_network(monkeypatch):
    import requests

    def offline(*_a, **_kw):
        raise requests.ConnectionError('tests run offline')
    monkeypatch.setattr(requests, 'get', offline)


def _seed_year(db, year):
    from app import Customer, Invoice
    Invoice.query.delete()
    db.session.commit()
    cust = Customer(name='Income Rule SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    rows = [
        ('IR-1', 'issued', 100.0, date(year, 1, 15), datetime(year, 1, 15)),   # unpaid
        ('IR-2', 'paid', 200.0, date(year, 4, 10), datetime(year, 4, 10)),
        ('IR-3', 'cancelled', 400.0, date(year, 7, 5), datetime(year, 7, 5)),
        ('IR-4', 'issued', 33.33, date(year, 10, 2), datetime(year, 10, 2)),
    ]
    for number, status, amount, d, issued in rows:
        db.session.add(Invoice(invoice_number=number, client_name=cust.name,
                               customer_id=cust.id, amount_usd=0.0, amount_eur=amount,
                               exchange_rate=1.0, invoice_date=d,
                               status=status, currency='EUR', issued_at=issued))
    db.session.commit()


def test_dashboard_income_counts_unpaid_and_skips_cancelled(loaded_modules, app,
                                                            client, no_network):
    from app import db
    year = date.today().year
    _seed_year(db, year)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    with _captured_templates(app) as templates:
        assert client.get('/').status_code == 200
    ctx = next(c for t, c in templates if t.name == 'dashboard.html')
    income = ctx['tax_breakdown']['income']
    assert income == pytest.approx(100.0 + 200.0 + 33.33)

    forms = loaded_modules.modules['tax_es_forms']
    box_01_q4 = forms._draft_130(year, 4)['boxes']['01']  # cumulative YTD income
    assert income == pytest.approx(box_01_q4)
