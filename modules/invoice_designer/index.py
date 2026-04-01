#!/usr/bin/env python3
"""
Invoice Designer Module
UI for creating parameterized invoice PDF templates.
Stores config as JSON; a universal generator renders PDFs from it.

Layout grid:
  top:    left | center | right
  header: left | center | right
  body:   items table + totals (configurable)
  bottom: left | center | right
  footer: left | center | right

Each block (logo, title, sender_info, recipient_info, invoice_meta,
bank_details, notes, payment_terms, custom_text) can be assigned to
a slot like "top-left", "header-right", "bottom-center", etc.
"""

from module_manager import BaseModule
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, Response)
from datetime import datetime, date
import json
import os
import logging

logger = logging.getLogger(__name__)

# All placeable blocks
BLOCK_IDS = [
    'logo', 'title', 'sender_info', 'recipient_info',
    'invoice_meta', 'bank_details', 'notes', 'payment_terms',
]

# Valid slot positions
SLOT_CHOICES = [
    'top-left', 'top-center', 'top-right',
    'header-left', 'header-center', 'header-right',
    'bottom-left', 'bottom-center', 'bottom-right',
    'footer-left', 'footer-center', 'footer-right',
    'hidden',
]

DEFAULT_CONFIG = {
    'accent_color': '#5B6FD8',
    'text_color': '#4A4A4A',
    'header_bg': '#F5F5F5',
    'page_bg': '',               # empty = white; hex color for page background
    'font': 'Helvetica',
    'title_font_size': 28,
    'show_logo': False,
    'logo_path': '',
    'layout': 'modern',          # modern | classic | minimal
    'show_bank_details': True,
    'show_notes': True,
    'show_payment_terms': True,
    'show_due_date': True,
    'show_vat_breakdown': True,
    'show_accent_line': True,
    'show_separator_lines': False,  # thin lines between zones
    'meta_gap': 10,                  # gap (pt) between label and value in invoice_meta block
    'labels': {
        'invoice_title': 'Invoice',
        'description': 'Description',
        'quantity': 'Qty',
        'unit_price': 'Unit Price',
        'total': 'Total',
        'subtotal': 'Subtotal',
        'tax': 'IVA',
        'total': 'Total',
        'notes': 'Notes',
        'bank_details': 'Bank Details',
        'due_date': 'Due Date',
        'issue_date': 'Issue Date',
        'invoice_number': 'Invoice #',
        'bill_to': 'Bill To',
        'payment_terms': 'Payment Terms',
    },
    # Block placement: block_id -> slot position
    'block_positions': {
        'logo':           'top-left',
        'title':          'top-right',
        'sender_info':    'header-left',
        'recipient_info': 'header-right',
        'invoice_meta':   'header-left',
        'bank_details':   'bottom-left',
        'notes':          'bottom-right',
        'payment_terms':  'bottom-center',
    },
    # Fine-tune offsets in points (pt): {block_id: {x: 0, y: 0}}
    'block_offsets': {},
    # Zone column widths in pt: {zone_name: [left, right] or [left, center, right]}
    # Total should equal ~512 (CONTENT_W). Empty = auto equal split.
    'zone_columns': {},
}

# Preset layout configurations inspired by common professional invoice designs
LAYOUT_PRESETS = {
    'standard': {
        'label': 'Standard — Logo left, title right, sender/recipient side-by-side',
        'block_positions': {
            'logo':           'top-left',
            'title':          'top-right',
            'sender_info':    'header-left',
            'recipient_info': 'header-right',
            'invoice_meta':   'top-right',
            'bank_details':   'bottom-left',
            'notes':          'bottom-right',
            'payment_terms':  'footer-left',
        },
    },
    'classic_right': {
        'label': 'Classic — Title + meta top-right, addresses below',
        'block_positions': {
            'logo':           'top-left',
            'title':          'top-right',
            'invoice_meta':   'top-right',
            'sender_info':    'header-left',
            'recipient_info': 'header-right',
            'bank_details':   'bottom-left',
            'notes':          'bottom-left',
            'payment_terms':  'bottom-right',
        },
    },
    'modern_center': {
        'label': 'Modern Center — Title centered, logo left, meta right',
        'block_positions': {
            'logo':           'top-left',
            'title':          'top-center',
            'invoice_meta':   'top-right',
            'sender_info':    'header-left',
            'recipient_info': 'header-right',
            'bank_details':   'bottom-center',
            'notes':          'footer-left',
            'payment_terms':  'footer-right',
        },
    },
    'minimal_left': {
        'label': 'Minimal Left — Everything stacked left, clean look',
        'block_positions': {
            'logo':           'top-left',
            'title':          'top-left',
            'invoice_meta':   'top-right',
            'sender_info':    'header-left',
            'recipient_info': 'header-right',
            'bank_details':   'footer-left',
            'notes':          'bottom-left',
            'payment_terms':  'hidden',
        },
    },
    'compact_header': {
        'label': 'Compact Header — Logo + sender left, title + meta + recipient right',
        'block_positions': {
            'logo':           'top-left',
            'sender_info':    'top-left',
            'title':          'top-right',
            'invoice_meta':   'top-right',
            'recipient_info': 'header-right',
            'bank_details':   'bottom-left',
            'notes':          'bottom-right',
            'payment_terms':  'footer-center',
        },
    },
    'bottom_bank': {
        'label': 'Bottom Bank — Bank details + payment terms in footer row',
        'block_positions': {
            'logo':           'top-left',
            'title':          'top-right',
            'invoice_meta':   'header-right',
            'sender_info':    'header-left',
            'recipient_info': 'header-right',
            'notes':          'bottom-left',
            'bank_details':   'footer-left',
            'payment_terms':  'footer-right',
        },
    },
}


class InvoiceDesignerModule(BaseModule):

    @property
    def module_id(self):
        return 'invoice_designer'

    @property
    def name(self):
        return 'Invoice Designer'

    @property
    def description(self):
        return 'Visual UI for creating custom invoice PDF templates with block positioning, colors, fonts, layout options and label overrides'

    @property
    def version(self):
        return '0.2.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Invoice Designer', 'endpoint': 'invoice_designer.designer_index', 'icon': '🎨', 'group': 'Invoices'}
        ]

    def register_models(self, db):
        self._db = db

        class InvoiceTemplate(db.Model):
            __tablename__ = 'invoice_template_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(200), nullable=False)
            config_json = db.Column(db.Text, nullable=False)
            logo_storage_key = db.Column(db.String(500))
            is_default = db.Column(db.Boolean, default=False)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            updated_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.InvoiceTemplate = InvoiceTemplate
        return {'InvoiceTemplate': InvoiceTemplate}

    def register_routes(self, app):
        bp = Blueprint('invoice_designer', __name__,
                       template_folder='templates', url_prefix='/invoice-designer')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def designer_index():
            return module._list_templates()

        @bp.route('/create', methods=['GET', 'POST'])
        @login_required
        def designer_create():
            return module._create_template()

        @bp.route('/edit/<int:id>', methods=['GET', 'POST'])
        @login_required
        def designer_edit(id):
            return module._edit_template(id)

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def designer_delete(id):
            return module._delete_template(id)

        @bp.route('/duplicate/<int:id>', methods=['POST'])
        @login_required
        def designer_duplicate(id):
            return module._duplicate_template(id)

        @bp.route('/preview/<int:id>')
        @login_required
        def designer_preview(id):
            return module._preview_template(id)

        @bp.route('/import', methods=['POST'])
        @login_required
        def designer_import():
            return module._import_template()

        @bp.route('/export/<int:id>')
        @login_required
        def designer_export(id):
            return module._export_template(id)

        app.register_blueprint(bp)

    def get_invoice_templates(self):
        """Register custom templates so they appear in Settings dropdown."""
        templates = []
        try:
            for tpl in self.InvoiceTemplate.query.all():
                templates.append({
                    'id': f'designer_{tpl.id}',
                    'name': f'🎨 {tpl.name}',
                    'path': '__designer__',
                    '_designer_id': tpl.id,
                })
        except Exception:
            pass
        return templates

    # ---- CRUD ----

    def _list_templates(self):
        templates = self.InvoiceTemplate.query.order_by(
            self.InvoiceTemplate.updated_at.desc()).all()
        return render_template('designer_list.html', templates=templates)

    def _create_template(self):
        if request.method == 'POST':
            return self._save_template(None)
        config = _deep_copy_config(DEFAULT_CONFIG)
        return render_template('designer_form.html', template=None,
                               config=config, config_json=json.dumps(config, indent=2),
                               block_ids=BLOCK_IDS, slot_choices=SLOT_CHOICES,
                               layout_presets=LAYOUT_PRESETS)

    def _edit_template(self, id):
        tpl = self.InvoiceTemplate.query.get_or_404(id)
        if request.method == 'POST':
            return self._save_template(tpl)
        config = json.loads(tpl.config_json)
        merged = _merge_config(config)
        return render_template('designer_form.html', template=tpl,
                               config=merged, config_json=json.dumps(merged, indent=2),
                               block_ids=BLOCK_IDS, slot_choices=SLOT_CHOICES,
                               layout_presets=LAYOUT_PRESETS)

    def _save_template(self, tpl):
        try:
            name = request.form.get('name', '').strip()
            if not name:
                flash('Template name is required.', 'danger')
                return redirect(request.url)

            # If submitted from JSON editor, use raw JSON directly
            raw_json = request.form.get('config_json_raw', '').strip()
            if raw_json:
                config = json.loads(raw_json)
                logo = request.files.get('logo')
                if logo and logo.filename:
                    from werkzeug.utils import secure_filename
                    fname = secure_filename(logo.filename)
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    rel = os.path.join('invoice_logos', f'{ts}_{fname}')
                    key = self.core.storage.save(logo, rel)
                    config['logo_path'] = key
                elif tpl:
                    old_config = json.loads(tpl.config_json)
                    if old_config.get('logo_path') and 'logo_path' not in config:
                        config['logo_path'] = old_config['logo_path']
                config_json = json.dumps(config)
            else:
                config = {
                    'accent_color': request.form.get('accent_color', '#5B6FD8'),
                    'text_color': request.form.get('text_color', '#4A4A4A'),
                    'header_bg': request.form.get('header_bg', '#F5F5F5'),
                    'page_bg': request.form.get('page_bg', '') if request.form.get('page_bg_enabled') or 'page_bg_enabled' in request.form else '',
                    'font': request.form.get('font', 'Helvetica'),
                    'title_font_size': int(request.form.get('title_font_size', 28) or 28),
                    'meta_gap': int(request.form.get('meta_gap', 10) or 10),
                    'layout': request.form.get('layout', 'modern'),
                    'show_logo': 'show_logo' in request.form,
                    'show_bank_details': 'show_bank_details' in request.form,
                    'show_notes': 'show_notes' in request.form,
                    'show_payment_terms': 'show_payment_terms' in request.form,
                    'show_due_date': 'show_due_date' in request.form,
                    'show_vat_breakdown': 'show_vat_breakdown' in request.form,
                    'show_accent_line': 'show_accent_line' in request.form,
                    'show_separator_lines': 'show_separator_lines' in request.form,
                    'labels': {},
                    'block_positions': {},
                    'block_offsets': {},
                }

                for key in DEFAULT_CONFIG['labels']:
                    val = request.form.get(f'label_{key}', '').strip()
                    if val:
                        config['labels'][key] = val

                # Block positions
                for bid in BLOCK_IDS:
                    pos = request.form.get(f'pos_{bid}', '').strip()
                    if pos:
                        config['block_positions'][bid] = pos
                    ox = request.form.get(f'offset_x_{bid}', '').strip()
                    oy = request.form.get(f'offset_y_{bid}', '').strip()
                    if ox or oy:
                        config['block_offsets'][bid] = {
                            'x': float(ox) if ox else 0,
                            'y': float(oy) if oy else 0,
                        }

                # Parse zone column widths
                config['zone_columns'] = {}
                for zone in ('top', 'header', 'bottom', 'footer'):
                    raw = request.form.get(f'zone_col_{zone}', '').strip()
                    if raw:
                        parts = [float(x.strip()) for x in raw.split(':') if x.strip()]
                        if len(parts) in (2, 3):
                            config['zone_columns'][zone] = parts

                # Handle logo upload
                logo = request.files.get('logo')
                if logo and logo.filename:
                    from werkzeug.utils import secure_filename
                    fname = secure_filename(logo.filename)
                    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                    rel = os.path.join('invoice_logos', f'{ts}_{fname}')
                    key = self.core.storage.save(logo, rel)
                    config['logo_path'] = key
                    config['show_logo'] = True
                elif tpl:
                    old_config = json.loads(tpl.config_json)
                    if old_config.get('logo_path'):
                        config['logo_path'] = old_config['logo_path']

                config_json = json.dumps(config)

            if tpl:
                tpl.name = name
                tpl.config_json = config_json
                tpl.updated_at = datetime.utcnow()
            else:
                tpl = self.InvoiceTemplate(name=name, config_json=config_json)
                self._db.session.add(tpl)

            self._db.session.commit()
            flash(f'Template "{name}" saved.', 'success')
            return redirect(url_for('invoice_designer.designer_index'))
        except Exception as e:
            self._db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(request.url)

    def _delete_template(self, id):
        tpl = self.InvoiceTemplate.query.get_or_404(id)
        name = tpl.name
        self._db.session.delete(tpl)
        self._db.session.commit()
        flash(f'Template "{name}" deleted.', 'success')
        return redirect(url_for('invoice_designer.designer_index'))

    def _duplicate_template(self, id):
        src = self.InvoiceTemplate.query.get_or_404(id)
        dup = self.InvoiceTemplate(
            name=f'{src.name} (copy)',
            config_json=src.config_json,
            logo_storage_key=src.logo_storage_key,
        )
        self._db.session.add(dup)
        self._db.session.commit()
        flash('Template duplicated.', 'success')
        return redirect(url_for('invoice_designer.designer_edit', id=dup.id))

    def _preview_template(self, id):
        tpl = self.InvoiceTemplate.query.get_or_404(id)
        config = json.loads(tpl.config_json)
        merged = _merge_config(config)

        settings = self.core.get_settings()

        # Try to get real default bank from DB
        bank = None
        try:
            from sqlalchemy import text as _text
            row = self._db.session.execute(
                _text('SELECT iban, swift, bank_name FROM bank WHERE is_default = 1 LIMIT 1')
            ).fetchone()
            if row:
                bank = _Obj(iban=row[0] or '', swift=row[1] or '', bank_name=row[2] or '')
        except Exception:
            pass
        if not bank:
            bank = _Obj(iban='ES00 0000 0000 0000 0000 0000', swift='ABCDESXX', bank_name='Demo Bank')

        # Try to get a real customer for preview
        customer = _demo_customer()
        try:
            row = self._db.session.execute(
                _text('SELECT name, vat_number, address, city, postal_code, country, tax_type FROM customer LIMIT 1')
            ).fetchone()
            if row:
                customer = _Obj(name=row[0] or 'Demo Client', vat_number=row[1] or '',
                                address=row[2] or '', city=row[3] or '',
                                postal_code=row[4] or '', country=row[5] or '',
                                tax_type=row[6] or 'eu_b2b')
        except Exception:
            pass

        invoice = _demo_invoice()
        invoice.bank = bank

        pdf_bytes = generate_pdf_from_config(
            invoice=invoice,
            customer=customer,
            settings=settings,
            config=merged,
            storage=self.core.storage,
        )
        return Response(pdf_bytes, mimetype='application/pdf',
                        headers={'Content-Disposition': 'inline'})

    def _import_template(self):
        """Import a template from uploaded JSON file."""
        try:
            f = request.files.get('json_file')
            if not f or not f.filename:
                flash('No file selected.', 'danger')
                return redirect(url_for('invoice_designer.designer_index'))
            raw = f.read().decode('utf-8')
            config = json.loads(raw)
            name = request.form.get('import_name', '').strip()
            if not name:
                name = f.filename.rsplit('.', 1)[0].replace('_', ' ').title()
            tpl = self.InvoiceTemplate(
                name=name,
                config_json=json.dumps(config),
            )
            self._db.session.add(tpl)
            self._db.session.commit()
            flash(f'Template "{name}" imported.', 'success')
            return redirect(url_for('invoice_designer.designer_edit', id=tpl.id))
        except Exception as e:
            self._db.session.rollback()
            flash(f'Import error: {e}', 'danger')
            return redirect(url_for('invoice_designer.designer_index'))

    def _export_template(self, id):
        """Export template config as JSON download."""
        tpl = self.InvoiceTemplate.query.get_or_404(id)
        config = json.loads(tpl.config_json)
        pretty = json.dumps(config, indent=2, ensure_ascii=False)
        safe_name = tpl.name.replace(' ', '_').lower()
        return Response(pretty, mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{safe_name}.json"'})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_copy_config(cfg):
    return json.loads(json.dumps(cfg))


def _merge_config(user_cfg):
    merged = _deep_copy_config(DEFAULT_CONFIG)
    for k, v in user_cfg.items():
        if k == 'labels':
            merged['labels'] = dict(DEFAULT_CONFIG['labels'])
            merged['labels'].update(v)
        elif k == 'block_positions':
            merged['block_positions'] = dict(DEFAULT_CONFIG['block_positions'])
            merged['block_positions'].update(v)
        elif k == 'block_offsets':
            merged['block_offsets'] = dict(DEFAULT_CONFIG.get('block_offsets', {}))
            merged['block_offsets'].update(v)
        elif k == 'zone_columns':
            merged['zone_columns'] = dict(DEFAULT_CONFIG.get('zone_columns', {}))
            merged['zone_columns'].update(v)
        else:
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Demo data for preview
# ---------------------------------------------------------------------------

class _Obj:
    """Simple attribute bag."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _demo_invoice():
    return _Obj(
        invoice_number='2026-DEMO',
        invoice_date=date.today(),
        due_date=date.today(),
        amount_usd=1500.00,
        amount_eur=1380.00,
        currency='EUR',
        description='Web Development Services',
        quantity=1,
        unit_price_usd=1500.00,
        notes='Thank you for your business!',
        status='draft',
        client_name='Demo Client',
        items=[
            _Obj(description='Frontend Development', quantity=10,
                 unit_price_usd=100.00, subtotal_usd=1000.00),
            _Obj(description='Backend API Integration', quantity=5,
                 unit_price_usd=100.00, subtotal_usd=500.00),
        ],
        bank=_Obj(iban='ES12 3456 7890 1234 5678 9012',
                   swift='ABCDESXX', bank_name='Demo Bank'),
    )


def _demo_customer():
    return _Obj(
        name='Demo Client SL',
        vat_number='DE123456789',
        address='123 Business Street',
        city='Berlin',
        postal_code='10115',
        country='Germany',
        tax_type='eu_b2b',
    )


# ---------------------------------------------------------------------------
# Universal PDF generator from config — grid-based layout
# ---------------------------------------------------------------------------

def generate_pdf_from_config(invoice, customer, settings, config, storage=None):
    """
    Generate invoice PDF bytes from a config dict.

    Layout zones (top → footer), each with left | center | right slots.
    Blocks are placed according to config['block_positions'].
    """
    import io as _io
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, Image)
    from currency_converter import get_currency_symbol

    PAGE_W = letter[0]  # 612
    MARGIN = 50
    CONTENT_W = PAGE_W - 2 * MARGIN  # 512

    c = config
    labels = c.get('labels', {})
    def L(key):
        return labels.get(key, DEFAULT_CONFIG['labels'].get(key, key))

    accent = colors.HexColor(c.get('accent_color', '#5B6FD8'))
    text_c = colors.HexColor(c.get('text_color', '#4A4A4A'))
    hdr_bg = colors.HexColor(c.get('header_bg', '#F5F5F5'))
    page_bg_hex = c.get('page_bg', '') or ''
    font = c.get('font', 'Helvetica')
    font_b = f'{font}-Bold' if font == 'Helvetica' else font
    layout = c.get('layout', 'modern')
    title_fs = c.get('title_font_size', 28)
    show_sep = c.get('show_separator_lines', False)

    positions = c.get('block_positions', DEFAULT_CONFIG['block_positions'])
    offsets = c.get('block_offsets', {})

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            rightMargin=MARGIN, leftMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)
    elements = []

    # ---- Styles ----
    s_title = ParagraphStyle('dt', fontSize=title_fs, fontName=font_b,
                             textColor=accent, spaceAfter=4)
    s_title_r = ParagraphStyle('dtr', fontSize=title_fs, fontName=font_b,
                               textColor=accent, spaceAfter=4, alignment=2)
    s_title_c = ParagraphStyle('dtc', fontSize=title_fs, fontName=font_b,
                               textColor=accent, spaceAfter=4, alignment=1)
    s_section = ParagraphStyle('ds', fontSize=10, fontName=font_b,
                               textColor=accent, spaceAfter=4)
    s_normal = ParagraphStyle('dn', fontSize=10, fontName=font,
                              textColor=text_c, leading=13)
    s_small = ParagraphStyle('dsm', fontSize=9, fontName=font,
                             textColor=text_c, leading=12)
    s_small_r = ParagraphStyle('dsmr', fontSize=9, fontName=font,
                               textColor=text_c, leading=12, alignment=2)
    s_small_c = ParagraphStyle('dsmc', fontSize=9, fontName=font,
                               textColor=text_c, leading=12, alignment=1)
    s_right = ParagraphStyle('dr', fontSize=10, fontName=font_b,
                             textColor=text_c, alignment=2)
    s_right_accent = ParagraphStyle('dra', fontSize=10, fontName=font_b,
                                    textColor=accent, alignment=0)
    s_total_label = ParagraphStyle('dtl', fontSize=13, fontName=font_b,
                                   textColor=accent, alignment=0)
    s_total_val = ParagraphStyle('dtv', fontSize=13, fontName=font_b,
                                 textColor=text_c, alignment=2)
    s_meta_label = ParagraphStyle('dml', fontSize=8, fontName=font,
                                  textColor=colors.HexColor('#999999'))
    s_meta_val = ParagraphStyle('dmv', fontSize=10, fontName=font_b,
                                textColor=text_c)

    def _style_for_slot(slot):
        """Return (text_style, alignment) based on slot position."""
        if slot.endswith('-right'):
            return s_small_r, 2
        elif slot.endswith('-center'):
            return s_small_c, 1
        return s_small, 0

    # ---- Build block content ----
    def _build_block(block_id):
        """Return a Paragraph/Flowable for the given block, or None."""
        slot = positions.get(block_id, 'hidden')
        if slot == 'hidden':
            return None
        style, align = _style_for_slot(slot)

        if block_id == 'logo':
            if not (c.get('show_logo') and c.get('logo_path') and storage):
                return None
            try:
                result = storage.get(c['logo_path'])
                if result:
                    logo_bytes, _ = result
                    logo_buf = _io.BytesIO(logo_bytes)
                    img = Image(logo_buf, width=120, height=40)
                    if align == 2:
                        img.hAlign = 'RIGHT'
                    elif align == 1:
                        img.hAlign = 'CENTER'
                    else:
                        img.hAlign = 'LEFT'
                    return img
            except Exception:
                pass
            return None

        if block_id == 'title':
            ts = s_title if align == 0 else (s_title_r if align == 2 else s_title_c)
            return Paragraph(f'<b>{L("invoice_title")}</b>', ts)

        if block_id == 'sender_info':
            gap = c.get('meta_gap', 10)
            sender_name = ''
            rows = []
            if settings:
                sender_name = getattr(settings, 'owner_name', '') or getattr(settings, 'business_name', '') or ''
                vat = getattr(settings, 'vat_number', '') or ''
                nie = getattr(settings, 'nie_number', '') or ''
                if nie:
                    rows.append(('NIE:', nie))
                if vat:
                    rows.append(('VAT:', vat))
                for fld in ('address', 'city', 'postal_code', 'country', 'phone', 'email'):
                    v = getattr(settings, fld, '') or ''
                    if v:
                        rows.append(('', v))
            s_lbl = ParagraphStyle('si_l', parent=style, alignment=2, fontName=font_b)
            s_val = ParagraphStyle('si_v', parent=style, alignment=0)
            tbl_rows = [[Paragraph(f'<b>{sender_name}</b>', style), '', '']]
            for lbl, val in rows:
                tbl_rows.append([Paragraph(f'<b>{lbl}</b>', s_lbl) if lbl else Paragraph('', s_lbl),
                                 '',
                                 Paragraph(val, s_val)])
            tbl = Table(tbl_rows, colWidths=[None, gap, None])
            slot = positions.get('sender_info', 'header-left')
            tbl.hAlign = 'RIGHT' if slot.endswith('-right') else ('CENTER' if slot.endswith('-center') else 'LEFT')
            tbl.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (2, 0), (2, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('SPAN', (0, 0), (2, 0)),
            ]))
            return tbl

        if block_id == 'recipient_info':
            gap = c.get('meta_gap', 10)
            cust_name = customer.name if customer else getattr(invoice, 'client_name', '')
            rows = []
            if customer:
                if customer.vat_number:
                    rows.append(('VAT:', customer.vat_number))
                for fld in ('address', 'city', 'postal_code', 'country'):
                    v = getattr(customer, fld, '') or ''
                    if v:
                        rows.append(('', v))
            s_lbl = ParagraphStyle('ri_l', parent=style, alignment=2, fontName=font_b)
            s_val = ParagraphStyle('ri_v', parent=style, alignment=0)
            tbl_rows = [
                [Paragraph(f'<b>{L("bill_to")}:</b>', style), '', ''],
                [Paragraph(f'<b>{cust_name}</b>', style), '', ''],
            ]
            for lbl, val in rows:
                tbl_rows.append([Paragraph(f'<b>{lbl}</b>', s_lbl) if lbl else Paragraph('', s_lbl),
                                 '',
                                 Paragraph(val, s_val)])
            tbl = Table(tbl_rows, colWidths=[None, gap, None])
            slot = positions.get('recipient_info', 'header-right')
            tbl.hAlign = 'RIGHT' if slot.endswith('-right') else ('CENTER' if slot.endswith('-center') else 'LEFT')
            tbl.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (2, 0), (2, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('SPAN', (0, 0), (2, 0)),
                ('SPAN', (0, 1), (2, 1)),
            ]))
            return tbl

        if block_id == 'invoice_meta':
            inv_num = str(invoice.invoice_number)
            issue = invoice.invoice_date.strftime('%d/%m/%Y')
            gap = c.get('meta_gap', 10)
            s_label = ParagraphStyle('meta_lbl', parent=style, alignment=2)
            s_value = ParagraphStyle('meta_val', parent=style, alignment=0)
            meta_rows = [
                [Paragraph(f'<b>{L("invoice_number")}:</b>', s_label),
                 '',
                 Paragraph(inv_num, s_value)],
                [Paragraph(f'<b>{L("issue_date")}:</b>', s_label),
                 '',
                 Paragraph(issue, s_value)],
            ]
            if c.get('show_due_date'):
                due = (invoice.due_date or invoice.invoice_date).strftime('%d/%m/%Y')
                meta_rows.append(
                    [Paragraph(f'<b>{L("due_date")}:</b>', s_label),
                     '',
                     Paragraph(due, s_value)])
            meta_tbl = Table(meta_rows, colWidths=[None, gap, None])
            slot = positions.get('invoice_meta', 'top-right')
            if slot.endswith('-right'):
                meta_tbl.hAlign = 'RIGHT'
            elif slot.endswith('-center'):
                meta_tbl.hAlign = 'CENTER'
            else:
                meta_tbl.hAlign = 'LEFT'
            meta_tbl.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (2, 0), (2, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            return meta_tbl

        if block_id == 'bank_details':
            if not c.get('show_bank_details'):
                return None
            if not invoice.bank:
                return None
            gap = c.get('meta_gap', 10)
            s_lbl = ParagraphStyle('bd_l', parent=style, alignment=2, fontName=font_b)
            s_val = ParagraphStyle('bd_v', parent=style, alignment=0)
            tbl_rows = [[Paragraph(f'<b>{L("bank_details")}</b>', style), '', '']]
            if invoice.bank.bank_name:
                tbl_rows.append([Paragraph('<b>Bank:</b>', s_lbl), '', Paragraph(invoice.bank.bank_name, s_val)])
            if invoice.bank.swift:
                tbl_rows.append([Paragraph('<b>SWIFT:</b>', s_lbl), '', Paragraph(invoice.bank.swift, s_val)])
            tbl_rows.append([Paragraph('<b>IBAN:</b>', s_lbl), '', Paragraph(invoice.bank.iban, s_val)])
            tbl = Table(tbl_rows, colWidths=[None, gap, None])
            slot = positions.get('bank_details', 'bottom-left')
            tbl.hAlign = 'RIGHT' if slot.endswith('-right') else ('CENTER' if slot.endswith('-center') else 'LEFT')
            tbl.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (2, 0), (2, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('SPAN', (0, 0), (2, 0)),
            ]))
            return tbl

        if block_id == 'notes':
            if not c.get('show_notes'):
                return None
            if not invoice.notes or not invoice.notes.strip() or invoice.notes == 'None':
                return None
            gap = c.get('meta_gap', 10)
            s_lbl = ParagraphStyle('nt_l', parent=style, alignment=0, fontName=font_b)
            s_val = ParagraphStyle('nt_v', parent=style, alignment=0)
            notes_html = invoice.notes.replace('\n', '<br/>')
            tbl_rows = [
                [Paragraph(f'<b>{L("notes")}:</b>', s_lbl), '', Paragraph(notes_html, s_val)],
            ]
            tbl = Table(tbl_rows, colWidths=[None, gap, None])
            slot = positions.get('notes', 'bottom-right')
            tbl.hAlign = 'RIGHT' if slot.endswith('-right') else ('CENTER' if slot.endswith('-center') else 'LEFT')
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            return tbl

        if block_id == 'payment_terms':
            if not c.get('show_payment_terms'):
                return None
            pt = ''
            if settings and hasattr(settings, 'default_payment_terms'):
                pt = settings.default_payment_terms or ''
            if not pt:
                pt = 'Bank Transfer'
            gap = c.get('meta_gap', 10)
            s_lbl = ParagraphStyle('pt_l', parent=style, alignment=0, fontName=font_b)
            s_val = ParagraphStyle('pt_v', parent=style, alignment=0)
            tbl_rows = [
                [Paragraph(f'<b>{L("payment_terms")}:</b>', s_lbl), '', Paragraph(pt, s_val)],
            ]
            tbl = Table(tbl_rows, colWidths=[None, gap, None])
            slot = positions.get('payment_terms', 'bottom-right')
            tbl.hAlign = 'RIGHT' if slot.endswith('-right') else ('CENTER' if slot.endswith('-center') else 'LEFT')
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            return tbl

        return None

    # ---- Zone renderer ----
    def _render_zone(zone_name):
        """Collect blocks assigned to zone_name and render as a 3-col table row."""
        left_blocks = []
        center_blocks = []
        right_blocks = []

        for bid in BLOCK_IDS:
            slot = positions.get(bid, 'hidden')
            if slot == 'hidden' or not slot.startswith(zone_name):
                continue
            flowable = _build_block(bid)
            if flowable is None:
                continue
            col = slot.split('-', 1)[1] if '-' in slot else 'left'
            if col == 'left':
                left_blocks.append(flowable)
            elif col == 'center':
                center_blocks.append(flowable)
            else:
                right_blocks.append(flowable)

        if not left_blocks and not center_blocks and not right_blocks:
            return  # nothing in this zone

        # Determine column layout
        has_center = len(center_blocks) > 0
        has_right = len(right_blocks) > 0
        has_left = len(left_blocks) > 0

        # Build cell content — stack multiple blocks with spacers
        def _stack(blocks):
            if not blocks:
                return Paragraph('', s_small)
            if len(blocks) == 1:
                return blocks[0]
            # Use a nested table to stack blocks (KeepTogether breaks in cells)
            rows = []
            for b in blocks:
                rows.append([b])
            inner = Table(rows, colWidths=[None])
            inner.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            return inner

        # Get custom column widths for this zone
        zc = c.get('zone_columns', {}).get(zone_name)

        if has_center:
            # 3-column layout
            if zc and len(zc) == 3:
                col_widths = [float(zc[0]), float(zc[1]), float(zc[2])]
            else:
                w_each = CONTENT_W / 3
                col_widths = [w_each, w_each, w_each]
            data = [[_stack(left_blocks), _stack(center_blocks), _stack(right_blocks)]]
            tbl = Table(data, colWidths=col_widths)
        elif has_left and has_right:
            # 2-column layout
            if zc and len(zc) >= 2:
                col_widths = [float(zc[0]), float(zc[-1])]
            else:
                col_widths = [CONTENT_W / 2, CONTENT_W / 2]
            data = [[_stack(left_blocks), _stack(right_blocks)]]
            tbl = Table(data, colWidths=col_widths)
        elif has_left:
            # Full width left
            data = [[_stack(left_blocks)]]
            tbl = Table(data, colWidths=[CONTENT_W])
        elif has_right:
            # Right only — push to right with empty left
            data = [['', _stack(right_blocks)]]
            tbl = Table(data, colWidths=[CONTENT_W / 2, CONTENT_W / 2])
        else:
            return

        zone_style = [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]

        # Apply header_bg to recipient column in classic layout
        if zone_name == 'header' and layout == 'classic':
            # shade the rightmost column
            col_idx = 2 if has_center else (1 if has_right else 0)
            zone_style.append(('BACKGROUND', (col_idx, 0), (col_idx, 0), hdr_bg))
            zone_style.append(('LEFTPADDING', (col_idx, 0), (col_idx, 0), 10))
            zone_style.append(('TOPPADDING', (col_idx, 0), (col_idx, 0), 8))
            zone_style.append(('BOTTOMPADDING', (col_idx, 0), (col_idx, 0), 8))

        tbl.setStyle(TableStyle(zone_style))
        elements.append(tbl)
        elements.append(Spacer(1, 10))

    def _separator():
        """Add a thin separator line if enabled."""
        if show_sep:
            sep = Table([['']], colWidths=[CONTENT_W])
            sep.setStyle(TableStyle([
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(sep)
            elements.append(Spacer(1, 8))

    # ================================================================
    # BUILD PDF
    # ================================================================

    # --- Accent line ---
    if c.get('show_accent_line') and layout in ('modern', 'classic'):
        line = Table([['']], colWidths=[CONTENT_W])
        line.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), accent),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(line)
        elements.append(Spacer(1, 12))

    # --- TOP zone ---
    _render_zone('top')

    _separator()

    # --- HEADER zone ---
    _render_zone('header')

    _separator()

    elements.append(Spacer(1, 10))

    # --- BODY: Items table ---
    inv_currency = invoice.currency or 'USD'
    sym = get_currency_symbol(inv_currency)
    inv_amount = invoice.amount_eur if inv_currency == 'EUR' else invoice.amount_usd

    hdr_style = ParagraphStyle('ih', fontSize=10, fontName=font_b, textColor=accent)
    cell_style = ParagraphStyle('ic', fontSize=9, fontName=font, textColor=text_c)

    items_data = [[
        Paragraph(f'<b>{L("description")}</b>', hdr_style),
        Paragraph(f'<b>{L("quantity")}</b>', hdr_style),
        Paragraph(f'<b>{L("unit_price")}</b>', hdr_style),
        Paragraph(f'<b>{L("total")}</b>', hdr_style),
    ]]

    if invoice.items and len(invoice.items) > 0:
        for item in invoice.items:
            if inv_currency == 'EUR' and invoice.amount_usd:
                ratio = invoice.amount_eur / invoice.amount_usd
                up = item.unit_price_usd * ratio
                st = item.subtotal_usd * ratio
            else:
                up = item.unit_price_usd
                st = item.subtotal_usd
            items_data.append([
                Paragraph(item.description or '', cell_style),
                Paragraph(str(int(item.quantity)), cell_style),
                Paragraph(f'{sym}{up:,.2f}', cell_style),
                Paragraph(f'{sym}{st:,.2f}', cell_style),
            ])
    else:
        up = invoice.unit_price_usd if hasattr(invoice, 'unit_price_usd') and invoice.unit_price_usd else inv_amount
        if inv_currency == 'EUR' and invoice.amount_usd:
            up = up * (invoice.amount_eur / invoice.amount_usd)
        items_data.append([
            Paragraph(invoice.description or '', cell_style),
            Paragraph(str(int(invoice.quantity) if hasattr(invoice, 'quantity') and invoice.quantity else 1), cell_style),
            Paragraph(f'{sym}{up:,.2f}', cell_style),
            Paragraph(f'{sym}{inv_amount:,.2f}', cell_style),
        ])

    items_tbl = Table(items_data, colWidths=[270, 60, 91, 91])
    items_tbl.setStyle(TableStyle([
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, accent),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(items_tbl)
    elements.append(Spacer(1, 20))

    # --- BODY: Totals ---
    vat_pct = settings.default_vat_rate if settings and hasattr(settings, 'default_vat_rate') and settings.default_vat_rate is not None else 21.0
    tax_rate = 0.0
    tax_label = f'{L("tax")} 0%'
    if customer and hasattr(customer, 'tax_type') and customer.tax_type:
        if customer.tax_type == 'standard':
            tax_rate = vat_pct / 100.0
        tax_label = f'{L("tax")} {vat_pct:g}%'
    tax_amount = inv_amount * tax_rate
    total = inv_amount + tax_amount

    totals_gap = c.get('meta_gap', 10)
    totals_data = [
        [Paragraph(f'<b>{L("subtotal")}</b>', s_right_accent),
         '', Paragraph(f'<b>{sym}{inv_amount:,.2f}</b>', s_right)],
    ]
    if c.get('show_vat_breakdown'):
        totals_data.append(
            [Paragraph(f'<b>{tax_label}</b>', s_right_accent),
             '', Paragraph(f'<b>{sym}{tax_amount:,.2f}</b>', s_right)])
    totals_data.append(
        [Paragraph(f'<b>{L("total")}</b>', s_total_label),
         '', Paragraph(f'<b>{sym}{total:,.2f}</b>', s_total_val)])

    totals_tbl = Table(totals_data, colWidths=[None, totals_gap, None])
    totals_tbl.hAlign = 'RIGHT'
    totals_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), hdr_bg),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEABOVE', (0, -1), (-1, -1), 1, accent),
    ]))
    # Wrap in outer table to push totals to the right
    wrapper = Table([[totals_tbl]], colWidths=[512])
    wrapper.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(wrapper)
    elements.append(Spacer(1, 25))

    _separator()

    # --- BOTTOM zone ---
    _render_zone('bottom')

    _separator()

    # --- FOOTER zone ---
    _render_zone('footer')

    # --- Build ---
    def _page_footer(canvas, doc):
        canvas.saveState()
        # Page background
        if page_bg_hex:
            canvas.setFillColor(colors.HexColor(page_bg_hex))
            canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
        canvas.setFont(font, 8)
        canvas.setFillColor(text_c)
        canvas.drawRightString(PAGE_W - MARGIN, 30, f'Page {canvas.getPageNumber()}')
        canvas.restoreState()

    doc.build(elements, onFirstPage=_page_footer, onLaterPages=_page_footer)
    buf.seek(0)
    return buf.read()
