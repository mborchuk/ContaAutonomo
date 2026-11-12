"""Invoice designer: the Verifactu QR as a placeable block, limited to the
positions and sizes the AEAT QR specification allows."""
import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

SETTINGS = SimpleNamespace(business_name='Test SL', owner_name='Owner', vat_number='B1',
                           nie_number='X1', address='Calle 1', postal_code='12006',
                           city='Castellón', country='Spain', email='owner@example.com',
                           phone='', default_vat_rate=21.0, logo_path=None)
QR = {'url': 'https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?nif=00000000T'
              '&numserie=2026%2F0001&fecha=12-11-2026&importe=121.00', 'phrase': None}


@pytest.fixture
def designer(standalone_module, monkeypatch):
    inst = standalone_module('invoice_designer')
    g = type(inst)._save_template.__globals__
    monkeypatch.setitem(g, 'url_for', lambda endpoint, **kw: f'/{endpoint}/{kw}')
    return inst, g


def _label_positions(pdf):
    """x of every 'QR tributario:' text on page 1."""
    xs = []
    def visit(text, cm, tm, font, size):
        if 'QR tributario' in text:
            xs.append(tm[4] * cm[0] + cm[4])
    PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=visit)
    return xs


def _render(g, slot, qr=QR, size=30):
    cfg = g['_merge_config']({'block_positions': {'verifactu_qr': slot},
                              'verifactu_qr_size_mm': size})
    return g['generate_pdf_from_config'](invoice=g['_demo_invoice'](), customer=g['_demo_customer'](),
                                         settings=SETTINGS, config=cfg, storage=None, qr=qr)


def test_qr_block_top_center_and_top_left(designer):
    _inst, g = designer
    centre = _label_positions(_render(g, 'top-center'))
    left = _label_positions(_render(g, 'top-left'))
    assert len(centre) == 1 and len(left) == 1
    assert 230 < centre[0] < 300          # letter page 612 pt wide, centred
    assert left[0] < 80                   # at the left margin (50 pt)


def test_no_qr_for_drafts_and_hidden_slot(designer):
    _inst, g = designer
    assert _label_positions(_render(g, 'top-center', qr=None)) == []
    assert _label_positions(_render(g, 'hidden')) == []


def test_disallowed_slot_falls_back_to_top_center(designer):
    _inst, g = designer
    xs = _label_positions(_render(g, 'footer-right'))
    assert len(xs) == 1 and 230 < xs[0] < 300


def test_sanitize_qr_config(designer):
    _inst, g = designer
    sanitize = g['sanitize_qr_config']
    assert sanitize({'block_positions': {'verifactu_qr': 'bottom-right'}})['block_positions']['verifactu_qr'] == 'top-center'
    assert sanitize({'verifactu_qr_size_mm': 99})['verifactu_qr_size_mm'] == 40
    assert sanitize({'verifactu_qr_size_mm': 'x'})['verifactu_qr_size_mm'] == 30
    assert sanitize({'block_positions': {'verifactu_qr': 'top-left'}, 'verifactu_qr_size_mm': 35}) == {
        'block_positions': {'verifactu_qr': 'top-left'}, 'verifactu_qr_size_mm': 35}


def test_form_offers_only_compliant_positions_and_saves_them(designer, app):
    inst, _g = designer
    form = {'name': 'QR left', 'accent_color': '#5B6FD8', 'text_color': '#4A4A4A',
            'header_bg': '#F5F5F5', 'font': 'Helvetica', 'title_font_size': '28',
            'layout': 'modern', 'pos_verifactu_qr': 'top-left', 'verifactu_qr_size_mm': '35'}
    with app.test_request_context('/invoice-designer/create', method='POST', data=form):
        inst._save_template(None)
    tpl = inst.InvoiceTemplate.query.filter_by(name='QR left').one()
    cfg = json.loads(tpl.config_json)
    assert cfg['block_positions']['verifactu_qr'] == 'top-left' and cfg['verifactu_qr_size_mm'] == 35

    form.update({'name': 'QR bad', 'pos_verifactu_qr': 'footer-right', 'verifactu_qr_size_mm': '80'})
    with app.test_request_context('/invoice-designer/create', method='POST', data=form):
        inst._save_template(None)
    cfg = json.loads(inst.InvoiceTemplate.query.filter_by(name='QR bad').one().config_json)
    assert cfg['block_positions']['verifactu_qr'] == 'top-center' and cfg['verifactu_qr_size_mm'] == 40


def test_form_template_restricts_qr_options():
    with open('modules/invoice_designer/templates/designer_form.html') as f:
        src = f.read()
    assert "{% if bid == 'verifactu_qr' %}" in src and 'qr_slot_choices' in src
    assert 'name="verifactu_qr_size_mm"' in src


def test_stamping_skips_a_pdf_that_already_has_the_qr(designer, loaded_modules):
    from modules.verifactu.pdf_qr import has_qr, stamp_qr
    _inst, g = designer
    placed = _render(g, 'top-left')
    assert has_qr(placed)
    plain = _render(g, 'hidden')
    assert not has_qr(plain)
    assert has_qr(stamp_qr(plain, QR['url']))


def test_issued_invoice_with_designer_template_gets_one_qr(designer, loaded_modules, client,
                                                           tmp_path, monkeypatch):
    from datetime import date
    from app import Customer, Invoice, Settings, _issue_invoice, db
    from module_manager import LocalStorageBackend
    inst, _g = designer
    monkeypatch.setitem(loaded_modules.modules, 'invoice_designer', inst)
    monkeypatch.setattr(loaded_modules.core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    tpl = inst.InvoiceTemplate(name='QR top-left', config_json=json.dumps(
        {'block_positions': {'verifactu_qr': 'top-left'}, 'verifactu_qr_size_mm': 30}))
    db.session.add(tpl)
    settings = Settings.query.first() or Settings()
    db.session.add(settings)
    db.session.flush()                      # assigns tpl.id
    settings.invoice_template = f'designer_{tpl.id}'
    cust = Customer(name='QR Designer SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number='QRD-1', client_name=cust.name, customer_id=cust.id,
                  amount_usd=0.0, amount_eur=100.0, exchange_rate=1.0,
                  invoice_date=date(2026, 11, 12), status='draft', currency='EUR')
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/generate-pdf/{inv.id}')
    assert resp.mimetype == 'application/pdf'
    xs = _label_positions(resp.data)
    assert len(xs) == 1 and xs[0] < 80          # placed by the template, not stamped again
    settings.invoice_template = 'default_template'
    db.session.commit()
