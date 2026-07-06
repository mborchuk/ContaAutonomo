"""F6 — RETA advisor: bracket lookup + projection engine tests."""
from datetime import date

import pytest

from modules.reta_advisor.brackets import BRACKETS, bracket_for
from modules.reta_advisor.engine import project


# --- bracket lookup ----------------------------------------------------------

def test_bracket_edges():
    # <=670 -> lowest bracket (200).
    assert bracket_for(670.0)[3] == 200.00
    assert bracket_for(670.01)[3] == 220.00
    # Split at 1700: reduced vs general table.
    assert bracket_for(1700.0)[3] == 294.00
    assert bracket_for(1700.01)[3] == 350.00
    # Top open-ended bracket.
    assert bracket_for(999999.0)[3] == 590.00


def test_bracket_negative_yield_lands_in_minimum():
    idx, lo, hi, quota = bracket_for(-500.0)
    assert idx == 0
    assert quota == 200.00


def test_bracket_table_is_monotonic():
    prev_hi = 0.0
    for lo, hi, quota in BRACKETS:
        assert lo == pytest.approx(prev_hi)
        if hi is not None:
            assert hi > lo
            prev_hi = hi


# --- projection ---------------------------------------------------------------

def test_project_basic_bracket_and_regularization():
    # 6 months: 18000 income, 3000 expenses, 7% deduction.
    # net = 15000 * 0.93 = 13950 -> 2325/month -> bracket 2330 (390/mo? no:
    # 2325 falls in 2030-2330 -> 390.00).
    result = project(18000.0, 3000.0, months_elapsed=6,
                     current_monthly_quota=294.0)
    assert result['monthly_net_yield'] == 2325.0
    assert result['recommended_monthly_quota'] == 390.00
    # projected annual 4680 vs paying 294*12=3528 -> owes 1152.
    assert result['regularization_delta'] == pytest.approx(1152.0)


def test_project_on_track_no_delta():
    # Monthly net 1600 -> quota 294; paying 294 -> delta 0.
    result = project(1600.0 / 0.93, 0.0, months_elapsed=1,
                     current_monthly_quota=294.0)
    assert result['recommended_monthly_quota'] == 294.00
    assert result['regularization_delta'] == pytest.approx(0.0)


def test_project_uses_actual_paid_when_given():
    # Paid 1000 in 4 months, current quota 200 -> projection 1000 + 200*8 = 2600.
    result = project(0.0, 0.0, months_elapsed=4,
                     current_monthly_quota=200.0, paid_ytd=1000.0)
    assert result['paid_projection'] == 2600.0
    # Zero yield -> minimum bracket 200*12 = 2400 -> refund 200.
    assert result['regularization_delta'] == pytest.approx(-200.0)


def test_project_clamps_months():
    result = project(1200.0, 0.0, months_elapsed=0, current_monthly_quota=0.0)
    assert result['months_elapsed'] == 1


# --- module smoke (DB) ---------------------------------------------------------

def test_module_projection_smoke(loaded_modules):
    ra = loaded_modules.modules['reta_advisor']
    proj = ra._projection(today=date(2026, 6, 30))
    # Data varies with other tests' leftovers; only shape is asserted.
    assert 'recommended_monthly_quota' in proj
    assert proj['months_elapsed'] == 6
    obligations = ra.get_tax_obligations({'currency_symbol': '€'})
    assert obligations['tax_total'] == 0        # display-only, no double count
    assert obligations['deductions'] == 0
    assert any('RETA advisor' in n for n in obligations['notes'])
