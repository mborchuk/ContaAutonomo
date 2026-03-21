#!/usr/bin/env python3
"""
Reports Module
Generate professional financial reports (PDF) for tax authorities or banks.
"""

from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from datetime import datetime, date
import calendar
import io
import os
import importlib.util


class ReportsModule(BaseModule):

    @property
    def module_id(self):
        return 'reports'

    @property
    def name(self):
        return 'Reports'

    @property
    def description(self):
        return 'Generate professional financial reports (PDF) for tax authorities or banks'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Reports', 'endpoint': 'reports.reports_index', 'icon': '📊'}
        ]

    def register_models(self, db):
        self._db = db

        # Reports only needs Invoice and Settings from core
        class Invoice(db.Model):
            __tablename__ = 'invoice'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            amount_usd = db.Column(db.Float)
            amount_eur = db.Column(db.Float)
            currency = db.Column(db.String(10))
            exchange_rate = db.Column(db.Float)

        class Settings(db.Model):
            __tablename__ = 'settings'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)

        self.Invoice = Invoice
        self.Settings = Settings
        return {}

    def _get_available_sections(self):
        """Collect available report sections from enabled modules."""
        sections = [
            {'id': 'income', 'title': 'Income (Invoices)',
             'description': 'Invoice data: number, date, client, amount and status.'}
        ]
        mgr = self.core.module_manager
        if mgr:
            for s in mgr.get_report_sections():
                sections.append({
                    'id': s.get('id', ''),
                    'title': s.get('title', s.get('id', 'Unknown')),
                    'description': s.get('description', ''),
                })
        return sections

    def register_routes(self, app):
        bp = Blueprint('reports', __name__,
                       template_folder='templates',
                       url_prefix='/reports')
        login_required = self.core.login_required
        module = self
        self._app = app

        @bp.route('/')
        @login_required
        def reports_index():
            current_year = datetime.now().year
            available_sections = module._get_available_sections()
            settings = module.core.get_settings()
            base_currency = settings.base_currency if settings and settings.base_currency else 'EUR'
            return render_template('reports.html',
                                   current_year=current_year,
                                   available_sections=available_sections,
                                   base_currency=base_currency)

        @bp.route('/generate', methods=['POST'])
        @login_required
        def reports_generate():
            return module._generate_report()

        app.register_blueprint(bp)

    def _generate_report(self):
        """Generate a financial report PDF.

        Uses module_manager.get_report_sections() to collect data from any
        enabled module. User selects which sections to include via checkboxes.
        """
        from repositories import InvoiceRepository, SettingsRepository

        invoice_repo = InvoiceRepository(self._db, self.Invoice)
        settings_repo = SettingsRepository(self._db, self.Settings)

        try:
            selected_sections = request.form.getlist('sections')
            year = int(request.form.get('year', datetime.now().year))
            period_type = request.form.get('period_type', 'quarter')
            include_cancelled = request.form.get('include_cancelled') == '1'

            if not selected_sections:
                flash('Please select at least one section for the report', 'danger')
                return redirect(url_for('reports.reports_index'))

            quarters = []
            if period_type == 'quarter':
                quarters = [int(request.form.get('quarter', 1))]
            elif period_type == 'quarters':
                quarters = [int(q) for q in request.form.getlist('quarters')]
            else:
                quarters = [1, 2, 3, 4]

            if not quarters:
                flash('Please select at least one quarter', 'danger')
                return redirect(url_for('reports.reports_index'))

            start_month = (min(quarters) - 1) * 3 + 1
            end_month = max(quarters) * 3
            start_date = date(year, start_month, 1)
            last_day = calendar.monthrange(year, end_month)[1]
            end_date = date(year, end_month, last_day)

            # Income data (core — included if user selected 'income')
            income_data = []
            currency_mode = request.form.get('currency_mode', 'base')  # 'base' or 'original'
            settings = settings_repo.get_settings()
            base_currency = settings.base_currency if settings and settings.base_currency else 'EUR'

            if 'income' in selected_sections:
                invoices = invoice_repo.get_by_date_range(start_date, end_date, include_cancelled)
                for inv in invoices:
                    if currency_mode == 'original':
                        # Show amount in the invoice's own currency
                        inv_currency = getattr(inv, 'currency', None) or 'USD'
                        if inv_currency == 'EUR':
                            display_amount = inv.amount_eur
                        else:
                            display_amount = inv.amount_usd
                        display_currency = inv_currency
                    else:
                        # Convert everything to base currency
                        display_amount = inv.amount_eur if base_currency == 'EUR' else inv.amount_usd
                        display_currency = base_currency

                    income_data.append({
                        'invoice_number': inv.invoice_number,
                        'invoice_date': inv.invoice_date.strftime('%d/%m/%Y'),
                        'client_name': inv.client_name,
                        'amount': display_amount,
                        'currency': display_currency,
                        'amount_eur': inv.amount_eur,
                        'status': inv.status
                    })

            # Collect data from enabled modules for selected sections
            expenses_data = []
            ss_data = []
            extra_sections = []
            mgr = self.core.module_manager
            if mgr:
                for section in mgr.get_report_sections():
                    sid = section.get('id', '')
                    if sid not in selected_sections:
                        continue
                    query_fn = section.get('query_fn')
                    if not query_fn:
                        continue
                    # Known section types with dedicated rendering
                    if sid == 'expenses':
                        expenses_data = query_fn(start_date, end_date)
                    elif sid == 'ss_payments':
                        ss_data = query_fn(start_date, end_date)
                    else:
                        # Generic section
                        data = query_fn(start_date, end_date)
                        if data:
                            extra_sections.append({
                                'id': sid,
                                'title': section.get('title', sid),
                                'columns': section.get('columns'),
                                'data': data,
                                'total_field': section.get('total_field'),
                            })

            # Period text
            if period_type == 'year':
                period_text = f"Full Year {year}"
            elif len(quarters) == 1:
                period_text = f"Q{quarters[0]} {year}"
            else:
                quarters_str = ', '.join([f"Q{q}" for q in sorted(quarters)])
                period_text = f"{quarters_str} {year}"

            has_expenses = len(expenses_data) > 0
            has_income = len(income_data) > 0
            has_any_data = has_income or has_expenses or len(ss_data) > 0 or len(extra_sections) > 0

            # Build report title from selected sections
            if len(selected_sections) == 1 and selected_sections[0] == 'income':
                report_title = 'Income Report'
            elif len(selected_sections) == 1 and selected_sections[0] == 'expenses':
                report_title = 'Expenses Report'
            else:
                report_title = 'Financial Report'

            report_data = {
                'report_type': report_title,
                'period_text': period_text,
                'currency_mode': currency_mode,
                'base_currency': base_currency,
                'show_income': 'income' in selected_sections and has_income,
                'show_expenses': 'expenses' in selected_sections and has_expenses,
                'show_summary': has_any_data and len(selected_sections) > 1,
                'income_data': income_data,
                'expenses_data': expenses_data,
                'ss_data': ss_data,
                'extra_sections': extra_sections,
            }

            # Load report template
            template_name = settings.report_template if settings and settings.report_template else 'official_template'
            template_path = os.path.join(os.path.dirname(__file__), 'report_templates', f'{template_name}.py')

            spec = importlib.util.spec_from_file_location(template_name, template_path)
            template_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(template_module)

            buffer = io.BytesIO()
            template_module.generate_report(buffer, report_data, settings)

            filename = f"report_{period_text.replace(' ', '_')}.pdf"
            return send_file(
                buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )

        except Exception as e:
            flash(f'Error generating report: {str(e)}', 'danger')
            return redirect(url_for('reports.reports_index'))
