"""F7 — Recurring invoices: cadence math + draft generation idempotency."""
import json
from datetime import date

import pytest

from modules.recurring_invoices.cadence import add_cadence, due_dates, MAX_CATCHUP_PERIODS


# --- cadence math (pure) -----------------------------------------------------

def test_add_cadence_monthly_clamps_day():
    assert add_cadence(date(2026, 1, 31), 'monthly') == date(2026, 2, 28)
    assert add_cadence(date(2026, 3, 15), 'monthly') == date(2026, 4, 15)
    assert add_cadence(date(2026, 12, 10), 'monthly') == date(2027, 1, 10)


def test_add_cadence_quarterly():
    assert add_cadence(date(2026, 1, 31), 'quarterly') == date(2026, 4, 30)
    assert add_cadence(date(2026, 11, 5), 'quarterly') == date(2027, 2, 5)


def test_add_cadence_rejects_unknown():
    with pytest.raises(ValueError):
        add_cadence(date(2026, 1, 1), 'weekly')


def test_due_dates_catches_up_missed_periods():
    due, nxt = due_dates(date(2026, 1, 1), date(2026, 3, 15), 'monthly')
    assert due == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert nxt == date(2026, 4, 1)


def test_due_dates_nothing_due_in_future():
    due, nxt = due_dates(date(2026, 6, 1), date(2026, 5, 20), 'monthly')
    assert due == []
    assert nxt == date(2026, 6, 1)


def test_due_dates_capped():
    due, _ = due_dates(date(2020, 1, 1), date(2026, 1, 1), 'monthly')
    assert len(due) == MAX_CATCHUP_PERIODS


# --- generation (DB) ---------------------------------------------------------

@pytest.fixture
def recurring_module(loaded_modules):
    return loaded_modules.modules['recurring_invoices']


def _template(module, db, next_run, cadence='monthly'):
    # Shared session-scoped DB: pause templates left over from earlier tests so
    # each test's _generate_due only sees its own template.
    for old in module.RecurringInvoice.query.all():
        old.active = False
    tpl = module.RecurringInvoice(
        name='ACME retainer', client_name='ACME', currency='EUR',
        items_json=json.dumps([{'description': 'Consulting', 'quantity': 10,
                                'unit_price': 100.0}]),
        cadence=cadence, next_run_date=next_run, active=True)
    db.session.add(tpl)
    db.session.commit()
    return tpl


def test_generation_creates_draft_with_items(recurring_module, monkeypatch):
    from app import db, Invoice

    # Deterministic exchange rate — no network in tests.
    import modules.recurring_invoices.index as rec_mod
    monkeypatch.setattr('currency_converter.get_exchange_rate',
                        lambda d: (1.1, d))

    tpl = _template(recurring_module, db, next_run=date(2026, 5, 1))
    created = recurring_module._generate_due(today=date(2026, 5, 2))
    assert len(created) == 1

    inv = db.session.get(Invoice, created[0])
    assert inv.status == 'draft'
    assert inv.invoice_date == date(2026, 5, 1)
    assert inv.currency == 'EUR'
    assert inv.amount_eur == 1000.0          # 10 × 100, EUR template
    assert len(inv.items) == 1
    assert inv.items[0].subtotal_usd == 1000.0
    # next_run advanced one month.
    assert tpl.next_run_date == date(2026, 6, 1)


def test_generation_idempotent_same_day(recurring_module, monkeypatch):
    from app import db

    monkeypatch.setattr('currency_converter.get_exchange_rate',
                        lambda d: (1.0, d))
    _template(recurring_module, db, next_run=date(2026, 7, 1))
    first = recurring_module._generate_due(today=date(2026, 7, 1))
    second = recurring_module._generate_due(today=date(2026, 7, 1))
    assert len(first) == 1
    assert second == []                       # rerun same day -> nothing new


def test_generation_skips_paused_templates(recurring_module, monkeypatch):
    from app import db

    monkeypatch.setattr('currency_converter.get_exchange_rate',
                        lambda d: (1.0, d))
    tpl = _template(recurring_module, db, next_run=date(2026, 8, 1))
    tpl.active = False
    db.session.commit()
    assert recurring_module._generate_due(today=date(2026, 8, 5)) == []
