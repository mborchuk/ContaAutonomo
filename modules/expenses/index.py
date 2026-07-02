#!/usr/bin/env python3
"""
Expenses Module
Handles expense tracking with file uploads, contractor relationships,
multi-currency support, and category filtering.
"""

from module_manager import BaseModule
from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime
from repositories import ExpenseRepository, SettingsRepository
import os


class ExpensesModule(BaseModule):
    """Expense tracking and management"""

    @property
    def module_id(self):
        return 'expenses'

    @property
    def name(self):
        return 'Expenses'

    @property
    def description(self):
        return 'Track expenses with file uploads, contractor links, multi-currency support and category filtering'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Expenses', 'endpoint': 'expenses.expenses_index', 'icon': '💰'}
        ]

    def register_models(self, db):
        """
        Expense and Contractor models are defined in core (app.py).
        We reference them via extend_existing so the module can query them.
        """
        self._db = db

        class Expense(db.Model):
            __tablename__ = 'expense'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            contractor_id = db.Column(db.Integer, db.ForeignKey('contractor.id'))
            amount = db.Column(db.Float, nullable=False)
            currency = db.Column(db.String(10), default='EUR')
            category = db.Column(db.String(100))
            description = db.Column(db.Text)
            expense_date = db.Column(db.Date, nullable=False)
            file_path = db.Column(db.String(500))
            invoice_number = db.Column(db.String(100))
            notes = db.Column(db.Text)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        class Contractor(db.Model):
            __tablename__ = 'contractor'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(200), nullable=False)

        class Settings(db.Model):
            __tablename__ = 'settings'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)

        self.Expense = Expense
        self.Contractor = Contractor
        self.Settings = Settings
        return {}  # No new tables to create — all exist in core

    def register_routes(self, app):
        """Register expense routes"""
        bp = Blueprint(
            'expenses',
            __name__,
            template_folder='templates',
            url_prefix='/expenses'
        )

        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def expenses_index():
            return module._list_expenses()

        @bp.route('/create', methods=['GET', 'POST'])
        @login_required
        def expenses_create():
            return module._create_expense()

        @bp.route('/edit/<int:id>', methods=['GET', 'POST'])
        @login_required
        def expenses_edit(id):
            return module._edit_expense(id)

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def expenses_delete(id):
            return module._delete_expense(id)

        @bp.route('/file/<path:filename>')
        @login_required
        def expenses_file(filename):
            return module._serve_file(filename)

        @bp.route('/download/<int:id>')
        @login_required
        def expenses_download(id):
            return module._download_expense(id)

        @bp.route('/preview/<int:id>')
        @login_required
        def expenses_preview(id):
            return module._preview_expense(id)

        app.register_blueprint(bp)

    # --- Business Logic ---

    def _get_repo(self):
        return ExpenseRepository(self._db, self.Expense, self.Contractor)

    def _get_settings_repo(self):
        return SettingsRepository(self._db, self.Settings)

    # --- REST API (served under /api/v1/m/expenses/... by the api module) ---

    def get_api_routes(self):
        """Expose expenses over the REST API."""
        return [
            {'path': 'expenses', 'methods': ['GET', 'POST'],
             'handler': self._api_expenses,
             'summary': 'List expenses (filters: category, contractor_id, page, '
                        'per_page) or create one'},
            {'path': 'expenses/<int:exp_id>', 'methods': ['GET'],
             'handler': self._api_get_expense,
             'summary': 'Get one expense'},
        ]

    def _api_serialize_expense(self, e):
        return {
            'id': e.id,
            'contractor_id': e.contractor_id,
            'amount': e.amount,
            'currency': e.currency,
            'category': e.category,
            'description': e.description,
            'expense_date': e.expense_date.isoformat() if e.expense_date else None,
            'invoice_number': e.invoice_number,
        }

    def _api_expenses(self, request):
        from modules.api.index import ApiError  # lazy: only when API calls in
        repo = self._get_repo()

        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            try:
                amount = float(body['amount'])
            except (KeyError, TypeError, ValueError):
                raise ApiError(400, 'bad_request', 'amount (number) is required')
            date_str = body.get('expense_date')
            try:
                exp_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except (TypeError, ValueError):
                raise ApiError(400, 'bad_request', 'expense_date must be YYYY-MM-DD')
            contractor_id = body.get('contractor_id')
            if contractor_id is not None:
                try:
                    contractor_id = int(contractor_id)
                except (TypeError, ValueError):
                    raise ApiError(400, 'bad_request', 'contractor_id must be an integer')
            exp = repo.create(
                amount=amount,
                currency=body.get('currency', 'EUR'),
                category=body.get('category'),
                description=body.get('description'),
                expense_date=exp_date,
                contractor_id=contractor_id,
                invoice_number=body.get('invoice_number'),
                notes=body.get('notes'),
            )
            self.core.log_activity('expense_created', 'expense',
                                   {'id': exp.id, 'amount': amount,
                                    'currency': exp.currency, 'via': 'api'})
            return self._api_serialize_expense(exp), 201

        # GET — paginated list with optional filters
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = min(100, max(1, int(request.args.get('per_page', 20))))
        except ValueError:
            raise ApiError(400, 'bad_request', 'page/per_page must be integers')
        category = request.args.get('category')
        contractor_id = request.args.get('contractor_id', type=int)
        pagination = repo.get_paginated(page=page, per_page=per_page,
                                        contractor_id=contractor_id, category=category)
        return {
            'data': [self._api_serialize_expense(e) for e in pagination.items],
            'page': page, 'per_page': per_page, 'total': pagination.total,
        }, 200

    def _api_get_expense(self, request, exp_id=None):
        from modules.api.index import ApiError
        exp = self.Expense.query.get(exp_id)
        if not exp:
            raise ApiError(404, 'not_found', f'Expense #{exp_id} not found')
        return self._api_serialize_expense(exp), 200

    def _list_expenses(self):
        """List all expenses grouped by year and quarter"""
        repo = self._get_repo()

        contractor_id = request.args.get('contractor_id', type=int)
        category = request.args.get('category')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        expenses_by_year = repo.get_grouped_by_year(
            contractor_id=contractor_id,
            category=category,
            date_from=date_from,
            date_to=date_to
        )
        years = sorted(expenses_by_year.keys(), reverse=True) if expenses_by_year else []
        contractors = repo.get_all_contractors()
        categories = repo.get_unique_categories()

        # Module Expense model have no 'contractor' relationship (core backref
        # live on a different mapped class), so template cannot reach
        # expense.contractor. Build id->name map and let template look up by
        # contractor_id — same trick report code already use.
        contractors_map = {c.id: c.name for c in contractors}

        return render_template('expenses.html',
                             expenses_by_year=expenses_by_year,
                             years=years,
                             contractors=contractors,
                             contractors_map=contractors_map,
                             categories=categories)

    def _create_expense(self):
        """Create a new expense"""
        repo = self._get_repo()
        settings_repo = self._get_settings_repo()

        if request.method == 'POST':
            try:
                file = request.files.get('file')
                repo.create_with_file(
                    app=self.core.app,
                    file=file,
                    storage=self.core,
                    contractor_id=request.form.get('contractor_id') or None,
                    amount=float(request.form['amount']),
                    currency=request.form.get('currency', 'EUR'),
                    category=request.form.get('category'),
                    description=request.form.get('description'),
                    expense_date=datetime.strptime(request.form['expense_date'], '%Y-%m-%d').date(),
                    invoice_number=request.form.get('invoice_number'),
                    notes=request.form.get('notes')
                )
                flash('Expense created successfully!', 'success')
                return redirect(url_for('expenses.expenses_index'))
            except Exception as e:
                self._db.session.rollback()
                self.logger.error('Error creating expense: %s', e)
                flash('Error creating record. Please try again.', 'danger')

        contractors = repo.get_all_contractors()
        tracked_currencies = settings_repo.get_tracked_currencies()
        return render_template('expense_form.html', expense=None,
                             contractors=contractors, tracked_currencies=tracked_currencies)

    def _edit_expense(self, id):
        """Edit an existing expense"""
        repo = self._get_repo()
        settings_repo = self._get_settings_repo()
        expense = repo.get_by_id(id)

        if request.method == 'POST':
            try:
                file = request.files.get('file')
                repo.update_with_file(
                    app=self.core.app,
                    expense=expense,
                    file=file,
                    storage=self.core,
                    contractor_id=request.form.get('contractor_id') or None,
                    amount=float(request.form['amount']),
                    currency=request.form.get('currency', 'EUR'),
                    category=request.form.get('category'),
                    description=request.form.get('description'),
                    expense_date=datetime.strptime(request.form['expense_date'], '%Y-%m-%d').date(),
                    invoice_number=request.form.get('invoice_number'),
                    notes=request.form.get('notes')
                )
                flash('Expense updated successfully!', 'success')
                return redirect(url_for('expenses.expenses_index'))
            except Exception as e:
                self._db.session.rollback()
                self.logger.error('Error updating expense: %s', e)
                flash('Error processing form data. Please check your input.', 'danger')

        contractors = repo.get_all_contractors()
        tracked_currencies = settings_repo.get_tracked_currencies()
        return render_template('expense_form.html', expense=expense,
                             contractors=contractors, tracked_currencies=tracked_currencies)

    def _delete_expense(self, id):
        """Delete an expense"""
        repo = self._get_repo()
        try:
            expense = repo.get_by_id(id)
            repo.delete_with_file(self.core.app, expense, storage=self.core)
            flash('Expense deleted successfully!', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error deleting expense: %s', e)
            flash('Error deleting record. Please try again.', 'danger')
        return redirect(url_for('expenses.expenses_index'))

    def _serve_file(self, filename):
        """Serve expense files (legacy route, kept for backward compat).
        If filename looks like a GDrive file ID (no extension, no path separators),
        try using it directly as a storage key."""
        if '.' not in filename and '/' not in filename:
            # Likely a GDrive file ID stored in file_path
            if self.core.file_exists(filename):
                return self.core.send_file(filename)
        storage_key = os.path.join('expenses_files', filename)
        return self.core.send_file(storage_key)

    def _download_expense(self, id):
        """Download expense file using storage key from DB"""
        expense = self.Expense.query.get_or_404(id)
        if not expense.file_path:
            flash('No file attached to this expense.', 'danger')
            return redirect(url_for('expenses.expenses_index'))
        if not self.core.file_exists(expense.file_path):
            flash('File not found.', 'danger')
            return redirect(url_for('expenses.expenses_index'))
        name = expense.file_path.split('/')[-1] if '/' in expense.file_path else None
        return self.core.send_file(expense.file_path, download_name=name)

    def _preview_expense(self, id):
        """Preview expense file inline in the browser"""
        expense = self.Expense.query.get_or_404(id)
        if not expense.file_path:
            flash('No file attached to this expense.', 'danger')
            return redirect(url_for('expenses.expenses_index'))
        # For GDrive IDs (no '/'), pass None as filename — backend will resolve it
        filename = expense.file_path.split('/')[-1] if '/' in expense.file_path else None
        resp = self.core.preview_file(expense.file_path, filename)
        if not resp:
            flash('File not found.', 'danger')
            return redirect(url_for('expenses.expenses_index'))
        return resp

    # --- Report Integration ---

    def get_report_sections(self):
        """Provide expense data for financial reports"""
        return [{
            'id': 'expenses',
            'title': 'Expenses',
            'description': 'Expense records with contractor, category, invoice number and amounts in EUR.',
            'query_fn': self._get_expenses_for_report
        }]

    def _convert_to(self, amount, from_currency, to_currency, when=None):
        """Convert an amount between currencies via the shared CurrencyService.

        Falls back to the original amount (logged) instead of a hard-coded rate
        if the provider has no rate available, so reports are never silently wrong.
        """
        if not amount or from_currency == to_currency:
            return amount
        date_str = None
        if when is not None and hasattr(when, 'strftime'):
            date_str = when.strftime('%Y-%m-%d')
        try:
            converted, _rate, _actual_date = self.core.currency_service.convert(
                amount, from_currency, to_currency, date_str)
            if converted is not None:
                return converted
        except Exception as e:
            self.logger.warning('Currency conversion %s->%s failed: %s',
                                 from_currency, to_currency, e)
        self.logger.warning('No exchange rate for %s->%s; using unconverted amount',
                             from_currency, to_currency)
        return amount

    def _get_expenses_for_report(self, start_date, end_date):
        """Query expenses for a date range"""
        expenses = self.Expense.query.filter(
            self.Expense.expense_date >= start_date,
            self.Expense.expense_date <= end_date
        ).order_by(self.Expense.expense_date).all()

        # Pre-load contractors for efficiency
        contractor_ids = {e.contractor_id for e in expenses if e.contractor_id}
        contractors_map = {}
        if contractor_ids:
            contractors = self.Contractor.query.filter(
                self.Contractor.id.in_(contractor_ids)
            ).all()
            contractors_map = {c.id: c.name for c in contractors}

        result = []
        for expense in expenses:
            amount_eur = self._convert_to(expense.amount, expense.currency, 'EUR',
                                          expense.expense_date)
            result.append({
                'expense_date': expense.expense_date.strftime('%d/%m/%Y'),
                'invoice_number': expense.invoice_number or '',
                'contractor_name': contractors_map.get(expense.contractor_id, 'N/A'),
                'category': expense.category or 'N/A',
                'description': expense.description or '',
                'amount_eur': amount_eur
            })
        return result

    # --- Tax Obligations Integration ---

    def get_tax_obligations(self, context):
        """Contribute expenses and VAT data to Tax Obligations panel"""
        current_year = context['current_year']
        base_currency = context['base_currency']

        expenses_query = self.Expense.query.filter(
            self._db.extract('year', self.Expense.expense_date) == current_year
        ).all()

        # Convert expenses to base currency
        total_expenses = 0
        vat_paid = 0
        for expense in expenses_query:
            amount_base = self._convert_to(expense.amount, expense.currency,
                                           base_currency, expense.expense_date)
            total_expenses += amount_base
            vat_rate = (context.get('settings').default_vat_rate or 21.0) / 100.0 if context.get('settings') and hasattr(context['settings'], 'default_vat_rate') else 0.21
            vat_paid += amount_base * vat_rate

        vat_collected = context.get('vat_collected', 0)
        vat_to_pay = vat_collected - vat_paid

        return {
            'summary_columns': [
                {'label': 'Expenses', 'value': total_expenses}
            ],
            'breakdown_rows': [
                {'label': 'VAT to Pay (IVA)', 'amount': vat_to_pay}
            ],
            'notes': [
                f"VAT: Collected {context['currency_symbol']}{vat_collected:.2f} - Paid {context['currency_symbol']}{vat_paid:.2f}"
            ],
            'deductions': total_expenses,
            'tax_total': vat_to_pay
        }
