#!/usr/bin/env python3
"""
Cadence math for recurring invoices — pure functions, no Flask, no DB.

CAVEMAN NOTE: keep dumb so tests feed dates and check dates. Monthly and
quarterly only (per PM scope, closed-ended picker).
"""

from datetime import date

CADENCES = ('monthly', 'quarterly')

# Safety cap: never materialize more than this many missed periods in one run
# (protects against a template with next_run_date years in the past).
MAX_CATCHUP_PERIODS = 12


def add_cadence(d, cadence):
    """Return `d` advanced by one cadence period, clamping the day-of-month.

    Jan 31 + monthly -> Feb 28/29 (clamped), like most billing systems do.
    """
    if cadence not in CADENCES:
        raise ValueError(f'Unknown cadence: {cadence}')
    months = 1 if cadence == 'monthly' else 3
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year, month):
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def due_dates(next_run_date, today, cadence):
    """All period dates due on or before `today`, capped at MAX_CATCHUP_PERIODS.

    Returns (list_of_due_dates, new_next_run_date). Idempotent by construction:
    caller persists new_next_run_date, so a rerun the same day yields [].
    """
    due = []
    cursor = next_run_date
    while cursor <= today and len(due) < MAX_CATCHUP_PERIODS:
        due.append(cursor)
        cursor = add_cadence(cursor, cadence)
    return due, cursor
