"""F3 — Fiscal calendar dataset + reminder logic tests."""
from datetime import date

import pytest

from modules.fiscal_calendar.calendar_data import (
    all_deadlines,
    upcoming,
    available_forms,
)


def test_dataset_has_quarterly_and_annual_forms():
    forms = available_forms()
    for f in ('303', '130', '349', '390', '100'):
        assert f in forms


def test_q4_window_rolls_into_next_january():
    # Modelo 303 Q4 2026 files 1-30 January 2027.
    q4 = [d for d in all_deadlines()
          if d['form'] == '303' and d['quarter'] == 4 and d['year'] == 2026]
    assert len(q4) == 1
    assert q4[0]['window_start'] == date(2027, 1, 1)
    assert q4[0]['window_end'] == date(2027, 1, 30)


def test_upcoming_marks_window_open():
    # 10 April 2026 -> Q1 filing window (1-20 Apr) is OPEN.
    items = upcoming(30, today=date(2026, 4, 10), selected_forms={'303'})
    q1 = [d for d in items if d['quarter'] == 1 and d['year'] == 2026]
    assert q1 and q1[0]['state'] == 'open'
    assert q1[0]['days_to_end'] == 10


def test_upcoming_filters_by_selected_forms():
    items = upcoming(400, today=date(2026, 1, 1), selected_forms={'130'})
    assert items
    assert all(d['form'] == '130' for d in items)


def test_upcoming_excludes_closed_windows():
    # After Q1 2026 window closed (25 Apr), Q1 must not appear.
    items = upcoming(5, today=date(2026, 4, 25), selected_forms={'303'})
    assert not any(d['quarter'] == 1 and d['year'] == 2026 for d in items)


def test_upcoming_respects_lookahead_horizon():
    # 1 Jan 2026: Q1 window opens 1 Apr (~90 days). A 30-day horizon hides it.
    near = upcoming(30, today=date(2026, 1, 1), selected_forms={'303'})
    assert not any(d['quarter'] == 1 and d['year'] == 2026 for d in near)
    far = upcoming(120, today=date(2026, 1, 1), selected_forms={'303'})
    assert any(d['quarter'] == 1 and d['year'] == 2026 for d in far)


# --- reminder job (DB-backed) ----------------------------------------------

@pytest.fixture
def fiscal_module(loaded_modules):
    import json
    from app import db
    fc = loaded_modules.modules['fiscal_calendar']
    # Pin selected forms so the test is deterministic.
    row = fc.Config.query.filter_by(key='selected_forms').first()
    if not row:
        row = fc.Config(key='selected_forms')
        db.session.add(row)
    row.value = json.dumps(['303'])
    # Reset any prior reminder state.
    state = fc.Config.query.filter_by(key='reminder_state').first()
    if state:
        state.value = '{}'
    db.session.commit()
    return fc


def test_reminders_fire_once_and_are_idempotent(fiscal_module):
    fc = fiscal_module
    # 6 days before Q1 2026 303 window close (20 Apr) -> the T-7 threshold on 13 Apr.
    day = date(2026, 4, 13)
    fired = fc._run_reminders(today=day)
    assert any(k.startswith('303:Q1 2026:7') for k in fired)
    # Second run same day -> nothing new (idempotent, survives restart via config).
    assert fc._run_reminders(today=day) == []

