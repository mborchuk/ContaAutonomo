#!/usr/bin/env python3
"""
Invoice Attachments Module
Allows uploading a ready-made invoice PDF instead of generating one.
Adds an "Attach Invoice" button on the invoice view page.
Uses core.invoice_service for safe, controlled access.
"""

from module_manager import BaseModule
from flask import Blueprint, request, redirect, url_for, flash, render_template
import os


class InvoiceAttachmentsModule(BaseModule):

    @property
    def module_id(self):
        return 'invoice_attachments'

    @property
    def name(self):
        return 'Invoice Attachments'

    @property
    def description(self):
        return 'Upload ready-made invoice PDFs instead of generating them'

    @property
    def version(self):
        return '0.1.0'

    def register_models(self, db):
        self._db = db
        return {}

    def register_routes(self, app):
        bp = Blueprint('invoice_attachments', __name__,
                       template_folder='templates',
                       url_prefix='/invoice-attachments')
        login_required = self.core.login_required
        module = self

        @bp.route('/attach/<int:invoice_id>', methods=['POST'])
        @login_required
        def attach_pdf(invoice_id):
            """Upload and attach a PDF to an existing invoice."""
            module.logger.info('Attach PDF request for invoice ID=%d', invoice_id)
            svc = module.core.invoice_service
            invoice = svc.get(invoice_id)
            if not invoice:
                module.logger.warning('Invoice ID=%d not found', invoice_id)
                module.core.log_activity(
                    'invoice_pdf_attach_failed', 'invoice',
                    f'Invoice #{invoice_id} not found (attach via view page)')
                flash('Invoice not found', 'danger')
                return redirect(url_for('index'))

            file = request.files.get('invoice_pdf')
            if not file or not file.filename:
                module.logger.info('No file selected for invoice #%s', invoice.invoice_number)
                flash('Please select a PDF file to attach', 'danger')
                return redirect(url_for('view_invoice', id=invoice_id))

            if not file.filename.lower().endswith('.pdf'):
                module.logger.warning('Non-PDF file rejected: %s for invoice #%s',
                                      file.filename, invoice.invoice_number)
                module.core.log_activity(
                    'invoice_pdf_attach_rejected', 'invoice',
                    f'Invoice #{invoice.invoice_number} | '
                    f'rejected non-PDF file: {file.filename}')
                flash('Only PDF files are allowed', 'danger')
                return redirect(url_for('view_invoice', id=invoice_id))

            try:
                path = svc.attach_pdf(invoice_id, file, file.filename)
                module.logger.info('PDF attached via view page: invoice #%s -> %s',
                                   invoice.invoice_number, path)
                flash('Invoice PDF attached successfully!', 'success')
            except ValueError as e:
                module.logger.error('Attach failed for invoice #%s: %s',
                                    invoice.invoice_number, e)
                module.core.log_activity(
                    'invoice_pdf_attach_failed', 'invoice',
                    f'Invoice #{invoice.invoice_number} | error: {e}')
                flash(str(e), 'danger')

            return redirect(url_for('view_invoice', id=invoice_id))

        app.register_blueprint(bp)

    def get_invoice_actions(self, invoice):
        """Provide extra actions for the invoice view page."""
        if self.core.invoice_service.is_locked(invoice):
            return []

        has_pdf = self.core.invoice_service.has_pdf(invoice)

        from flask import render_template
        html = render_template(
            'attach_button.html',
            invoice=invoice,
            has_pdf=has_pdf,
            is_locked=False,
        )
        return [html]

    def get_create_form_html(self):
        """Inject PDF upload field into the invoice create form."""
        return '''
    <div class="form-group">
        <label for="invoice_pdf">📎 Attach Invoice PDF (optional)</label>
        <input type="file" id="invoice_pdf" name="invoice_pdf" accept=".pdf"
               style="padding: 8px; border: 1px dashed #ccc; border-radius: 5px; background: #fafafa;">
        <small style="display: block; margin-top: 5px; color: #666;">
            Upload a ready-made invoice PDF instead of generating one later.
        </small>
    </div>
        '''

    def get_edit_form_html(self, invoice):
        """Inject PDF upload field into the invoice edit form."""
        has_pdf = self.core.invoice_service.has_pdf(invoice)
        label = '📎 Replace Invoice PDF' if has_pdf else '📎 Attach Invoice PDF (optional)'
        hint = 'Upload a signed or updated invoice PDF.' if has_pdf else 'Upload a ready-made invoice PDF instead of generating one later.'
        return f'''
    <div class="form-group">
        <label for="invoice_pdf">{label}</label>
        <input type="file" id="invoice_pdf" name="invoice_pdf" accept=".pdf"
               style="padding: 8px; border: 1px dashed #ccc; border-radius: 5px; background: #fafafa;">
        <small style="display: block; margin-top: 5px; color: #666;">
            {hint}
        </small>
    </div>
        '''

    def on_invoice_created(self, invoice, request):
        """Attach uploaded PDF after invoice creation."""
        self.logger.info('on_invoice_created hook for invoice #%s', invoice.invoice_number)
        self._handle_pdf_upload(invoice, request, source='create')

    def on_invoice_updated(self, invoice, request):
        """Attach/replace uploaded PDF after invoice update."""
        self.logger.info('on_invoice_updated hook for invoice #%s', invoice.invoice_number)
        self._handle_pdf_upload(invoice, request, source='edit')

    def _handle_pdf_upload(self, invoice, request, source='form'):
        """Common handler for PDF upload from create/edit forms."""
        file = request.files.get('invoice_pdf')
        if not file or not file.filename:
            self.logger.debug('No PDF in request for invoice #%s (source=%s)',
                              invoice.invoice_number, source)
            return

        self.logger.info('Processing PDF upload: file=%s, invoice=#%s, source=%s',
                         file.filename, invoice.invoice_number, source)

        if not file.filename.lower().endswith('.pdf'):
            self.logger.warning('Rejected non-PDF file: %s for invoice #%s',
                                file.filename, invoice.invoice_number)
            self.core.log_activity(
                'invoice_pdf_attach_rejected', 'invoice',
                f'Invoice #{invoice.invoice_number} | '
                f'rejected non-PDF file from {source} form: {file.filename}')
            from flask import flash
            flash('Attached file is not a PDF — skipped', 'warning')
            return

        try:
            path = self.core.invoice_service.attach_pdf(invoice, file, file.filename)
            self.logger.info('PDF attached successfully: invoice #%s -> %s (source=%s)',
                             invoice.invoice_number, path, source)
            from flask import flash
            flash('Invoice PDF attached!', 'success')
        except Exception as e:
            self.logger.error('PDF attachment failed: invoice #%s, file=%s, source=%s, error=%s',
                              invoice.invoice_number, file.filename, source, e)
            self.core.log_activity(
                'invoice_pdf_attach_failed', 'invoice',
                f'Invoice #{invoice.invoice_number} | '
                f'file={file.filename} | source={source} | error: {e}')
            from flask import flash
            flash(f'PDF attachment failed: {e}', 'warning')
