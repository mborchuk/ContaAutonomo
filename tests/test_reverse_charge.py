"""Reverse charge (inversión del sujeto pasivo) on purchases — Modelo 303.

A service bought from a supplier outside Spain with no Spanish VAT on the
invoice ("tax to be paid on reverse charge basis"): the buyer declares the
VAT and deducts it in the same return. Synthetic figures.
"""
from datetime import date
from types import SimpleNamespace as NS

import pytest

from modules.tax_es_forms.calculator import compute_modelo_303


def rc_expense(net, region, category='Services', deductible=True, vat_rate=0.0):
    return NS(net_amount=net, vat_amount=0.0, vat_rate=vat_rate, amount=net,
              category=category, deductible=deductible, deductible_pct=100.0,
              reverse_charge=region, currency='EUR', expense_date=date(2026, 7, 9))


def is_equipment(exp, net_eur):
    return exp.category == 'Equipment' and net_eur >= 300


def test_non_eu_supplier_boxes_12_13_and_28_29():
    b = compute_modelo_303([], [rc_expense(20.0, 'non_eu')], 0.21)['boxes']
    assert (b['12'], b['13']) == (20.0, 4.2)
    assert (b['28'], b['29']) == (20.0, 4.2)
    assert (b['27'], b['45'], b['46']) == (4.2, 4.2, 0.0)


def test_eu_supplier_boxes_10_11_and_36_to_39():
    b = compute_modelo_303(
        [], [rc_expense(100.0, 'eu'), rc_expense(500.0, 'eu', 'Equipment')],
        0.21, is_equipment=is_equipment)['boxes']
    assert (b['10'], b['11']) == (600.0, 126.0)
    assert (b['36'], b['37']) == (100.0, 21.0)
    assert (b['38'], b['39']) == (500.0, 105.0)
    assert b['46'] == 0.0


def test_not_deductible_is_declared_but_not_deducted():
    b = compute_modelo_303([], [rc_expense(50.0, 'non_eu', deductible=False)], 0.21)['boxes']
    assert (b['13'], b['29'], b['46']) == (10.5, 0.0, 10.5)


def test_reduced_rate_on_the_expense_is_used():
    b = compute_modelo_303([], [rc_expense(100.0, 'non_eu', vat_rate=10.0)], 0.21)['boxes']
    assert b['13'] == 10.0


def test_expense_without_reverse_charge_and_no_vat_stays_out():
    b = compute_modelo_303([], [rc_expense(20.0, None)], 0.21)['boxes']
    assert (b['12'], b['28'], b['27']) == (0.0, 0.0, 0.0)


def test_expense_form_and_api_keep_the_marker(app, loaded_modules, client):
    from app import Expense

    with client.session_transaction() as sess:
        sess['authenticated'] = True
    assert 'name="reverse_charge"' in client.get('/expenses/create').get_data(as_text=True)
    client.post('/expenses/create', data={
        'amount': '18.00', 'currency': 'EUR', 'category': 'Services',
        'description': 'synthetic subscription', 'expense_date': '2026-07-09',
        'vat_rate': '0', 'net_amount': '18.00', 'vat_amount': '0',
        'deductible': 'on', 'deductible_pct': '100', 'reverse_charge': 'non_eu'})
    exp = Expense.query.filter_by(description='synthetic subscription').one()
    assert exp.reverse_charge == 'non_eu'
    serialized = loaded_modules.modules['expenses']._api_serialize_expense(exp)
    assert serialized['reverse_charge'] == 'non_eu'
