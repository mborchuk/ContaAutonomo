"""G-4 — Modelo 347: counterparties above 3,005.06 EUR, after exclusions."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from modules.fiscal_calendar.calendar_data import deadlines_for_year
from modules.tax_es_forms.boxes import MODELO_347_EXCLUSIONS, modelo_347_rules_for_year
from modules.tax_es_forms.calculator import compute_modelo_347

THRESHOLD = 3005.06
VAT = 0.21


def _customer(name, tax_type='standard', vat='B12345678', cid=None, country='España'):
    return SimpleNamespace(id=cid, name=name, tax_type=tax_type, vat_number=vat,
                           country=country)


def invoice(customer, base, d=date(2031, 3, 10), status='issued', retention=0.0,
            cuota=None):
    """Issued invoice with its fiscal snapshot: base + cuota = total."""
    rate = 21.0 if customer.tax_type == 'standard' else 0.0
    if cuota is None:
        cuota = round(base * rate / 100, 2)
    return SimpleNamespace(
        invoice_date=d, status=status, amount_eur=base, customer=customer,
        customer_id=customer.id, client_name=customer.name,
        snap_vat_rate=rate, snap_taxable_base=base, snap_vat_amount=cuota,
        snap_customer=json.dumps({'name': customer.name, 'vat_number': customer.vat_number,
                                  'country': customer.country,
                                  'tax_type': customer.tax_type}),
        irpf_retention_amount=retention, irpf_retention_pct=0.0)


def expense(gross, contractor=None, category='Software', d=date(2031, 5, 2),
            reverse_charge=None, description='Synthetic purchase'):
    return SimpleNamespace(amount=gross, currency='EUR', expense_date=d,
                           category=category, contractor=contractor,
                           reverse_charge=reverse_charge, description=description)


def _supplier(name='Proveedor Uno SL', vat='B87654321', cid=7, country='España'):
    return SimpleNamespace(id=cid, name=name, vat_number=vat, country=country)


def _total_with_vat(total):
    """(base, cuota) whose sum is exactly `total`."""
    base = round(total / 1.21, 2)
    return base, round(total - base, 2)


@pytest.mark.parametrize('total,listed', [(3005.07, True), (3005.06, False)])
def test_threshold_boundary_on_sales(total, listed):
    base, cuota = _total_with_vat(total)
    cust = _customer('Cliente Frontera SL', cid=1)
    out = compute_modelo_347([invoice(cust, base, cuota=cuota)], [], VAT, THRESHOLD)
    assert (len(out['lines']) == 1) is listed
    if listed:
        line = out['lines'][0]
        assert line['key'] == 'B' and line['total'] == total
        assert line['nif'] == 'B12345678'
    else:
        assert out['below_threshold'][0]['total'] == total


@pytest.mark.parametrize('total,listed', [(3005.07, True), (3005.06, False)])
def test_threshold_boundary_on_purchases(total, listed):
    out = compute_modelo_347([], [expense(total, _supplier())], VAT, THRESHOLD)
    assert (len(out['lines']) == 1) is listed
    if listed:
        assert out['lines'][0]['key'] == 'A' and out['lines'][0]['total'] == total


def test_quarterly_split_and_amounts_include_vat():
    cust = _customer('Cliente Trimestral SL', cid=2)
    invoices = [invoice(cust, 1000.0, d=date(2031, 2, 1)),
                invoice(cust, 1000.0, d=date(2031, 8, 1)),
                invoice(cust, 1000.0, d=date(2031, 11, 30))]
    out = compute_modelo_347(invoices, [], VAT, THRESHOLD)
    line = out['lines'][0]
    assert line['quarters'] == [1210.0, 0.0, 1210.0, 1210.0]
    assert line['total'] == 3630.0


def test_sales_and_purchases_are_counted_separately():
    # Same company is customer (2,000) and supplier (2,000): neither side exceeds.
    cust = _customer('Mixta SL', cid=3, vat='B11111111')
    supplier = _supplier('Mixta SL', vat='B11111111', cid=3)
    base, cuota = _total_with_vat(2000.0)
    out = compute_modelo_347([invoice(cust, base, cuota=cuota)],
                             [expense(2000.0, supplier)], VAT, THRESHOLD)
    assert out['lines'] == []
    assert {g['key'] for g in out['below_threshold']} == {'A', 'B'}


def test_excluded_operations_are_not_counted_and_say_why():
    eu = _customer('EU Client GmbH', tax_type='eu_b2b', vat='DE123456789', cid=4)
    spanish = _customer('Cliente Retenedor SL', cid=5)
    invoices = [
        invoice(eu, 5000.0),                                   # Modelo 349
        invoice(spanish, 5000.0, retention=750.0),             # Modelo 190
        invoice(_customer('Anulada SL', cid=6), 5000.0, status='cancelled'),
    ]
    expenses = [
        expense(5000.0, _supplier('EU Supplier BV', vat='NL1', cid=8), reverse_charge='eu'),
        expense(5000.0, _supplier('Aseguradora SA', cid=9), category='Insurance'),
        expense(5000.0, None, category='Social Security', description='RETA'),
        expense(5000.0, None, description='Laptop, no supplier'),
    ]
    out = compute_modelo_347(invoices, expenses, VAT, THRESHOLD)
    assert out['lines'] == [] and out['below_threshold'] == []
    reasons = {row['reason'] for row in out['excluded']}
    assert reasons == {'intra_eu', 'retention', 'cancelled', 'insurance',
                       'social_security', 'no_supplier'}
    for reason in reasons:     # every reason has a title and a rule for the page
        title, rule = MODELO_347_EXCLUSIONS[reason]
        assert title and rule


def test_non_eu_customer_and_reverse_charge_supplier_are_listed():
    us = _customer('US Client Inc', tax_type='non_eu', vat='US99', cid=10, country='USA')
    out = compute_modelo_347(
        [invoice(us, 4000.0)],
        [expense(4000.0, _supplier('Cloud Ltd', vat='GB1', cid=11), reverse_charge='non_eu')],
        VAT, THRESHOLD)
    by_key = {line['key']: line for line in out['lines']}
    assert by_key['B']['total'] == 4000.0 and by_key['B']['country'] == 'USA'
    assert by_key['A']['reverse_charge'] is True


def test_line_without_nif_is_flagged():
    cust = _customer('Sin NIF SL', vat='', cid=12)
    out = compute_modelo_347([invoice(cust, 4000.0)], [], VAT, THRESHOLD)
    assert out['lines'] and 'no NIF' in out['meta']['warnings'][0]


def test_threshold_table_and_fallback():
    year, rules, fallback = modelo_347_rules_for_year(2025)
    assert rules['threshold'] == THRESHOLD and not fallback
    assert 'RD 1065/2007' in rules['source']
    assert modelo_347_rules_for_year(2031)[2] is True


@pytest.mark.parametrize('year,end', [
    (2026, date(2027, 3, 1)),    # 28 Feb 2027 is a Sunday -> Monday
    (2027, date(2028, 2, 29)),   # leap year
])
def test_filing_window_is_february(year, end):
    entry = next(e for e in deadlines_for_year(year) if e['form'] == '347')
    assert entry['window_start'] == date(year + 1, 2, 1)
    assert entry['window_end'] == end
    assert entry['quarter'] is None


# --- module + page ---------------------------------------------------------- #

def test_draft_347_from_issued_invoices_and_page(loaded_modules, app, client):
    from contaautonomo.app import Contractor, Customer, Expense, Invoice, _issue_invoice, db
    cust = Customer(name='Cliente 347 SL', vat_number='B22222222', tax_type='standard')
    over = Contractor(name='Proveedor 347 SL', vat_number='B33333333')
    db.session.add_all([cust, over])
    db.session.flush()
    inv = Invoice(invoice_number='T347-1', client_name=cust.name, customer_id=cust.id,
                  amount_usd=0.0, amount_eur=2500.0, exchange_rate=1.0,
                  invoice_date=date(2031, 6, 1), status='draft', currency='EUR')
    db.session.add(inv)
    db.session.add(Expense(amount=3005.07, currency='EUR', category='Services',
                           description='Synthetic service', expense_date=date(2031, 9, 1),
                           contractor_id=over.id))
    db.session.commit()
    _issue_invoice(inv)
    db.session.commit()

    draft = loaded_modules.modules['tax_es_forms']._draft_347(2031)
    names = {line['name']: line for line in draft['lines']}
    assert names['Cliente 347 SL']['total'] == pytest.approx(3025.0)   # 2,500 + 21 %
    assert names['Proveedor 347 SL']['quarters'][2] == pytest.approx(3005.07)
    assert draft['deadline']['window_end'] == date(2032, 3, 1)

    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/tax-forms-draft/347/2031').get_data(as_text=True)
    assert 'Cliente 347 SL' in html and 'Proveedor 347 SL' in html
    assert 'Estimate, not tax advice.' in html
    assert 'Not counted, and why' in html
    assert '/tax-forms-draft/347/' in client.get('/tax-forms-draft/').get_data(as_text=True)


def test_api_draft_347_is_json(loaded_modules, app):
    from flask import json as flask_json
    forms = loaded_modules.modules['tax_es_forms']
    with app.test_request_context('/api/v1/m/tax_es_forms/draft/347/2031'):
        data, status = forms._api_draft_347(None, year=2031)
        assert status == 200
        body = flask_json.loads(flask_json.dumps(data))
    assert body['form'] == '347' and body['threshold'] == THRESHOLD
    assert body['deadline']['window_start'] == '2032-02-01'


def test_draft_347_without_a_calendar_has_no_deadline(loaded_modules, app, client, monkeypatch):
    monkeypatch.delitem(loaded_modules.modules, 'fiscal_calendar')
    assert loaded_modules.modules['tax_es_forms']._draft_347(2031)['deadline'] is None
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get('/tax-forms-draft/347/2031')
    assert resp.status_code == 200 and 'Filing window' not in resp.get_data(as_text=True)
