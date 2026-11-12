"""A-3 — invoices in currencies other than EUR/USD use their own EUR rate."""
from datetime import date

import pytest

USD_EUR = 0.9      # stubbed USD->EUR rate
GBP_EUR = 1.17     # stubbed GBP->EUR rate


@pytest.fixture
def rates(loaded_modules, monkeypatch):
    """Offline rates: USD via currency_converter, GBP via a test provider."""
    monkeypatch.setattr('currency_converter.get_exchange_rate', lambda d: (USD_EUR, d))
    svc = loaded_modules.core.currency_service
    previous = svc.active_provider

    def provider(frm, to, date_str):
        if (frm, to) == ('GBP', 'EUR'):
            return GBP_EUR, date_str
        return None, None
    svc.register_provider('a3-test', provider)
    svc.set_active_provider('a3-test')
    # No network fallback for unknown pairs.
    monkeypatch.setattr(svc, '_default_rate', lambda f, t, d: (None, None))
    yield svc
    svc.unregister_provider('a3-test')
    if previous and previous in svc.available_providers:
        svc.set_active_provider(previous)


def test_gbp_uses_gbp_rate(rates):
    from app import invoice_amounts
    amount_orig, amount_eur, rate = invoice_amounts(100.0, 'GBP', '2026-03-02')
    assert amount_orig == 100.0
    assert amount_eur == pytest.approx(117.0)
    assert rate == GBP_EUR


def test_eur_and_usd_unchanged(rates):
    from app import invoice_amounts
    assert invoice_amounts(100.0, 'EUR', '2026-03-02') == (100.0 * USD_EUR, 100.0, USD_EUR)
    assert invoice_amounts(100.0, 'USD', '2026-03-02') == (100.0, 100.0 * USD_EUR, USD_EUR)


def test_unknown_currency_raises(rates):
    from app import NoExchangeRate, invoice_amounts
    with pytest.raises(NoExchangeRate):
        invoice_amounts(100.0, 'XYZ', '2026-03-02')


def _form(number, currency):
    return {
        'invoice_number': number, 'client_name': 'Pound Client Ltd',
        'invoice_date': '2026-03-02', 'currency': currency,
        'items[0][description]': 'Consulting', 'items[0][quantity]': '2',
        'items[0][unit_price]': '50',
    }


def test_create_route_stores_gbp_eur_amount(rates, client):
    from app import Invoice
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.post('/create', data=_form('A3-GBP', 'GBP'))
    inv = Invoice.query.filter_by(invoice_number='A3-GBP').one()
    assert inv.currency == 'GBP'
    assert inv.amount_usd == 100.0                 # entered total, invoice currency
    assert inv.amount_eur == pytest.approx(117.0)  # tax base via GBP rate
    assert inv.exchange_rate == GBP_EUR


def test_create_route_refuses_currency_without_rate(rates, client):
    from app import Invoice
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.post('/create', data=_form('A3-XYZ', 'XYZ'))
    assert resp.status_code == 302
    assert Invoice.query.filter_by(invoice_number='A3-XYZ').first() is None


def test_recurring_gbp_template_uses_gbp_rate(rates, loaded_modules):
    import json
    from app import Invoice, db
    module = loaded_modules.modules['recurring_invoices']
    for old in module.RecurringInvoice.query.all():
        old.active = False
    tpl = module.RecurringInvoice(
        name='GBP retainer', client_name='Pound Client Ltd', currency='GBP',
        items_json=json.dumps([{'description': 'Retainer', 'quantity': 1,
                                'unit_price': 200.0}]),
        cadence='monthly', next_run_date=date(2026, 4, 1), active=True)
    db.session.add(tpl)
    db.session.commit()
    created = module._generate_due(today=date(2026, 4, 2))
    inv = db.session.get(Invoice, created[0])
    assert inv.amount_eur == pytest.approx(234.0)
    tpl.active = False
    db.session.commit()


def test_currency_service_has_no_silent_one_to_one(loaded_modules, monkeypatch):
    svc = loaded_modules.core.currency_service
    monkeypatch.setattr('currency_converter.get_multiple_exchange_rates',
                        lambda *a, **k: {})
    assert svc._default_rate('GBP', 'EUR', '2026-03-02') == (None, None)
