"""A-5 — the invoice view shows the invoice's own currency."""
from datetime import date


def _make(db, number, currency, amount_usd, amount_eur):
    from app import Invoice
    inv = Invoice.query.filter_by(invoice_number=number).first()
    if inv:
        return inv
    inv = Invoice(invoice_number=number, client_name='View Currency SL',
                  amount_usd=amount_usd, amount_eur=amount_eur, exchange_rate=1.1,
                  invoice_date=date(2026, 2, 1), status='draft', currency=currency)
    db.session.add(inv)
    db.session.commit()
    return inv


def _view(client, inv):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/view/{inv.id}')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _amount_section(html):
    start = html.index('Unit Price') if 'Unit Price' in html else 0
    return html[start:html.index('Notes', start)]


def test_eur_invoice_shows_euro_and_no_usd(loaded_modules, client):
    from app import db
    inv = _make(db, 'VC-EUR', 'EUR', amount_usd=110.0, amount_eur=100.0)
    section = _amount_section(_view(client, inv))
    assert 'USD' not in section and '$' not in section
    assert '€100.00' in section
    assert 'EUR equivalent' not in section


def test_usd_invoice_shows_usd_and_eur_equivalent(loaded_modules, client):
    from app import db
    inv = _make(db, 'VC-USD', 'USD', amount_usd=220.0, amount_eur=200.0)
    section = _amount_section(_view(client, inv))
    assert 'Total (USD)' in section and '$220.00' in section
    assert 'EUR equivalent' in section and '€200.00' in section


def test_amount_in_currency_property():
    from app import Invoice
    assert Invoice(currency='EUR', amount_usd=1.1, amount_eur=1.0).amount_in_currency == 1.0
    assert Invoice(currency='USD', amount_usd=1.1, amount_eur=1.0).amount_in_currency == 1.1
