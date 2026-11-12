"""B-1 — year pickers list the current year and every year with data."""
import re
from datetime import date


def _login(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def _seed(db):
    from app import Expense, Invoice
    if Invoice.query.filter_by(invoice_number='YP-1').first():
        return
    db.session.add(Invoice(invoice_number='YP-1', client_name='Old Client SL',
                           amount_usd=0.0, amount_eur=100.0, exchange_rate=1.0,
                           invoice_date=date(2019, 5, 1), status='draft', currency='EUR'))
    db.session.add(Expense(description='Far future expense', amount=10.0,
                           expense_date=date(2031, 2, 1)))
    db.session.commit()


def test_data_years_has_current_year_without_data(loaded_modules):
    from app import Expense, Invoice, db
    Invoice.query.delete()
    Expense.query.delete()
    db.session.commit()
    years = loaded_modules.core.data_years()
    assert years == [date.today().year]


def test_data_years_has_every_data_year_newest_first(loaded_modules):
    from app import db
    _seed(db)
    years = loaded_modules.core.data_years()
    assert {2019, 2031, date.today().year} <= set(years)
    assert years == sorted(years, reverse=True)


def test_reports_picker_lists_data_years_and_current_year(loaded_modules, client):
    from app import db
    _seed(db)
    _login(client)
    html = client.get('/reports/').get_data(as_text=True)
    picker = html[html.index('id="year"'):]
    picker = picker[:picker.index('</select>')]
    options = {int(y) for y in re.findall(r'<option value="(\d{4})"', picker)}
    assert {2019, 2031, date.today().year} <= options
    assert f'<option value="{date.today().year}" selected' in picker


def test_reports_template_has_no_year_literal():
    with open('modules/reports/templates/reports.html') as f:
        assert not re.search(r'range\(\s*20\d\d', f.read())
