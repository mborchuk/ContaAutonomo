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

import io
import json
import zipfile
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, Response, url_for

from contaautonomo.core.module_manager import BaseModule

from . import events as ev
from .aeat import VERIFACTU_PHRASE, qr_url
from .event_log import EventLogMixin, SYSTEM_DEFAULTS
from .obligations import CHECKED_ON, OBLIGATIONS_VERSION, obligations
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
from contaautonomo.utils.timeutil import utcnow


class VerifactuModule(EventLogMixin, BaseModule):
    """Hash-chained billing records + QR (Verifactu compliance assist), and
    the event log of a NO VERI*FACTU system (event_log.py)."""

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
        return '0.2.0'

    @property
    def settings_tab(self):
        return {'id': 'verifactu', 'label': 'Verifactu'}

    @property
    def nav_items(self):
        return [
            {'label': 'Verifactu', 'endpoint': 'verifactu.verifactu_index',
             'icon': '🔏', 'group': 'Invoices'}
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

        class VerifactuEvent(db.Model):
            """Event record (registro de evento, Orden HAC/1177/2024 art. 9).
            Append-only, chained like the billing records. `prev_huella` is
            unique ('' for the first event), so the chain cannot fork."""
            __tablename__ = 'verifactu_event'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            event_type = db.Column(db.String(2), nullable=False)        # L2E
            generated_at = db.Column(db.String(32), nullable=False)     # as hashed, with offset
            prev_event_type = db.Column(db.String(2))
            prev_generated_at = db.Column(db.String(32))
            prev_huella = db.Column(db.String(64), nullable=False, unique=True)
            huella = db.Column(db.String(64), nullable=False, unique=True)
            signature = db.Column(db.String(64), nullable=False)
            key_id = db.Column(db.String(16))
            issuer_nif = db.Column(db.String(20))
            issuer_name = db.Column(db.String(120))
            system_json = db.Column(db.Text, nullable=False)  # SistemaInformatico, as hashed
            data_json = db.Column(db.Text)                     # DatosPropiosEvento / OtrosDatos
            created_at = db.Column(db.DateTime, default=utcnow)  # UTC, for periods

        class VerifactuConfig(db.Model):
            __tablename__ = 'verifactu_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        self.VerifactuRecord = VerifactuRecord
        self.VerifactuEvent = VerifactuEvent
        self.VerifactuConfig = VerifactuConfig
        return {'VerifactuRecord': VerifactuRecord, 'VerifactuEvent': VerifactuEvent,
                'VerifactuConfig': VerifactuConfig}

    def on_enable(self):
        self._migrate_signing_key()
        self.core.add_activity_listener(self.on_activity)
        try:
            self.record_start_or_end()
        except Exception:  # pragma: no cover - defensive: never block start-up
            self._db.session.rollback()
            self.logger.exception('Verifactu start-of-operation event failed')
        self.core.scheduler.add_job(
            job_id='verifactu.event_summary', func=self.summary_job, job_type='interval',
            interval=1800, description='Verifactu event summary (every 6 hours of operation)',
            timeout=300)
        self.core.scheduler.add_job(
            job_id='verifactu.anomaly_check', func=self.run_checks, job_type='daily',
            time_str='03:30', description='Verifactu anomaly detection (billing and event records)',
            timeout=900)
        if not self.core.app.config.get('TESTING'):
            import atexit
            atexit.register(self.summary_on_exit)
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
            self.logger.exception('Failed to register submission job: %s', e)

    def on_disable(self):
        self.core.remove_activity_listener(self.on_activity)
        for job in ('verifactu.event_summary', 'verifactu.anomaly_check', 'verifactu.submit'):
            self.core.scheduler.remove_job(job)
        try:
            self.record_summary()
            self.record_end('Verifactu module disabled')
        except Exception:  # pragma: no cover - defensive
            self._db.session.rollback()
            self.logger.exception('Verifactu end-of-operation event failed')

    # --- Settings → Verifactu: the system's identity in the event records ----- #

    def get_settings_html(self, settings):
        return self.render_settings_template(
            'verifactu_settings_panel.html', config=self._config(), system=self.system_info(),
            defaults=SYSTEM_DEFAULTS, mode=self._qr_mode())

    def save_settings(self, settings, form):
        if 'verifactu_submitted' not in form:
            return  # another tab was saved
        config = self._config()
        limits = {'producer_name': 120, 'producer_nif': 9, 'system_name': 30, 'system_id': 2,
                  'installation_number': 100}
        for field, limit in limits.items():
            value = (form.get(f'verifactu_{field}') or '').strip()
            if field == 'producer_nif':
                value = value.replace(' ', '').upper()
            if field == 'installation_number' and not value:
                continue   # never blank: it identifies this installation
            config[field] = value[:limit]
        self._save_config(config)

    # --- Record building ---------------------------------------------------- #

    def _secret(self):
        """The signing key: VERIFACTU_KEY, else instance/verifactu.key."""
        # Separate from SECRET_KEY, so rotating the session key never breaks
        # the record chain.
        return self.core.deployment_key('VERIFACTU_KEY', 'Verifactu signing', 'verifactu.key')

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
            self.logger.exception('Verifactu signing-key migration failed: %s', e)

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
        self.log('verifactu_record_added',
                 f'{record_type} record #{record.id} for invoice '
                 f'#{invoice.invoice_number}',
                 category='verifactu')
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
            records, billing, events, event_problems = module.detect_on_view()
            first = records[0].created_at.date() if records and records[0].created_at else None
            return render_template('verifactu_status.html',
                                   records=records[-20:],
                                   record_count=len(records),
                                   chain_ok=not billing, billing_anomalies=billing,
                                   events=events[-20:][::-1], event_count=len(events),
                                   event_anomalies=event_problems,
                                   events_active=module.events_active(),
                                   event_types=ev.EVENT_TYPES,
                                   anomaly_types=ev.ANOMALY_TYPES,
                                   today=date.today(), first_day=first or date.today(),
                                   spec_version=CHAIN_SPEC_VERSION,
                                   obligations=obligations(),
                                   obligations_version=OBLIGATIONS_VERSION,
                                   obligations_checked_on=CHECKED_ON)

        @bp.route('/check', methods=['POST'])
        @login_required
        def verifactu_check():
            """Run the anomaly detection now (events 03-06)."""
            billing, found = module.run_checks()
            if billing or found:
                flash(f'Anomalies found: {len(billing)} in the billing records, '
                      f'{len(found)} in the event records. They are in the event log.', 'danger')
            else:
                flash('Both chains checked: no anomaly found.', 'success')
            return redirect(url_for('verifactu.verifactu_index'))

        def _period_args():
            """(date_from, date_to) from the query string; the whole log by
            default. None when a date is not valid."""
            try:
                start = date.fromisoformat(request.args.get('date_from') or '2000-01-01')
                end = date.fromisoformat(request.args.get('date_to')
                                         or date.today().isoformat())
            except ValueError:
                return None
            return (start, end) if start <= end else None

        @bp.route('/events/export')
        @login_required
        def verifactu_events_export():
            """The event records of a period, one XML file per event (anexo
            apartado 5), in a ZIP. Records event 09."""
            period = _period_args()
            if period is None:
                flash('Choose a valid period.', 'danger')
                return redirect(url_for('verifactu.verifactu_index'))
            start, end, *_ = module._period(*period)
            rows = module.events_between(start, end)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for row in rows:
                    data = json.loads(row.data_json or '{}')
                    archive.writestr(f'evento_{row.id:06d}_{row.event_type}.xml',
                                     ev.event_xml(row, data))
            module.record_event_export(*period, rows)
            name = f'verifactu_events_{period[0]:%Y%m%d}_{period[1]:%Y%m%d}.zip'
            return Response(buffer.getvalue(), mimetype='application/zip',
                            headers={'Content-Disposition': f'attachment; filename={name}'})

        @bp.route('/export')
        @login_required
        def verifactu_export():
            """The billing records of a period (all by default), JSON lines.
            Records event 08."""
            period = _period_args()
            if period is None:
                flash('Choose a valid period.', 'danger')
                return redirect(url_for('verifactu.verifactu_index'))
            start, end, *_ = module._period(*period)
            records = module.billing_records_between(start, end)
            lines = [json.dumps({
                'id': r.id, 'record_type': r.record_type,
                'invoice_number': r.invoice_number, 'payload': r.payload,
                'prev_hash': r.prev_hash, 'record_hash': r.record_hash,
                'signature': r.signature, 'key_id': r.key_id,
                'spec_version': r.spec_version,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }, ensure_ascii=False) for r in records]
            module.record_billing_export(*period, records)
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
