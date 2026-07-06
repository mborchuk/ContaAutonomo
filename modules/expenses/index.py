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
            # F4 — must stay in sync with the core Expense model in app.py.
            net_amount = db.Column(db.Float)
            vat_rate = db.Column(db.Float)
            vat_amount = db.Column(db.Float)
            deductible = db.Column(db.Boolean, default=True)
            deductible_pct = db.Column(db.Float, default=100.0)
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

        class ExpensesConfig(db.Model):
            __tablename__ = 'expenses_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        self.Expense = Expense
        self.Contractor = Contractor
        self.Settings = Settings
        self.ExpensesConfig = ExpensesConfig
        # expenses_config is module-owned -> let the manager create it.
        return {'ExpensesConfig': ExpensesConfig}

    # F4 — seed category defaults (VAT rate % + deductible %). Shipped values;
    # user override stored as JSON in expenses_config['category_defaults'].
    SEED_CATEGORY_DEFAULTS = {
        'Office Supplies': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Software': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Equipment': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Services': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Professional Services': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Telecommunications': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Utilities': {'vat_rate': 21.0, 'deductible_pct': 100.0},
        'Travel': {'vat_rate': 10.0, 'deductible_pct': 100.0},
        'Insurance': {'vat_rate': 0.0, 'deductible_pct': 100.0},   # exempt
        'Social Security': {'vat_rate': 0.0, 'deductible_pct': 100.0},
    }

    def on_enable(self):
        """F4 — add VAT/deductibility columns to the expense table (idempotent)."""
        from sqlalchemy import inspect as sa_inspect, text
        migrations = [
            ('net_amount', 'FLOAT'),
            ('vat_rate', 'FLOAT'),
            ('vat_amount', 'FLOAT'),
            ('deductible', 'BOOLEAN'),
            ('deductible_pct', 'FLOAT'),
        ]
        try:
            inspector = sa_inspect(self._db.engine)
            cols = [c['name'] for c in inspector.get_columns('expense')]
            with self._db.engine.connect() as conn:
                for name, coltype in migrations:
                    if name not in cols:
                        conn.execute(text(
                            f'ALTER TABLE expense ADD COLUMN {name} {coltype}'))
                conn.commit()
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Expense VAT migration failed: %s', e)

    def get_category_defaults(self):
        """Merged category defaults: seed values overlaid with user overrides."""
        import json
        defaults = {k: dict(v) for k, v in self.SEED_CATEGORY_DEFAULTS.items()}
        try:
            row = self.ExpensesConfig.query.filter_by(key='category_defaults').first()
            if row and row.value:
                defaults.update(json.loads(row.value))
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading category defaults failed: %s', e)
        return defaults

    # --- Settings panel (F4-D3): edit category VAT/deductible defaults ---- #

    @property
    def settings_tab(self):
        return {'id': 'expenses', 'label': 'Expenses'}

    def get_settings_html(self, settings):
        import json
        current = json.dumps(self.get_category_defaults(), indent=2, ensure_ascii=False)
        return f'''
        <h3>Expense Category VAT Defaults</h3>
        <p style="font-size: 13px; color: var(--color-text-muted);">
            JSON map of category → default VAT rate (%) and deductible (%). Applied
            when picking a category on the expense form (never overwrites a field
            you already touched).
        </p>
        <div class="form-group">
            <textarea name="expense_category_defaults" rows="12"
                      style="width: 100%; font-family: monospace; font-size: 12px;">{current}</textarea>
        </div>
        '''

    def save_settings(self, settings, form):
        import json
        if 'expense_category_defaults' not in form:
            return  # guard: unrelated tab save
        raw = form.get('expense_category_defaults', '').strip()
        try:
            parsed = json.loads(raw) if raw else {}
            if not isinstance(parsed, dict):
                raise ValueError('must be a JSON object')
        except (ValueError, TypeError) as e:
            self.core.flash(f'Category defaults not saved (invalid JSON): {e}', 'danger')
            return
        row = self.ExpensesConfig.query.filter_by(key='category_defaults').first()
        if not row:
            row = self.ExpensesConfig(key='category_defaults')
            self._db.session.add(row)
        row.value = json.dumps(parsed, ensure_ascii=False)
        self._db.session.commit()

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
            # F4 — VAT breakdown & deductibility.
            'net_amount': e.net_amount,
            'vat_rate': e.vat_rate,
            'vat_amount': e.vat_amount,
            'deductible': e.deductible,
            'deductible_pct': e.deductible_pct,
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
            def _num(name):
                v = body.get(name)
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    raise ApiError(400, 'bad_request', f'{name} must be a number')
            exp = repo.create(
                amount=amount,
                currency=body.get('currency', 'EUR'),
                category=body.get('category'),
                description=body.get('description'),
                expense_date=exp_date,
                contractor_id=contractor_id,
                invoice_number=body.get('invoice_number'),
                notes=body.get('notes'),
                # F4 fields (all optional).
                net_amount=_num('net_amount'),
                vat_rate=_num('vat_rate'),
                vat_amount=_num('vat_amount'),
                deductible=bool(body.get('deductible', True)),
                deductible_pct=_num('deductible_pct') if body.get('deductible_pct') is not None else 100.0,
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

    @staticmethod
    def _parse_vat_fields(form, gross):
        """Build the VAT/deductibility kwargs from the submitted form.

        Gross is authoritative. If net/vat not supplied but a rate is, split
        gross by the rate (gross = net * (1 + rate)). Everything overridable.
        Returns a dict of expense fields (values may be None = unknown).
        """
        def _f(name):
            raw = form.get(name)
            if raw in (None, ''):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        net = _f('net_amount')
        vat_rate = _f('vat_rate')
        vat_amount = _f('vat_amount')

        if gross is not None and vat_rate is not None and net is None and vat_amount is None:
            # Derive net + VAT from gross + rate.
            net = round(gross / (1 + vat_rate / 100.0), 2) if vat_rate else gross
            vat_amount = round(gross - net, 2)
        elif gross is not None and net is not None and vat_amount is None:
            vat_amount = round(gross - net, 2)

        # Unchecked HTML checkboxes are omitted from the form, so absence = False.
        deductible = form.get('deductible') not in (None, '', 'false', '0', 'off')
        deductible_pct = _f('deductible_pct')
        if deductible_pct is None:
            deductible_pct = 100.0

        return {
            'net_amount': net,
            'vat_rate': vat_rate,
            'vat_amount': vat_amount,
            'deductible': deductible,
            'deductible_pct': deductible_pct,
        }

    def _create_expense(self):
        """Create a new expense"""
        repo = self._get_repo()
        settings_repo = self._get_settings_repo()

        if request.method == 'POST':
            try:
                file = request.files.get('file')
                gross = float(request.form['amount'])
                vat_fields = self._parse_vat_fields(request.form, gross)
                repo.create_with_file(
                    app=self.core.app,
                    file=file,
                    storage=self.core,
                    contractor_id=request.form.get('contractor_id') or None,
                    amount=gross,
                    currency=request.form.get('currency', 'EUR'),
                    category=request.form.get('category'),
                    description=request.form.get('description'),
                    expense_date=datetime.strptime(request.form['expense_date'], '%Y-%m-%d').date(),
                    invoice_number=request.form.get('invoice_number'),
                    notes=request.form.get('notes'),
                    **vat_fields
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
                             contractors=contractors, tracked_currencies=tracked_currencies,
                             category_defaults=self.get_category_defaults())

    def _edit_expense(self, id):
        """Edit an existing expense"""
        repo = self._get_repo()
        settings_repo = self._get_settings_repo()
        expense = repo.get_by_id(id)

        if request.method == 'POST':
            try:
                file = request.files.get('file')
                gross = float(request.form['amount'])
                vat_fields = self._parse_vat_fields(request.form, gross)
                repo.update_with_file(
                    app=self.core.app,
                    expense=expense,
                    file=file,
                    storage=self.core,
                    contractor_id=request.form.get('contractor_id') or None,
                    amount=gross,
                    currency=request.form.get('currency', 'EUR'),
                    category=request.form.get('category'),
                    description=request.form.get('description'),
                    expense_date=datetime.strptime(request.form['expense_date'], '%Y-%m-%d').date(),
                    invoice_number=request.form.get('invoice_number'),
                    notes=request.form.get('notes'),
                    **vat_fields
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
                             contractors=contractors, tracked_currencies=tracked_currencies,
                             category_defaults=self.get_category_defaults())

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
            vat_eur = None
            if expense.vat_amount is not None:
                vat_eur = self._convert_to(expense.vat_amount, expense.currency, 'EUR',
                                           expense.expense_date)
            result.append({
                'expense_date': expense.expense_date.strftime('%d/%m/%Y'),
                'invoice_number': expense.invoice_number or '',
                'contractor_name': contractors_map.get(expense.contractor_id, 'N/A'),
                'category': expense.category or 'N/A',
                'description': expense.description or '',
                'amount_eur': amount_eur,
                # F4 — VAT split (None on legacy rows).
                'vat_eur': vat_eur,
                'deductible_pct': expense.deductible_pct,
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

        default_vat_rate = ((context.get('settings').default_vat_rate or 21.0) / 100.0
                            if context.get('settings') and hasattr(context['settings'], 'default_vat_rate')
                            else 0.21)

        # Convert expenses to base currency. VAT paid (IVA soportado) now uses the
        # real per-expense vat_amount × deductible_pct when F4 data is present;
        # legacy rows (no vat_amount) fall back to the derived estimate and are
        # counted so the user knows the deducible figure is approximate.
        total_expenses = 0
        vat_paid = 0
        missing_vat_count = 0
        for expense in expenses_query:
            amount_base = self._convert_to(expense.amount, expense.currency,
                                           base_currency, expense.expense_date)
            total_expenses += amount_base
            if expense.vat_amount is not None:
                if getattr(expense, 'deductible', True):
                    vat_base = self._convert_to(expense.vat_amount, expense.currency,
                                                base_currency, expense.expense_date)
                    pct = (expense.deductible_pct if expense.deductible_pct is not None else 100.0)
                    vat_paid += vat_base * (pct / 100.0)
            else:
                missing_vat_count += 1
                vat_paid += amount_base * default_vat_rate  # legacy estimate

        vat_collected = context.get('vat_collected', 0)
        vat_to_pay = vat_collected - vat_paid

        notes = [
            f"VAT: Collected {context['currency_symbol']}{vat_collected:.2f} - Paid {context['currency_symbol']}{vat_paid:.2f}"
        ]
        if missing_vat_count:
            notes.append(
                f"{missing_vat_count} expense(s) lack VAT data — IVA soportado "
                f"estimated for those.")

        return {
            'summary_columns': [
                {'label': 'Expenses', 'value': total_expenses}
            ],
            'breakdown_rows': [
                {'label': 'VAT to Pay (IVA)', 'amount': vat_to_pay}
            ],
            'notes': notes,
            'deductions': total_expenses,
            'tax_total': vat_to_pay
        }
