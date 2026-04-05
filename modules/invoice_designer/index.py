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
    'invoice_meta', 'due_date', 'bank_details', 'notes', 'payment_terms',
]

# Valid slot positions
SLOT_CHOICES = [
    'top-left', 'top-center', 'top-right',
    'header-left', 'header-center', 'header-right',
    'subtotal-left', 'subtotal-right',
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
    'layout': 'modern',
    'show_vat_breakdown': True,
    'show_accent_line': True,
    'show_separator_lines': False,
    'show_vertical_lines': False,
    'block_gaps': {},                # per-block gap override: {block_id: pt}
    'block_label_widths': {},        # per-block label column width override: {block_id: pt}
    'block_styles': {},              # per-block: {block_id: {color: '#hex', bg: '#hex', show_label: true}}
    'labels': {
        'invoice_title': 'Invoice',
        'sender_info': 'From',
        'description': 'Description',
        'quantity': 'Qty',
        'unit_price': 'Unit Price',
        'item_total': 'Total',
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
        'due_date':       'subtotal-left',
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
        except Exception:  # noqa: DB query may fail if table missing
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
                if tpl:
                    return redirect(url_for('invoice_designer.designer_edit', id=tpl.id))
                return redirect(url_for('invoice_designer.designer_create'))

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
                    'layout': request.form.get('layout', 'modern'),
                    'show_logo': 'show_logo' in request.form,
                    'show_vat_breakdown': 'show_vat_breakdown' in request.form,
                    'show_accent_line': 'show_accent_line' in request.form,
                    'show_separator_lines': 'show_separator_lines' in request.form,
                    'show_vertical_lines': 'show_vertical_lines' in request.form,
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
                for zone in ('top', 'header', 'subtotal', 'bottom', 'footer'):
                    vals = []
                    for i in range(3):
                        raw = request.form.get(f'zone_col_{zone}_{i}', '').strip()
                        vals.append(float(raw) if raw else None)
                    # Only save if at least 2 non-None values
                    non_none = [v for v in vals if v is not None]
                    if len(non_none) >= 2:
                        config['zone_columns'][zone] = vals

                # Parse per-block gaps
                config['block_gaps'] = {}
                for bid in BLOCK_IDS + ['totals']:
                    raw = request.form.get(f'block_gap_{bid}', '').strip()
                    if raw != '':
                        try:
                            config['block_gaps'][bid] = int(raw)
                        except ValueError:
                            raw_sanitized = raw.replace('\r', '').replace('\n', '')
                            logger.debug(
                                "Ignoring invalid integer for block_gap_%s: %r",
                                bid,
                                raw_sanitized,
                            )

                # Parse per-block label widths
                config['block_label_widths'] = {}
                for bid in BLOCK_IDS + ['totals']:
                    raw = request.form.get(f'block_lw_{bid}', '').strip()
                    if raw != '':
                        try:
                            config['block_label_widths'][bid] = int(raw)
                        except ValueError:
                            raw_sanitized = raw.replace('\r', '').replace('\n', '')
                            logger.debug(
                                "Ignoring invalid integer for block_lw_%s: %r",
                                bid,
                                raw_sanitized,
                            )

                # Parse per-block styles (color, bg, show_label)
                config['block_styles'] = {}
                for bid in BLOCK_IDS + ['totals']:
                    bs = {}
                    clr = request.form.get(f'bs_color_{bid}', '').strip()
                    if clr:
                        bs['color'] = clr
                    bg = request.form.get(f'bs_bg_{bid}', '').strip()
                    if bg:
                        bs['bg'] = bg
                    bs['show_label'] = f'bs_label_{bid}' in request.form
                    if bs.get('color') or bs.get('bg') or not bs['show_label']:
                        config['block_styles'][bid] = bs

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
            flash(f'Error saving template.', 'danger')
            if tpl:
                return redirect(url_for('invoice_designer.designer_edit', id=tpl.id))
            return redirect(url_for('invoice_designer.designer_create'))

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
        except Exception:  # noqa: DB query may fail if table missing
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
        except Exception:  # noqa: DB query may fail if table missing
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
        elif k == 'block_gaps':
            merged['block_gaps'] = dict(DEFAULT_CONFIG.get('block_gaps', {}))
            merged['block_gaps'].update(v)
        elif k == 'block_label_widths':
            merged['block_label_widths'] = dict(DEFAULT_CONFIG.get('block_label_widths', {}))
            merged['block_label_widths'].update(v)
        elif k == 'block_styles':
            merged['block_styles'] = dict(DEFAULT_CONFIG.get('block_styles', {}))
            merged['block_styles'].update(v)
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
    show_vert = c.get('show_vertical_lines', False)

    positions = c.get('block_positions', DEFAULT_CONFIG['block_positions'])
    offsets = c.get('block_offsets', {})
    _block_label_widths = c.get('block_label_widths', {})

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            rightMargin=MARGIN, leftMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)
    elements = []

    # ---- Styles ----
    s_title = ParagraphStyle('dt', fontSize=title_fs, fontName=font_b,
                             textColor=accent, spaceAfter=4)
    s_small = ParagraphStyle('dsm', fontSize=9, fontName=font,
                             textColor=text_c, leading=12)

    def _make_block_flowables(block_id, title_text, kv_pairs, value_align='left'):
        """Build flowables for a key:value block.
        Reads per-block styles: font color, background, show_label.
        """
        lw = _block_label_widths.get(block_id, 0)
        bs = c.get('block_styles', {}).get(block_id, {})
        blk_color = colors.HexColor(bs['color']) if bs.get('color') else text_c
        show_label = bs.get('show_label', True)

        result = []

        if title_text and show_label:
            s_t = ParagraphStyle('kv_title', parent=s_small, fontName=font_b,
                                 leftIndent=lw, textColor=blk_color)
            result.append(Paragraph(f'<b>{title_text}</b>', s_t))

        if kv_pairs:
            if value_align == 'right':
                s_lbl = ParagraphStyle('kv_l2', parent=s_small, alignment=0,
                                       fontName=font_b, textColor=blk_color)
                s_val = ParagraphStyle('kv_v2', parent=s_small, alignment=2,
                                       textColor=blk_color)
                rows = []
                for k, v in kv_pairs:
                    rows.append([
                        Paragraph(f'<b>{k}</b>', s_lbl) if k else Paragraph('', s_lbl),
                        Paragraph(v, s_val)
                    ])
                t = Table(rows, colWidths=[None, None], spaceBefore=0, spaceAfter=0)
                t.hAlign = 'LEFT'
                tbl_style = [
                    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                    ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (0, -1), lw),
                    ('LEFTPADDING', (1, 0), (1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 1),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ]
                t.setStyle(TableStyle(tbl_style))
                result.append(t)
            else:
                for k, v in kv_pairs:
                    if k:
                        line = f'<b>{k}</b>&nbsp;&nbsp;{v}'
                    else:
                        line = v
                    s_kv = ParagraphStyle('kv_line', parent=s_small, leftIndent=lw,
                                          textColor=blk_color)
                    result.append(Paragraph(line, s_kv))

        return result

    # ---- Pre-calculate invoice amounts for totals block ----
    inv_currency = invoice.currency or 'USD'
    sym = get_currency_symbol(inv_currency)
    inv_amount = invoice.amount_eur if inv_currency == 'EUR' else invoice.amount_usd
    vat_pct = settings.default_vat_rate if settings and hasattr(settings, 'default_vat_rate') and settings.default_vat_rate is not None else 21.0
    _tax_rate = 0.0
    _tax_label = f'{L("tax")} 0%'
    if customer and hasattr(customer, 'tax_type') and customer.tax_type:
        if customer.tax_type == 'standard':
            _tax_rate = vat_pct / 100.0
        _tax_label = f'{L("tax")} {vat_pct:g}%'
    _tax_amount = inv_amount * _tax_rate
    _total = inv_amount + _tax_amount

    # ---- Build block content ----
    def _build_block(block_id):
        """Return a list of flowables for the given block, or empty list."""
        slot = positions.get(block_id, 'hidden')
        if slot == 'hidden':
            return []

        if block_id == 'logo':
            if not (c.get('show_logo') and c.get('logo_path') and storage):
                return []
            try:
                result = storage.get(c['logo_path'])
                if result:
                    logo_bytes, _ = result
                    logo_buf = _io.BytesIO(logo_bytes)
                    img = Image(logo_buf, width=120, height=40)
                    img.hAlign = 'LEFT'
                    return [img]
            except Exception:  # logo file may be missing or corrupted
                pass
            return []

        if block_id == 'title':
            lw = _block_label_widths.get('title', 0)
            ts = ParagraphStyle('dt_zone', parent=s_title, alignment=0, leftIndent=lw)
            return [Paragraph(f'<b>{L("invoice_title")}</b>', ts)]

        if block_id == 'sender_info':
            kv = []
            if settings:
                owner = getattr(settings, 'owner_name', '') or ''
                business = getattr(settings, 'business_name', '') or ''
                # Show business name or owner name as first line (always)
                display_name = business if business else owner
                if display_name:
                    kv.append(('', f'<b>{display_name}</b>'))
                # If both exist and different, show owner on second line
                if business and owner and business != owner:
                    kv.append(('', f'<b>{owner}</b>'))
                nie = getattr(settings, 'nie_number', '') or ''
                vat = getattr(settings, 'vat_number', '') or ''
                id_parts = []
                if nie:
                    id_parts.append(f'NIE: {nie}')
                if vat:
                    id_parts.append(f'VAT: {vat}')
                if id_parts:
                    kv.append(('', '<b>' + ', '.join(id_parts) + '</b>'))
                addr = getattr(settings, 'address', '') or ''
                if addr:
                    kv.append(('', addr))
                postal = getattr(settings, 'postal_code', '') or ''
                city = getattr(settings, 'city', '') or ''
                country = getattr(settings, 'country', '') or ''
                loc_parts = [p for p in [f'{postal} {city}'.strip(), country] if p]
                if loc_parts:
                    kv.append(('', ', '.join(loc_parts)))
                phone = getattr(settings, 'phone', '') or ''
                if phone:
                    kv.append(('', phone))
                email = getattr(settings, 'email', '') or ''
                if email:
                    kv.append(('', email))
            bs_si = c.get('block_styles', {}).get('sender_info', {})
            si_title = L("sender_info") if bs_si.get('show_label', True) else None
            return _make_block_flowables('sender_info', si_title, kv)

        if block_id == 'recipient_info':
            cust_name = customer.name if customer else getattr(invoice, 'client_name', '')
            kv = [('', f'<b>{cust_name}</b>')]
            if customer:
                if customer.vat_number:
                    kv.append(('', f'<b>VAT: {customer.vat_number}</b>'))
                addr = getattr(customer, 'address', '') or ''
                if addr:
                    kv.append(('', addr))
                # postal_code + city on one line
                postal = getattr(customer, 'postal_code', '') or ''
                city = getattr(customer, 'city', '') or ''
                city_line = f'{postal} {city}'.strip()
                if city_line:
                    kv.append(('', city_line))
                country = getattr(customer, 'country', '') or ''
                if country:
                    kv.append(('', country))
            return _make_block_flowables('recipient_info', L("bill_to"), kv)

        if block_id == 'invoice_meta':
            inv_num = str(invoice.invoice_number)
            issue = invoice.invoice_date.strftime('%d/%m/%Y')
            kv = [
                (f'{L("invoice_number")}:', inv_num),
                (f'{L("issue_date")}:', issue),
            ]
            return _make_block_flowables('invoice_meta', None, kv, value_align='right')

        if block_id == 'due_date':
            due = (invoice.due_date or invoice.invoice_date).strftime('%d/%m/%Y')
            return _make_block_flowables('due_date', L("due_date"), [('', due)])

        if block_id == 'bank_details':
            if not invoice.bank:
                return []
            kv = []
            kv.append(('IBAN:', invoice.bank.iban))
            if invoice.bank.swift:
                kv.append(('SWIFT/BIC:', invoice.bank.swift))
            if invoice.bank.bank_name:
                kv.append(('Bank name:', invoice.bank.bank_name))
            return _make_block_flowables('bank_details', L("bank_details"), kv, value_align='right')

        if block_id == 'notes':
            if not invoice.notes or not invoice.notes.strip() or invoice.notes == 'None':
                return []
            return _make_block_flowables('notes', L("notes"),
                                         [('', invoice.notes.replace('\n', '<br/>'))])

        if block_id == 'payment_terms':
            pt = ''
            if settings and hasattr(settings, 'default_payment_terms'):
                pt = settings.default_payment_terms or ''
            if not pt:
                pt = 'Bank Transfer'
            return _make_block_flowables('payment_terms', L("payment_terms"),
                                         [('', pt)])

        return []

    # ---- Zone renderer ----
    def _render_zone(zone_name):
        """Render a zone as a single Table. Returns True if anything was rendered."""
        col_bids = {0: [], 1: [], 2: []}
        for bid in BLOCK_IDS:
            slot = positions.get(bid, 'hidden')
            if slot == 'hidden' or not slot.startswith(zone_name):
                continue
            col = slot.split('-', 1)[1] if '-' in slot else 'left'
            idx = {'left': 0, 'center': 1, 'right': 2}.get(col, 0)
            col_bids[idx].append(bid)

        if not any(col_bids[i] for i in range(3)):
            return False

        # Determine columns from zone_columns or auto-detect
        zc_raw = c.get('zone_columns', {}).get(zone_name, [None, None, None])
        if not zc_raw or len(zc_raw) < 3:
            zc_raw = list(zc_raw or []) + [None] * (3 - len(zc_raw or []))

        active = [(i, float(zc_raw[i])) for i in range(3) if zc_raw[i] is not None]

        if not active:
            has_l, has_c, has_r = bool(col_bids[0]), bool(col_bids[1]), bool(col_bids[2])
            if has_c:
                active = [(0, CONTENT_W/3), (1, CONTENT_W/3), (2, CONTENT_W/3)]
            elif has_l and has_r:
                active = [(0, CONTENT_W/2), (2, CONTENT_W/2)]
            elif has_l:
                active = [(0, CONTENT_W)]
            elif has_r:
                active = [(2, CONTENT_W)]
            else:
                return False

        # Build flowables per column
        col_widths = []
        cell_contents = []
        col_bg_colors = []  # per-column background color
        for idx, w in active:
            col_widths.append(w)
            flowables = []
            col_bg = None
            for bid in col_bids[idx]:
                bid_offset = offsets.get(bid, {})
                oy = bid_offset.get('y', 0)
                if oy:
                    flowables.append(Spacer(1, oy))
                block_flows = _build_block(bid)
                flowables.extend(block_flows)
                # Collect bg color from block_styles (ignore white = no bg)
                bs = c.get('block_styles', {}).get(bid, {})
                bg_val = bs.get('bg', '')
                if bg_val and bg_val.lower() not in ('', '#ffffff', '#fff') and not col_bg:
                    col_bg = bg_val
            if not flowables:
                flowables = [Paragraph('', s_small)]
            cell_contents.append(flowables)
            col_bg_colors.append(col_bg)

        # Build zone table
        data = [cell_contents]
        tbl = Table(data, colWidths=col_widths)

        zone_style = [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]

        # Apply per-column background colors
        for ci, bg in enumerate(col_bg_colors):
            if bg:
                zone_style.append(('BACKGROUND', (ci, 0), (ci, -1), colors.HexColor(bg)))
                zone_style.append(('LEFTPADDING', (ci, 0), (ci, -1), 8))
                zone_style.append(('RIGHTPADDING', (ci, 0), (ci, -1), 8))
                zone_style.append(('TOPPADDING', (ci, 0), (ci, -1), 8))
                zone_style.append(('BOTTOMPADDING', (ci, 0), (ci, -1), 8))

        # Vertical lines between columns — skip if either adjacent column has bg
        if show_vert and len(col_widths) > 1:
            for ci in range(len(col_widths) - 1):
                left_has_bg = col_bg_colors[ci] if ci < len(col_bg_colors) else None
                right_has_bg = col_bg_colors[ci + 1] if ci + 1 < len(col_bg_colors) else None
                if not left_has_bg and not right_has_bg:
                    zone_style.append(('LINEAFTER', (ci, 0), (ci, -1), 0.5, colors.HexColor('#CCCCCC')))

        if zone_name == 'header' and layout == 'classic':
            ci = len(col_widths) - 1
            zone_style += [
                ('BACKGROUND', (ci, 0), (ci, 0), hdr_bg),
                ('LEFTPADDING', (ci, 0), (ci, 0), 10),
                ('TOPPADDING', (ci, 0), (ci, 0), 8),
                ('BOTTOMPADDING', (ci, 0), (ci, 0), 8),
            ]

        tbl.setStyle(TableStyle(zone_style))
        elements.append(tbl)
        elements.append(Spacer(1, 10))
        return True

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
    if c.get('show_accent_line'):
        line = Table([['']], colWidths=[CONTENT_W])
        line.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), accent),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(line)
        elements.append(Spacer(1, 12))

    # --- TOP zone ---
    _has_top = _render_zone('top')

    if _has_top:
        _separator()

    # --- HEADER zone ---
    _has_header = _render_zone('header')

    if _has_header:
        _separator()

    elements.append(Spacer(1, 10))

    # --- BODY: Items table ---

    hdr_style = ParagraphStyle('ih', fontSize=10, fontName=font_b, textColor=accent)
    cell_style = ParagraphStyle('ic', fontSize=9, fontName=font, textColor=text_c)

    items_data = [[
        Paragraph(f'<b>{L("description")}</b>', hdr_style),
        Paragraph(f'<b>{L("quantity")}</b>', hdr_style),
        Paragraph(f'<b>{L("unit_price")}</b>', hdr_style),
        Paragraph(f'<b>{L("item_total")}</b>', hdr_style),
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

    # --- SUBTOTAL zone ---
    # Totals always present; optional block can be placed in subtotal-left or subtotal-right
    totals_bs = c.get('block_styles', {}).get('totals', {})
    totals_color = colors.HexColor(totals_bs['color']) if totals_bs.get('color') else text_c
    totals_bg_hex = totals_bs.get('bg', '')
    totals_bg = colors.HexColor(totals_bg_hex) if totals_bg_hex and totals_bg_hex.lower() not in ('#ffffff', '#fff', '') else None

    s_totals_lbl = ParagraphStyle('stl', parent=s_small, fontName=font_b, alignment=0, textColor=totals_color)
    s_totals_val = ParagraphStyle('stv', parent=s_small, fontName=font_b, alignment=2, textColor=totals_color)
    s_totals_total_lbl = ParagraphStyle('sttl', fontSize=13, fontName=font_b, textColor=accent, alignment=0)
    s_totals_total_val = ParagraphStyle('sttv', fontSize=13, fontName=font_b, textColor=totals_color, alignment=2)

    totals_rows = [
        [Paragraph(f'<b>{L("subtotal")}</b>', s_totals_lbl),
         Paragraph(f'<b>{sym}{inv_amount:,.2f}</b>', s_totals_val)],
    ]
    if c.get('show_vat_breakdown'):
        totals_rows.append(
            [Paragraph(f'<b>{_tax_label}</b>', s_totals_lbl),
             Paragraph(f'<b>{sym}{_tax_amount:,.2f}</b>', s_totals_val)])
    totals_rows.append(
        [Paragraph(f'<b>{L("total")}</b>', s_totals_total_lbl),
         Paragraph(f'<b>{sym}{_total:,.2f}</b>', s_totals_total_val)])

    pad = 8 if totals_bg else 0
    last_row = len(totals_rows) - 1
    totals_tbl_style = [
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), pad),
        ('RIGHTPADDING', (0, 0), (-1, -1), pad),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEABOVE', (0, last_row), (-1, last_row), 0.5, colors.HexColor('#CCCCCC')),
    ]
    if totals_bg:
        totals_tbl_style.append(('BACKGROUND', (0, 0), (-1, -1), totals_bg))

    totals_tbl = Table(totals_rows, colWidths=[None, None])
    totals_tbl.setStyle(TableStyle(totals_tbl_style))

    # Check if any block is placed in subtotal zone
    sub_left_bids = []
    sub_right_bids = []
    for bid in BLOCK_IDS:
        slot = positions.get(bid, 'hidden')
        if slot == 'subtotal-left':
            sub_left_bids.append(bid)
        elif slot == 'subtotal-right':
            sub_right_bids.append(bid)

    # Get subtotal zone column widths
    sub_zc = c.get('zone_columns', {}).get('subtotal', [None, None, None])
    if not sub_zc or len(sub_zc) < 3:
        sub_zc = list(sub_zc or []) + [None] * (3 - len(sub_zc or []))

    if sub_left_bids:
        # Block on left, totals on right
        left_flowables = []
        for bid in sub_left_bids:
            left_flowables.extend(_build_block(bid))
        if not left_flowables:
            left_flowables = [Paragraph('', s_small)]
        left_w = float(sub_zc[0]) if sub_zc[0] is not None else CONTENT_W / 2
        right_w = float(sub_zc[2]) if sub_zc[2] is not None else CONTENT_W - left_w
        zone_tbl = Table([[left_flowables, totals_tbl]], colWidths=[left_w, right_w])
        zone_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(zone_tbl)
    elif sub_right_bids:
        # Totals on left, block on right
        right_flowables = []
        for bid in sub_right_bids:
            right_flowables.extend(_build_block(bid))
        if not right_flowables:
            right_flowables = [Paragraph('', s_small)]
        left_w = float(sub_zc[0]) if sub_zc[0] is not None else CONTENT_W / 2
        right_w = float(sub_zc[2]) if sub_zc[2] is not None else CONTENT_W - left_w
        zone_tbl = Table([[totals_tbl, right_flowables]], colWidths=[left_w, right_w])
        zone_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(zone_tbl)
    else:
        # Totals only, full width
        elements.append(totals_tbl)

    elements.append(Spacer(1, 25))

    _separator()

    # --- BOTTOM zone ---
    _has_bottom = _render_zone('bottom')

    if _has_bottom:
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
