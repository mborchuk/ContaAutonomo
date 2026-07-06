#!/usr/bin/env python3
"""
Invoice Email Module (F8) — send invoice PDFs by SMTP + overdue reminders.

CAVEMAN NOTE: app had zero mail code. This module bring stdlib smtplib, an
SMTP settings tab, a "send by email" panel on the invoice view with a send log,
a daily overdue-reminder job (opt-in, idempotent), and a `notify` capability so
fiscal_calendar reminders can ride email with zero coupling.

Password stored in module config (plaintext SQLite) — same posture as the
existing AI-provider API keys; documented, self-hosted single-user app.
"""

import json
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from flask import Blueprint, render_template, request, redirect, url_for, flash

from module_manager import BaseModule

# Overdue reminder offsets (days after due_date) and hard cap per invoice.
REMINDER_OFFSETS = (3, 10)


class InvoiceEmailModule(BaseModule):
    """Email delivery for invoices + payment reminders."""

    @property
    def module_id(self):
        return 'invoice_email'

    @property
    def name(self):
        return 'Invoice Email'

    @property
    def description(self):
        return ('Send invoice PDFs to customers via your SMTP server, with '
                'optional overdue payment reminders.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def settings_tab(self):
        return {'id': 'invoice_email', 'label': 'Email'}

    def register_models(self, db):
        self._db = db

        class InvoiceEmailConfig(db.Model):
            __tablename__ = 'invoice_email_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        class EmailLog(db.Model):
            __tablename__ = 'invoice_email_log'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            invoice_id = db.Column(db.Integer)
            kind = db.Column(db.String(20), default='send')  # send | reminder | notify
            recipient = db.Column(db.String(200))
            subject = db.Column(db.String(500))
            status = db.Column(db.String(20))  # sent | failed
            error = db.Column(db.Text)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.Config = InvoiceEmailConfig
        self.EmailLog = EmailLog
        return {'InvoiceEmailConfig': InvoiceEmailConfig, 'EmailLog': EmailLog}

    def on_enable(self):
        try:
            self.core.scheduler.add_job(
                job_id='invoice_email.reminders',
                func=self._run_overdue_reminders,
                job_type='daily',
                time_str='09:00',
                description='Overdue invoice payment reminders (email)',
                timeout=120,
            )
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register reminder job: %s', e)

    # --- SMTP config ---------------------------------------------------------- #

    _DEFAULTS = {
        'host': '', 'port': 587, 'security': 'starttls',  # starttls | ssl | none
        'username': '', 'password': '', 'from_addr': '',
        'reminders_enabled': False,
    }

    def _smtp_config(self):
        cfg = dict(self._DEFAULTS)
        try:
            row = self.Config.query.filter_by(key='smtp').first()
            if row and row.value:
                cfg.update(json.loads(row.value))
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading SMTP config failed: %s', e)
        return cfg

    def _save_smtp_config(self, cfg):
        row = self.Config.query.filter_by(key='smtp').first()
        if not row:
            row = self.Config(key='smtp')
            self._db.session.add(row)
        row.value = json.dumps(cfg)
        self._db.session.commit()

    def _configured(self):
        cfg = self._smtp_config()
        return bool(cfg['host'] and cfg['from_addr'])

    # --- Sending ---------------------------------------------------------------- #

    def _send_email(self, to, subject, body, attachment=None, filename=None):
        """Send one email via the configured SMTP server. Raises on failure."""
        cfg = self._smtp_config()
        if not cfg['host'] or not cfg['from_addr']:
            raise ValueError('SMTP is not configured (Settings → Email)')

        msg = EmailMessage()
        msg['From'] = cfg['from_addr']
        msg['To'] = to
        msg['Subject'] = subject
        msg.set_content(body)
        if attachment:
            msg.add_attachment(attachment, maintype='application',
                               subtype='pdf', filename=filename or 'invoice.pdf')

        port = int(cfg.get('port') or 587)
        if cfg.get('security') == 'ssl':
            server = smtplib.SMTP_SSL(cfg['host'], port, timeout=30)
        else:
            server = smtplib.SMTP(cfg['host'], port, timeout=30)
        try:
            if cfg.get('security') == 'starttls':
                server.starttls()
            if cfg.get('username'):
                server.login(cfg['username'], cfg.get('password') or '')
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # pragma: no cover - close best-effort
                pass

    def _log_email(self, invoice_id, kind, recipient, subject, status, error=None):
        self._db.session.add(self.EmailLog(
            invoice_id=invoice_id, kind=kind, recipient=recipient,
            subject=subject, status=status, error=str(error) if error else None))
        self._db.session.commit()

    def _render_placeholders(self, text, invoice):
        due = invoice.due_date.strftime('%d/%m/%Y') if invoice.due_date else '-'
        return (text
                .replace('{invoice_number}', invoice.invoice_number or '')
                .replace('{client_name}', invoice.client_name or '')
                .replace('{amount}', f'{invoice.amount_eur or 0:.2f} EUR'
                         if invoice.currency == 'EUR'
                         else f'{invoice.amount_usd or 0:.2f} {invoice.currency}')
                .replace('{due_date}', due))

    def _send_invoice(self, invoice, recipient, subject, body, kind='send'):
        """Send an invoice email (PDF attached when available) and log result."""
        subject = self._render_placeholders(subject, invoice)
        body = self._render_placeholders(body, invoice)
        attachment = None
        filename = None
        pdf = self.core.invoice_service.get_pdf(invoice)
        if pdf:
            attachment, filename = pdf
        try:
            self._send_email(recipient, subject, body,
                             attachment=attachment, filename=filename)
            self._log_email(invoice.id, kind, recipient, subject, 'sent')
            self.core.log_activity('invoice_email_sent', 'invoice',
                                   f'#{invoice.invoice_number} -> {recipient}')
            return True, None
        except Exception as e:
            self._log_email(invoice.id, kind, recipient, subject, 'failed', e)
            self.logger.error('Email send failed for #%s: %s',
                              invoice.invoice_number, e)
            return False, str(e)

    # --- Invoice view panel (send form + history) ------------------------------- #

    def get_invoice_view_panels(self, invoice):
        history = self.EmailLog.query.filter_by(invoice_id=invoice.id).order_by(
            self.EmailLog.created_at.desc()).limit(10).all()
        recipient = ''
        if invoice.customer and invoice.customer.email:
            recipient = invoice.customer.email
        return [render_template('invoice_email_panel.html',
                                invoice=invoice,
                                history=history,
                                recipient=recipient,
                                configured=self._configured())]

    # --- Routes -------------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('invoice_email', __name__,
                       template_folder='templates',
                       url_prefix='/invoice-email')
        login_required = self.core.login_required
        module = self

        @bp.route('/send/<int:invoice_id>', methods=['POST'])
        @login_required
        def email_send(invoice_id):
            from app import Invoice
            invoice = Invoice.query.get_or_404(invoice_id)
            recipient = (request.form.get('recipient') or '').strip()
            if not recipient or '@' not in recipient:
                flash('A valid recipient email is required.', 'danger')
                return redirect(url_for('view_invoice', id=invoice_id))
            subject = request.form.get('subject') or 'Invoice {invoice_number}'
            body = request.form.get('body') or (
                'Dear {client_name},\n\nPlease find attached invoice '
                '{invoice_number} for {amount}, due {due_date}.\n\nBest regards')
            ok, err = module._send_invoice(invoice, recipient, subject, body)
            if ok:
                flash(f'Invoice emailed to {recipient}.', 'success')
            else:
                flash(f'Email failed: {err}', 'danger')
            return redirect(url_for('view_invoice', id=invoice_id))

        @bp.route('/test', methods=['POST'])
        @login_required
        def email_test():
            cfg = module._smtp_config()
            to = cfg.get('from_addr')
            try:
                module._send_email(to, 'ContaAutónomo SMTP test',
                                   'SMTP configuration works.')
                flash(f'Test email sent to {to}.', 'success')
            except Exception as e:
                flash(f'SMTP test failed: {e}', 'danger')
            return redirect(url_for('settings') + '#invoice_email')

        app.register_blueprint(bp)

    # --- Settings tab ------------------------------------------------------------------ #

    def get_settings_html(self, settings):
        cfg = self._smtp_config()
        sec = cfg.get('security', 'starttls')

        def _sel(v):
            return 'selected' if sec == v else ''

        checked = 'checked' if cfg.get('reminders_enabled') else ''
        test_url = url_for('invoice_email.email_test')
        return f'''
        <h3>SMTP Email Settings</h3>
        <p style="font-size:13px;color:var(--color-text-muted);">
            Credentials are stored in the local database (plaintext, same as other
            provider API keys). Self-hosted, single-user posture.
        </p>
        <div style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;">
            <div class="form-group"><label>SMTP host</label>
                <input type="text" name="smtp_host" value="{cfg['host']}"></div>
            <div class="form-group"><label>Port</label>
                <input type="number" name="smtp_port" value="{cfg['port']}"></div>
            <div class="form-group"><label>Security</label>
                <select name="smtp_security">
                    <option value="starttls" {_sel('starttls')}>STARTTLS</option>
                    <option value="ssl" {_sel('ssl')}>SSL</option>
                    <option value="none" {_sel('none')}>None</option>
                </select></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
            <div class="form-group"><label>Username</label>
                <input type="text" name="smtp_username" value="{cfg['username']}"></div>
            <div class="form-group"><label>Password</label>
                <input type="password" name="smtp_password" value="{cfg['password']}"></div>
            <div class="form-group"><label>From address</label>
                <input type="email" name="smtp_from" value="{cfg['from_addr']}"></div>
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:14px;margin:8px 0;">
            <input type="checkbox" name="smtp_reminders_enabled" {checked}>
            Send overdue payment reminders (due +{REMINDER_OFFSETS[0]} and +{REMINDER_OFFSETS[1]} days)
        </label>
        <input type="hidden" name="invoice_email_submitted" value="1">
        <p style="font-size:12px;color:var(--color-text-muted);">
            Save settings first, then test: form posts to
            <code>{test_url}</code> via the button on the invoice view or an API call.
        </p>
        '''

    def save_settings(self, settings, form):
        if 'invoice_email_submitted' not in form:
            return  # guard: unrelated tab save
        cfg = self._smtp_config()
        cfg.update({
            'host': form.get('smtp_host', '').strip(),
            'port': int(form.get('smtp_port') or 587),
            'security': form.get('smtp_security', 'starttls'),
            'username': form.get('smtp_username', '').strip(),
            'password': form.get('smtp_password', ''),
            'from_addr': form.get('smtp_from', '').strip(),
            'reminders_enabled': 'smtp_reminders_enabled' in form,
        })
        self._save_smtp_config(cfg)

    # --- Overdue reminder job (F8-D3) ------------------------------------------------------ #

    def _run_overdue_reminders(self, today=None):
        """Daily job: remind on overdue unpaid invoices. Opt-in + idempotent.

        Returns list of (invoice_id, offset) reminders sent (used by tests).
        """
        cfg = self._smtp_config()
        if not cfg.get('reminders_enabled') or not self._configured():
            return []
        from app import Invoice
        today = today or date.today()
        sent = []
        candidates = Invoice.query.filter(
            Invoice.due_date.isnot(None),
            Invoice.status.in_(('issued', 'pending')),
        ).all()
        for inv in candidates:
            for offset in REMINDER_OFFSETS:
                if inv.due_date + timedelta(days=offset) != today:
                    continue
                marker = f'reminder+{offset}'
                already = self.EmailLog.query.filter_by(
                    invoice_id=inv.id, kind=marker, status='sent').first()
                if already:
                    continue
                recipient = (inv.customer.email
                             if inv.customer and inv.customer.email else None)
                if not recipient:
                    continue
                ok, _err = self._send_invoice(
                    inv, recipient,
                    'Payment reminder — invoice {invoice_number}',
                    'Dear {client_name},\n\nFriendly reminder: invoice '
                    '{invoice_number} for {amount} was due {due_date}.\n\n'
                    'Best regards',
                    kind=marker)
                if ok:
                    sent.append((inv.id, offset))
        return sent

    # --- Notify capability (F8-D4) ---------------------------------------------------------- #

    def get_capabilities(self):
        return [{
            'type': 'notify',
            'method': 'email',
            'name': 'Email notification',
            'action': self._notify,
        }]

    def _notify(self, subject, body):
        """Send a notification email to the app owner (settings.email)."""
        settings = self.core.get_settings()
        to = getattr(settings, 'email', None) or self._smtp_config().get('from_addr')
        if not to or not self._configured():
            return False
        try:
            self._send_email(to, subject, body)
            self._log_email(None, 'notify', to, subject, 'sent')
            return True
        except Exception as e:
            self._log_email(None, 'notify', to, subject, 'failed', e)
            self.logger.warning('Notify email failed: %s', e)
            return False
