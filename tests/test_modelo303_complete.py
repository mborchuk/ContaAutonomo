"""Modelo 303: EU / non-EU sales boxes, equipment boxes, carried VAT credit.

Synthetic figures. Rules confirmed against the owner's filed 2026 returns:
EU business customers in box 59, non-EU customers in box 120, equipment in
boxes 30/31, purchases without Spanish VAT left out of the deductible base,
the credit of the previous filed return carried in box 110.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

import pytest

from modules.tax_es_forms.calculator import compute_modelo_303


def invoice(amount, tax_type, status='paid'):
    return NS(amount_eur=amount, status=status, snap_vat_rate=None,
              snap_customer=None, customer=NS(tax_type=tax_type))


def expense(net, vat, category='Services', deductible=True):
    return NS(net_amount=net, vat_amount=vat, amount=net + (vat or 0),
              category=category, deductible=deductible, deductible_pct=100.0,
              currency='EUR', expense_date=date(2026, 7, 1))


def is_equipment(exp, net_eur):
    return exp.category == 'Equipment' and net_eur >= 300


def test_sales_boxes_and_equipment_split():
    result = compute_modelo_303(
        [invoice(1000.0, 'eu_b2b'), invoice(500.0, 'non_eu'),
         invoice(200.0, 'standard'), invoice(999.0, 'eu_b2b', status='cancelled')],
        [expense(150.0, 31.5), expense(1000.0, 210.0, 'Equipment'),
         expense(40.0, 8.4, 'Equipment'),        # small: current
         expense(18.0, 0.0),                     # no Spanish VAT: not in the base
         expense(60.0, None)],                   # VAT unknown: counted as missing
        0.21, is_equipment=is_equipment)
    b = result['boxes']
    assert (b['01'], b['03']) == (200.0, 42.0)
    assert (b['59'], b['120']) == (1000.0, 500.0)
    assert (b['28'], b['29']) == (190.0, 39.9)
    assert (b['30'], b['31']) == (1000.0, 210.0)
    assert b['45'] == 249.9
    assert b['46'] == pytest.approx(42.0 - 249.9)
    assert result['meta']['missing_expense_vat_count'] == 1


def test_credit_carried_when_result_negative():
    b = compute_modelo_303([], [expense(100.0, 21.0)], 0.21,
                           carried_forward=50.0)['boxes']
    assert (b['46'], b['110'], b['78'], b['87'], b['71']) == (-21.0, 50.0, 0.0, 50.0, -21.0)


def test_credit_applied_to_a_positive_result():
    b = compute_modelo_303([invoice(1000.0, 'standard')], [], 0.21,
                           carried_forward=50.0)['boxes']
    assert (b['46'], b['110'], b['78'], b['87'], b['71']) == (210.0, 50.0, 50.0, 0.0, 160.0)


def test_snapshot_tax_type_wins_over_current_customer():
    inv = invoice(700.0, 'standard')
    inv.snap_vat_rate = 0.0
    inv.snap_customer = json.dumps({'tax_type': 'eu_b2b'})
    assert compute_modelo_303([inv], [], 0.21)['boxes']['59'] == 700.0


# ── Draft: carried credit from the filed previous return ─────────────────

@pytest.fixture
def drafts(app, loaded_modules):
    return loaded_modules.modules['tax_es_forms'], loaded_modules.modules['tax_management']


def _filed_303(tm, year, quarter, boxes):
    from app import db
    db.session.add(tm.TaxForm(form_type='303', year=year, quarter=quarter,
                              file_path='', status='filed',
                              filed_boxes=json.dumps(boxes)))
    db.session.commit()


def test_box_110_from_previous_filed_quarter(drafts):
    forms, tm = drafts
    _filed_303(tm, 2026, 2, {'87': 50.0, '71': -200.0})
    draft = forms._draft_303(2026, 3)
    assert draft['boxes']['110'] == 250.0
    assert draft['meta']['carried_from'] == 'filed Modelo 303 2T 2026'


def test_box_110_in_q1_comes_from_q4_of_last_year(drafts):
    forms, tm = drafts
    _filed_303(tm, 2025, 4, {'87': 10.0, '71': -5.0})
    assert forms._draft_303(2026, 1)['boxes']['110'] == 15.0


def test_refunded_result_is_not_carried(drafts):
    forms, tm = drafts
    _filed_303(tm, 2025, 4, {'71': -80.0, '73': 80.0})
    assert forms._draft_303(2026, 1)['boxes']['110'] == 0.0


def test_no_filed_return_means_no_carried_credit(drafts):
    forms, _ = drafts
    draft = forms._draft_303(2026, 3)
    assert draft['boxes']['110'] == 0.0 and draft['meta']['carried_from'] is None


def test_draft_page_shows_new_boxes(drafts, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    page = client.get('/tax-forms-draft/303/2026/3').get_data(as_text=True)
    for box in ('data-box="59"', 'data-box="120"', 'data-box="30"', 'data-box="110"'):
        assert box in page
