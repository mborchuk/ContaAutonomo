"""E-2 — Invoice Designer: template create and edit, config merge, PDF render."""
import io
import json

import pytest
from pypdf import PdfReader


@pytest.fixture
def designer(loaded_modules, monkeypatch):
    """Standalone instance (the module is not enabled in the test session, and
    blueprints cannot be registered after the first request), with url_for
    stubbed for its redirects."""
    from app import db
    inst = loaded_modules.discovered['invoice_designer'](loaded_modules.core)
    inst.register_models(db)
    db.create_all()
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

    listed = inst.get_invoice_templates()
    assert {'id': f'designer_{tpl.id}', 'name': '🎨 Plain v2', 'path': '__designer__',
            '_designer_id': tpl.id} in listed


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
