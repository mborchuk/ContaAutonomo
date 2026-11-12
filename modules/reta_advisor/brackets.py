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

Annual update (each January): add the new year to BRACKET_TABLES from the BOE
orden de cotización. Years without a table use the latest earlier one and the
advisor shows a "verify" note.
"""

BRACKET_TABLE_VERSION = "2026.1"

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


def brackets_for_year(year):
    """Return (table_year, brackets, is_fallback)."""
    from year_tables import resolve
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
