"""F10 — Tax filing workflow: obligation rows, record filing, report section."""
from datetime import date

import pytest


@pytest.fixture
def tax_mgmt(loaded_modules):
    return loaded_modules.modules['tax_management']


def _pin_forms(loaded_modules, forms):
    """Pin fiscal_calendar's selected forms so rows are deterministic."""
    import json
    from app import db
    fc = loaded_modules.modules['fiscal_calendar']
    row = fc.Config.query.filter_by(key='selected_forms').first()
    if not row:
        row = fc.Config(key='selected_forms')
        db.session.add(row)
    row.value = json.dumps(forms)
    db.session.commit()


def test_obligation_rows_cross_calendar_with_filings(tax_mgmt, loaded_modules):
    from app import db

    _pin_forms(loaded_modules, ['303'])
    # Record a filed Q1 2026 303 with amount.
    tf = tax_mgmt.TaxForm(form_type='303', year=2026, quarter=1,
                          file_path='', status='filed', amount=350.0,
                          filed_date=date(2026, 4, 10))
    db.session.add(tf)
    db.session.commit()

    rows = tax_mgmt._obligation_rows(2026, today=date(2026, 8, 1))
    by_period = {r['period_label']: r for r in rows}
    # Q1 filed; Q2 window (1-20 Jul) closed unfiled -> overdue; Q3 pending.
    assert by_period['Q1 2026']['status'] == 'filed'
    assert by_period['Q1 2026']['tax_form'].amount == 350.0
    assert by_period['Q2 2026']['status'] == 'overdue'
    assert by_period['Q3 2026']['status'] == 'pending'


def test_record_filing_creates_and_updates(tax_mgmt, loaded_modules, client):
    from app import db

    _pin_forms(loaded_modules, ['130'])
    with client.session_transaction() as sess:
        sess['authenticated'] = True

    resp = client.post('/tax-forms/obligations/record', data={
        'form_type': '130', 'year': '2026', 'quarter': '2',
        'status': 'paid', 'amount': '412.50',
    }, follow_redirects=False)
    assert resp.status_code == 302

    tf = tax_mgmt.TaxForm.query.filter_by(form_type='130', year=2026,
                                          quarter=2).first()
    assert tf is not None
    assert tf.status == 'paid'
    assert tf.amount == 412.50
    assert tf.filed_date is not None
    assert tf.payment_date is not None
    # Record-only row: no evidence file yet.
    assert tf.file_path == ''


def test_filings_report_section(tax_mgmt):
    from app import db

    db.session.add(tax_mgmt.TaxForm(form_type='303', year=2026, quarter=3,
                                    file_path='', status='paid', amount=99.0))
    db.session.commit()

    rows = tax_mgmt._get_filings_for_report(date(2026, 1, 1), date(2026, 12, 31))
    match = [r for r in rows if r['form_type'] == '303' and '2026 Q3' in r['period']]
    assert match and match[0]['amount_eur'] == 99.0
    assert match[0]['status'] == 'PAID'


def test_overdue_note_on_tax_panel(tax_mgmt, loaded_modules):
    from app import db, Settings

    _pin_forms(loaded_modules, ['303'])
    settings = Settings.query.first()
    if not settings:
        settings = Settings(social_security_monthly=0.0)
        db.session.add(settings)
        db.session.commit()

    rows = tax_mgmt._obligation_rows(2026, today=date(2026, 8, 1))
    assert any(r['status'] == 'overdue' for r in rows)
