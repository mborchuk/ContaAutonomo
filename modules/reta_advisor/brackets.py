#!/usr/bin/env python3
"""
RETA contribution brackets — versioned config data, keyed by year.

The bracket table changes every year by BOE (standing rule F6-D1).
Values below follow the published cotización-by-real-income table trajectory
(reduced table ≤ €1,700/month, general table above; monthly quotas ≈ €200–590,
MEI included). VERIFY against current Seguridad Social / BOE sources before
each fiscal year — table version is shown in the UI.

Sources to check on annual update:
  - https://www.seg-social.es (Cotización RETA / rendimientos netos)
  - BOE: orden de cotización for the year

Each bracket: (monthly net yield lower bound exclusive, upper bound inclusive,
monthly quota EUR). Upper bound None = no limit.

Annual update (each January): the owner checks the new year in Taxes → Tax
Data and confirms the table or enters the new brackets and rate (`apply_values`
below). Years without a table use the latest earlier one and the advisor shows
a "verify" note until then. A release adds the year here.
"""
from .yearly import resolve

BRACKET_TABLE_VERSION = "2026.2"

# Generic deduction applied to net yield before bracket lookup (7% general,
# 3% for corporate autónomos — setting on the module page).
GENERIC_DEDUCTION_PCT = 7.0

# (lower_exclusive, upper_inclusive, monthly_quota)
BRACKETS = [
    (0.0,     670.0,  200.00),   # reduced table
    (670.0,   900.0,  220.00),
    (900.0,  1166.70, 260.00),
    (1166.70, 1300.0, 291.00),
    (1300.0,  1500.0, 294.00),
    (1500.0,  1700.0, 294.00),
    (1700.0,  1850.0, 350.00),   # general table
    (1850.0,  2030.0, 370.00),
    (2030.0,  2330.0, 390.00),
    (2330.0,  2760.0, 415.00),
    (2760.0,  3190.0, 440.00),
    (3190.0,  3620.0, 465.00),
    (3620.0,  4050.0, 490.00),
    (4050.0,  6000.0, 530.00),
    (6000.0,  None,   590.00),
]


# Tables per year. BRACKETS above is the 2026 table.
BRACKET_TABLES = {
    2026: BRACKETS,
}

# Total RETA contribution rate on the chosen base (quota = base × rate).
# 2026: 31.50 % = contingencias comunes 28.30 + profesionales 1.30 + cese de
# actividad 0.90 + formación profesional 0.10 + MEI 0.90, per the 2026
# cotización order (Orden PJC/297/2026, BOE 31 March 2026). VERIFY each year
# against the Seguridad Social; used by the IRPF what-if simulator to turn a
# base into a monthly quota.
CONTRIBUTION_RATES = {
    2026: 31.50,
}


def contribution_rate_for_year(year):
    """Return (table_year, rate_pct, is_fallback)."""
    return resolve(CONTRIBUTION_RATES, year)


def brackets_for_year(year):
    """Return (table_year, brackets, is_fallback)."""
    return resolve(BRACKET_TABLES, year)


def bracket_for(monthly_net_yield, brackets=None):
    """Return (index, lower, upper, quota) for a monthly net yield in EUR.

    `brackets` defaults to the 2026 table. Negative/zero yields land in the
    lowest bracket (minimum quota still due).
    """
    brackets = brackets or BRACKETS
    value = max(0.0, monthly_net_yield)
    for idx, (lo, hi, quota) in enumerate(brackets):
        if hi is None or value <= hi:
            if value > lo or idx == 0:
                return idx, lo, hi, quota
    # Fallback: top bracket (should be unreachable).
    idx = len(brackets) - 1
    lo, hi, quota = brackets[idx]
    return idx, lo, hi, quota


# --- Values the owner can change in Tax Data ---------------------------------- #

def fields(brackets, rate_pct):
    """The RETA table as Tax Data fields: the rate, and for each bracket its
    upper limit (not the last: no limit) and its monthly quota."""
    out = [{'key': 'rate', 'label': 'Contribution rate (%)', 'kind': 'percent',
            'value': rate_pct}]
    for i, (_lo, hi, quota) in enumerate(brackets):
        row = f'Bracket {i + 1}'
        if hi is not None:
            out.append({'key': f'upper_{i}', 'label': f'{row}: up to (EUR a month)',
                        'kind': 'money', 'value': hi, 'row': row,
                        'col': 'Net income up to (EUR a month)'})
        out.append({'key': f'quota_{i}', 'label': f'{row}: monthly quota (EUR)',
                    'kind': 'money', 'value': quota, 'row': row,
                    'col': 'Monthly quota (EUR)'})
    return out


def apply_values(brackets, rate_pct, values):
    """(brackets, rate_pct) with the values entered in Tax Data. Each lower
    limit is the upper limit of the bracket before it."""
    out, lower = [], 0.0
    for i, (_lo, hi, quota) in enumerate(brackets):
        upper = values.get(f'upper_{i}', hi) if hi is not None else None
        out.append((lower, upper, values.get(f'quota_{i}', quota)))
        lower = upper
    return out, values.get('rate', rate_pct)


def validate(brackets, values):
    """A sentence when the entered values cannot be a bracket table, else None."""
    new, rate = apply_values(brackets, 0.0, values)
    if 'rate' in values and not 0 < values['rate'] < 100:
        return 'The contribution rate must be between 0 and 100 %.'
    uppers = [hi for _lo, hi, _q in new if hi is not None]
    if any(b <= a for a, b in zip(uppers, uppers[1:])) or (uppers and uppers[0] <= 0):
        return 'Each bracket must end above the one before it.'
    if any(q <= 0 for _lo, _hi, q in new):
        return 'Every monthly quota must be above zero.'
    return None
