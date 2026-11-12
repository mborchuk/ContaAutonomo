#!/usr/bin/env python3
"""
Spanish fiscal calendar dataset — local data, no external API.

AEAT publishes no machine-readable calendar, so the deadlines are generated
here from per-form rules for any fiscal year. Dates that AEAT moves in a given
year (for example around a national holiday) go into WINDOW_OVERRIDES.

Annual update (each January): compare the generated dates for the new fiscal
year with the official AEAT calendar, add overrides where they differ, then add
the year to VERIFIED_YEARS and bump CALENDAR_VERSION. Years outside
VERIFIED_YEARS still get deadlines, and the UI shows a "verify" warning.

Each deadline entry is a dict:
    form, period ('Q1'..'Q4' or 'annual'), period_label, year (fiscal year the
    filing covers), quarter (1-4 or None), window_start, window_end (date),
    verified (bool: the year is in VERIFIED_YEARS).
The `year`/`quarter` are what Tax Management matches a TaxForm against
(form_type + year + quarter).
"""

from datetime import date, timedelta

# Bump + verify against AEAT when editing.
CALENDAR_VERSION = "2026.1"

# Fiscal years whose generated dates were checked against the AEAT calendar.
VERIFIED_YEARS = (2026, 2027)

# Quarterly forms: day 1-20 of the month after the quarter end; Q4 files
# 1-30 January of the next year.
QUARTERLY_FORMS = ('303', '130', '349', '111')

_QUARTER_WINDOWS = {
    1: ((4, 1), (4, 20)),    # Q1 -> 1-20 Apr
    2: ((7, 1), (7, 20)),    # Q2 -> 1-20 Jul
    3: ((10, 1), (10, 20)),  # Q3 -> 1-20 Oct
    # Q4 handled specially (rolls into next January).
}

# Per-year exceptions: (fiscal_year, form, period) -> (window_start, window_end).
# Use for dates AEAT moves that the rules below do not produce.
WINDOW_OVERRIDES = {}


def _next_working_day(day):
    """A deadline on a Saturday or Sunday moves to the following Monday
    (Ley 39/2015, art. 30.5). National holidays go into WINDOW_OVERRIDES."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _entry(form, period, label, year, quarter, start, end):
    start, end = WINDOW_OVERRIDES.get((year, form, period), (start, _next_working_day(end)))
    return {
        'form': form, 'period': period, 'period_label': label,
        'year': year, 'quarter': quarter,
        'window_start': start, 'window_end': end,
        'verified': year in VERIFIED_YEARS,
    }


def _quarterly_entries(year):
    entries = []
    for form in QUARTERLY_FORMS:
        for q in (1, 2, 3):
            (sm, sd), (em, ed) = _QUARTER_WINDOWS[q]
            entries.append(_entry(form, f'Q{q}', f'Q{q} {year}', year, q,
                                  date(year, sm, sd), date(year, em, ed)))
        entries.append(_entry(form, 'Q4', f'Q4 {year}', year, 4,
                              date(year + 1, 1, 1), date(year + 1, 1, 30)))
    return entries


def _annual_entries(year):
    return [
        _entry('390', 'annual', f'Annual VAT {year}', year, None,
               date(year + 1, 1, 1), date(year + 1, 1, 30)),
        _entry('100', 'annual', f'Renta {year}', year, None,
               date(year + 1, 4, 1), date(year + 1, 6, 30)),
    ]


def deadlines_for_year(year):
    """All deadlines for one fiscal year, sorted by window end."""
    entries = _quarterly_entries(year) + _annual_entries(year)
    return sorted(entries, key=lambda e: e['window_end'])


def all_deadlines(years=None, today=None):
    """Deadlines for `years` (default: previous, current and next fiscal year
    relative to `today`), sorted by window end. The default covers every
    window that can be open or upcoming today."""
    if years is None:
        current = (today or date.today()).year
        years = (current - 1, current, current + 1)
    entries = []
    for year in years:
        entries.extend(deadlines_for_year(year))
    return sorted(entries, key=lambda e: e['window_end'])


def is_verified(year):
    return year in VERIFIED_YEARS


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
    years = range(today.year - 1, today.year + 2 + days // 365)
    out = []
    for entry in all_deadlines(years=years):
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
    return sorted({e['form'] for e in deadlines_for_year(date.today().year)})
