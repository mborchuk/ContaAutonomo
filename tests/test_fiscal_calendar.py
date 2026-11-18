"""F3 — Fiscal calendar dataset + reminder logic tests."""
from datetime import date, timedelta

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
    # Modelo 303 Q4 2025 files 1-30 January 2026 (a Friday).
    q4 = [d for d in all_deadlines(years=[2025])
          if d['form'] == '303' and d['quarter'] == 4]
    assert len(q4) == 1
    assert q4[0]['window_start'] == date(2026, 1, 1)
    assert q4[0]['window_end'] == date(2026, 1, 30)


def test_deadline_on_weekend_moves_to_monday():
    # 30 January 2027 is a Saturday -> Q4 2026 closes Monday 1 February 2027.
    q4 = [d for d in all_deadlines(years=[2026])
          if d['form'] == '303' and d['quarter'] == 4]
    assert q4[0]['window_end'] == date(2027, 2, 1)


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

def _clear_messages():
    from sqlalchemy.exc import OperationalError
    from app import Message, db
    try:
        Message.query.filter_by(source='fiscal_calendar').delete()
        db.session.commit()
    except OperationalError:   # another test's app fixture dropped the tables
        db.session.rollback()
    db.session.expunge_all()


@pytest.fixture
def fiscal_module(loaded_modules):
    import json
    from app import db
    db.create_all()
    _clear_messages()
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
    yield fc
    _clear_messages()


def test_reminders_fire_once_and_are_idempotent(fiscal_module):
    fc = fiscal_module
    # 6 days before Q1 2026 303 window close (20 Apr) -> the T-7 threshold on 13 Apr.
    day = date(2026, 4, 13)
    fired = fc._run_reminders(today=day)
    assert any(k.startswith('303:Q1 2026:7') for k in fired)
    # Second run same day -> nothing new (idempotent, survives restart via config).
    assert fc._run_reminders(today=day) == []



# --- message center ---------------------------------------------------------

def _messages():
    from app import Message
    return Message.query.filter_by(source='fiscal_calendar').order_by(Message.id).all()


def test_upcoming_window_is_one_message_with_both_dates(fiscal_module):
    fc = fiscal_module
    day = date(2026, 2, 15)   # Q1 2026 303 window (01/04 - 20/04) opens in 45 days
    assert fc.post_window_messages(today=day) == 1
    (msg,) = _messages()
    assert msg.level == 'info'
    assert msg.dedupe_key == 'window:303:2026:1'
    assert 'Modelo 303 · Q1 2026: file from 01/04/2026 to 20/04/2026' in msg.text
    assert '[fiscal calendar](/fiscal-calendar/)' in msg.text
    # Posted once: later runs, also after it was read, add nothing.
    fc.core.message_center.mark_read(msg.id)
    assert fc.post_window_messages(today=day + timedelta(days=10)) == 0
    assert len(_messages()) == 1


def test_window_beyond_lookahead_is_not_posted(fiscal_module):
    # Q1 2026 closed on 20/04; Q2 opens 01/07/2026, 67 days after 25/04/2026.
    assert fiscal_module.post_window_messages(today=date(2026, 4, 25)) == 0
    assert _messages() == []


def test_open_window_message_says_it_can_be_filed_now(fiscal_module):
    fiscal_module.post_window_messages(today=date(2026, 4, 5))
    (msg,) = _messages()
    assert 'Modelo 303 · Q1 2026 can be filed now, until 20/04/2026' in msg.text


def test_reminders_are_messages_with_rising_level(fiscal_module):
    fc = fiscal_module
    fc._run_reminders(today=date(2026, 4, 6))    # 14 days left
    fc._run_reminders(today=date(2026, 4, 13))   # 7 days left
    fc._run_reminders(today=date(2026, 4, 19))   # 1 day left
    reminders = {m.dedupe_key: m for m in _messages() if m.dedupe_key.startswith('reminder:')}
    assert {k: m.level for k, m in reminders.items()} == {
        'reminder:303:2026:1:14': 'info',
        'reminder:303:2026:1:7': 'warning',
        'reminder:303:2026:1:1': 'danger',
    }
    assert 'closes in 7 day(s), on 20/04/2026' in reminders['reminder:303:2026:1:7'].text
    # The daily job also posts the window message.
    assert any(m.dedupe_key == 'window:303:2026:1' for m in _messages())


def test_dashboard_has_no_panel_and_posts_messages(fiscal_module):
    assert fiscal_module.get_dashboard_panels() == []


def test_messages_are_skipped_without_the_function(fiscal_module, monkeypatch):
    fc = fiscal_module
    monkeypatch.setattr(fc.core.functions, 'available', lambda name: False)
    assert fc.post_window_messages(today=date(2026, 2, 15)) == 0
    assert fc._run_reminders(today=date(2026, 4, 13))   # reminders still fire
    assert _messages() == []
