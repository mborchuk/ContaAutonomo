"""Reports: expense invoice attachments (on by default) and the Documents
section (off by default)."""
import io
import re
import zipfile
from datetime import date

import pytest


@pytest.fixture
def reports(loaded_modules):
    return loaded_modules.modules['reports']


@pytest.fixture
def stored_expense(loaded_modules):
    """Expense in Q1 2026 with a stored image invoice.

    Storage is the real local filesystem (only the DB is in-memory), so the
    file is removed afterwards.
    """
    from app import db, Expense

    key = loaded_modules.core.save_file(io.BytesIO(b'\xff\xd8\xff fake jpg'),
                                        'expenses_files', 'rptatt_ticket.jpg')
    expense = Expense(amount=12.5, currency='EUR', category='Travel',
                      expense_date=date(2026, 2, 22), file_path=key)
    db.session.add(expense)
    db.session.commit()
    yield expense
    loaded_modules.core.delete_file(key)


def test_section_defaults(reports):
    sections = {s['id']: s for s in reports._get_available_sections()}
    assert sections['expenses']['has_files'] is True
    assert sections['expenses']['attach_default'] is True
    assert sections['expenses']['selected_default'] is True
    assert sections['documents']['selected_default'] is False
    assert sections['documents']['attach_default'] is False
    assert sections['income']['selected_default'] is True


def test_report_page_renders_defaults(reports, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/reports/').get_data(as_text=True)

    def is_checked(name, value=None):
        marker = f'name="{name}"' + (f' value="{value}"' if value else '')
        start = html.index(marker)
        tag = html[start:html.index('>', start)]
        # The onchange handler reads `this.checked`; only the bare attribute counts.
        return re.search(r'(?<![.\w])checked(?!\w)', tag) is not None

    assert is_checked('sections', 'expenses')
    assert not is_checked('sections', 'documents')
    assert is_checked('include_files_expenses')
    assert not is_checked('include_files_documents')


def test_expense_invoices_attached_to_zip(reports, stored_expense):
    data, filename, mimetype = reports._build_report(
        selected_sections=['expenses'], year=2026, period_type='quarter',
        quarters=[1], include_files_sids={'expenses'})
    assert mimetype == 'application/zip'
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert any(n.startswith('expenses/') and n.endswith('rptatt_ticket.jpg')
               for n in names), names


def test_expense_file_list_for_picker(reports, stored_expense, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.post('/reports/file-list', json={
        'section_id': 'expenses', 'year': 2026, 'quarters': [1]})
    ids = [f['id'] for f in resp.get_json()['files']]
    assert stored_expense.id in ids


def test_unreadable_file_does_not_drop_others(reports, stored_expense,
                                              loaded_modules, monkeypatch):
    """One file the backend cannot read must not abort the rest."""
    real_get = loaded_modules.core.storage.get

    def flaky_get(key):
        if key == 'broken-key':
            raise ConnectionError('backend unavailable')
        return real_get(key)
    monkeypatch.setattr(loaded_modules.core.storage, 'get', flaky_get)

    expenses = loaded_modules.modules['expenses']
    real_files = expenses._report_files
    monkeypatch.setattr(expenses, '_report_files', lambda *a, **k: (
        [{'name': 'broken', 'storage_key': 'broken-key'}] + real_files(*a, **k)))

    data, _, _ = reports._build_report(
        selected_sections=['expenses'], year=2026, period_type='quarter',
        quarters=[1], include_files_sids={'expenses'})
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert any(n.endswith('rptatt_ticket.jpg') for n in names), names
