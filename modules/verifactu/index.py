#!/usr/bin/env python3
"""
Verifactu Module (F1) — tamper-evident billing records per RD 1007/2023.

Every invoice issue or annulment writes one append-only record into a hash
chain (see chain.py). The record is written in the same transaction as the
F2 lifecycle transition: if the record fails, the transition is rolled back
(a compliance veto). The QR payload is rendered on the invoice view. Records
are kept locally first; real-time VERI*FACTU submission is scaffolded but
waits for access to the AEAT test environment (F1-D6) — until an endpoint is
configured, the job only logs and does nothing.

STANDING RULE: record format, QR content and chaining are versioned drafts —
verify against current AEAT technical specs before real use (see chain.py and
README.md). Compliance assist, not legal advice.
"""

import json
from datetime import date

from flask import Blueprint, render_template, Response, url_for

from module_manager import BaseModule

from .aeat import VERIFACTU_PHRASE, qr_url
from .pdf_qr import MAX_SIZE_MM, MIN_SIZE_MM, QR_SIZE_MM, has_qr, qr_flowables, stamp_qr
from .chain import (
    CHAIN_SPEC_VERSION,
    GENESIS_HASH,
    canonical_payload,
    key_id,
    record_hash,
    sign_hash,
    verify_chain,
    verify_signature,
)
from timeutil import utcnow


class VerifactuModule(BaseModule):
    """Hash-chained billing records + QR (Verifactu compliance assist)."""

    @property
    def module_id(self):
        return 'verifactu'

    @property
    def name(self):
        return 'Verifactu'

    @property
    def description(self):
        return ('Tamper-evident, hash-chained billing records for issued/'
                'annulled invoices (RD 1007/2023 compliance assist) with '
                'AEAT-style QR payloads. Draft spec — verify before real use.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Verifactu', 'endpoint': 'verifactu.verifactu_index',
             'icon': '🔏', 'group': 'System'}
        ]

    def register_models(self, db):
        self._db = db

        class VerifactuRecord(db.Model):
            """Append-only. No update/delete path exists anywhere in the app."""
            __tablename__ = 'verifactu_record'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            record_type = db.Column(db.String(20), nullable=False)  # alta | anulacion
            invoice_id = db.Column(db.Integer, nullable=False)
            invoice_number = db.Column(db.String(50))
            payload = db.Column(db.Text, nullable=False)      # canonical JSON
            prev_hash = db.Column(db.String(64), nullable=False)
            record_hash = db.Column(db.String(64), nullable=False, unique=True)
            signature = db.Column(db.String(64), nullable=False)
            # Fingerprint of the signing key (chain.key_id). NULL = signed with
            # the key derived from SECRET_KEY, before VERIFACTU_KEY existed.
            key_id = db.Column(db.String(16))
            spec_version = db.Column(db.String(20), default=CHAIN_SPEC_VERSION)
            created_at = db.Column(db.DateTime, default=utcnow)
            submitted_at = db.Column(db.DateTime)  # VERI*FACTU mode only

        self.VerifactuRecord = VerifactuRecord
        return {'VerifactuRecord': VerifactuRecord}

    def on_enable(self):
        self._migrate_signing_key()
        # VERI*FACTU submission job — no-op until an AEAT endpoint is
        # configured (F1-D6 is gated on test-environment access).
        try:
            self.core.scheduler.add_job(
                job_id='verifactu.submit',
                func=self._submit_pending,
                job_type='interval',
                interval=900,
                description='VERI*FACTU record submission (gated: needs AEAT endpoint)',
                timeout=120,
            )
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register submission job: %s', e)

    # --- Record building ---------------------------------------------------- #

    def _secret(self):
        """The signing key: VERIFACTU_KEY, else instance/verifactu.key."""
        return self.core.deployment_key('VERIFACTU_KEY', 'VERIFACTU_KEY_FILE',
                                        'Verifactu signing')

    def _legacy_secret(self):
        """Key of records signed before VERIFACTU_KEY existed."""
        return self.core.app.config.get('SECRET_KEY', '')

    def _key_for(self, record):
        """Key that signed `record`, or None when it is not available."""
        rec_key_id = getattr(record, 'key_id', None)
        if rec_key_id is None:
            return self._legacy_secret()
        current = self._secret()
        return current if key_id(current) == rec_key_id else None

    def _verify_all(self):
        records = self.VerifactuRecord.query.order_by(self.VerifactuRecord.id).all()
        return records, verify_chain(records, self._key_for)

    def _migrate_signing_key(self):
        """Add `key_id` and move records signed with SECRET_KEY to VERIFACTU_KEY.

        Idempotent. Only the signature and key_id change; the record hash and
        chain are untouched. A record whose signature no longer verifies with
        the current SECRET_KEY is left as it is, so a real mismatch stays
        visible on the status page.
        """
        from sqlalchemy import inspect as sa_inspect, text
        try:
            cols = [c['name'] for c in sa_inspect(self._db.engine).get_columns('verifactu_record')]
            if 'key_id' not in cols:
                with self._db.engine.connect() as conn:
                    conn.execute(text('ALTER TABLE verifactu_record ADD COLUMN key_id VARCHAR(16)'))
                    conn.commit()
            legacy = self.VerifactuRecord.query.filter(
                self.VerifactuRecord.key_id.is_(None)).all()
            if not legacy:
                return
            old_key, new_key = self._legacy_secret(), self._secret()
            moved = 0
            for rec in legacy:
                if verify_signature(rec.record_hash, rec.signature, old_key):
                    rec.signature = sign_hash(rec.record_hash, new_key)
                    rec.key_id = key_id(new_key)
                    moved += 1
            self._db.session.commit()
            if moved:
                self.logger.info('Re-signed %d Verifactu record(s) with VERIFACTU_KEY', moved)
        except Exception as e:  # pragma: no cover - defensive
            self._db.session.rollback()
            self.logger.error('Verifactu signing-key migration failed: %s', e)

    def _last_hash(self):
        last = self.VerifactuRecord.query.order_by(
            self.VerifactuRecord.id.desc()).first()
        return last.record_hash if last else GENESIS_HASH

    def _build_payload(self, invoice, record_type):
        """Fiscal payload per F1-D1 field mapping (draft version)."""
        settings = self.core.get_settings()
        snap = {}
        if getattr(invoice, 'snap_customer', None):
            try:
                snap = json.loads(invoice.snap_customer)
            except (ValueError, TypeError):
                snap = {}
        return {
            'spec_version': CHAIN_SPEC_VERSION,
            'record_type': record_type,
            'issuer_nif': (settings.vat_number or settings.nie_number or ''
                           ) if settings else '',
            'invoice_number': invoice.invoice_number,
            'invoice_date': invoice.invoice_date.isoformat()
                            if invoice.invoice_date else None,
            'customer': {'name': snap.get('name') or invoice.client_name,
                         'vat_number': snap.get('vat_number'),
                         'tax_type': snap.get('tax_type')},
            'taxable_base': getattr(invoice, 'snap_taxable_base', None),
            'vat_rate': getattr(invoice, 'snap_vat_rate', None),
            'vat_amount': getattr(invoice, 'snap_vat_amount', None),
            'total_eur': invoice.amount_eur,
            'rectifies': getattr(invoice, 'rectifies_invoice_id', None),
            'rectification_type': getattr(invoice, 'rectification_type', None),
            'timestamp': utcnow().isoformat(),
        }

    def _append_record(self, invoice, record_type):
        """Build, hash, sign and add one record to the current DB session.

        Runs inside the F2 lifecycle transaction (hooks fire before commit):
        raising here aborts the whole issue/annul. Never swallow errors.
        """
        payload = self._build_payload(invoice, record_type)
        prev = self._last_hash()
        rec_hash = record_hash(prev, payload)
        secret = self._secret()
        record = self.VerifactuRecord(
            record_type=record_type,
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            payload=canonical_payload(payload),
            prev_hash=prev,
            record_hash=rec_hash,
            signature=sign_hash(rec_hash, secret),
            key_id=key_id(secret),
        )
        self._db.session.add(record)
        self._db.session.flush()
        self.core.log_activity(
            'verifactu_record', 'invoice',
            f'{record_type} record #{record.id} for invoice '
            f'#{invoice.invoice_number}')
        return record

    # --- F2 lifecycle hooks (issue-blocking) --------------------------------- #

    def on_invoice_issued(self, invoice, request):
        # Rectificative issues carry the linkage inside the alta payload.
        self._append_record(invoice, 'alta')

    def on_invoice_annulled(self, invoice, request):
        self._append_record(invoice, 'anulacion')

    # --- QR (F1-D4, draft payload) ------------------------------------------- #

    def _qr_mode(self):
        """'no_verifactu' until records are actually submitted to AEAT; the
        VERI*FACTU QR and phrase are only allowed for a submitting system."""
        import os
        mode = (self.core.app.config.get('VERIFACTU_MODE')
                or os.environ.get('VERIFACTU_MODE', 'no_verifactu'))
        return mode if mode in ('verifactu', 'no_verifactu') else 'no_verifactu'

    def _qr_environment(self):
        import os
        env = (self.core.app.config.get('VERIFACTU_QR_ENV')
               or os.environ.get('VERIFACTU_QR_ENV', 'production'))
        return env if env in ('production', 'test') else 'production'

    @staticmethod
    def _issuer_nif(settings):
        nif = ((settings.vat_number or settings.nie_number or '') if settings else '')
        nif = nif.replace(' ', '').upper()
        if len(nif) == 11 and nif.startswith('ES'):
            nif = nif[2:]          # the QR takes the NIF, not the EU VAT number
        return nif

    @staticmethod
    def _importe_total(invoice):
        """Invoice total (base + IVA) in EUR. In this app the stored amount is
        the taxable base; the IVA comes from the snapshot frozen at issue."""
        base = getattr(invoice, 'snap_taxable_base', None)
        if base is None:
            base = invoice.amount_eur or 0.0
        return round(base + (getattr(invoice, 'snap_vat_amount', None) or 0.0), 2)

    def qr_payload(self, invoice):
        """Cotejo URL for the invoice QR, per the AEAT QR specification."""
        return qr_url(self._issuer_nif(self.core.get_settings()), invoice.invoice_number,
                      invoice.invoice_date, self._importe_total(invoice),
                      mode=self._qr_mode(), environment=self._qr_environment())

    def _has_record(self, invoice):
        return self.VerifactuRecord.query.filter_by(
            invoice_id=invoice.id, record_type='alta').first() is not None

    def qr_for_invoice(self, invoice):
        """QR content for an invoice that has a Verifactu record:
        {'url': ..., 'phrase': 'VERI*FACTU' or None}. None for drafts.
        Used by this module's PDF stamping and by the invoice designer."""
        if not invoice or not getattr(invoice, 'id', None) or not self._has_record(invoice):
            return None
        return {'url': self.qr_payload(invoice),
                'phrase': VERIFACTU_PHRASE if self._qr_mode() == 'verifactu' else None}

    def get_invoice_blocks(self):
        """The tax QR as an invoice-designer block, placed as the AEAT QR
        specification (v0.5.0, section 3) allows: top of page 1, centred
        (preferred) or towards the left, 30-40 mm."""
        return [{
            'id': 'verifactu_qr',
            'label': 'Verifactu QR',
            'help': ('AEAT rules: top of page 1, centred or towards the left, '
                     '30–40 mm, with “QR tributario:” above it. Shown on issued '
                     'invoices only; the preview uses a sample QR.'),
            'required': True,
            'slots': ['top-center', 'top-left'],
            'default_slot': 'top-center',
            'options': [{
                'id': 'size_mm', 'label': 'Size',
                'choices': [[s, f'{s} × {s} mm'] for s in range(MIN_SIZE_MM, MAX_SIZE_MM + 1, 5)],
                'default': QR_SIZE_MM,
            }],
            'render': self._render_qr_block,
        }]

    def _render_qr_block(self, invoice, ctx):
        phrase = VERIFACTU_PHRASE if self._qr_mode() == 'verifactu' else None
        if ctx.get('preview'):
            qr = {'url': qr_url('00000000T', 'DEMO-0001', date.today(), 121.00,
                                environment='test'), 'phrase': phrase}
        else:
            qr = self.qr_for_invoice(invoice)
        if not qr:
            return []   # drafts have no Verifactu record, hence no QR
        return qr_flowables(qr['url'], qr['phrase'], ctx.get('options', {}).get('size_mm'),
                            align='left' if ctx.get('align') == 'left' else 'center',
                            font_bold=ctx.get('font_bold') or 'Helvetica-Bold')

    def filter_invoice_pdf(self, invoice, pdf_bytes):
        """Core hook: put the QR on page 1 of every PDF of an issued invoice,
        unless the layout already placed it (the invoice designer block)."""
        qr = self.qr_for_invoice(invoice)
        if qr is None or has_qr(pdf_bytes):
            return pdf_bytes
        return stamp_qr(pdf_bytes, qr['url'], qr['phrase'])

    def _qr_svg(self, invoice):
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderSVG
        widget = QrCodeWidget(self.qr_payload(invoice), barLevel='M')
        b = widget.getBounds()
        size = 160
        drawing = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0,
                                                 size / (b[3] - b[1]), 0, 0])
        drawing.add(widget)
        return renderSVG.drawToString(drawing)

    def get_invoice_view_panels(self, invoice):
        if invoice.status not in ('issued', 'paid', 'cancelled'):
            return []  # drafts carry no QR and no records
        records = self.VerifactuRecord.query.filter_by(
            invoice_id=invoice.id).order_by(self.VerifactuRecord.id).all()
        return [render_template('verifactu_panel.html', invoice=invoice,
                                records=records,
                                qr_url=url_for('verifactu.verifactu_qr',
                                               invoice_id=invoice.id))]

    # --- Routes ----------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('verifactu', __name__,
                       template_folder='templates',
                       url_prefix='/verifactu')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def verifactu_index():
            records, (ok, bad_idx, reason) = module._verify_all()
            return render_template('verifactu_status.html',
                                   records=records[-20:],
                                   record_count=len(records),
                                   chain_ok=ok, bad_index=bad_idx,
                                   bad_reason=reason,
                                   spec_version=CHAIN_SPEC_VERSION)

        @bp.route('/export')
        @login_required
        def verifactu_export():
            """F1-D5 — full record log export (JSON lines, spec draft)."""
            records = module.VerifactuRecord.query.order_by(
                module.VerifactuRecord.id).all()
            lines = [json.dumps({
                'id': r.id, 'record_type': r.record_type,
                'invoice_number': r.invoice_number, 'payload': r.payload,
                'prev_hash': r.prev_hash, 'record_hash': r.record_hash,
                'signature': r.signature, 'key_id': r.key_id,
                'spec_version': r.spec_version,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }, ensure_ascii=False) for r in records]
            return Response('\n'.join(lines), mimetype='application/x-ndjson',
                            headers={'Content-Disposition':
                                     'attachment; filename=verifactu_records.ndjson'})

        @bp.route('/qr/<int:invoice_id>.svg')
        @login_required
        def verifactu_qr(invoice_id):
            invoice = module.core.invoice_service.get(invoice_id)
            if not invoice:
                return Response('not found', status=404)
            return Response(module._qr_svg(invoice), mimetype='image/svg+xml')

        app.register_blueprint(bp)

    # --- VERI*FACTU submission scaffold (F1-D6, gated) ---------------------------- #

    def _submit_pending(self):
        """Submit unsent records to AEAT — GATED.

        Real submission needs the AEAT test-environment endpoint + credentials
        from the F1-D1 spike. Until configured, this logs and exits so the
        scheduler job is harmless.
        """
        endpoint = self.core.app.config.get('VERIFACTU_ENDPOINT')
        if not endpoint:
            self.logger.info('VERI*FACTU submission not available (no endpoint); '
                             'the app works as a non-VERI*FACTU system')
            return 0
        # Not implemented: needs AEAT test-environment access (SPEC_COMPARISON.md).
        self.logger.warning('VERIFACTU_ENDPOINT is set but submission is not '
                            'available yet; see modules/verifactu/SPEC_COMPARISON.md')
        return 0
