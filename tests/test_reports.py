"""Reports module: IVA column, Art. 194 reverse-charge note, invoice attach."""
import io
import zipfile
from datetime import date

import pytest


@pytest.fixture
def reports_module(loaded_modules):
    return loaded_modules.modules['reports']


def _seed(db, number_prefix):
    """One standard + one eu_b2b issued invoice and one VAT expense in Q1 2026."""
    from app import Customer, Invoice, Expense, Settings, _issue_invoice

    if not Settings.query.first():
        db.session.add(Settings(default_vat_rate=21.0, base_currency='EUR',
                                report_template='official_template'))
    std = Customer(name=f'{number_prefix} Spain SL', tax_type='standard')
    eu = Customer(name=f'{number_prefix} GmbH', tax_type='eu_b2b')
    db.session.add_all([std, eu])
    db.session.flush()
    inv_std = Invoice(invoice_number=f'{number_prefix}-STD', client_name=std.name,
                      amount_usd=0.0, amount_eur=1000.0, exchange_rate=1.0,
                      invoice_date=date(2026, 2, 10), status='draft',
                      currency='EUR', customer_id=std.id)
    inv_eu = Invoice(invoice_number=f'{number_prefix}-EU', client_name=eu.name,
                     amount_usd=0.0, amount_eur=500.0, exchange_rate=1.0,
                     invoice_date=date(2026, 2, 15), status='draft',
                     currency='EUR', customer_id=eu.id)
    db.session.add_all([inv_std, inv_eu])
    db.session.commit()
    _issue_invoice(inv_std)   # snapshot: 21% of 1000
    _issue_invoice(inv_eu)    # snapshot: 0% (reverse charge)
    db.session.add(Expense(amount=121.0, currency='EUR', category='Software',
                           expense_date=date(2026, 2, 20), net_amount=100.0,
                           vat_rate=21.0, vat_amount=21.0, deductible=True,
                           deductible_pct=100.0))
    db.session.commit()
    return inv_std, inv_eu


def _pdf_text(data):
    from pypdf import PdfReader
    return ''.join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def test_report_pdf_has_iva_column_and_art194_note(reports_module):
    from app import db

    _seed(db, 'RPT1')
    data, fname, mime = reports_module._build_report(
        selected_sections=['income', 'expenses'], year=2026,
        period_type='quarter', quarters=[1])
    assert mime == 'application/pdf'
    text = _pdf_text(data)
    # IVA column with rate and EUR amount.
    assert 'IVA' in text
    assert '21%' in text
    assert '210.00' in text            # 21% of the 1000 EUR invoice
    # eu_b2b row marked and footnote present.
    assert '0% (1)' in text
    assert 'Inversión del sujeto pasivo' in text
    assert 'Artículo 194 Directiva' in text
    # Totals include IVA lines.
    assert 'Total IVA:' in text
    assert 'Total IVA soportado:' in text


def test_report_attaches_invoice_pdfs_when_requested(reports_module, loaded_modules):
    from app import db

    inv_std, _ = _seed(db, 'RPT2')
    # attach_pdf writes to the real local invoices_pdf/ dir — clean up after.
    loaded_modules.core.invoice_service.attach_pdf(
        inv_std.id, b'%PDF-1.4 fake', 'x.pdf')
    try:
        data, fname, mime = reports_module._build_report(
            selected_sections=['income'], year=2026,
            period_type='quarter', quarters=[1],
            include_files_sids={'income'})
        assert mime == 'application/zip'
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        # Report itself plus at least the attached invoice; '/' in invoice
        # numbers is sanitized for ZIP entry names.
        assert any(n.startswith('invoices/') and n.endswith('.pdf') for n in names)
        assert all('/' not in n.replace('invoices/', '').replace('.pdf', '')
                   for n in names if n.startswith('invoices/'))
    finally:
        if inv_std.pdf_storage_key:
            loaded_modules.core.delete_file(inv_std.pdf_storage_key)


def test_report_without_attach_stays_plain_pdf(reports_module):
    from app import db

    _seed(db, 'RPT3')
    data, fname, mime = reports_module._build_report(
        selected_sections=['income'], year=2026,
        period_type='quarter', quarters=[1])
    assert mime == 'application/pdf'
    assert fname.endswith('.pdf')


def test_ui_defaults_invoices_and_expenses_on_documents_off(reports_module):
    """Attach defaults: income invoices ON, expenses receipts ON, documents OFF.

    Renders the actual sections page so the attach_default flags are exercised,
    not just the template source.
    """
    from flask import render_template
    from app import app as flask_app

    sections = reports_module._get_available_sections()
    by_id = {s['id']: s for s in sections}
    assert by_id['income'].get('attach_invoices') is True
    assert by_id['expenses'].get('attach_default') is True

    with flask_app.test_request_context('/reports/'):
        html = render_template('reports.html', current_year=2026,
                               available_sections=sections, base_currency='EUR')

    def _tag_region(name):
        # The input tags span several lines; grab the chunk from the name
        # attribute to the closing '>'.
        idx = html.index(f'name="{name}"')
        return html[idx:html.index('>', idx)]

    # Income invoice-attach and expenses receipt-attach default to checked.
    assert 'checked' in _tag_region('include_files_income')
    assert 'checked' in _tag_region('include_files_expenses')

    # Documents (if the module were enabled) must stay unchecked — verified via
    # the flag: only sections that opt in get attach_default.
    for sid, section in by_id.items():
        if sid not in ('income', 'expenses'):
            assert not section.get('attach_default'), \
                f'section {sid} unexpectedly defaults to attach'


def test_report_attaches_expense_receipts(reports_module, loaded_modules):
    """Expense receipt files land in the ZIP under expenses/."""
    from app import db, Expense

    _seed(db, 'RPT4')
    # Expense with a stored receipt file. Storage is the real local FS (only
    # the DB is in-memory), so always delete the file afterwards.
    key = loaded_modules.core.save_file(io.BytesIO(b'%PDF-1.4 receipt'),
                                        'expenses_files', 'rpt4_receipt.pdf')
    try:
        db.session.add(Expense(amount=85.22, currency='EUR', category='Equipment',
                               expense_date=date(2026, 2, 21), vat_rate=0.0,
                               vat_amount=0.0, net_amount=85.22, file_path=key))
        db.session.commit()

        data, fname, mime = reports_module._build_report(
            selected_sections=['expenses'], year=2026,
            period_type='quarter', quarters=[1],
            include_files_sids={'expenses'})
        assert mime == 'application/zip'
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.startswith('expenses/') and n.endswith('rpt4_receipt.pdf')
                   for n in names), names
    finally:
        loaded_modules.core.delete_file(key)


def test_report_attaches_image_receipts_with_extension(reports_module, loaded_modules):
    """Receipts are not only PDFs — a JPG keeps its extension in the ZIP."""
    from app import db, Expense

    _seed(db, 'RPT5')
    key = loaded_modules.core.save_file(io.BytesIO(b'\xff\xd8\xff fake jpg'),
                                        'expenses_files', 'rpt5_ticket.jpg')
    try:
        db.session.add(Expense(amount=12.5, currency='EUR', category='Travel',
                               expense_date=date(2026, 2, 22), vat_rate=10.0,
                               vat_amount=1.14, net_amount=11.36, file_path=key))
        db.session.commit()

        data, fname, mime = reports_module._build_report(
            selected_sections=['expenses'], year=2026,
            period_type='quarter', quarters=[1],
            include_files_sids={'expenses'})
        assert mime == 'application/zip'
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.startswith('expenses/') and n.endswith('rpt5_ticket.jpg')
                   for n in names), names
    finally:
        loaded_modules.core.delete_file(key)
