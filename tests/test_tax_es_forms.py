"""Unit tests for the tax_es_forms Modelo 303/130 aggregation engine.

Pure-math tests: feed plain objects, assert box numbers. No DB, no Flask.
"""
from datetime import date
from types import SimpleNamespace

from modules.tax_es_forms.calculator import (
    compute_modelo_303,
    compute_modelo_130,
    quarter_of,
    quarter_bounds,
)


def _inv(amount_eur, tax_type='standard', status='pending', d=date(2026, 2, 1),
         snap_vat_rate=None, snap_vat_amount=None, snap_taxable_base=None):
    customer = SimpleNamespace(tax_type=tax_type) if tax_type else None
    return SimpleNamespace(amount_eur=amount_eur, customer=customer,
                           status=status, invoice_date=d,
                           snap_vat_rate=snap_vat_rate, snap_vat_amount=snap_vat_amount,
                           snap_taxable_base=snap_taxable_base)


def _exp(amount, currency='EUR', d=date(2026, 2, 1), **extra):
    return SimpleNamespace(amount=amount, currency=currency, expense_date=d, **extra)


# --- quarter helpers --------------------------------------------------------

def test_quarter_of_boundaries():
    assert quarter_of(date(2026, 1, 1)) == 1
    assert quarter_of(date(2026, 3, 31)) == 1
    assert quarter_of(date(2026, 4, 1)) == 2
    assert quarter_of(date(2026, 12, 31)) == 4


def test_quarter_bounds_q1_and_q4():
    assert quarter_bounds(2026, 1) == (date(2026, 1, 1), date(2026, 3, 31))
    assert quarter_bounds(2026, 4) == (date(2026, 10, 1), date(2026, 12, 31))


# --- Modelo 303 -------------------------------------------------------------

def test_303_output_vat_only_on_standard_customers():
    invoices = [
        _inv(1000.0, tax_type='standard'),
        _inv(500.0, tax_type='eu_b2b'),    # reverse charge -> no output VAT
        _inv(300.0, tax_type='non_eu'),    # export -> no output VAT
    ]
    result = compute_modelo_303(invoices, [], vat_rate=0.21)
    assert result['boxes']['01'] == 1000.0     # base only from standard
    assert result['boxes']['03'] == 210.0      # 21% of 1000
    assert result['boxes']['27'] == 210.0
    # No deductible expenses -> result equals output VAT.
    assert result['boxes']['71'] == 210.0


def test_303_excludes_cancelled_invoices():
    invoices = [
        _inv(1000.0, tax_type='standard', status='pending'),
        _inv(1000.0, tax_type='standard', status='cancelled'),
    ]
    result = compute_modelo_303(invoices, [], vat_rate=0.21)
    assert result['boxes']['01'] == 1000.0


def test_303_counts_expenses_missing_vat_data():
    expenses = [_exp(121.0), _exp(50.0)]  # no vat_amount attr -> missing
    result = compute_modelo_303([], expenses, vat_rate=0.21)
    assert result['boxes']['29'] == 0.0
    assert result['meta']['missing_expense_vat_count'] == 2


def test_303_deducts_expense_vat_when_present():
    # F4-shaped expense: carries explicit VAT fields.
    expenses = [
        _exp(121.0, net_amount=100.0, vat_amount=21.0, deductible=True,
             deductible_pct=100.0),
        _exp(110.0, net_amount=100.0, vat_amount=10.0, deductible=True,
             deductible_pct=50.0),   # 50% deductible (e.g. vehicle)
    ]
    result = compute_modelo_303([], expenses, vat_rate=0.21)
    assert result['boxes']['28'] == 150.0      # 100 + 100*0.5
    assert result['boxes']['29'] == 26.0       # 21 + 10*0.5
    assert result['boxes']['45'] == 26.0
    assert result['meta']['missing_expense_vat_count'] == 0


def test_303_uses_fiscal_snapshot_when_present():
    # Issued invoice: snapshot wins over the live customer tax_type.
    invoices = [_inv(1000.0, tax_type='non_eu', status='issued',
                     snap_vat_rate=21.0, snap_taxable_base=1000.0, snap_vat_amount=210.0)]
    result = compute_modelo_303(invoices, [], vat_rate=0.10)  # live rate ignored
    assert result['boxes']['01'] == 1000.0
    assert result['boxes']['03'] == 210.0   # from snapshot, not 0.10*1000


def test_303_snapshot_zero_rate_excludes_from_base():
    # Issued eu_b2b invoice: snapshot rate 0 -> not in the general base.
    invoices = [_inv(500.0, tax_type='eu_b2b', status='issued',
                     snap_vat_rate=0.0, snap_taxable_base=500.0, snap_vat_amount=0.0)]
    result = compute_modelo_303(invoices, [], vat_rate=0.21)
    assert result['boxes']['01'] == 0.0
    assert result['boxes']['03'] == 0.0


def test_130_prefers_net_amount_over_gross():
    invoices = [_inv(1000.0, tax_type='standard')]
    # F4 expense: gross 121, net 100 -> IRPF gastos should use net (100).
    expenses = [_exp(121.0, net_amount=100.0, vat_amount=21.0)]
    result = compute_modelo_130(invoices, expenses, irpf_rate=0.20)
    assert result['boxes']['02'] == 100.0


def test_303_result_is_devengado_minus_deducible():
    invoices = [_inv(1000.0, tax_type='standard')]
    expenses = [_exp(121.0, net_amount=100.0, vat_amount=21.0)]
    result = compute_modelo_303(invoices, expenses, vat_rate=0.21)
    assert result['boxes']['27'] == 210.0
    assert result['boxes']['45'] == 21.0
    assert result['boxes']['71'] == 189.0      # 210 - 21


# --- Modelo 130 -------------------------------------------------------------

def test_130_basic_quarter_no_prior_payments():
    invoices = [_inv(3000.0, tax_type='standard')]
    expenses = [_exp(1000.0)]
    result = compute_modelo_130(invoices, expenses, irpf_rate=0.20)
    assert result['boxes']['01'] == 3000.0
    assert result['boxes']['02'] == 1000.0
    assert result['boxes']['03'] == 2000.0
    assert result['boxes']['04'] == 400.0      # 20% of 2000
    assert result['boxes']['05'] == 0.0
    assert result['boxes']['07'] == 400.0


def test_130_subtracts_prior_quarter_payments():
    # YTD to Q2: 6000 income, 2000 expenses -> rendimiento 4000, pago 800.
    # Prior (Q1): 3000 income, 1000 expenses -> prior pago 400.
    invoices = [_inv(6000.0, tax_type='standard')]
    expenses = [_exp(2000.0)]
    result = compute_modelo_130(invoices, expenses, irpf_rate=0.20,
                               prior_income=3000.0, prior_expenses=1000.0)
    assert result['boxes']['04'] == 800.0
    assert result['boxes']['05'] == 400.0
    assert result['boxes']['07'] == 400.0      # 800 - 400


def test_130_never_negative():
    invoices = [_inv(500.0, tax_type='standard')]
    expenses = [_exp(2000.0)]                  # loss-making quarter
    result = compute_modelo_130(invoices, expenses, irpf_rate=0.20)
    assert result['boxes']['03'] == -1500.0    # rendimiento can be negative
    assert result['boxes']['04'] == 0.0        # but pago floors at 0
    assert result['boxes']['07'] == 0.0


def test_130_converts_expense_currency():
    invoices = [_inv(1000.0, tax_type='standard')]
    # 100 USD expense, convert fn doubles it to EUR.
    expenses = [_exp(100.0, currency='USD')]
    convert = lambda amount, currency, when: amount * 2 if currency == 'USD' else amount
    result = compute_modelo_130(invoices, expenses, irpf_rate=0.20,
                               convert_expense=convert)
    assert result['boxes']['02'] == 200.0
