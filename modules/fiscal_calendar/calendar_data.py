#!/usr/bin/env python3
"""
Spanish fiscal calendar dataset — local data, no external API.

CAVEMAN NOTE: AEAT publish no machine-readable calendar, so deadlines live here
as code. Windows generated from per-form rules for the covered years. Annual
update = edit COVERED_YEARS / FORM_RULES in this one file, then verify against
the official AEAT calendar (standing rule F3-D1).

Each deadline entry is a dict:
    form, period ('Q1'..'Q4' or 'annual'), period_label, year (fiscal year the
    filing covers), quarter (1-4 or None), window_start, window_end (date).
The `year`/`quarter` are what F10 later match a TaxForm against (form_type +
year + quarter).
"""

from datetime import date

# Bump + verify against AEAT when editing.
CALENDAR_VERSION = "2026.0"

# Fiscal years for which we ship deadlines. Filing windows for Q4 / annual VAT
# land in January of the FOLLOWING calendar year.
COVERED_YEARS = (2026, 2027)

# Quarterly forms: same window shape (day 1-20 of the month after quarter end;
# Q4 files 1-30 January of next year).
QUARTERLY_FORMS = ('303', '130', '349', '111')

# Which quarterly forms actually exist per form (all four quarters).
_QUARTER_WINDOWS = {
    1: ((4, 1), (4, 20)),    # Q1 -> 1-20 Apr
    2: ((7, 1), (7, 20)),    # Q2 -> 1-20 Jul
    3: ((10, 1), (10, 20)),  # Q3 -> 1-20 Oct
    # Q4 handled specially (rolls into next January).
}


def _quarterly_entries(year):
    entries = []
    for form in QUARTERLY_FORMS:
        for q in (1, 2, 3):
            (sm, sd), (em, ed) = _QUARTER_WINDOWS[q]
            entries.append({
                'form': form, 'period': f'Q{q}', 'period_label': f'Q{q} {year}',
                'year': year, 'quarter': q,
                'window_start': date(year, sm, sd),
                'window_end': date(year, em, ed),
            })
        # Q4 files 1-30 January of the following year.
        entries.append({
            'form': form, 'period': 'Q4', 'period_label': f'Q4 {year}',
            'year': year, 'quarter': 4,
            'window_start': date(year + 1, 1, 1),
            'window_end': date(year + 1, 1, 30),
        })
    return entries


def _annual_entries(year):
    return [
        {'form': '390', 'period': 'annual', 'period_label': f'Annual VAT {year}',
         'year': year, 'quarter': None,
         'window_start': date(year + 1, 1, 1), 'window_end': date(year + 1, 1, 30)},
        {'form': '100', 'period': 'annual', 'period_label': f'Renta {year}',
         'year': year, 'quarter': None,
         'window_start': date(year + 1, 4, 1), 'window_end': date(year + 1, 6, 30)},
    ]


def all_deadlines():
    """Full deadline list across the covered years, sorted by window end."""
    entries = []
    for year in COVERED_YEARS:
        entries.extend(_quarterly_entries(year))
        entries.extend(_annual_entries(year))
    return sorted(entries, key=lambda e: e['window_end'])


def _state(entry, today):
    if today < entry['window_start']:
        return 'upcoming'
    if today <= entry['window_end']:
        return 'open'
    return 'closed'


def upcoming(days, today=None, selected_forms=None):
    """Deadlines whose filing window is still ahead or open within `days`.

    Returns each entry enriched with `state` (upcoming/open/closed) and
    `days_to_end` (days until the window closes; negative = already closed).
    Only forms in `selected_forms` are kept (None = all forms).
    """
    today = today or date.today()
    out = []
    for entry in all_deadlines():
        if selected_forms is not None and entry['form'] not in selected_forms:
            continue
        state = _state(entry, today)
        if state == 'closed':
            continue
        days_to_start = (entry['window_start'] - today).days
        if state == 'upcoming' and days_to_start > days:
            continue
        enriched = dict(entry)
        enriched['state'] = state
        enriched['days_to_end'] = (entry['window_end'] - today).days
        enriched['days_to_start'] = days_to_start
        out.append(enriched)
    return out


def available_forms():
    """Distinct forms present in the dataset."""
    return sorted({e['form'] for e in all_deadlines()})
