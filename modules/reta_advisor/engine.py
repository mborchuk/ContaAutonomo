#!/usr/bin/env python3
"""
RETA projection engine — pure functions, no Flask, no DB.

CAVEMAN NOTE: same discipline as tax_es_forms/calculator.py — module fetch
data, engine only count. Simple linear annualization (v1). Everything is
ESTIMATE — regularization is settled by Seguridad Social after year end.

Not modeled in v1 (flagged, not calculated): tarifa plana, pluriactividad,
mid-year base changes already made this year.
"""

from .brackets import bracket_for, GENERIC_DEDUCTION_PCT


def project(ytd_income, ytd_expenses, months_elapsed,
            current_monthly_quota=0.0, paid_ytd=None,
            deduction_pct=GENERIC_DEDUCTION_PCT):
    """Project the RETA position from year-to-date figures.

    Args:
        ytd_income: EUR income (issued/paid invoices), 1 Jan -> today.
        ytd_expenses: EUR deductible expenses (net where known) same window.
        months_elapsed: how many months of the year have data (>= 1).
        current_monthly_quota: what the user pays now per month.
        paid_ytd: total actually paid so far (None -> current quota × months).
        deduction_pct: generic deduction (7% general / 3% corporate).

    Returns dict: monthly_net_yield, bracket (index/lo/hi/quota),
    projected_annual_quota, paid_projection, regularization_delta
    (positive = user will owe money at regularization).
    """
    months = max(1, min(12, months_elapsed))
    net_yield_ytd = (ytd_income - ytd_expenses) * (1.0 - deduction_pct / 100.0)
    monthly_net = net_yield_ytd / months

    idx, lo, hi, quota = bracket_for(monthly_net)

    projected_annual_quota = quota * 12
    if paid_ytd is None:
        paid_ytd = current_monthly_quota * months
    # Assume the user keeps paying the current quota for the rest of the year.
    paid_projection = paid_ytd + current_monthly_quota * (12 - months)
    regularization_delta = projected_annual_quota - paid_projection

    return {
        'ytd_income': ytd_income,
        'ytd_expenses': ytd_expenses,
        'deduction_pct': deduction_pct,
        'monthly_net_yield': round(monthly_net, 2),
        'bracket_index': idx,
        'bracket_low': lo,
        'bracket_high': hi,
        'recommended_monthly_quota': quota,
        'current_monthly_quota': current_monthly_quota,
        'projected_annual_quota': round(projected_annual_quota, 2),
        'paid_projection': round(paid_projection, 2),
        'regularization_delta': round(regularization_delta, 2),
        'months_elapsed': months,
    }
