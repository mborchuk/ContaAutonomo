"""E-2 — Invoice Designer: template create and edit, config merge, PDF render."""
import io
import json

import pytest
from pypdf import PdfReader


@pytest.fixture
def designer(standalone_module, monkeypatch):
    """Standalone instance (the module is not enabled in the test session, and
    blueprints cannot be registered after the first request), with url_for
    stubbed for its redirects."""
    inst = standalone_module('invoice_designer')
    module_globals = type(inst)._save_template.__globals__
    monkeypatch.setitem(module_globals, 'url_for', lambda endpoint, **kw: f'/{endpoint}/{kw}')
    return inst, module_globals


def _post(app, form):
    return app.test_request_context('/invoice-designer/create', method='POST', data=form)


def test_create_and_edit_template_from_json(designer, app):
    inst, g = designer
    cfg = {'accent_color': '#112233', 'labels': {'invoice_title': 'FACTURA'}}
    with _post(app, {'name': 'Plain', 'config_json_raw': json.dumps(cfg)}):
        resp = inst._save_template(None)
    assert resp.status_code == 302
    tpl = inst.InvoiceTemplate.query.filter_by(name='Plain').one()
    assert json.loads(tpl.config_json)['accent_color'] == '#112233'

    cfg['accent_color'] = '#445566'
    with _post(app, {'name': 'Plain v2', 'config_json_raw': json.dumps(cfg)}):
        inst._save_template(tpl)
    inst._db.session.refresh(tpl)
    assert tpl.name == 'Plain v2'
    assert json.loads(tpl.config_json)['accent_color'] == '#445566'

    listed = {t['id']: t for t in inst.get_invoice_templates()}
    entry = listed[f'designer_{tpl.id}']
    assert entry['name'] == '🎨 Plain v2' and callable(entry['render'])
    assert 'path' not in entry


def test_template_renders_itself_and_reports_a_missing_one(designer, monkeypatch):
    inst, g = designer
    from types import SimpleNamespace
    tpl = inst.InvoiceTemplate(name='Render me',
                               config_json=json.dumps({'labels': {'invoice_title': 'FACTURA'}}))
    inst._db.session.add(tpl)
    inst._db.session.commit()
    seen = {}

    def fake_generate(invoice, customer, settings, config, storage=None, blocks=None):
        seen.update(config=config, blocks=blocks)
        return b'%PDF-fake'
    monkeypatch.setitem(g, 'generate_pdf_from_config', fake_generate)
    render = {t['id']: t for t in inst.get_invoice_templates()}[f'designer_{tpl.id}']['render']
    assert render(SimpleNamespace(), None, None) == b'%PDF-fake'
    assert seen['config']['labels']['invoice_title'] == 'FACTURA'
    assert set(g['DEFAULT_CONFIG']['labels']) <= set(seen['config']['labels'])
    assert isinstance(seen['blocks'], list)
    assert inst.render_invoice_pdf(10 ** 6, SimpleNamespace(), None, None) is None


def test_name_is_required(designer, app):
    inst, _ = designer
    before = inst.InvoiceTemplate.query.count()
    with _post(app, {'name': '  ', 'config_json_raw': '{}'}):
        resp = inst._save_template(None)
    assert resp.status_code == 302
    assert inst.InvoiceTemplate.query.count() == before


def test_merge_config_keeps_default_labels(designer):
    _inst, g = designer
    merged = g['_merge_config']({'labels': {'invoice_title': 'FACTURA'}, 'font': 'Courier'})
    defaults = g['DEFAULT_CONFIG']
    assert merged['labels']['invoice_title'] == 'FACTURA'
    assert set(defaults['labels']) <= set(merged['labels'])
    assert merged['font'] == 'Courier'
    assert defaults['labels'].get('invoice_title') != 'FACTURA'   # defaults untouched


def test_render_pdf_from_config(designer):
    _inst, g = designer
    from types import SimpleNamespace
    settings = SimpleNamespace(business_name='Test SL', owner_name='Owner', vat_number='B1',
                               nie_number='X1', address='Calle 1', postal_code='12006',
                               city='Castellón', country='Spain', email='owner@example.com',
                               phone='', default_vat_rate=21.0, logo_path=None)
    pdf = g['generate_pdf_from_config'](
        invoice=g['_demo_invoice'](), customer=g['_demo_customer'](),
        settings=settings, config=g['_merge_config']({}), storage=None)
    assert pdf[:5] == b'%PDF-'
    text = ''.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
    assert '2026-DEMO' in text and 'Frontend Development' in text


def test_core_renders_a_module_template_through_its_callable(loaded_modules, client,
                                                             monkeypatch):
    from datetime import date
    from app import Invoice, Settings, db
    from module_manager import BaseModule
    seen = []

    class Templates(BaseModule):
        module_id = 'zz_templates'
        name = 'Templates'

        def get_invoice_templates(self):
            return [{'id': 'zz_plain', 'name': 'Plain',
                     'render': lambda inv, cust, settings: seen.append(inv.id) or b'%PDF-zz'}]

    settings = Settings.query.first() or Settings()
    db.session.add(settings)
    before = settings.invoice_template
    settings.invoice_template = 'zz_plain'
    inv = Invoice(invoice_number='TPL-1', client_name='Template SL', amount_usd=0.0,
                  amount_eur=50.0, exchange_rate=1.0, invoice_date=date(2026, 11, 12),
                  status='draft', currency='EUR')
    db.session.add(inv)
    db.session.commit()
    monkeypatch.setitem(loaded_modules.modules, 'zz_templates', Templates(loaded_modules.core))
    monkeypatch.setattr(loaded_modules, 'filter_invoice_pdf', lambda invoice, pdf: pdf)
    try:
        with client.session_transaction() as sess:
            sess['authenticated'] = True
        resp = client.get(f'/preview-pdf/{inv.id}')
    finally:
        settings.invoice_template = before
        db.session.commit()
    assert resp.data == b'%PDF-zz' and seen == [inv.id]
