"""G-6 — deduction checks: one test per rule on each side of its limit,
every notice names its rule and source, and no rule edits data."""
import copy
from datetime import date
from types import SimpleNamespace

import pytest

from modules.expenses.deduction_rules import (
    CHECK, EXCEEDS, RULES, RULES_BY_ID, UNDER_CLAIMED, check_expenses,
    check_home_supplies, check_meal_day, check_vehicle)


def exp(category, pct=100.0, amount=100.0, currency='EUR', d=date(2032, 3, 2),
        deductible=True, id=1):
    return SimpleNamespace(id=id, category=category, deductible_pct=pct, amount=amount,
                           currency=currency, expense_date=d, deductible=deductible)


def _severity(notices):
    return notices[0]['severity'] if notices else None


# --- home-office supplies (LIRPF 30.2.5ª b) -------------------------------- #

@pytest.mark.parametrize('pct,expected', [
    (6.0, None),            # 30 % of a 20 % share = 6 %
    (6.1, EXCEEDS),
    (5.0, UNDER_CLAIMED),
])
def test_home_supplies_with_a_home_office_share(pct, expected):
    assert _severity(check_home_supplies(exp('Utilities', pct), 20.0)) == expected


@pytest.mark.parametrize('pct,expected', [(30.0, None), (30.1, EXCEEDS), (0.0, None)])
def test_home_supplies_without_a_share_use_the_30_pct_maximum(pct, expected):
    assert _severity(check_home_supplies(exp('Utilities', pct), None)) == expected


def test_home_supplies_ignore_other_categories():
    assert check_home_supplies(exp('Software', 100.0), 20.0) == []


# --- dietas (LIRPF 30.2.5ª c, RIRPF 9.A.3.a) -------------------------------- #

@pytest.mark.parametrize('total,abroad,expected', [
    (26.67, False, None), (26.68, False, CHECK),
    (53.34, False, CHECK), (53.35, False, EXCEEDS),
    (48.08, True, None), (48.09, True, CHECK),
    (91.35, True, CHECK), (91.36, True, EXCEEDS),
])
def test_meal_limits_per_day(total, abroad, expected):
    assert _severity(check_meal_day(total, abroad, exp('Meals'))) == expected


def test_meals_of_one_day_are_added_up():
    lunch = exp('Meals', amount=20.0, id=1)
    dinner = exp('Meals', amount=10.0, id=2)
    other_day = exp('Meals', amount=20.0, d=date(2032, 3, 3), id=3)
    found = check_expenses([lunch, dinner, other_day])
    assert set(found) == {1, 2}                       # 30.00 on 2 March > 26.67
    assert all(n['severity'] == CHECK for n in found[1])


def test_meals_in_another_currency_use_the_abroad_limits():
    dinner = exp('Meals', amount=40.0, currency='GBP')
    assert check_expenses([dinner], to_eur=lambda e: 47.0) == {}
    assert _severity(check_expenses([dinner], to_eur=lambda e: 49.0)[1]) == CHECK


# --- vehicles (RIRPF 22.4, LIVA 95.Tres.2ª) --------------------------------- #

@pytest.mark.parametrize('pct,deductible,expected', [
    (50.0, True, None),
    (51.0, True, CHECK),
    (49.0, True, UNDER_CLAIMED),
    (100.0, False, UNDER_CLAIMED),
])
def test_vehicle_presumption(pct, deductible, expected):
    assert _severity(check_vehicle(exp('Vehicle', pct, deductible=deductible))) == expected


# --- every rule ------------------------------------------------------------- #

def test_every_notice_names_its_rule_and_source():
    samples = [exp('Utilities', 50.0, id=1), exp('Meals', amount=60.0, id=2),
               exp('Vehicle', 100.0, id=3)]
    found = check_expenses(samples, home_office_area_pct=10.0)
    assert set(found) == {1, 2, 3}
    for notices in found.values():
        for n in notices:
            rule = RULES_BY_ID[n['rule_id']]
            assert n['source'] == rule['source'] and n['title'] == rule['title']
            assert n['message'] and n['severity_label']


def test_rule_table_cites_a_source_for_every_rule():
    assert {r['id'] for r in RULES} == {'home_supplies', 'meal_limits', 'vehicle_use',
                                        'reverse_charge_region'}
    for rule in RULES:
        assert rule['source'] and any(law in rule['source'] for law in ('LIRPF', 'RIRPF', 'LIVA'))


def test_no_rule_edits_the_expense():
    samples = [exp('Utilities', 50.0, id=1), exp('Meals', amount=60.0, id=2),
               exp('Vehicle', 100.0, id=3), exp('Vehicle', 10.0, deductible=False, id=4)]
    before = [copy.deepcopy(vars(e)) for e in samples]
    check_expenses(samples, home_office_area_pct=10.0)
    assert [vars(e) for e in samples] == before


# --- module: list, form, dismissal ------------------------------------------ #

def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def _expense_rows(db):
    from sqlalchemy import text
    with db.engine.connect() as conn:
        return sorted(map(repr, conn.execute(text('SELECT * FROM expense'))))


@pytest.fixture
def vehicle_expense(loaded_modules, app):
    from contaautonomo.app import Expense, db
    e = Expense(amount=121.0, currency='EUR', category='Vehicle', description='Synthetic fuel',
                expense_date=date(2032, 4, 4), net_amount=100.0, vat_rate=21.0,
                vat_amount=21.0, deductible=True, deductible_pct=100.0)
    db.session.add(e)
    db.session.commit()
    return e


def test_list_and_form_show_the_notice_without_changing_the_expense(
        vehicle_expense, client):
    from contaautonomo.app import db
    _auth(client)
    before = _expense_rows(db)
    listing = client.get('/expenses/').get_data(as_text=True)
    assert 'deduction-notice' in listing and 'Vehicle costs' in listing
    form = client.get(f'/expenses/edit/{vehicle_expense.id}').get_data(as_text=True)
    assert 'RD 439/2007 (RIRPF), art. 22.4' in form
    assert 'Dismiss for this expense' in form
    assert _expense_rows(db) == before


def test_dismissal_is_recorded_per_expense(vehicle_expense, loaded_modules, client):
    from contaautonomo.app import db
    expenses = loaded_modules.modules['expenses']
    _auth(client)
    before = _expense_rows(db)
    resp = client.post(f'/expenses/{vehicle_expense.id}/notices/vehicle_use/dismiss')
    assert resp.status_code == 302
    row = expenses.NoticeDismissal.query.filter_by(expense_id=vehicle_expense.id).one()
    assert row.rule_id == 'vehicle_use' and row.dismissed_at is not None
    client.post(f'/expenses/{vehicle_expense.id}/notices/vehicle_use/dismiss')  # idempotent
    assert expenses.NoticeDismissal.query.filter_by(expense_id=vehicle_expense.id).count() == 1

    active, dismissed = expenses.deduction_notices([vehicle_expense])
    assert vehicle_expense.id not in active
    assert dismissed[vehicle_expense.id][0]['rule_id'] == 'vehicle_use'
    form = client.get(f'/expenses/edit/{vehicle_expense.id}').get_data(as_text=True)
    assert 'Dismissed: Vehicle costs' in form
    assert _expense_rows(db) == before          # dismissing never edits the expense


def test_unknown_rule_cannot_be_dismissed(vehicle_expense, loaded_modules, client):
    _auth(client)
    client.post(f'/expenses/{vehicle_expense.id}/notices/made_up/dismiss')
    assert loaded_modules.modules['expenses'].NoticeDismissal.query.count() == 0


def test_dismiss_requires_post_and_login(vehicle_expense, client):
    assert client.post(f'/expenses/{vehicle_expense.id}/notices/vehicle_use/dismiss'
                       ).status_code == 302          # to the login page
    _auth(client)
    assert client.get(f'/expenses/{vehicle_expense.id}/notices/vehicle_use/dismiss'
                      ).status_code in (302, 405)


def test_deleting_the_expense_removes_its_dismissals(vehicle_expense, loaded_modules, client):
    expenses = loaded_modules.modules['expenses']
    _auth(client)
    client.post(f'/expenses/{vehicle_expense.id}/notices/vehicle_use/dismiss')
    client.post(f'/expenses/delete/{vehicle_expense.id}')
    assert expenses.NoticeDismissal.query.count() == 0


def test_home_office_share_setting_round_trip(loaded_modules, app):
    expenses = loaded_modules.modules['expenses']
    with app.test_request_context('/settings'):
        expenses.save_settings(None, {'expense_category_defaults': '{}',
                                      'expense_home_office_area_pct': '25'})
        assert expenses.home_office_area_pct() == 25.0
        html = expenses.get_settings_html(None)
        assert 'value="25"' in html
        expenses.save_settings(None, {'expense_category_defaults': '{}',
                                      'expense_home_office_area_pct': '150'})
        assert expenses.home_office_area_pct() == 25.0     # out of range: kept
        expenses.save_settings(None, {'expense_category_defaults': '{}',
                                      'expense_home_office_area_pct': ''})
        assert expenses.home_office_area_pct() is None
