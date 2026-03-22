#!/usr/bin/env python3
"""
Tax Management Module
Handles tax forms (Modelo 349, 303, 130, 390, 100) and Social Security payments.
"""

from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime


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
            {'label': 'Tax Forms', 'endpoint': 'tax_management.tax_forms_index', 'icon': '📋'}
        ]

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

        app.register_blueprint(bp)

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
                    notes=notes
                )
                self._db.session.add(new_form)

            self._db.session.commit()
            flash(f'Tax form {form_type} {"updated" if existing else "uploaded"} successfully!', 'success')

        except Exception as e:
            self._db.session.rollback()
            flash(f'Error uploading tax form: {str(e)}', 'danger')

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
            flash(f'Error deleting tax form: {str(e)}', 'danger')
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
            flash(f'Error adding SS payment: {str(e)}', 'danger')
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
            flash(f'Error updating SS payment: {str(e)}', 'danger')
        return redirect(url_for('tax_management.tax_forms_index'))

    def _delete_ss_payment(self, id):
        """Delete a Social Security payment"""
        try:
            payment = self.SSPayment.query.get_or_404(id)
            self._db.session.delete(payment)
            self._db.session.commit()
            flash('SS payment deleted!', 'success')
        except Exception as e:
            self._db.session.rollback()
            flash(f'Error deleting SS payment: {str(e)}', 'danger')
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

    def get_report_sections(self):
        """Provide SS data for financial reports"""
        return [{
            'id': 'ss_payments',
            'title': 'Social Security Payments',
            'description': 'Monthly Social Security (autónomo) payments with dates and amounts.',
            'query_fn': self._get_ss_for_report
        }]

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

        return {
            'summary_columns': [],
            'breakdown_rows': [
                {'label': f'Social Security (€{ss_monthly:.2f}/month)', 'amount': ss_annual}
            ],
            'notes': [],
            'deductions': 0,
            'tax_total': ss_annual
        }
