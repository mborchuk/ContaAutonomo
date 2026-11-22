"""Reverse charge must match where the supplier is (Spanish Tax Forms,
modules/tax_es_forms/supplier_region.py, registry function
vat.reverse_charge_check): a
notice on the expense (deduction checks) and a check on the Modelo 303
draft. Synthetic suppliers only."""
from datetime import date
from types import SimpleNamespace

import pytest

from modules.expenses import deduction_rules
from modules.tax_es_forms.supplier_region import (EU, NON_EU, SPAIN, region_of,
                                                  reverse_charge_problem)


@pytest.mark.parametrize('country, vat, region', [
    ('United States', None, NON_EU), ('USA', None, NON_EU), ('us', None, NON_EU),
    ('Ireland', None, EU), ('IE', None, EU), ('Alemania', None, EU), ('Greece', None, EU),
    ('Spain', None, SPAIN), ('España', None, SPAIN),
    (None, 'IE1234567T', EU), (None, 'EL123456789', EU), (None, 'ESB12345678', SPAIN),
    (None, 'EU372000041', NON_EU),          # non-Union one-stop-shop number
    (None, 'GB123456789', NON_EU), (None, 'CHE-123.456.789', NON_EU),
    (None, '12-3456789', None), ('Atlantis', None, None), (None, None, None),
    ('', 'XI123456789', None),
    ('United States', 'IE1234567T', NON_EU),   # a recognised country wins
])
def test_region_of(country, vat, region):
    assert region_of(country, vat) == region


def _expense(mark, country=None, vat=None, name='Synthetic Supplier Inc.'):
    supplier = SimpleNamespace(name=name, country=country, vat_number=vat)
    return SimpleNamespace(id=1, reverse_charge=mark, contractor=supplier,
                           category='Services', expense_date=date(2026, 8, 9),
                           deductible=True, deductible_pct=100.0, amount=18.0,
                           currency='EUR', description='Subscription')


def test_eu_mark_for_a_us_supplier_is_flagged():
    problem = reverse_charge_problem(_expense('eu', country='United States'))
    assert 'outside the EU (United States)' in problem and 'boxes 12/13' in problem


def test_non_eu_mark_for_an_eu_supplier_is_flagged():
    problem = reverse_charge_problem(_expense('non_eu', vat='IE1234567T'))
    assert 'another EU country (VAT number IE1234567T)' in problem and '349' in problem


def test_reverse_charge_for_a_spanish_supplier_is_flagged():
    assert 'is in Spain' in reverse_charge_problem(_expense('eu', country='Spain'))


@pytest.mark.parametrize('expense', [
    _expense('eu', country='Ireland'), _expense('non_eu', country='United States'),
    _expense('', country='United States'), _expense(None, country='Spain'),
    _expense('eu', country='Atlantis'),                   # unknown: nothing said
    SimpleNamespace(reverse_charge='eu', contractor=None),
])
def test_matching_or_unknown_is_not_flagged(expense):
    assert reverse_charge_problem(expense) is None


def test_deduction_check_notice():
    found = deduction_rules.check_expenses([_expense('eu', country='USA')],
                                           region_check=reverse_charge_problem)
    (notice,) = found[1]
    assert notice['rule_id'] == 'reverse_charge_region' and notice['severity'] == 'check'
    assert 'LIVA' in notice['source'] and 'outside the EU' in notice['message']
    assert deduction_rules.check_expenses([_expense('non_eu', country='USA')],
                                          region_check=reverse_charge_problem) == {}
    # Without a region check (Spanish Tax Forms off) the rule says nothing.
    assert deduction_rules.check_expenses([_expense('eu', country='USA')]) == {}


def test_registry_function_takes_plain_values(loaded_modules):
    functions = loaded_modules.functions
    assert functions.provider('vat.reverse_charge_check') == 'tax_es_forms'
    out = functions.call('vat.reverse_charge_check', mark='eu', country='United States',
                         vat_number='', supplier='Synthetic Cloud Inc.')
    assert 'outside the EU (United States)' in out['problem']
    assert functions.call('vat.reverse_charge_check', mark='non_eu',
                          country='United States')['problem'] is None


def test_expenses_have_no_region_notice_without_the_function(loaded_modules, monkeypatch):
    expenses = loaded_modules.modules['expenses']
    monkeypatch.setattr(loaded_modules.functions, 'available', lambda name: False)
    assert expenses._region_check() is None


def test_303_draft_and_expense_page_list_the_mismatch(loaded_modules):
    from app import Contractor, Expense, db
    forms = loaded_modules.modules['tax_es_forms']
    supplier = Contractor(name='Synthetic Cloud Inc.', country='United States')
    db.session.add(supplier)
    db.session.flush()
    exp = Expense(contractor_id=supplier.id, amount=18.0, currency='EUR',
                  category='Services', description='Synthetic subscription',
                  expense_date=date(2037, 8, 9), vat_amount=0.0, vat_rate=0.0,
                  net_amount=18.0, reverse_charge='eu')
    db.session.add(exp)
    db.session.commit()
    try:
        checks = forms._draft_303(2037, 3)['meta']['checks']
        assert any('Synthetic Cloud Inc. (09/08/2037)' in c and 'reverse charge' in c
                   for c in checks)
        # The Expenses module shows the same mismatch on the expense.
        active, _ = loaded_modules.modules['expenses'].deduction_notices([exp])
        assert [n['rule_id'] for n in active[exp.id]] == ['reverse_charge_region']
    finally:
        db.session.delete(exp)
        db.session.delete(supplier)
        db.session.commit()
