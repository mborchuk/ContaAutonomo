"""F4 — Expense VAT breakdown & deductibility tests."""
from datetime import date

import pytest

from modules.expenses.index import ExpensesModule


@pytest.fixture
def expenses_module(loaded_modules):
    return loaded_modules.modules['expenses']


# --- F4-D1: both Expense declarations stay in sync -------------------------

def test_expense_model_column_parity():
    """Core app.py Expense and the module mirror must expose the same columns."""
    from app import Expense as CoreExpense
    from sqlalchemy import inspect as sa_inspect

    core_cols = {c.key for c in sa_inspect(CoreExpense).columns}
    for field in ('net_amount', 'vat_rate', 'vat_amount', 'deductible', 'deductible_pct'):
        assert field in core_cols, f'core Expense missing {field}'


def test_module_expense_has_vat_columns(expenses_module):
    from sqlalchemy import inspect as sa_inspect
    mod_cols = {c.key for c in sa_inspect(expenses_module.Expense).columns}
    from app import Expense as CoreExpense
    core_cols = {c.key for c in sa_inspect(CoreExpense).columns}
    # Parity for the F4 fields across both declaration sites.
    for field in ('net_amount', 'vat_rate', 'vat_amount', 'deductible', 'deductible_pct'):
        assert field in mod_cols, f'module Expense missing {field}'
        assert field in core_cols


# --- F4-D2: gross+rate splitting -------------------------------------------

def test_parse_vat_from_gross_and_rate():
    fields = ExpensesModule._parse_vat_fields({'vat_rate': '21'}, gross=121.0)
    assert fields['net_amount'] == 100.0
    assert fields['vat_amount'] == 21.0
    assert fields['vat_rate'] == 21.0
    assert fields['deductible'] is False   # checkbox absent -> unchecked
    assert fields['deductible_pct'] == 100.0


def test_parse_vat_explicit_net_overrides_split():
    fields = ExpensesModule._parse_vat_fields(
        {'vat_rate': '10', 'net_amount': '90', 'deductible': 'on',
         'deductible_pct': '50'}, gross=100.0)
    assert fields['net_amount'] == 90.0
    assert fields['vat_amount'] == 10.0       # gross - net
    assert fields['deductible'] is True
    assert fields['deductible_pct'] == 50.0


def test_parse_vat_no_rate_leaves_unknown():
    fields = ExpensesModule._parse_vat_fields({}, gross=50.0)
    assert fields['net_amount'] is None
    assert fields['vat_amount'] is None


# --- F4-D4: tax obligations use real VAT, count missing --------------------

def test_tax_obligations_uses_real_vat_and_counts_missing(expenses_module):
    from app import db

    em = expenses_module
    # One expense with VAT data, one legacy (no VAT).
    db.session.add(em.Expense(amount=121.0, currency='EUR', category='Software',
                              expense_date=date(2026, 3, 1), net_amount=100.0,
                              vat_amount=21.0, vat_rate=21.0, deductible=True,
                              deductible_pct=100.0))
    db.session.add(em.Expense(amount=50.0, currency='EUR', category='Legacy',
                              expense_date=date(2026, 3, 2)))
    db.session.commit()

    settings = db.session.query(em.Settings).first()
    ctx = {'current_year': 2026, 'base_currency': 'EUR', 'currency_symbol': '€',
           'settings': settings, 'vat_collected': 0}
    result = em.get_tax_obligations(ctx)
    # vat_paid = 21 (real) + 50*0.21 (legacy estimate) = 31.5 -> vat_to_pay = -31.5
    assert round(result['tax_total'], 2) == -31.5
    assert any('lack VAT data' in n for n in result['notes'])
