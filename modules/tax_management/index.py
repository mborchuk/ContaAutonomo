#!/usr/bin/env python3
"""
Tax Management Module
Handles tax forms (Modelo 349, 303, 130, 390, 100) and Social Security payments.
"""

import csv
import io

from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from datetime import datetime


def _csv_safe(value):
    """Neutralize spreadsheet formula injection in user-entered text."""
    if value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def build_ss_payments_csv(payments):
    """Render SS payments to CSV text (Date, Description, Amount) with a total row."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Date', 'Description', 'Amount (EUR)'])
    total = 0.0
    for p in payments:
        writer.writerow([
            p.payment_date.isoformat(),
            _csv_safe(p.description or ''),
            f'{p.amount:.2f}',
        ])
        total += p.amount
    writer.writerow(['', 'Total', f'{total:.2f}'])
    return buf.getvalue()


class TaxManagementModule(BaseModule):
    """Tax forms and Social Security payment management"""

    @property
    def module_id(self):
        return 'tax_management'

    @property
    def name(self):
        return 'Tax Management'

    @property
    def description(self):
        return 'Manage Spanish tax forms (Modelo 349, 303, 130, 390, 100) and Social Security payments'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Tax Forms', 'endpoint': 'tax_management.tax_forms_index', 'icon': '📋'},
            {'label': 'Obligations', 'endpoint': 'tax_management.obligations_index',
             'icon': '✅', 'group': 'System'},
        ]

    def on_enable(self):
        """F10 — filing-workflow columns on tax_form (idempotent migration).

        Existing rows backfill to status='filed': they carry an evidence PDF,
        so "filed" is the honest historical default.
        """
        from sqlalchemy import inspect as sa_inspect, text
        migrations = [
            ('status', "VARCHAR(20) DEFAULT 'filed'"),
            ('amount', 'FLOAT'),
            ('filed_date', 'DATE'),
            ('payment_date', 'DATE'),
        ]
        try:
            inspector = sa_inspect(self._db.engine)
            cols = [c['name'] for c in inspector.get_columns('tax_form')]
            with self._db.engine.connect() as conn:
                for name, typedef in migrations:
                    if name not in cols:
                        conn.execute(text(
                            f'ALTER TABLE tax_form ADD COLUMN {name} {typedef}'))
                conn.execute(text(
                    "UPDATE tax_form SET status='filed' WHERE status IS NULL"))
                conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Tax form workflow migration failed: %s', e)

    @property
    def settings_panels(self):
        return []  # No separate settings tab needed

    def register_models(self, db):
        """Register TaxForm and SSPayment models"""
        self._db = db
        self._define_models(db)
        return {
            'TaxForm': self.TaxForm,
            'SSPayment': self.SSPayment
        }

    def _define_models(self, db):
        """Define module-specific database models"""

        class TaxForm(db.Model):
            __tablename__ = 'tax_form'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            form_type = db.Column(db.String(50), nullable=False)
            year = db.Column(db.Integer, nullable=False)
            quarter = db.Column(db.Integer)
            file_path = db.Column(db.String(500), nullable=False)
            original_filename = db.Column(db.String(200))
            uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
            notes = db.Column(db.Text)
            # F10 — filing workflow: pending -> filed -> paid, with amount and
            # evidence. Rows created by upload default to 'filed' (they carry a
            # PDF); record-only rows (no file) use file_path=''.
            status = db.Column(db.String(20), default='filed')
            amount = db.Column(db.Float)
            filed_date = db.Column(db.Date)
            payment_date = db.Column(db.Date)

            def __repr__(self):
                if self.quarter:
                    return f'<TaxForm {self.form_type}-Q{self.quarter} {self.year}>'
                return f'<TaxForm {self.form_type} {self.year}>'

        class SSPayment(db.Model):
            __tablename__ = 'ss_payment'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            payment_date = db.Column(db.Date, nullable=False)
            amount = db.Column(db.Float, nullable=False)
            description = db.Column(db.String(300))
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

            def __repr__(self):
                return f'<SSPayment {self.payment_date} {self.amount}>'

        self.TaxForm = TaxForm
        self.SSPayment = SSPayment

    def register_routes(self, app):
        """Register tax management routes"""
        bp = Blueprint(
            'tax_management',
            __name__,
            template_folder='templates',
            url_prefix='/tax-forms'
        )

        login_required = self.core.login_required

        @bp.route('/')
        @login_required
        def tax_forms_index():
            return self._list_tax_forms()

        @bp.route('/upload', methods=['POST'])
        @login_required
        def tax_forms_upload():
            return self._upload_tax_form()

        @bp.route('/download/<int:id>')
        @login_required
        def tax_forms_download(id):
            return self._download_tax_form(id)

        @bp.route('/preview/<int:id>')
        @login_required
        def tax_forms_preview(id):
            return self._preview_tax_form(id)

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def tax_forms_delete(id):
            return self._delete_tax_form(id)

        @bp.route('/obligations')
        @login_required
        def obligations_index():
            return self._obligations_view()

        @bp.route('/obligations/record', methods=['POST'])
        @login_required
        def obligations_record():
            return self._record_filing()

        @bp.route('/ss-payments/add', methods=['POST'])
        @login_required
        def ss_payment_add():
            return self._add_ss_payment()

        @bp.route('/ss-payments/<int:id>/edit', methods=['POST'])
        @login_required
        def ss_payment_edit(id):
            return self._edit_ss_payment(id)

        @bp.route('/ss-payments/<int:id>/delete', methods=['POST'])
        @login_required
        def ss_payment_delete(id):
            return self._delete_ss_payment(id)

        @bp.route('/ss-payments/export')
        @login_required
        def ss_payment_export():
            return self._export_ss_payments()

        app.register_blueprint(bp)

    # --- REST API (served under /api/v1/m/tax_management/... ) ---

    def get_api_routes(self):
        """Expose tax forms and SS payments (read-only)."""
        return [
            {'path': 'tax-forms', 'methods': ['GET'],
             'handler': self._api_tax_forms, 'summary': 'List tax forms'},
            {'path': 'ss-payments', 'methods': ['GET'],
             'handler': self._api_ss_payments, 'summary': 'List Social Security payments'},
        ]

    def _api_tax_forms(self, request):
        forms = self.TaxForm.query.order_by(
            self.TaxForm.year.desc(), self.TaxForm.form_type, self.TaxForm.quarter
        ).all()
        data = [{
            'id': f.id,
            'form_type': f.form_type,
            'year': f.year,
            'quarter': f.quarter,
            'original_filename': f.original_filename,
            'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
            'notes': f.notes,
        } for f in forms]
        return {'data': data, 'total': len(data)}, 200

    def _api_ss_payments(self, request):
        payments = self.SSPayment.query.order_by(
            self.SSPayment.payment_date.desc()).all()
        data = [{
            'id': p.id,
            'payment_date': p.payment_date.isoformat() if p.payment_date else None,
            'amount': p.amount,
            'description': p.description,
        } for p in payments]
        return {'data': data, 'total': len(data)}, 200

    # --- Tax Forms Logic ---

    def _list_tax_forms(self):
        """Display tax forms page with SS payments"""
        forms_by_year = {}
        all_forms = self.TaxForm.query.order_by(
            self.TaxForm.year.desc(), self.TaxForm.form_type, self.TaxForm.quarter
        ).all()

        for form in all_forms:
            year = form.year
            if year not in forms_by_year:
                forms_by_year[year] = {}
            q_key = form.quarter if form.quarter else 0
            if q_key not in forms_by_year[year]:
                forms_by_year[year][q_key] = []
            forms_by_year[year][q_key].append(form)

        years = sorted(forms_by_year.keys(), reverse=True) if forms_by_year else []
        current_year = datetime.now().year

        form_types = {
            '349': {'name': 'Modelo 349', 'quarterly': True},
            '303': {'name': 'Modelo 303', 'quarterly': True},
            '130': {'name': 'Modelo 130', 'quarterly': True},
            '390': {'name': 'Modelo 390', 'quarterly': False},
            '100': {'name': 'Modelo 100', 'quarterly': False},
        }

        # SS Payments grouped by year
        ss_by_year = {}
        all_ss = self.SSPayment.query.order_by(self.SSPayment.payment_date.desc()).all()
        for p in all_ss:
            y = p.payment_date.year
            if y not in ss_by_year:
                ss_by_year[y] = []
            ss_by_year[y].append(p)
        ss_years = sorted(ss_by_year.keys(), reverse=True)

        return render_template('tax_forms.html',
                             forms_by_year=forms_by_year,
                             years=years,
                             current_year=current_year,
                             form_types=form_types,
                             ss_by_year=ss_by_year,
                             ss_years=ss_years)

    def _upload_tax_form(self):
        """Upload a tax form"""
        try:
            form_type = request.form.get('form_type')
            year = int(request.form.get('year'))
            quarter = request.form.get('quarter')
            notes = request.form.get('notes', '')

            if not form_type or not year:
                flash('Form type and year are required', 'danger')
                return redirect(url_for('tax_management.tax_forms_index'))

            if 'file' not in request.files or request.files['file'].filename == '':
                flash('No file selected', 'danger')
                return redirect(url_for('tax_management.tax_forms_index'))

            file = request.files['file']
            allowed = {'pdf', 'xlsx', 'xls', 'doc', 'docx'}
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if ext not in allowed:
                flash('Invalid file type. Allowed: PDF, Excel, Word', 'danger')
                return redirect(url_for('tax_management.tax_forms_index'))
            # Validate real content, not just the extension (rename bypass).
            from file_validation import validate_filestorage
            if not validate_filestorage(file, ext):
                flash('File content does not match its extension.', 'danger')
                return redirect(url_for('tax_management.tax_forms_index'))

            # Check for existing
            query = self.TaxForm.query.filter_by(form_type=form_type, year=year)
            if quarter:
                query = query.filter_by(quarter=int(quarter))
            existing = query.first()

            # Build subfolder and filename
            subfolder = f'tax_forms/{year}'
            if quarter:
                subfolder = f'tax_forms/{year}/Q{quarter}'
            filename = f'{form_type}-Q{quarter}.{ext}' if quarter else f'{form_type}.{ext}'

            file_path = self.core.save_file(file, subfolder, filename)

            if existing:
                if existing.file_path != file_path:
                    self.core.delete_file(existing.file_path)
                existing.file_path = file_path
                existing.original_filename = file.filename
                existing.notes = notes
                existing.uploaded_at = datetime.utcnow()
            else:
                new_form = self.TaxForm(
                    form_type=form_type, year=year,
                    quarter=int(quarter) if quarter else None,
                    file_path=file_path, original_filename=file.filename,
                    notes=notes, status='filed',
                    filed_date=datetime.utcnow().date(),
                )
                self._db.session.add(new_form)

            # F10 — optional workflow fields on upload.
            target = existing or new_form
            raw_amount = request.form.get('amount')
            if raw_amount not in (None, ''):
                try:
                    target.amount = float(raw_amount)
                except (TypeError, ValueError):
                    pass
            status = request.form.get('status')
            if status in ('pending', 'filed', 'paid'):
                target.status = status
                if status == 'paid' and not target.payment_date:
                    target.payment_date = datetime.utcnow().date()

            self._db.session.commit()
            flash(f'Tax form {form_type} {"updated" if existing else "uploaded"} successfully!', 'success')

        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error uploading tax form: %s', e)
            flash('Error processing form data. Please check your input.', 'danger')

        return redirect(url_for('tax_management.tax_forms_index'))

    def _download_tax_form(self, id):
        """Download a tax form"""
        tax_form = self.TaxForm.query.get_or_404(id)
        if not self.core.file_exists(tax_form.file_path):
            flash('File not found', 'danger')
            return redirect(url_for('tax_management.tax_forms_index'))
        return self.core.send_file(tax_form.file_path, tax_form.original_filename)

    def _preview_tax_form(self, id):
        """Preview a tax form inline in the browser"""
        tax_form = self.TaxForm.query.get_or_404(id)
        resp = self.core.preview_file(tax_form.file_path, tax_form.original_filename)
        if not resp:
            flash('File not found', 'danger')
            return redirect(url_for('tax_management.tax_forms_index'))
        return resp

    def _delete_tax_form(self, id):
        """Delete a tax form"""
        try:
            tax_form = self.TaxForm.query.get_or_404(id)
            self.core.delete_file(tax_form.file_path)
            self._db.session.delete(tax_form)
            self._db.session.commit()
            flash('Tax form deleted successfully!', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error deleting tax form: %s', e)
            flash('Error deleting record. Please try again.', 'danger')
        return redirect(url_for('tax_management.tax_forms_index'))

    # --- Social Security Logic ---

    def _add_ss_payment(self):
        """Add a Social Security payment"""
        try:
            payment = self.SSPayment(
                payment_date=datetime.strptime(request.form['payment_date'], '%Y-%m-%d').date(),
                amount=float(request.form['amount']),
                description=request.form.get('description', '')
            )
            self._db.session.add(payment)
            self._db.session.commit()
            flash('SS payment added successfully!', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error adding SS payment: %s', e)
            flash('Error creating record. Please try again.', 'danger')
        return redirect(url_for('tax_management.tax_forms_index'))

    def _edit_ss_payment(self, id):
        """Edit a Social Security payment"""
        try:
            payment = self.SSPayment.query.get_or_404(id)
            payment.payment_date = datetime.strptime(request.form['payment_date'], '%Y-%m-%d').date()
            payment.amount = float(request.form['amount'])
            payment.description = request.form.get('description', '')
            self._db.session.commit()
            flash('SS payment updated!', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error updating SS payment: %s', e)
            flash('Error processing form data. Please check your input.', 'danger')
        return redirect(url_for('tax_management.tax_forms_index'))

    def _export_ss_payments(self):
        """Export SS payments as a CSV download, optionally filtered by ?year=YYYY"""
        year = request.args.get('year', type=int)
        query = self.SSPayment.query
        if year:
            query = query.filter(
                self._db.extract('year', self.SSPayment.payment_date) == year
            )
        payments = query.order_by(self.SSPayment.payment_date).all()

        csv_text = build_ss_payments_csv(payments)
        filename = f'ss_payments_{year}.csv' if year else 'ss_payments.csv'
        self.core.log_activity(
            'ss_payments_exported', 'tax',
            f'{len(payments)} payments' + (f', year {year}' if year else '')
        )
        return Response(
            csv_text,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

    def _delete_ss_payment(self, id):
        """Delete a Social Security payment"""
        try:
            payment = self.SSPayment.query.get_or_404(id)
            self._db.session.delete(payment)
            self._db.session.commit()
            flash('SS payment deleted!', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error deleting SS payment: %s', e)
            flash('Error deleting record. Please try again.', 'danger')
        return redirect(url_for('tax_management.tax_forms_index'))

    # --- Report & Dashboard Integration ---

    def get_dashboard_panels(self):
        """Provide SS data for dashboard tax obligations panel"""
        from datetime import date
        current_year = datetime.now().year
        start = date(current_year, 1, 1)
        end = date(current_year, 12, 31)

        ss_payments = self.SSPayment.query.filter(
            self.SSPayment.payment_date >= start,
            self.SSPayment.payment_date <= end
        ).all()

        total_ss = sum(p.amount for p in ss_payments)
        months_paid = len(ss_payments)

        return [{
            'id': 'ss_dashboard',
            'data': {
                'ss_total': total_ss,
                'ss_months_paid': months_paid,
                'ss_payments': ss_payments
            },
            'order': 10
        }]

    # --- F10: obligations workflow ---------------------------------------- #

    def _obligation_rows(self, year, today=None):
        """Cross the fiscal-calendar dataset with recorded TaxForms.

        One row per obligation instance (form × period) for `year`, each with
        its filing status: pending / filed / paid / overdue. The calendar
        dataset ships with the fiscal_calendar module but is plain data — safe
        to import whether or not that module is enabled.
        """
        from datetime import date as _date
        from modules.fiscal_calendar.calendar_data import all_deadlines

        today = today or _date.today()

        # Which forms does the user file? Prefer fiscal_calendar's selection,
        # else infer from TaxForm history, else all.
        selected = None
        fc = self.core.module_manager.modules.get('fiscal_calendar') \
            if self.core.module_manager else None
        if fc:
            try:
                selected = fc._selected_forms()
            except Exception:  # pragma: no cover - defensive
                selected = None
        if selected is None:
            rows = self._db.session.query(self.TaxForm.form_type).distinct().all()
            selected = {r[0] for r in rows if r[0]} or None

        forms = {f.form_type + f'|{f.year}|{f.quarter or 0}': f
                 for f in self.TaxForm.query.filter_by(year=year).all()}

        out = []
        for entry in all_deadlines():
            if entry['year'] != year:
                continue
            if selected is not None and entry['form'] not in selected:
                continue
            key = f"{entry['form']}|{entry['year']}|{entry['quarter'] or 0}"
            tax_form = forms.get(key)
            if tax_form and tax_form.status in ('filed', 'paid'):
                status = tax_form.status
            elif today > entry['window_end']:
                status = 'overdue'
            else:
                status = 'pending'
            out.append({
                'form': entry['form'],
                'period_label': entry['period_label'],
                'quarter': entry['quarter'],
                'window_start': entry['window_start'],
                'window_end': entry['window_end'],
                'status': status,
                'tax_form': tax_form,
            })
        return out

    def _obligations_view(self):
        year = request.args.get('year', type=int) or datetime.now().year
        rows = self._obligation_rows(year)
        years = sorted({y for (y,) in
                        self._db.session.query(self.TaxForm.year).distinct()}
                       | {datetime.now().year, year}, reverse=True)
        # Yearly totals per form type (F10-D3: "what did I pay in 303s?").
        totals = {}
        for f in self.TaxForm.query.filter_by(year=year).all():
            if f.amount:
                totals[f.form_type] = totals.get(f.form_type, 0.0) + f.amount
        return render_template('tax_obligations.html', rows=rows, year=year,
                               years=years, totals=totals)

    def _record_filing(self):
        """Record a filing for an obligation: status + amount, evidence optional.

        Links to an existing uploaded TaxForm when one matches; otherwise
        creates a record-only row (file_path='' — evidence can be uploaded
        later through the normal upload flow, which updates the same row).
        """
        try:
            form_type = request.form['form_type']
            year = int(request.form['year'])
            quarter = request.form.get('quarter') or None
            status = request.form.get('status', 'filed')
            if status not in ('pending', 'filed', 'paid'):
                status = 'filed'
            amount = request.form.get('amount')

            query = self.TaxForm.query.filter_by(form_type=form_type, year=year)
            query = query.filter_by(quarter=int(quarter) if quarter else None)
            tax_form = query.first()
            if not tax_form:
                tax_form = self.TaxForm(form_type=form_type, year=year,
                                        quarter=int(quarter) if quarter else None,
                                        file_path='')
                self._db.session.add(tax_form)
            tax_form.status = status
            if amount not in (None, ''):
                tax_form.amount = float(amount)
            if status in ('filed', 'paid') and not tax_form.filed_date:
                tax_form.filed_date = datetime.utcnow().date()
            if status == 'paid' and not tax_form.payment_date:
                tax_form.payment_date = datetime.utcnow().date()
            self._db.session.commit()
            self.core.log_activity(
                'tax_filing_recorded', 'system',
                f'{form_type} {year}' + (f' Q{quarter}' if quarter else '')
                + f' -> {status}')
            flash(f'Filing recorded: {form_type} {year}'
                  + (f' Q{quarter}' if quarter else '') + f' — {status}.',
                  'success')
        except (KeyError, TypeError, ValueError) as e:
            self._db.session.rollback()
            self.logger.error('Record filing failed: %s', e)
            flash('Could not record filing. Check the input.', 'danger')
        return redirect(url_for('tax_management.obligations_index',
                                year=request.form.get('year')))

    def get_report_sections(self):
        """Provide SS data + filings history for financial reports"""
        return [{
            'id': 'ss_payments',
            'title': 'Social Security Payments',
            'description': 'Monthly Social Security (autónomo) payments with dates and amounts.',
            'query_fn': self._get_ss_for_report
        }, {
            'id': 'tax_filings',
            'title': 'Tax Filings',
            'description': 'Recorded tax filings (form, period, status, amount).',
            'query_fn': self._get_filings_for_report,
            'columns': [
                {'key': 'form_type', 'label': 'Form', 'width': 2.5},
                {'key': 'period', 'label': 'Period', 'width': 3},
                {'key': 'status', 'label': 'Status', 'width': 2.5},
                {'key': 'filed_date', 'label': 'Filed', 'width': 3},
                {'key': 'amount_eur', 'label': 'Amount (EUR)', 'width': 3},
            ],
            'total_field': 'amount_eur',
        }]

    def _get_filings_for_report(self, start_date, end_date, doc_ids=None):
        """Filings whose period falls inside the report window (by year)."""
        forms = self.TaxForm.query.filter(
            self.TaxForm.year >= start_date.year,
            self.TaxForm.year <= end_date.year,
        ).order_by(self.TaxForm.year, self.TaxForm.quarter).all()
        return [{
            'form_type': f.form_type,
            'period': f'{f.year}' + (f' Q{f.quarter}' if f.quarter else ' (annual)'),
            'status': (f.status or 'filed').upper(),
            'filed_date': f.filed_date.strftime('%d/%m/%Y') if f.filed_date else '',
            'amount_eur': f.amount or 0.0,
        } for f in forms]

    def _get_ss_for_report(self, start_date, end_date):
        """Query SS payments for a date range (used by reports)"""
        payments = self.SSPayment.query.filter(
            self.SSPayment.payment_date >= start_date,
            self.SSPayment.payment_date <= end_date
        ).order_by(self.SSPayment.payment_date).all()

        return [{
            'payment_date': p.payment_date.strftime('%d/%m/%Y'),
            'amount': p.amount,
            'description': p.description or ''
        } for p in payments]

    # --- Settings Integration ---

    def get_settings_html(self, settings):
        """Provide SS monthly quota field for General Settings tab"""
        value = settings.social_security_monthly if settings and settings.social_security_monthly else 0
        annual = value * 12
        return f'''
        <h3 style="margin-bottom: 15px; color: #333;">Social Security (Autónomo)</h3>
        <div class="form-group">
            <label for="social_security_monthly">Monthly SS Quota (€)</label>
            <input type="number" id="social_security_monthly" name="social_security_monthly"
                   value="{value}" step="0.01" min="0" style="width: 200px;">
            <small style="display: block; margin-top: 5px; color: #666;">
                Your monthly Seguridad Social payment (cuota de autónomo). This is used to calculate annual Social Security in the Tax Obligations panel.
                Current annual total: €{annual:.2f}
            </small>
        </div>'''

    def save_settings(self, settings, form):
        """Save SS monthly quota from General Settings form"""
        if 'social_security_monthly' not in form:
            return
        ss_monthly = form.get('social_security_monthly', '0')
        try:
            settings.social_security_monthly = float(ss_monthly)
        except ValueError:
            settings.social_security_monthly = 0.0

    # --- Tax Obligations Integration ---

    def get_tax_obligations(self, context):
        """Contribute Social Security data to Tax Obligations panel"""
        current_year = context['current_year']
        settings = context['settings']

        current_year_ss = self.SSPayment.query.filter(
            self._db.extract('year', self.SSPayment.payment_date) == current_year
        ).all()

        ss_annual = sum(p.amount for p in current_year_ss)
        ss_monthly = ss_annual / 12 if ss_annual > 0 else (
            settings.social_security_monthly if settings and settings.social_security_monthly else 0.0
        )
        if ss_annual == 0 and settings and settings.social_security_monthly:
            ss_monthly = settings.social_security_monthly
            ss_annual = ss_monthly * 12

        # F10 — surface unfiled-overdue obligations on the dashboard panel.
        notes = []
        try:
            overdue = [r for r in self._obligation_rows(current_year)
                       if r['status'] == 'overdue']
            if overdue:
                labels = ', '.join(f"{r['form']} {r['period_label']}"
                                   for r in overdue[:4])
                notes.append(f'⚠ {len(overdue)} overdue unfiled obligation(s): '
                             f'{labels}.')
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Overdue obligations check failed: %s', e)

        return {
            'summary_columns': [],
            'breakdown_rows': [
                {'label': f'Social Security (€{ss_monthly:.2f}/month)', 'amount': ss_annual}
            ],
            'notes': notes,
            'deductions': 0,
            'tax_total': ss_annual
        }
