"""Invoice designer: blocks provided by other modules (get_invoice_blocks),
placed only where the module allows - here the Verifactu tax QR, which the
AEAT QR specification puts at the top of page 1, centred or towards the left."""
import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

SETTINGS = SimpleNamespace(business_name='Test SL', owner_name='Owner', vat_number='B1',
                           nie_number='X1', address='Calle 1', postal_code='12006',
                           city='Castellón', country='Spain', email='owner@example.com',
                           phone='', default_vat_rate=21.0, logo_path=None)


@pytest.fixture
def designer(standalone_module, monkeypatch):
    inst = standalone_module('invoice_designer')
    g = type(inst)._save_template.__globals__
    monkeypatch.setitem(g, 'url_for', lambda endpoint, **kw: f'/{endpoint}/{kw}')
    return inst, g


def _text_positions(pdf, needle):
    """x of every text containing `needle` on page 1."""
    xs = []
    def visit(text, cm, tm, font, size):
        if needle in text:
            xs.append(tm[4] * cm[0] + cm[4])
    PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=visit)
    return xs


def _qr_positions(pdf):
    return _text_positions(pdf, 'QR tributario')


def _render(g, config, blocks, invoice=None, preview=False):
    cfg = g['_merge_config'](config)
    return g['generate_pdf_from_config'](
        invoice=invoice or g['_demo_invoice'](), customer=g['_demo_customer'](),
        settings=SETTINGS, config=cfg, storage=None, blocks=blocks, preview=preview)


def _fake_block(**overrides):
    """A suggested block that writes 'FAKE-<align>-<n>' and records each ctx."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    calls = []

    def render(invoice, ctx):
        calls.append(ctx)
        return [Paragraph(f'FAKE-{ctx["align"]}-{ctx["options"]["n"]}', ParagraphStyle('f'))]

    block = {'id': 'fake', 'label': 'Fake', 'required': False,
             'slots': ['bottom-left', 'bottom-right', 'nowhere'], 'default_slot': 'bottom-right',
             'options': [{'id': 'n', 'label': 'N', 'choices': [[1, 'one'], [2, 'two']],
                          'default': 1}],
             'render': render, 'module_id': 'fake_module'}
    block.update(overrides)
    return block, calls


# ---- module manager ----

def test_manager_collects_module_blocks(loaded_modules):
    blocks = {b['id']: b for b in loaded_modules.get_invoice_blocks()}
    qr = blocks['verifactu_qr']
    assert qr['module_id'] == 'verifactu' and qr['required'] is True
    assert qr['slots'] == ['top-center', 'top-left'] and qr['default_slot'] == 'top-center'
    assert [c[0] for c in qr['options'][0]['choices']] == [30, 35, 40]


def test_manager_skips_invalid_and_failing_blocks(loaded_modules, monkeypatch):
    def broken():
        raise RuntimeError('boom')
    ok = {'id': 'extra', 'render': lambda invoice, ctx: []}
    monkeypatch.setitem(loaded_modules.modules, 'zz_fake', SimpleNamespace(
        module_id='zz_fake', get_invoice_blocks=lambda: [
            {'id': 'no_render'}, 'not a dict', ok,
            {'id': 'verifactu_qr', 'render': lambda invoice, ctx: []}]))
    monkeypatch.setitem(loaded_modules.modules, 'zz_broken', SimpleNamespace(
        module_id='zz_broken', get_invoice_blocks=broken))
    ids = [b['id'] for b in loaded_modules.get_invoice_blocks()]
    assert ids.count('verifactu_qr') == 1 and 'extra' in ids and 'no_render' not in ids
    extra = [b for b in loaded_modules.get_invoice_blocks() if b['id'] == 'extra'][0]
    assert extra['module_id'] == 'zz_fake'


# ---- normalizing and sanitizing ----

def test_normalize_blocks(designer):
    _inst, g = designer
    fake, _calls = _fake_block()
    required, _ = _fake_block(id='req', required=True, default_slot='footer-left')
    clash, _ = _fake_block(id='logo')
    no_slot, _ = _fake_block(id='none', slots=['nowhere', 'hidden'])
    norm = {b['id']: b for b in g['normalize_blocks']([fake, required, clash, no_slot])}
    assert set(norm) == {'fake', 'req'}
    assert norm['fake']['slots'] == ['bottom-left', 'bottom-right', 'hidden']   # suggested: may hide
    assert norm['req']['slots'] == ['bottom-left', 'bottom-right']             # required: may not
    assert norm['req']['default_slot'] == 'bottom-left'                         # invalid default


def test_sanitize_module_blocks(designer):
    _inst, g = designer
    fake, _ = _fake_block()
    blocks = g['normalize_blocks']([fake])
    sanitize = g['sanitize_module_blocks']
    cfg = sanitize({'block_positions': {'fake': 'top-left', 'other': 'x'},
                    'block_options': {'fake': {'n': '9'}, 'other': {'k': 1}}}, blocks)
    assert cfg['block_positions'] == {'fake': 'bottom-right', 'other': 'x'}   # unknown kept
    assert cfg['block_options'] == {'fake': {'n': 1}, 'other': {'k': 1}}
    cfg = sanitize({'block_positions': {'fake': 'hidden'}, 'block_options': {'fake': {'n': '2'}}},
                   blocks)
    assert cfg['block_positions']['fake'] == 'hidden' and cfg['block_options']['fake'] == {'n': 2}
    assert sanitize({}, blocks)['block_positions']['fake'] == 'bottom-right'


# ---- rendering ----

def test_module_block_rendered_in_chosen_slot_with_options(designer):
    _inst, g = designer
    fake, calls = _fake_block()
    pdf = _render(g, {'block_positions': {'fake': 'bottom-left'},
                      'block_options': {'fake': {'n': 2}}}, [fake])
    assert _text_positions(pdf, 'FAKE-left-2')
    assert calls[0]['slot'] == 'bottom-left' and calls[0]['preview'] is False

    calls.clear()
    pdf = _render(g, {}, [fake])                       # never placed: suggested slot
    assert _text_positions(pdf, 'FAKE-right-1')
    calls.clear()
    pdf = _render(g, {'block_positions': {'fake': 'hidden'}}, [fake])
    assert calls == [] and not _text_positions(pdf, 'FAKE')


def test_failing_block_does_not_break_the_pdf(designer):
    _inst, g = designer
    def render(invoice, ctx):
        raise RuntimeError('boom')
    fake, _ = _fake_block(render=render)
    pdf = _render(g, {}, [fake])
    assert pdf.startswith(b'%PDF')


def test_qr_block_top_center_and_top_left(designer, loaded_modules):
    _inst, g = designer
    blocks = loaded_modules.get_invoice_blocks()
    centre = _qr_positions(_render(g, {}, blocks, preview=True))          # default slot
    left = _qr_positions(_render(g, {'block_positions': {'verifactu_qr': 'top-left'}},
                                 blocks, preview=True))
    assert len(centre) == 1 and len(left) == 1
    assert 230 < centre[0] < 300          # letter page 612 pt wide, centred
    assert left[0] < 80                   # at the left margin (50 pt)
    # A slot the module does not allow falls back to its default (centred).
    other = _qr_positions(_render(g, {'block_positions': {'verifactu_qr': 'footer-right'}},
                                  blocks, preview=True))
    assert len(other) == 1 and 230 < other[0] < 300


def test_no_qr_for_drafts(designer, loaded_modules):
    _inst, g = designer
    assert _qr_positions(_render(g, {}, loaded_modules.get_invoice_blocks())) == []


def _text_y(pdf, needle):
    ys = []
    def visit(text, cm, tm, font, size):
        if needle in text:
            ys.append(tm[5] * cm[3] + cm[5])
    PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=visit)
    return ys[0]


def test_qr_size_option(designer, loaded_modules):
    from modules.verifactu.pdf_qr import qr_flowables
    _inst, g = designer
    blocks = loaded_modules.get_invoice_blocks()
    sizes = [round(qr_flowables('https://example.test/qr', size_mm=s)[2].width / 72 * 25.4)
             for s in (30, 40, 99)]
    assert sizes == [30, 40, 40]
    small = _render(g, {}, blocks, preview=True)
    big = _render(g, {'block_options': {'verifactu_qr': {'size_mm': '40'}}}, blocks, preview=True)
    # The 10 mm larger QR pushes the content below it down by about 28 pt.
    shift = _text_y(small, 'Bill To') - _text_y(big, 'Bill To')
    assert 25 < shift < 31


# ---- the designer form ----

def _form(**extra):
    form = {'accent_color': '#5B6FD8', 'text_color': '#4A4A4A', 'header_bg': '#F5F5F5',
            'font': 'Helvetica', 'title_font_size': '28', 'layout': 'modern'}
    form.update(extra)
    return form


def test_form_saves_module_block_position_and_options(designer, loaded_modules, app):
    inst, _g = designer
    form = _form(name='QR left', pos_verifactu_qr='top-left', opt_verifactu_qr_size_mm='35')
    with app.test_request_context('/invoice-designer/create', method='POST', data=form):
        inst._save_template(None)
    tpl = inst.InvoiceTemplate.query.filter_by(name='QR left').one()
    cfg = json.loads(tpl.config_json)
    assert cfg['block_positions']['verifactu_qr'] == 'top-left'
    assert cfg['block_options']['verifactu_qr'] == {'size_mm': 35}

    # Not allowed: hidden (required block), a footer slot, an 80 mm QR.
    for pos in ('hidden', 'footer-right'):
        form = _form(name=f'QR {pos}', pos_verifactu_qr=pos, opt_verifactu_qr_size_mm='80')
        with app.test_request_context('/invoice-designer/create', method='POST', data=form):
            inst._save_template(None)
        cfg = json.loads(inst.InvoiceTemplate.query.filter_by(name=f'QR {pos}').one().config_json)
        assert cfg['block_positions']['verifactu_qr'] == 'top-center'
        assert cfg['block_options']['verifactu_qr'] == {'size_mm': 30}


def test_form_keeps_blocks_of_disabled_modules(designer, loaded_modules, app, monkeypatch):
    inst, _g = designer
    tpl = inst.InvoiceTemplate(name='Keep', config_json=json.dumps(
        {'block_positions': {'verifactu_qr': 'top-left'},
         'block_options': {'verifactu_qr': {'size_mm': 35}}}))
    from app import db
    db.session.add(tpl)
    db.session.commit()
    monkeypatch.delitem(loaded_modules.modules, 'verifactu')
    with app.test_request_context('/invoice-designer/edit', method='POST', data=_form(name='Keep')):
        inst._save_template(tpl)
    cfg = json.loads(tpl.config_json)
    assert cfg['block_positions']['verifactu_qr'] == 'top-left'
    assert cfg['block_options']['verifactu_qr'] == {'size_mm': 35}


def test_form_lists_module_blocks_with_allowed_slots(designer, loaded_modules, app):
    import os
    from jinja2 import ChoiceLoader, FileSystemLoader
    inst, g = designer
    blocks = inst._module_blocks()
    config = g['sanitize_module_blocks'](g['_deep_copy_config'](g['DEFAULT_CONFIG']), blocks)
    env = app.jinja_env.overlay(loader=ChoiceLoader([
        FileSystemLoader(os.path.join(inst.module_dir, 'templates')), app.jinja_loader]))
    with app.test_request_context('/invoice-designer/create'):
        ctx = {'template': None, 'config': config, 'config_json': json.dumps(config),
               'block_ids': g['BLOCK_IDS'], 'slot_choices': g['SLOT_CHOICES'],
               'module_blocks': blocks, 'layout_presets': g['LAYOUT_PRESETS'],
               'vertical_line_zones': g['VERTICAL_LINE_ZONES'],
               'line_below_zones': g['LINE_BELOW_ZONES'],
               'url_for': lambda endpoint, **kw: f'/{endpoint}'}
        app.update_template_context(ctx)
        html = env.get_template('designer_form.html').render(ctx)
    start = html.index('name="pos_verifactu_qr"')
    select = html[start:html.index('</select>', start)]
    assert 'Blocks from modules' in html and 'Required' in html
    assert 'value="top-center" selected' in select and 'value="top-left"' in select
    assert 'hidden' not in select and 'footer' not in select
    assert 'name="opt_verifactu_qr_size_mm"' in html
    # The zone map knows each module block's allowed cells and that it is required.
    assert 'name="line_v_top"' in html and 'name="line_b_top"' in html
    assert 'name="line_v_subtotal"' not in html and 'name="line_b_footer"' not in html
    assert 'id="zoneMap"' in html and 'data-slot="top-left"' in html and 'data-slot="hidden"' in html
    assert ('blockMeta["verifactu_qr"] = {slots: ["top-center", "top-left"], required: true}'
            in html)


# ---- with the Verifactu PDF hook ----

def test_stamping_skips_a_pdf_that_already_has_the_qr(designer, loaded_modules):
    from modules.verifactu.pdf_qr import has_qr, stamp_qr
    _inst, g = designer
    placed = _render(g, {}, loaded_modules.get_invoice_blocks(), preview=True)
    assert has_qr(placed)
    plain = _render(g, {}, [])
    assert not has_qr(plain)
    assert has_qr(stamp_qr(plain, 'https://example.test/qr'))


def test_issued_invoice_with_designer_template_gets_one_qr(designer, loaded_modules, client,
                                                           tmp_path, monkeypatch):
    from datetime import date
    from app import Customer, Invoice, Settings, _issue_invoice, db
    from module_manager import LocalStorageBackend
    inst, _g = designer
    monkeypatch.setitem(loaded_modules.modules, 'invoice_designer', inst)
    monkeypatch.setattr(loaded_modules.core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    tpl = inst.InvoiceTemplate(name='QR top-left', config_json=json.dumps(
        {'block_positions': {'verifactu_qr': 'top-left'}}))
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
    xs = _qr_positions(resp.data)
    assert len(xs) == 1 and xs[0] < 80          # placed by the template, not stamped again
    settings.invoice_template = 'default_template'
    db.session.commit()


# ---- alignment of module blocks and lines per zone ----

def test_module_block_parts_share_one_axis(designer):
    """A table cell ignores a flowable's own alignment; the wrapper centres
    (or left/right-aligns) every part of the block in its column."""
    from reportlab.platypus import Spacer
    _inst, g = designer
    drawn = []

    class Part(Spacer):
        def drawOn(self, canvas, x, y, _sW=0):
            drawn.append((x, self.width))

    for align, expected in (('center', [50, 70]), ('left', [0, 0]), ('right', [100, 140])):
        drawn.clear()
        block = g['aligned_block']([Part(100, 10), Part(60, 20)], align)
        assert block.wrap(200, 500) == (200, 30)
        block.canv = None
        block.draw()
        assert [x for x, _w in drawn] == expected


def _line_ops(pdf):
    content = PdfReader(io.BytesIO(pdf)).pages[0].get_contents().get_data()
    return content.count(b' l S') + content.count(b' l\nS')


def test_lines_per_zone(designer):
    _inst, g = designer
    base = {'show_vertical_lines': True, 'show_separator_lines': True,
            'block_positions': {'logo': 'hidden', 'title': 'top-left', 'invoice_meta': 'top-right'}}
    all_lines = _line_ops(_render(g, base, []))
    no_top_vertical = _line_ops(_render(g, dict(base, zone_lines={'top': {'vertical': False}}), []))
    no_top_below = _line_ops(_render(g, dict(base, zone_lines={'top': {'below': False}}), []))
    assert no_top_vertical == all_lines - 1 and no_top_below == all_lines - 1
    # The global switches still turn every line of their kind off.
    off = _line_ops(_render(g, dict(base, show_vertical_lines=False), []))
    assert off < all_lines and _line_ops(_render(
        g, dict(base, show_vertical_lines=False, zone_lines={'top': {'vertical': True}}), [])) == off


def test_form_saves_lines_per_zone(designer, loaded_modules, app):
    inst, _g = designer
    form = _form(name='Lines', show_vertical_lines='on', line_v_header='on', line_v_bottom='on',
                 line_v_footer='on', line_b_top='on', line_b_header='on', line_b_subtotal='on')
    with app.test_request_context('/invoice-designer/create', method='POST', data=form):
        inst._save_template(None)
    cfg = json.loads(inst.InvoiceTemplate.query.filter_by(name='Lines').one().config_json)
    assert cfg['zone_lines']['top'] == {'vertical': False, 'below': True}
    assert cfg['zone_lines']['bottom'] == {'vertical': True, 'below': False}
    assert cfg['zone_lines']['footer'] == {'vertical': True}
    assert cfg['zone_lines']['subtotal'] == {'below': True}
