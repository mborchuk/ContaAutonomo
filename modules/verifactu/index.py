#!/usr/bin/env python3
"""
Verifactu Module (F1) — tamper-evident billing records per RD 1007/2023.

CAVEMAN NOTE: every invoice ISSUE / ANNUL write one append-only record into a
hash chain (see chain.py). Record write ride the same transaction as the F2
lifecycle transition — record fail => transition roll back (compliance veto).
QR payload render on the invoice view. Local conservation first; VERI*FACTU
real-time submission is scaffolded but gated on AEAT test-environment access
(F1-D6) — job no-ops with a log until an endpoint is configured.

STANDING RULE: record format, QR content and chaining are versioned drafts —
verify against current AEAT technical specs before real use (see chain.py and
README.md). Compliance assist, not legal advice.
"""

import json
from datetime import datetime

from flask import Blueprint, render_template, request, Response, url_for

from module_manager import BaseModule

from .chain import (
    CHAIN_SPEC_VERSION,
    GENESIS_HASH,
    canonical_payload,
    record_hash,
    sign_hash,
    verify_chain,
)


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
            spec_version = db.Column(db.String(20), default=CHAIN_SPEC_VERSION)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            submitted_at = db.Column(db.DateTime)  # VERI*FACTU mode only

        self.VerifactuRecord = VerifactuRecord
        return {'VerifactuRecord': VerifactuRecord}

    def on_enable(self):
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
        return self.core.app.config.get('SECRET_KEY', '')

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
            'timestamp': datetime.utcnow().isoformat(),
        }

    def _append_record(self, invoice, record_type):
        """Build, hash, sign and add one record to the current DB session.

        Runs inside the F2 lifecycle transaction (hooks fire before commit):
        raising here aborts the whole issue/annul. Never swallow errors.
        """
        payload = self._build_payload(invoice, record_type)
        prev = self._last_hash()
        rec_hash = record_hash(prev, payload)
        record = self.VerifactuRecord(
            record_type=record_type,
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            payload=canonical_payload(payload),
            prev_hash=prev,
            record_hash=rec_hash,
            signature=sign_hash(rec_hash, self._secret()),
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

    def qr_payload(self, invoice):
        """AEAT-style QR content (draft: URL with NIF, serie, fecha, importe).

        Final URL + parameter spec comes from the AEAT annexes (F1-D1).
        """
        settings = self.core.get_settings()
        nif = (settings.vat_number or settings.nie_number or '') if settings else ''
        fecha = invoice.invoice_date.strftime('%d-%m-%Y') if invoice.invoice_date else ''
        importe = f'{invoice.amount_eur or 0:.2f}'
        return ('https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR'
                f'?nif={nif}&numserie={invoice.invoice_number}'
                f'&fecha={fecha}&importe={importe}')

    def _qr_svg(self, invoice):
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderSVG
        widget = QrCodeWidget(self.qr_payload(invoice))
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
            records = module.VerifactuRecord.query.order_by(
                module.VerifactuRecord.id).all()
            ok, bad_idx, reason = verify_chain(records, module._secret())
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
                'signature': r.signature, 'spec_version': r.spec_version,
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
            self.logger.info('VERI*FACTU submission skipped: no endpoint configured')
            return 0
        # Submission implementation lands with AEAT test access (F1-D6).
        self.logger.warning('VERIFACTU_ENDPOINT set but submission not yet '
                            'implemented (gated on AEAT test environment)')
        return 0
