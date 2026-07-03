#!/usr/bin/env python3
"""
Tax ES Forms Module (F5) — Modelo 303 / 130 draft calculator.

CAVEMAN NOTE: this module DO NOT file anything and give NO tax advice. It just
add up numbers already in the app and lay them out in the boxes of Modelo 303
(IVA) and Modelo 130 (IRPF pago fraccionado), so user can copy them into AEAT
portal. Every page shout "estimate, not tax advice".

Pure math live in calculator.py; box numbers live in boxes.py (versioned).
This file only fetch data and render.
"""

from datetime import datetime

from flask import Blueprint, render_template, abort

from module_manager import BaseModule

from .boxes import (
    BOX_TABLE_VERSION,
    MODELO_303_BOXES,
    MODELO_130_BOXES,
)
from .calculator import (
    compute_modelo_303,
    compute_modelo_130,
    quarter_of,
)


class TaxEsFormsModule(BaseModule):
    """Modelo 303/130 quarterly draft calculator (estimate only)."""

    @property
    def module_id(self):
        return 'tax_es_forms'

    @property
    def name(self):
        return 'Modelo 303/130 Drafts'

    @property
    def description(self):
        return ('Quarterly Modelo 303 (IVA) and 130 (IRPF pago fraccionado) '
                'box-level drafts from your invoices and expenses. Estimate, '
                'not tax advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Tax Drafts', 'endpoint': 'tax_es_forms.drafts_index',
             'icon': '🧮', 'group': 'System'}
        ]

    def register_models(self, db):
        # No own tables — pure read/aggregate over core Invoice + Expense.
        self._db = db
        return {}

    # --- Rates & currency helpers ---------------------------------------- #

    def _rates(self):
        """Return (vat_rate, irpf_rate) as fractions, from Settings."""
        settings = self.core.get_settings()
        vat = (getattr(settings, 'default_vat_rate', None) or 21.0) / 100.0
        irpf = (getattr(settings, 'default_irpf_rate', None) or 20.0) / 100.0
        return vat, irpf

    def _convert_to_eur(self, amount, from_currency, when=None):
        """Convert an amount to EUR via the shared CurrencyService.

        Fall back to the raw amount (logged) when no rate is available, so a
        draft is never silently blank.
        """
        if not amount or from_currency == 'EUR':
            return amount or 0.0
        date_str = when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else None
        try:
            converted, _rate, _actual = self.core.currency_service.convert(
                amount, from_currency, 'EUR', date_str)
            if converted is not None:
                return converted
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Currency conversion %s->EUR failed: %s',
                                from_currency, e)
        self.logger.warning('No EUR rate for %s; using unconverted amount',
                            from_currency)
        return amount

    # --- Data fetching ---------------------------------------------------- #

    def _all_invoices(self):
        """Core invoices with the customer relationship intact."""
        return self.core.invoice_service.get_all()

    def _all_expenses(self):
        from app import Expense  # core model, avoids mapper duplication
        return Expense.query.all()

    def _invoices_in(self, year, quarter=None, up_to_quarter=None):
        out = []
        for inv in self._all_invoices():
            d = inv.invoice_date
            if not d or d.year != year:
                continue
            q = quarter_of(d)
            if quarter is not None and q != quarter:
                continue
            if up_to_quarter is not None and q > up_to_quarter:
                continue
            out.append(inv)
        return out

    def _expenses_in(self, year, quarter=None, up_to_quarter=None):
        out = []
        for exp in self._all_expenses():
            d = exp.expense_date
            if not d or d.year != year:
                continue
            q = quarter_of(d)
            if quarter is not None and q != quarter:
                continue
            if up_to_quarter is not None and q > up_to_quarter:
                continue
            out.append(exp)
        return out

    def _ytd_totals(self, year, up_to_quarter):
        """(income_eur, expenses_eur) cumulative through up_to_quarter.

        Expenses use the VAT-excluded net when F4 data is present (matches the
        130 engine), else gross — so the prior-payments estimate is consistent.
        """
        income = sum(inv.amount_eur or 0.0
                     for inv in self._invoices_in(year, up_to_quarter=up_to_quarter)
                     if getattr(inv, 'status', None) != 'cancelled')
        expenses = 0.0
        for exp in self._expenses_in(year, up_to_quarter=up_to_quarter):
            base = exp.net_amount if getattr(exp, 'net_amount', None) is not None else exp.amount
            expenses += self._convert_to_eur(base, exp.currency, exp.expense_date)
        return income, expenses

    # --- Computation entry points (shared by pages + API) ---------------- #

    def _draft_303(self, year, quarter):
        vat_rate, _ = self._rates()
        invoices = self._invoices_in(year, quarter=quarter)
        expenses = self._expenses_in(year, quarter=quarter)
        result = compute_modelo_303(invoices, expenses, vat_rate,
                                    convert_expense=self._convert_to_eur)
        result['labels'] = MODELO_303_BOXES
        result['year'] = year
        result['quarter'] = quarter
        result['table_version'] = BOX_TABLE_VERSION
        return result

    def _draft_130(self, year, quarter):
        _, irpf_rate = self._rates()
        invoices_ytd = self._invoices_in(year, up_to_quarter=quarter)
        expenses_ytd = self._expenses_in(year, up_to_quarter=quarter)
        prior_income, prior_expenses = (
            self._ytd_totals(year, quarter - 1) if quarter > 1 else (0.0, 0.0))
        result = compute_modelo_130(
            invoices_ytd, expenses_ytd, irpf_rate,
            prior_income=prior_income, prior_expenses=prior_expenses,
            convert_expense=self._convert_to_eur)
        result['labels'] = MODELO_130_BOXES
        result['year'] = year
        result['quarter'] = quarter
        result['table_version'] = BOX_TABLE_VERSION
        return result

    def _available_years(self):
        years = set()
        for inv in self._all_invoices():
            if inv.invoice_date:
                years.add(inv.invoice_date.year)
        for exp in self._all_expenses():
            if exp.expense_date:
                years.add(exp.expense_date.year)
        if not years:
            years.add(datetime.now().year)
        return sorted(years, reverse=True)

    # --- Routes ----------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('tax_es_forms', __name__,
                       template_folder='templates',
                       url_prefix='/tax-forms-draft')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def drafts_index():
            now = datetime.now()
            return render_template(
                'tax_forms_draft_index.html',
                years=module._available_years(),
                current_year=now.year,
                current_quarter=quarter_of(now.date()),
                table_version=BOX_TABLE_VERSION)

        @bp.route('/303/<int:year>/<int:quarter>')
        @login_required
        def draft_303(year, quarter):
            if quarter not in (1, 2, 3, 4):
                abort(404)
            draft = module._draft_303(year, quarter)
            return render_template('tax_forms_draft_303.html', draft=draft)

        @bp.route('/130/<int:year>/<int:quarter>')
        @login_required
        def draft_130(year, quarter):
            if quarter not in (1, 2, 3, 4):
                abort(404)
            draft = module._draft_130(year, quarter)
            return render_template('tax_forms_draft_130.html', draft=draft)

        app.register_blueprint(bp)

    # --- REST API (F5-D4) ------------------------------------------------- #

    def get_api_routes(self):
        return [
            {'path': 'draft/303/<int:year>/<int:quarter>', 'methods': ['GET'],
             'handler': self._api_draft_303,
             'summary': 'Modelo 303 (IVA) draft boxes for a quarter'},
            {'path': 'draft/130/<int:year>/<int:quarter>', 'methods': ['GET'],
             'handler': self._api_draft_130,
             'summary': 'Modelo 130 (IRPF) draft boxes for a quarter'},
        ]

    def _api_validate_quarter(self, quarter):
        from modules.api.index import ApiError
        if quarter not in (1, 2, 3, 4):
            raise ApiError(400, 'bad_request', 'quarter must be 1-4')

    def _api_draft_303(self, request, year=None, quarter=None):
        self._api_validate_quarter(quarter)
        return self._draft_303(year, quarter), 200

    def _api_draft_130(self, request, year=None, quarter=None):
        self._api_validate_quarter(quarter)
        return self._draft_130(year, quarter), 200

    # --- Dashboard Tax Obligations integration (F5-D4) ------------------- #

    def get_tax_obligations(self, context):
        """Show the current-quarter running 303/130 position on the panel.

        Display-only: deductions/tax_total stay 0 so this never disturbs the
        panel's grand total (the expenses/tax modules own that). Single source
        of truth for the figures is the same engine the draft pages use.
        """
        now = datetime.now()
        year = now.year
        quarter = quarter_of(now.date())
        symbol = context.get('currency_symbol', '€')

        d303 = self._draft_303(year, quarter)
        d130 = self._draft_130(year, quarter)
        iva_result = d303['boxes']['71']
        irpf_result = d130['boxes']['07']

        notes = [
            f"Modelo 303 Q{quarter} {year} (estimate): {symbol}{iva_result:.2f} — "
            f"Modelo 130 Q{quarter} {year} (estimate): {symbol}{irpf_result:.2f}. "
            f"Draft only, not tax advice."
        ]
        missing = d303['meta']['missing_expense_vat_count']
        if missing:
            notes.append(
                f"{missing} expense(s) this quarter lack VAT data — deductible "
                f"IVA understated until expense VAT breakdown is captured.")

        return {
            'summary_columns': [
                {'label': f'IVA Q{quarter} (303)', 'value': iva_result},
                {'label': f'IRPF Q{quarter} (130)', 'value': irpf_result},
            ],
            'breakdown_rows': [],
            'notes': notes,
            'deductions': 0,
            'tax_total': 0,
        }
