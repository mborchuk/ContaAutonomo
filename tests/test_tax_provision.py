"""G-2 — tax provision panel: the total is the sum of the engine results."""
from contextlib import contextmanager
from datetime import date

import pytest
from flask import template_rendered

from modules.tax_provision.provision import direction, summarise

YEAR = 2033          # synthetic year, no other test writes it
TODAY = date(YEAR, 5, 15)   # open quarter: Q2


# --- pure ------------------------------------------------------------------- #

def test_total_is_the_sum_of_available_parts():
    out = summarise([{'amount': 100.0}, {'amount': 50.25}, {'amount': -20.0}])
    assert out['total'] == 130.25
    assert out['complete'] and out['missing'] == []


def test_refund_reduces_the_total():
    out = summarise([{'amount': 100.0}, {'amount': -150.0}])
    assert out['total'] == -50.0
    assert out['total_direction'] == 'refund'


def test_missing_part_is_left_out_and_named():
    part = {'label': 'RETA', 'amount': None}
    out = summarise([{'amount': 10.0}, part])
    assert out['total'] == 10.0
    assert not out['complete'] and out['missing'] == [part]


def test_direction_words():
    assert direction(1) == 'pay' and direction(0) == 'nil'
    assert direction(-1) == 'refund' and direction(-1, 'compensate') == 'compensate'
    assert direction(None) is None


# --- module, synthetic year ------------------------------------------------- #

@pytest.fixture
def synthetic_year(loaded_modules, app):
    from contaautonomo.app import Customer, Expense, Invoice, db
    cust = Customer(name='Provision Test SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    for number, d, amount in (('TP-1', date(YEAR, 1, 20), 4000.0),
                              ('TP-2', date(YEAR, 4, 10), 3000.0),
                              ('TP-3', date(YEAR, 5, 5), 1500.0)):
        db.session.add(Invoice(invoice_number=number, client_name=cust.name,
                               customer_id=cust.id, amount_usd=0.0, amount_eur=amount,
                               exchange_rate=1.0, invoice_date=d, status='draft',
                               currency='EUR'))
    db.session.add(Expense(amount=121.0, currency='EUR', category='Software',
                           description='Synthetic licence', expense_date=date(YEAR, 4, 2),
                           net_amount=100.0, vat_rate=21.0, vat_amount=21.0,
                           deductible=True, deductible_pct=100.0))
    db.session.commit()
    return loaded_modules


def _engines(mm):
    forms = mm.modules['tax_es_forms']
    irpf = mm.modules['tax_es_irpf']
    reta = mm.modules['reta_advisor']
    return (forms.draft_303(YEAR, 2)['boxes']['71'],
            irpf.modelo130_boxes(YEAR, 2)[0]['07'],
            irpf.modelo100_result(YEAR)['summary']['modelo100_diferencial'],
            reta.projection(TODAY)['regularization_delta'])


def test_total_equals_the_four_engine_results(synthetic_year):
    provision = synthetic_year.modules['tax_provision'].provision(TODAY)
    iva, m130, renta, reta = _engines(synthetic_year)
    amounts = {p['id']: p['amount'] for p in provision['parts']}
    assert amounts == {'iva': pytest.approx(iva), 'm130': pytest.approx(m130),
                       'renta': pytest.approx(renta), 'reta': pytest.approx(reta)}
    assert provision['total'] == pytest.approx(iva + m130 + renta + reta, abs=0.01)
    assert provision['complete']
    assert iva > 0 and m130 > 0   # the synthetic quarter owes VAT and IRPF


def test_vat_credit_reduces_the_total(synthetic_year):
    from contaautonomo.app import Expense, db
    tp = synthetic_year.modules['tax_provision']
    before = tp.provision(TODAY)
    db.session.add(Expense(amount=12100.0, currency='EUR', category='Software',
                           description='Synthetic big purchase', expense_date=date(YEAR, 5, 1),
                           net_amount=10000.0, vat_rate=21.0, vat_amount=2100.0,
                           deductible=True, deductible_pct=100.0))
    db.session.commit()
    after = tp.provision(TODAY)
    iva = next(p for p in after['parts'] if p['id'] == 'iva')
    assert iva['amount'] < 0 and iva['direction'] == 'compensate'
    iva_before = next(p for p in before['parts'] if p['id'] == 'iva')['amount']
    others_after = sum(p['amount'] for p in after['parts'] if p['id'] != 'iva')
    assert after['total'] == pytest.approx(iva['amount'] + others_after, abs=0.01)
    assert iva['amount'] < iva_before


def test_disabled_module_is_named_as_missing(synthetic_year, monkeypatch):
    monkeypatch.delitem(synthetic_year.modules, 'reta_advisor')
    provision = synthetic_year.modules['tax_provision'].provision(TODAY)
    reta = next(p for p in provision['parts'] if p['id'] == 'reta')
    assert reta['amount'] is None and 'not enabled' in reta['reason']
    assert not provision['complete']
    assert provision['total'] == pytest.approx(
        sum(p['amount'] for p in provision['parts'] if p['amount'] is not None), abs=0.01)


def test_filing_dates_come_from_the_fiscal_calendar(synthetic_year):
    provision = synthetic_year.modules['tax_provision'].provision(TODAY)
    by_id = {p['id']: p for p in provision['parts']}
    assert by_id['iva']['deadline']['window_end'] == date(YEAR, 7, 20)
    assert by_id['m130']['deadline']['form'] == '130'
    assert by_id['renta']['deadline']['window_end'] == date(YEAR + 1, 6, 30)
    assert by_id['reta']['deadline'] is None and by_id['reta']['deadline_note']
    assert provision['calendar_enabled']


def test_no_filing_dates_without_a_calendar(synthetic_year, monkeypatch):
    monkeypatch.delitem(synthetic_year.modules, 'fiscal_calendar')
    provision = synthetic_year.modules['tax_provision'].provision(TODAY)
    assert not provision['calendar_enabled']
    assert all(p['deadline'] is None for p in provision['parts'])
    assert provision['total'] is not None


# --- dashboard -------------------------------------------------------------- #

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
        seen.append(template.name)
    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def test_dashboard_shows_the_panel_with_estimate_wording(loaded_modules, app, client,
                                                         no_network):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    with _captured(app) as seen:
        resp = client.get('/')
    assert resp.status_code == 200
    assert 'dashboard.html' in seen
    html = resp.get_data(as_text=True)
    assert 'id="tax-provision"' in html      # included panels do not signal a render
    assert 'Tax provision' in html
    assert 'Estimate, not tax advice.' in html
