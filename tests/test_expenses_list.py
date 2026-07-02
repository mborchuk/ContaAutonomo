"""Regression test: Expenses list view must render the Contractor column.

Bug: module Expense model have no 'contractor' relationship (core backref live
on a separate mapped class), so template `expense.contractor` resolve to
Undefined and Contractor column show empty for every row. List view now pass a
contractor id->name map; this test guard that the saved contractor name reach
the rendered table.
"""
from datetime import date

import pytest

from module_manager import ModuleManager


@pytest.fixture(scope='module')
def expenses_module():
    """Load the Expenses module once against the app + a fresh DB.

    Module-scoped so the ModuleManager (which registers the singleton
    `module_enabled` table) is built only once — re-building it per test would
    redefine that table in the shared metadata and error.
    """
    import app as appmod
    from app import app as flask_app, db, Settings

    with flask_app.app_context():
        db.create_all()

        mm = ModuleManager(flask_app, db)
        mm.core._settings_model = Settings
        mm.init_db()
        mm.discover_modules()

        enabled_model = mm._get_module_enabled_model()
        db.session.add(enabled_model(module_id='expenses', enabled=True))
        db.session.commit()
        mm.load_enabled_modules()

        # Templates reference these Jinja globals.
        appmod.module_manager = mm
        flask_app.jinja_env.globals.setdefault('module_manager', mm)
        flask_app.jinja_env.globals.setdefault('app_version', 'test')

        yield mm.modules['expenses']

        db.session.remove()
        db.drop_all()


def test_list_view_renders_contractor_name(expenses_module):
    from app import app as flask_app, db

    em = expenses_module
    contractor = em.Contractor(name='ACME Corp')
    db.session.add(contractor)
    db.session.flush()
    db.session.add(em.Expense(
        contractor_id=contractor.id,
        amount=42.0,
        currency='EUR',
        category='Tools',
        expense_date=date(2026, 1, 15),
    ))
    db.session.commit()

    with flask_app.test_request_context('/expenses/'):
        html = em._list_expenses()

    # Assert on the table CELL, not just any mention — the contractor also
    # shows up in the filter dropdown, so a bare substring check would pass even
    # with the bug present.
    assert '<td>ACME Corp</td>' in html


def test_list_view_handles_missing_contractor(expenses_module):
    from app import app as flask_app, db

    em = expenses_module
    db.session.add(em.Expense(
        contractor_id=None,
        amount=9.0,
        currency='EUR',
        category='MiscNoContractor',
        expense_date=date(2026, 2, 1),
    ))
    db.session.commit()

    with flask_app.test_request_context('/expenses/'):
        html = em._list_expenses()

    # No contractor -> placeholder, and rendering must not raise.
    assert 'MiscNoContractor' in html
