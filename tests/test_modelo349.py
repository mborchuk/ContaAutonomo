"""Modelo 349: one line per EU business customer, total equals 303 box 59."""
from datetime import date
from types import SimpleNamespace as NS

from modules.tax_es_forms.calculator import compute_modelo_349


def invoice(amount, customer, status='paid'):
    return NS(amount_eur=amount, status=status, snap_vat_rate=None,
              snap_customer=None, customer=customer, client_name=customer.name)


ACME = NS(name='ACME Synthetic s.r.o.', tax_type='eu_b2b', vat_number='CZ 123 45 678')
BETA = NS(name='Beta Synthetic GmbH', tax_type='eu_b2b', vat_number='de123456789')
US = NS(name='Gamma Synthetic LLC', tax_type='non_eu', vat_number='')
NOVAT = NS(name='Delta Synthetic SA', tax_type='eu_b2b', vat_number='')


def test_lines_per_customer_and_totals():
    result = compute_modelo_349([
        invoice(1000.0, ACME), invoice(500.0, ACME), invoice(300.0, BETA),
        invoice(700.0, US), invoice(999.0, ACME, status='cancelled')])
    assert result['boxes'] == {'01': 2, '02': 1800.0}
    assert result['operators'] == [
        {'country': 'CZ', 'vat_number': '12345678', 'name': 'ACME Synthetic s.r.o.',
         'key': 'S', 'base': 1500.0},
        {'country': 'DE', 'vat_number': '123456789', 'name': 'Beta Synthetic GmbH',
         'key': 'S', 'base': 300.0}]
    assert result['meta']['warnings'] == []


def test_missing_vat_number_is_flagged():
    result = compute_modelo_349([invoice(100.0, NOVAT)])
    assert result['meta']['warnings'] == ['Delta Synthetic SA: no EU VAT number — line incomplete']


def test_draft_page_matches_303_box_59(app, loaded_modules, client):
    from app import Customer, Invoice, db

    customer = Customer(name='ACME Synthetic s.r.o.', tax_type='eu_b2b',
                        vat_number='CZ 123 45 678')
    db.session.add(customer)
    db.session.flush()
    db.session.add(Invoice(invoice_number='S-1', client_name=customer.name,
                           customer_id=customer.id, amount_usd=1200.0,
                           amount_eur=1200.0, exchange_rate=1.0, currency='EUR',
                           invoice_date=date(2026, 8, 31), status='pending'))
    db.session.commit()
    draft = loaded_modules.modules['tax_es_forms']._draft_349(2026, 3)
    assert draft['boxes']['02'] == draft['box_59'] == 1200.0
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    page = client.get('/tax-forms-draft/349/2026/3').get_data(as_text=True)
    assert '12345678' in page and 'differs from Modelo 303' not in page
