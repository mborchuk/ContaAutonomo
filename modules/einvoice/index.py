#!/usr/bin/env python3
"""
E-Invoice Module (F9, Phase 1) — Facturae export for issued invoices.

CAVEMAN NOTE: Phase 1 only — generate Facturae 3.2.2 XML (UNSIGNED, see
facturae.py for the XAdES gap) with a data-readiness check. Exchange with
platforms / status messages (Phase 2) is gated on the ministerial order for
RD 238/2026 — design notes in README.md, no code.

Readiness ride the structured party data that already exist on Customer and
Settings (address/city/postal_code/country) — the missing-data checklist tell
the user exactly what to fill before an invoice is e-invoice ready.
"""

from flask import Blueprint, Response, flash, redirect, request, url_for

from module_manager import BaseModule

from . import xades
from .facturae import check_readiness, generate_facturae


class EInvoiceModule(BaseModule):
    """Facturae (EN 16931) structured e-invoicing — Phase 1: export."""

    @property
    def module_id(self):
        return 'einvoice'

    @property
    def name(self):
        return 'E-Invoice (Facturae)'

    @property
    def description(self):
        return ('Export issued invoices as Facturae 3.2.2 XML (XAdES-signed with '
                'the PDF Signature certificate when one is configured) with '
                'an e-invoice readiness check. Phase 1 of RD 238/2026 support.')

    @property
    def version(self):
        return '0.1.0'

    def register_models(self, db):
        self._db = db
        return {}

    # --- Readiness ---------------------------------------------------------- #

    def _readiness(self, invoice):
        from app import Customer
        seller = self.core.get_settings()
        customer = Customer.query.get(invoice.customer_id) \
            if invoice.customer_id else None
        return check_readiness(invoice, seller, customer), seller, customer

    # --- Signing (D-4) --------------------------------------------------------- #

    def _certificate(self):
        """(pfx_bytes, password) of the PDF Signature module's certificate, or
        None when signing is not available (module off, no certificate, no
        XAdES library)."""
        if not xades.available():
            return None
        mm = getattr(self.core, 'module_manager', None)
        pdf_sig = mm.provider_of('pdf_signature') if mm else None
        if pdf_sig is None or not hasattr(pdf_sig, '_get_config'):
            return None
        cfg = pdf_sig._get_config()
        if not cfg.certificate_key:
            return None
        stored = self.core.storage.get(cfg.certificate_key)
        if not stored:
            return None
        pfx_bytes, _name = stored
        return pfx_bytes, cfg.certificate_password or ''

    def _export(self, invoice, seller, customer, unsigned=False):
        """(xml_bytes, signed) — signed with XAdES-EPES when a certificate is
        configured, unless `unsigned` is requested."""
        xml = generate_facturae(invoice, seller, customer)
        cert = None if unsigned else self._certificate()
        if cert is None:
            return xml, False
        return xades.sign_facturae(xml, *cert), True

    # --- Invoice view integration -------------------------------------------- #

    def get_invoice_actions(self, invoice):
        if invoice.status not in ('issued', 'paid'):
            return []
        url = url_for('einvoice.einvoice_download', invoice_id=invoice.id)
        return [f'<a href="{url}" class="btn btn-secondary">Facturae XML</a>']

    def get_invoice_view_panels(self, invoice):
        if invoice.status not in ('issued', 'paid'):
            return []
        problems, _seller, _customer = self._readiness(invoice)
        if not problems:
            return []   # ready: the action button is enough
        items = ''.join(f'<li>{p}</li>' for p in problems)
        return [f'''
        <div class="inline-panel">
            <h3>📨 E-invoice readiness: no</h3>
            <p class="muted">Missing data for a Facturae export:</p>
            <ul class="fine-print">{items}</ul>
        </div>''']

    # --- Routes ------------------------------------------------------------------ #

    def register_routes(self, app):
        bp = Blueprint('einvoice', __name__, url_prefix='/einvoice')
        login_required = self.core.login_required
        module = self

        @bp.route('/facturae/<int:invoice_id>.xml')
        @login_required
        def einvoice_download(invoice_id):
            invoice = module.core.invoice_service.get(invoice_id)
            if not invoice:
                return Response('not found', status=404)
            problems, seller, customer = module._readiness(invoice)
            if problems:
                flash('Not e-invoice ready: ' + '; '.join(problems), 'danger')
                return redirect(url_for('view_invoice', id=invoice_id))
            unsigned = request.args.get('unsigned') == '1'
            try:
                xml, signed = module._export(invoice, seller, customer, unsigned=unsigned)
            except ValueError as e:   # unreadable certificate or wrong password
                module.logger.error('Facturae signing failed for invoice %s: %s',
                                    invoice.id, e)
                flash('Facturae could not be signed — check the certificate and its '
                      'password in Settings → PDF Signature.', 'danger')
                return redirect(url_for('view_invoice', id=invoice_id))
            safe = (invoice.invoice_number or str(invoice.id)).replace('/', '-')
            ext = 'xsig' if signed else 'xml'
            return Response(
                xml, mimetype='application/xml',
                headers={'Content-Disposition':
                         f'attachment; filename=facturae_{safe}.{ext}'})

        app.register_blueprint(bp)

    # --- REST API ------------------------------------------------------------------- #

    def get_api_routes(self):
        return [
            {'path': 'facturae/<int:invoice_id>', 'methods': ['GET'],
             'handler': self._api_facturae,
             'summary': 'Facturae XML for an issued invoice (XAdES-signed when a '
                        'certificate is configured; ?unsigned=1 for the unsigned file)'},
        ]

    def _api_facturae(self, request, invoice_id=None):
        from modules.api.index import ApiError
        invoice = self.core.invoice_service.get(invoice_id)
        if not invoice:
            raise ApiError(404, 'not_found', f'Invoice #{invoice_id} not found')
        problems, seller, customer = self._readiness(invoice)
        if problems:
            raise ApiError(409, 'not_ready',
                           'Not e-invoice ready: ' + '; '.join(problems))
        try:
            xml, signed = self._export(invoice, seller, customer,
                                       unsigned=request.args.get('unsigned') == '1')
        except ValueError as e:
            self.logger.error('Facturae signing failed for invoice %s: %s', invoice.id, e)
            raise ApiError(409, 'signing_failed',
                           'Facturae could not be signed: check the PDF Signature certificate')
        return {'invoice_id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'facturae_xml': xml.decode('utf-8'),
                'signed': signed}, 200
