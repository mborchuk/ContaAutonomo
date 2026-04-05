#!/usr/bin/env python3
"""
PDF Signature Module
Adds optional visual and digital (X.509) signing of invoice PDFs.
Integrates via BaseModule hooks without modifying core models.
"""

import io
from datetime import datetime
import importlib.util

from module_manager import BaseModule
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for

# Graceful degradation: try importing optional dependencies
try:
    PILLOW_AVAILABLE = importlib.util.find_spec("PIL") is not None
except (ImportError, AttributeError):
    PILLOW_AVAILABLE = False

try:
    PYHANKO_AVAILABLE = importlib.util.find_spec("pyhanko") is not None
except (ImportError, AttributeError):
    PYHANKO_AVAILABLE = False



class PDFSignatureModule(BaseModule):

    @property
    def module_id(self):
        return 'pdf_signature'

    @property
    def name(self):
        return 'PDF Signature'

    @property
    def description(self):
        return 'Visual and digital (X.509) signing of invoice PDFs'

    @property
    def version(self):
        return '0.1.0'

    @property
    def settings_tab(self):
        return {'id': 'pdf_signature', 'label': 'PDF Signature'}

    def register_models(self, db):
        self._db = db

        class PDFSignatureConfig(db.Model):
            __tablename__ = 'pdf_signature_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            visual_enabled = db.Column(db.Boolean, default=False)
            digital_enabled = db.Column(db.Boolean, default=False)
            signature_image_key = db.Column(db.String(500), default='')
            certificate_key = db.Column(db.String(500), default='')
            certificate_password = db.Column(db.String(500), default='')
            sig_position = db.Column(db.String(20), default='bottom-left')
            sig_margin_x = db.Column(db.Integer, default=40)
            sig_margin_y = db.Column(db.Integer, default=40)
            sig_max_width = db.Column(db.Integer, default=150)

        self.PDFSignatureConfig = PDFSignatureConfig

        class PDFSignatureInvoice(db.Model):
            __tablename__ = 'pdf_signature_invoice'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), unique=True)
            is_signed = db.Column(db.Boolean, default=False)
            has_visual = db.Column(db.Boolean, default=False)
            has_digital = db.Column(db.Boolean, default=False)
            signed_at = db.Column(db.DateTime, nullable=True)

        self.PDFSignatureInvoice = PDFSignatureInvoice

        # Migrate existing tables to add new columns if needed
        self._migrate_config_table()

        return {
            'pdf_signature_config': PDFSignatureConfig,
            'pdf_signature_invoice': PDFSignatureInvoice,
        }

    def _migrate_config_table(self):
        """Add missing columns to existing pdf_signature_config table."""
        new_columns = [
            ('sig_position', "VARCHAR(20) DEFAULT 'bottom-left'"),
            ('sig_margin_x', 'INTEGER DEFAULT 40'),
            ('sig_margin_y', 'INTEGER DEFAULT 40'),
            ('sig_max_width', 'INTEGER DEFAULT 150'),
        ]
        for col_name, col_def in new_columns:
            try:
                self._db.session.execute(
                    self._db.text(
                        f'ALTER TABLE pdf_signature_config ADD COLUMN {col_name} {col_def}'
                    )
                )
                self._db.session.commit()
                self.logger.info('PDF Signature: migrated — added column %s', col_name)
            except Exception:
                self._db.session.rollback()

    # --- Config helper ---

    def _get_config(self):
        """Load config singleton from DB, create default if missing."""
        cfg = self.PDFSignatureConfig.query.first()
        if not cfg:
            cfg = self.PDFSignatureConfig()
            self._db.session.add(cfg)
            self._db.session.commit()
            self.logger.info('PDF Signature: created default config record')
        self.logger.debug(
            'PDF Signature config: visual_enabled=%s, digital_enabled=%s, '
            'image_key=%r, cert_key=%r',
            cfg.visual_enabled, cfg.digital_enabled,
            cfg.signature_image_key, cfg.certificate_key,
        )
        return cfg

    def _get_invoice_sig(self, invoice_id):
        """Load or create PDFSignatureInvoice record for an invoice."""
        sig = self.PDFSignatureInvoice.query.filter_by(invoice_id=invoice_id).first()
        if not sig:
            sig = self.PDFSignatureInvoice(invoice_id=invoice_id)
            self._db.session.add(sig)
            self._db.session.commit()
            self.logger.info('PDF Signature: created sig record for invoice_id=%d', invoice_id)
        self.logger.debug(
            'PDF Signature sig: invoice_id=%d, is_signed=%s, has_visual=%s, has_digital=%s',
            invoice_id, sig.is_signed, sig.has_visual, sig.has_digital,
        )
        return sig

    # --- PDF signing methods ---

    def _apply_visual_signature(self, pdf_bytes):
        if not PILLOW_AVAILABLE:
            raise RuntimeError('Pillow library is not installed — cannot apply visual signature.')

        import os
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas as rl_canvas
        from PIL import Image

        cfg = self._get_config()
        self.logger.info(
            'PDF Signature: applying visual signature, image_key=%r, type=%s',
            cfg.signature_image_key, type(cfg.signature_image_key).__name__,
        )
        if not cfg.signature_image_key:
            raise RuntimeError('Signature image key is empty in config.')
        result = self.core.storage.get(cfg.signature_image_key)
        if not result:
            raise RuntimeError(
                f'Signature image not found in storage (key={cfg.signature_image_key!r}).'
            )
        img_bytes, _ = result
        self.logger.info('PDF Signature: loaded signature image, %d bytes, type=%s',
                         len(img_bytes), type(img_bytes).__name__)

        # Open image to get dimensions
        img = Image.open(io.BytesIO(img_bytes))
        img_w, img_h = img.size
        self.logger.info('PDF Signature: image dimensions: %dx%d, mode=%s', img_w, img_h, img.mode)

        # Save image to pdf_signature_files via core.storage for reportlab (needs file path)
        import os
        tmp_key = 'pdf_signature_files/_tmp_overlay.png'
        try:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            img_buf = io.BytesIO()
            img.save(img_buf, format='PNG')
            self.core.storage.save(img_buf.getvalue(), tmp_key)
            tmp_path = os.path.join(self.core.app_path, tmp_key)
            self.logger.info('PDF Signature: saved temp image to %s', tmp_path)

            # Read the source PDF
            reader = PdfReader(io.BytesIO(pdf_bytes))
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)

            # Build overlay for the last page
            last_page = writer.pages[-1]
            page_w = float(last_page.mediabox.width)
            page_h = float(last_page.mediabox.height)

            # Scale signature (keep aspect ratio)
            max_w = cfg.sig_max_width or 150
            scale = min(max_w / img_w, 1.0)
            draw_w = img_w * scale
            draw_h = img_h * scale

            # Position based on config
            margin_x = cfg.sig_margin_x if cfg.sig_margin_x is not None else 40
            margin_y = cfg.sig_margin_y if cfg.sig_margin_y is not None else 40
            position = cfg.sig_position or 'bottom-left'

            if position == 'bottom-right':
                x = page_w - draw_w - margin_x
                y = margin_y
            elif position == 'bottom-center':
                x = (page_w - draw_w) / 2
                y = margin_y
            elif position == 'top-left':
                x = margin_x
                y = page_h - draw_h - margin_y
            elif position == 'top-right':
                x = page_w - draw_w - margin_x
                y = page_h - draw_h - margin_y
            elif position == 'top-center':
                x = (page_w - draw_w) / 2
                y = page_h - draw_h - margin_y
            else:  # bottom-left (default)
                x = margin_x
                y = margin_y

            # Create overlay PDF via reportlab using temp file path
            overlay_buf = io.BytesIO()
            c = rl_canvas.Canvas(overlay_buf, pagesize=(page_w, page_h))
            c.drawImage(tmp_path, x, y, width=draw_w, height=draw_h, mask='auto')
            c.save()

            # Merge overlay onto last page
            overlay_buf.seek(0)
            overlay_reader = PdfReader(overlay_buf)
            last_page.merge_page(overlay_reader.pages[0])

            # Write result
            out = io.BytesIO()
            writer.write(out)
            result_bytes = out.getvalue()
            self.logger.info(
                'PDF Signature: visual signature applied, page=%dx%d, sig=%dx%d at (%.0f,%.0f), result=%d bytes',
                int(page_w), int(page_h), int(draw_w), int(draw_h), x, y, len(result_bytes),
            )
            return result_bytes
        finally:
            try:
                self.core.storage.delete(tmp_key)
            except Exception:
                pass


    def _apply_digital_signature(self, pdf_bytes):
        """Cryptographically sign the PDF using a PFX certificate via pyHanko."""
        if not PYHANKO_AVAILABLE:
            raise RuntimeError('pyHanko library is not installed — cannot apply digital signature.')

        from pyhanko.sign import signers
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

        cfg = self._get_config()
        self.logger.info(
            'PDF Signature: applying digital signature, cert_key=%r',
            cfg.certificate_key,
        )
        result = self.core.storage.get(cfg.certificate_key)
        if not result:
            raise RuntimeError(
                f'PFX certificate not found in storage (key={cfg.certificate_key!r}).'
            )
        pfx_data, _ = result
        self.logger.info('PDF Signature: loaded PFX certificate, %d bytes', len(pfx_data))

        password = (cfg.certificate_password or '').encode('utf-8')

        # Save PFX to pdf_signature_files via core.storage — pyHanko 0.34+ expects a file path
        import os
        tmp_key = 'pdf_signature_files/_tmp_certificate.p12'
        try:
            self.core.storage.save(pfx_data, tmp_key)
            tmp_path = os.path.join(self.core.app_path, tmp_key)
            self.logger.info('PDF Signature: saved PFX to %s', tmp_path)

            signer = signers.SimpleSigner.load_pkcs12(
                pfx_file=tmp_path,
                passphrase=password,
            )

            pdf_in = io.BytesIO(pdf_bytes)
            w = IncrementalPdfFileWriter(pdf_in)
            out = signers.sign_pdf(
                w,
                signers.PdfSignatureMetadata(field_name='Signature'),
                signer=signer,
            )
            signed_bytes = out.getvalue()
            self.logger.info('PDF Signature: digital signature applied, result=%d bytes', len(signed_bytes))
            return signed_bytes
        finally:
            try:
                self.core.storage.delete(tmp_key)
            except Exception:
                pass

    def _sign_invoice_pdf(self, invoice, visual=False, digital=False):
        """Orchestrate PDF signing: fetch PDF, apply requested signatures, save back.

        Args:
            invoice: Invoice model instance
            visual: whether to apply visual signature
            digital: whether to apply digital signature

        On failure the original PDF is left unchanged; a warning is flashed
        and the error is logged.
        """
        if not visual and not digital:
            self.logger.debug('PDF Signature: _sign_invoice_pdf called with no flags, skipping')
            return

        self.logger.info(
            'PDF Signature: starting signing for invoice #%s (visual=%s, digital=%s)',
            invoice.invoice_number, visual, digital,
        )

        try:
            result = self.core.invoice_service.get_pdf(invoice)
            if not result:
                self.logger.warning(
                    'PDF Signature: no PDF found for invoice #%s — skipping signing.',
                    invoice.invoice_number,
                )
                return

            pdf_bytes, _filename = result
            self.logger.info(
                'PDF Signature: loaded PDF for invoice #%s, %d bytes',
                invoice.invoice_number, len(pdf_bytes),
            )

            # Apply visual first, then digital (order matters for digital integrity)
            if visual:
                pdf_bytes = self._apply_visual_signature(pdf_bytes)

            if digital:
                pdf_bytes = self._apply_digital_signature(pdf_bytes)

            # Save the signed PDF back
            self.core.invoice_service.attach_pdf(invoice, pdf_bytes)

            # Update per-invoice signature record
            sig = self._get_invoice_sig(invoice.id)
            sig.is_signed = True
            sig.has_visual = visual
            sig.has_digital = digital
            sig.signed_at = datetime.utcnow()
            self._db.session.commit()

            self.logger.info(
                'PDF Signature: invoice #%s signed (visual=%s, digital=%s)',
                invoice.invoice_number, visual, digital,
            )

        except Exception as exc:
            import traceback
            self.logger.error(
                'PDF Signature: failed to sign invoice #%s — %s\n%s',
                invoice.invoice_number, exc, traceback.format_exc(),
            )
            try:
                flash('PDF signing failed. The original PDF was left unchanged.', 'warning')
            except RuntimeError:
                # Outside request context — flash not available
                pass

    # --- Invoice view page actions ---

    def get_invoice_actions(self, invoice):
        """Return HTML showing signature status badge or sign button on the view page."""
        sig = self.PDFSignatureInvoice.query.filter_by(invoice_id=invoice.id).first()

        if sig and sig.is_signed:
            # Already signed — show green badge with details
            html = render_template(
                'sign_button.html',
                invoice=invoice,
                is_signed=True,
                can_sign=False,
                has_visual=sig.has_visual,
                has_digital=sig.has_digital,
            )
            return [html]

        if sig and (sig.has_visual or sig.has_digital) and not sig.is_signed:
            # Signing intent stored but not yet signed — show sign button
            # only if PDF exists and invoice is not locked
            has_pdf = self.core.invoice_service.has_pdf(invoice)
            is_locked = self.core.invoice_service.is_locked(invoice)
            if has_pdf and not is_locked:
                html = render_template(
                    'sign_button.html',
                    invoice=invoice,
                    is_signed=False,
                    can_sign=True,
                    has_visual=sig.has_visual,
                    has_digital=sig.has_digital,
                )
                return [html]

        # No signing intent — show neutral badge
        html = render_template(
            'sign_button.html',
            invoice=invoice,
            is_signed=False,
            can_sign=False,
            has_visual=False,
            has_digital=False,
        )
        return [html]

    # --- Create/Edit form checkboxes ---

    def _render_signature_checkboxes(self, visual_checked=False, digital_checked=False):
        """Build the signature checkboxes HTML in Report Configuration style."""
        cfg = self._get_config()

        visual_available = cfg.visual_enabled and cfg.signature_image_key and PILLOW_AVAILABLE
        digital_available = cfg.digital_enabled and cfg.certificate_key and PYHANKO_AVAILABLE

        # If neither option is enabled in settings, don't show the section at all
        if not cfg.visual_enabled and not cfg.digital_enabled:
            return ''

        self.logger.info(
            'PDF Signature: rendering checkboxes — visual_available=%s '
            '(enabled=%s, image_key=%r, pillow=%s), digital_available=%s '
            '(enabled=%s, cert_key=%r, pyhanko=%s)',
            visual_available, cfg.visual_enabled, cfg.signature_image_key, PILLOW_AVAILABLE,
            digital_available, cfg.digital_enabled, cfg.certificate_key, PYHANKO_AVAILABLE,
        )

        visual_hint = ''
        if not PILLOW_AVAILABLE:
            visual_hint = 'Pillow library not installed.'
        elif not cfg.visual_enabled:
            visual_hint = 'Visual signature disabled in settings.'
        elif not cfg.signature_image_key:
            visual_hint = 'Signature image not configured.'
        else:
            visual_hint = 'Overlay your signature image on the last page of the PDF.'

        digital_hint = ''
        if not PYHANKO_AVAILABLE:
            digital_hint = 'pyHanko library not installed.'
        elif not cfg.digital_enabled:
            digital_hint = 'Digital signature disabled in settings.'
        elif not cfg.certificate_key:
            digital_hint = 'Certificate not configured.'
        else:
            digital_hint = 'Cryptographically sign the PDF with your X.509 certificate.'

        v_dis = '' if visual_available else 'disabled'
        d_dis = '' if digital_available else 'disabled'
        v_chk = 'checked' if visual_checked else ''
        d_chk = 'checked' if digital_checked else ''
        v_opacity = '1' if visual_available else '0.5'
        d_opacity = '1' if digital_available else '0.5'

        return f'''
    <div style="background: #f5f5f5; padding: 20px; border-radius: 5px; margin-bottom: 20px;">
        <h3 style="margin: 0 0 15px 0; color: #333; font-size: 18px;">PDF Signature Options</h3>

        <div style="margin-bottom: 14px; opacity: {v_opacity};">
            <label style="display: flex; align-items: flex-start; cursor: {'pointer' if visual_available else 'not-allowed'};">
                <input type="checkbox" name="pdf_sig_visual" value="1" {v_dis} {v_chk}
                       style="margin-right: 10px; margin-top: 3px; width: 16px; height: 16px;">
                <span>
                    <strong style="display: block; font-size: 15px;">✍️ Add visual signature</strong>
                    <span style="color: #666; font-size: 13px;">{visual_hint}</span>
                </span>
            </label>
        </div>

        <div style="opacity: {d_opacity};">
            <label style="display: flex; align-items: flex-start; cursor: {'pointer' if digital_available else 'not-allowed'};">
                <input type="checkbox" name="pdf_sig_digital" value="1" {d_dis} {d_chk}
                       style="margin-right: 10px; margin-top: 3px; width: 16px; height: 16px;">
                <span>
                    <strong style="display: block; font-size: 15px;">🔏 Digitally sign PDF</strong>
                    <span style="color: #666; font-size: 13px;">{digital_hint}</span>
                </span>
            </label>
        </div>
    </div>
        '''

    def get_create_form_html(self):
        """Inject signature checkboxes into the invoice create form."""
        return self._render_signature_checkboxes()


    def get_edit_form_html(self, invoice):
        """Inject signature checkboxes into the invoice edit form."""
        if self.core.invoice_service.is_locked(invoice):
            return None
        sig = self._get_invoice_sig(invoice.id)
        return self._render_signature_checkboxes(
            visual_checked=sig.has_visual,
            digital_checked=sig.has_digital,
        )


    # --- Invoice hooks ---

    def on_invoice_created(self, invoice, request):
        """Store signing intent after invoice creation.

        PDF is generated AFTER this hook, so we only persist which
        checkboxes the user selected. Actual signing happens later
        via the /pdf-signature/sign/<id> route.
        """
        want_visual = bool(request.form.get('pdf_sig_visual'))
        want_digital = bool(request.form.get('pdf_sig_digital'))

        if not want_visual and not want_digital:
            return

        sig = self._get_invoice_sig(invoice.id)
        sig.has_visual = want_visual
        sig.has_digital = want_digital
        sig.is_signed = False
        self._db.session.commit()

        self.logger.info(
            'PDF Signature: stored signing intent for new invoice #%s '
            '(visual=%s, digital=%s)',
            invoice.invoice_number, want_visual, want_digital,
        )

    def on_invoice_updated(self, invoice, request):
        """Process signature checkboxes after invoice update.

        If the invoice is locked (paid), skip entirely.
        Otherwise update the signing intent and, when a PDF already
        exists, trigger (re-)signing immediately.
        """
        if self.core.invoice_service.is_locked(invoice):
            return

        want_visual = bool(request.form.get('pdf_sig_visual'))
        want_digital = bool(request.form.get('pdf_sig_digital'))

        sig = self._get_invoice_sig(invoice.id)
        sig.has_visual = want_visual
        sig.has_digital = want_digital

        if not want_visual and not want_digital:
            # User unchecked everything — mark as unsigned
            sig.is_signed = False
            self._db.session.commit()
            self.logger.info(
                'PDF Signature: signing removed for invoice #%s',
                invoice.invoice_number,
            )
            return

        self._db.session.commit()

        # If a PDF already exists, sign (or re-sign) it now
        has_pdf = self.core.invoice_service.get_pdf(invoice)
        if has_pdf:
            self._sign_invoice_pdf(invoice, visual=want_visual, digital=want_digital)
        else:
            self.logger.info(
                'PDF Signature: no PDF yet for invoice #%s — signing deferred',
                invoice.invoice_number,
            )

    # --- Settings integration ---

    def get_settings_html(self, settings):
        cfg = self._get_config()
        return render_template(
            'pdf_signature_settings.html',
            cfg=cfg,
            pillow_available=PILLOW_AVAILABLE,
            pyhanko_available=PYHANKO_AVAILABLE,
        )

    def save_settings(self, settings, form):
        if '_settings_tab' in form and form['_settings_tab'] != 'pdf_signature':
            return
        cfg = self._get_config()
        self.logger.info(
            'PDF Signature: save_settings called — form keys: %s',
            list(form.keys()),
        )
        cfg.visual_enabled = bool(form.get('pdf_sig_visual_enabled'))
        cfg.digital_enabled = bool(form.get('pdf_sig_digital_enabled'))
        # Image key and cert key come from hidden fields (set by AJAX upload)
        image_key = form.get('pdf_sig_image_key', '').strip()
        if image_key:
            cfg.signature_image_key = image_key
            self.logger.info('PDF Signature: updated image_key=%r', image_key)
        cert_key = form.get('pdf_sig_cert_key', '').strip()
        if cert_key:
            cfg.certificate_key = cert_key
            self.logger.info('PDF Signature: updated cert_key=%r', cert_key)
        cert_password = form.get('pdf_sig_cert_password')
        if cert_password is not None:
            cfg.certificate_password = cert_password
        # Signature position settings
        sig_position = form.get('pdf_sig_position', 'bottom-left').strip()
        if sig_position in ('bottom-left', 'bottom-right', 'bottom-center',
                            'top-left', 'top-right', 'top-center'):
            cfg.sig_position = sig_position
        try:
            cfg.sig_margin_x = int(form.get('pdf_sig_margin_x', 40))
        except (ValueError, TypeError):
            cfg.sig_margin_x = 40
        try:
            cfg.sig_margin_y = int(form.get('pdf_sig_margin_y', 40))
        except (ValueError, TypeError):
            cfg.sig_margin_y = 40
        try:
            cfg.sig_max_width = int(form.get('pdf_sig_max_width', 150))
        except (ValueError, TypeError):
            cfg.sig_max_width = 150
        self._db.session.commit()
        self.logger.info(
            'PDF Signature settings saved: visual_enabled=%s, digital_enabled=%s, '
            'image_key=%r, cert_key=%r',
            cfg.visual_enabled, cfg.digital_enabled,
            cfg.signature_image_key, cfg.certificate_key,
        )

    # --- Routes ---

    def register_routes(self, app):
        bp = Blueprint('pdf_signature', __name__,
                       template_folder='templates',
                       url_prefix='/pdf-signature')
        login_required = self.core.login_required
        module = self

        @bp.route('/upload-file', methods=['POST'])
        @login_required
        def upload_file():
            """Handle signature image and certificate file uploads via AJAX."""
            file = request.files.get('file')
            upload_type = request.form.get('type', '')

            if not file or not file.filename:
                return jsonify({'error': 'No file selected.'}), 400

            filename = file.filename
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

            if upload_type == 'signature':
                if ext not in ('png', 'jpg', 'jpeg'):
                    return jsonify({'error': 'Invalid format. Please upload PNG or JPG.'}), 400
                safe_name = f'signature.{ext}'
            elif upload_type == 'certificate':
                if ext not in ('pfx', 'p12'):
                    return jsonify({'error': 'Invalid format. Please upload .pfx or .p12 file.'}), 400
                safe_name = f'certificate.{ext}'
            else:
                return jsonify({'error': 'Invalid upload type.'}), 400

            try:
                storage_key = module.core.save_file(
                    file, 'pdf_signature_files', safe_name
                )
                # Also persist the key immediately in config
                cfg = module._get_config()
                if upload_type == 'signature':
                    cfg.signature_image_key = storage_key
                else:
                    cfg.certificate_key = storage_key
                module._db.session.commit()

                module.logger.info('PDF Signature file uploaded: type=%s, key=%s',
                                   upload_type, storage_key)
                return jsonify({'storage_key': storage_key})
            except Exception as e:
                module.logger.error('PDF Signature upload failed: %s', e)
                return jsonify({'error': 'Upload failed. Check server logs.'}), 500

        @bp.route('/preview-signature')
        @login_required
        def preview_signature():
            """Serve the signature image for preview in settings."""
            cfg = module._get_config()
            if not cfg.signature_image_key:
                return '', 404
            try:
                return module.core.send_file(cfg.signature_image_key)
            except Exception:
                return '', 404

        @bp.route('/sign/<int:invoice_id>', methods=['POST'])
        @login_required
        def sign_invoice(invoice_id):
            """Trigger PDF signing for an invoice."""
            module.logger.info('PDF Signature: sign route called for invoice_id=%d', invoice_id)
            svc = module.core.invoice_service
            invoice = svc.get(invoice_id)
            if not invoice:
                module.logger.warning('PDF Signature: invoice_id=%d not found', invoice_id)
                flash('Invoice not found', 'danger')
                return redirect(url_for('index'))
            if svc.is_locked(invoice):
                module.logger.warning('PDF Signature: invoice #%s is locked', invoice.invoice_number)
                flash('Cannot sign a paid invoice', 'warning')
                return redirect(url_for('view_invoice', id=invoice_id))
            sig = module._get_invoice_sig(invoice_id)
            module.logger.info(
                'PDF Signature: triggering sign for invoice #%s (has_visual=%s, has_digital=%s)',
                invoice.invoice_number, sig.has_visual, sig.has_digital,
            )
            if not sig.has_visual and not sig.has_digital:
                module.logger.warning('PDF Signature: no signing intent for invoice #%s', invoice.invoice_number)
                flash('No signature type selected. Edit the invoice and check signature options first.', 'warning')
                return redirect(url_for('view_invoice', id=invoice_id))
            module._sign_invoice_pdf(
                invoice, visual=sig.has_visual, digital=sig.has_digital,
            )
            return redirect(url_for('view_invoice', id=invoice_id))

        app.register_blueprint(bp)
