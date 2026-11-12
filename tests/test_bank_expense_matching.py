"""D-5 — bank import suggests expenses for debit movements (never auto-links)."""
from datetime import date
from types import SimpleNamespace

import pytest

from modules.bank_import.matching import suggest_expenses_for_movement
from modules.bank_import.parsers import movement_hash


def _exp(id, amount, d, contractor='', number=''):
    return SimpleNamespace(id=id, amount=amount, expense_date=d, contractor_name=contractor,
                           invoice_number=number, description='')


def _mov(amount, d, description='', counterparty=None):
    return {'amount': amount, 'date': d, 'description': description,
            'counterparty': counterparty}


def test_same_amount_within_window_is_first():
    expenses = [_exp(1, 59.99, date(2025, 6, 1)),          # outside the window
                _exp(2, 59.99, date(2026, 3, 1)),          # within the window
                _exp(3, 10.00, date(2026, 3, 2))]          # other amount
    ranked = suggest_expenses_for_movement(_mov(-59.99, date(2026, 3, 4)), expenses)
    assert [e.id for _s, e, _r in ranked] == [2, 1]
    assert 'from expense date' in ranked[0][2]


def test_contractor_and_invoice_number_raise_the_score():
    expenses = [_exp(1, 100.0, date(2026, 3, 1)),
                _exp(2, 100.0, date(2026, 3, 1), contractor='Hosting Co'),
                _exp(3, 100.0, date(2026, 3, 1), number='INV-778')]
    ranked = suggest_expenses_for_movement(
        _mov(-100.0, date(2026, 3, 3), 'Card payment INV-778', 'HOSTING CO'), expenses)
    order = [e.id for _s, e, _r in ranked]
    assert order[0] == 3 and order[1] == 2 and order[2] == 1


def test_credits_never_suggest_expenses():
    assert suggest_expenses_for_movement(_mov(50.0, date(2026, 3, 1)),
                                         [_exp(1, 50.0, date(2026, 3, 1))]) == []


# --- module integration ----------------------------------------------------------

@pytest.fixture
def bank(loaded_modules):
    return loaded_modules.modules['bank_import']


def _seed(bank, tag, amount=42.42):
    from app import Contractor, Expense, db
    contractor = Contractor(name=f'Supplier {tag}')
    db.session.add(contractor)
    db.session.flush()
    exp = Expense(description=f'Service {tag}', amount=amount, currency='EUR',
                  expense_date=date(2026, 5, 2), contractor_id=contractor.id)
    m = bank.BankMovement(batch_id=998, date=date(2026, 5, 5), amount=-amount, currency='EUR',
                          description=f'Payment supplier {tag}',
                          hash=movement_hash('2026-05-05', -amount, f'exp-{tag}'))
    db.session.add_all([exp, m])
    db.session.commit()
    return exp, m


def test_module_suggests_and_link_requires_confirmation(bank, client):
    exp, m = _seed(bank, 'D5A')
    suggestions = bank._expense_suggestions([m])
    assert suggestions[m.id][0][1].id == exp.id
    assert 'contractor name in description' in suggestions[m.id][0][2]
    assert m.status == 'new'                       # nothing linked automatically

    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/bank-import/').get_data(as_text=True)
    assert f'/bank-import/link-expense/{m.id}' in html and 'Supplier D5A' in html

    resp = client.post(f'/bank-import/link-expense/{m.id}', data={'expense_id': exp.id})
    assert resp.status_code == 302
    assert m.status == 'matched' and m.matched_type == 'expense' and m.matched_id == exp.id


def test_linked_expense_is_not_suggested_again(bank):
    from app import db
    exp, m = _seed(bank, 'D5B', amount=13.13)
    m.status, m.matched_type, m.matched_id = 'matched', 'expense', exp.id
    db.session.commit()
    _exp2, m2 = _seed(bank, 'D5C', amount=13.13)
    ids = [e.id for _s, e, _r in bank._expense_suggestions([m2])[m2.id]]
    assert exp.id not in ids
