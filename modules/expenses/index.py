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

        return render_template('expenses.html',
                             expenses_by_year=expenses_by_year,
                             years=years,
                             contractors=contractors,
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
                flash(f'Error creating expense: {str(e)}', 'danger')

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
                flash(f'Error updating expense: {str(e)}', 'danger')

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
            flash(f'Error deleting expense: {str(e)}', 'danger')
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
            amount_eur = expense.amount
            if expense.currency != 'EUR':
                if expense.currency == 'USD':
                    amount_eur = expense.amount * 0.92
                elif expense.currency == 'GBP':
                    amount_eur = expense.amount * 1.17
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
        exchange_rates = context['exchange_rates']

        expenses_query = self.Expense.query.filter(
            self._db.extract('year', self.Expense.expense_date) == current_year
        ).all()

        # Convert expenses to base currency
        total_expenses = 0
        vat_paid = 0
        for expense in expenses_query:
            amount_base = expense.amount
            if expense.currency != base_currency:
                if expense.currency == 'EUR' and base_currency == 'USD':
                    amount_base = expense.amount * exchange_rates.get('EUR', 1.0)
                elif expense.currency == 'USD' and base_currency == 'EUR':
                    eur_rate = exchange_rates.get('EUR', 1.0)
                    amount_base = expense.amount / eur_rate if eur_rate > 0 else expense.amount
                # else: approximate as-is
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
