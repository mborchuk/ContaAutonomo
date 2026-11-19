"""Invoice PDFs go through the core registry functions invoice.render_pdf and
invoice.list_pdf_templates (invoice_pdf.py): one code path for the new-invoice
PDF, the download and the preview, with the default template as fallback."""
import io
from datetime import date

import pytest
from pypdf import PdfReader

from functions import InvalidArguments
from module_manager import BaseModule, LocalStorageBackend


class Templates(BaseModule):
    """A module that adds two templates: one that renders, one that is gone."""
    module_id = 'zz_pdf_templates'
    name = 'Templates'

    def get_invoice_templates(self):
        return [{'id': 'zz_plain', 'name': 'Plain', 'render': lambda inv, cust, s: b'%PDF-zz'},
                {'id': 'zz_gone', 'name': 'Gone', 'render': lambda inv, cust, s: None}]


@pytest.fixture
def pdfs(loaded_modules, client, tmp_path, monkeypatch):
    from app import Invoice, Settings, db
    monkeypatch.setattr(loaded_modules.core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    monkeypatch.setitem(loaded_modules.modules, 'zz_pdf_templates', Templates(loaded_modules.core))
    settings = Settings.query.first() or Settings()
    db.session.add(settings)
    before = settings.invoice_template
    inv = Invoice(invoice_number='REG-PDF-1', client_name='Registry PDF SL', amount_usd=0.0,
                  amount_eur=80.0, exchange_rate=1.0, invoice_date=date(2026, 11, 19),
                  status='draft', currency='EUR')
    db.session.add(inv)
    db.session.commit()
    with client.session_transaction() as sess:
        sess['authenticated'] = True

    def choose(template_id):
        settings.invoice_template = template_id
        db.session.commit()
    yield loaded_modules.functions, inv, choose, client
    settings.invoice_template = before
    db.session.delete(inv)
    db.session.commit()


def test_core_owns_both_functions(loaded_modules):
    for name in ('invoice.render_pdf', 'invoice.list_pdf_templates'):
        assert loaded_modules.functions.provider(name) == 'core'
    assert loaded_modules.functions.get('invoice.render_pdf').read_only


def test_templates_list_core_files_and_module_templates(pdfs):
    functions = pdfs[0]
    listed = {t['id']: t for t in functions.call('invoice.list_pdf_templates')['data']}
    assert listed['default_template']['module_id'] == 'core'
    assert listed['modern_blue_template']['module_id'] == 'core'
    assert listed['zz_plain'] == {'id': 'zz_plain', 'name': 'Plain',
                                  'module_id': 'zz_pdf_templates'}


def test_module_template_renders_and_modules_filter_the_pdf(pdfs, loaded_modules, monkeypatch):
    functions, inv, choose, _ = pdfs
    choose('zz_plain')
    monkeypatch.setattr(loaded_modules, 'filter_invoice_pdf', lambda invoice, pdf: pdf + b'+qr')
    assert functions.call('invoice.render_pdf', invoice_id=inv.id) == {
        'pdf': b'%PDF-zz+qr', 'template': 'zz_plain', 'fallback': False}


def test_core_file_template_renders(pdfs):
    functions, inv, choose, _ = pdfs
    choose('modern_blue_template')
    result = functions.call('invoice.render_pdf', invoice_id=inv.id)
    assert result['template'] == 'modern_blue_template' and not result['fallback']
    assert 'REG-PDF-1' in PdfReader(io.BytesIO(result['pdf'])).pages[0].extract_text()


@pytest.mark.parametrize('chosen', ['zz_gone', 'not_listed', '../../app'])
def test_unavailable_template_falls_back_to_the_default(pdfs, chosen):
    functions, inv, choose, _ = pdfs
    choose(chosen)
    result = functions.call('invoice.render_pdf', invoice_id=inv.id)
    assert result['template'] == 'default_template' and result['fallback'] is True
    assert result['pdf'].startswith(b'%PDF')


def test_unknown_invoice_is_refused(pdfs):
    with pytest.raises(InvalidArguments):
        pdfs[0].call('invoice.render_pdf', invoice_id=999_999)


def test_preview_and_download_use_the_function(pdfs):
    functions, inv, choose, client = pdfs
    choose('zz_plain')
    assert client.get(f'/preview-pdf/{inv.id}').data.startswith(b'%PDF-zz')
    assert client.get(f'/generate-pdf/{inv.id}').data.startswith(b'%PDF-zz')


def test_download_says_when_the_default_template_was_used(pdfs):
    functions, inv, choose, client = pdfs
    choose('zz_gone')
    resp = client.get(f'/generate-pdf/{inv.id}?force=true')
    assert resp.status_code == 200 and resp.data.startswith(b'%PDF')
    with client.session_transaction() as sess:
        flashes = [m for _, m in sess.get('_flashes', [])]
    assert any('Using the default template' in m for m in flashes)


def test_new_invoice_pdf_uses_the_chosen_template(pdfs):
    from app import Invoice
    functions, _, choose, client = pdfs
    choose('zz_plain')
    client.post('/create', data={
        'invoice_number': 'REG-PDF-2', 'client_name': 'Registry PDF SL',
        'invoice_date': '2026-11-19', 'currency': 'EUR',
        'items[0][description]': 'Work', 'items[0][quantity]': '1',
        'items[0][unit_price]': '10'})
    created = Invoice.query.filter_by(invoice_number='REG-PDF-2').one()
    assert client.get(f'/preview-pdf/{created.id}').data.startswith(b'%PDF-zz')


def test_settings_lists_templates_from_the_function(pdfs):
    html = pdfs[3].get('/settings').get_data(as_text=True)
    assert 'value="zz_plain"' in html and 'Default Template' in html
