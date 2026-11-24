#!/usr/bin/env python3
"""
RETA Quota Advisor Module (F6) — project real-income bracket + regularization.

Replaces guesswork around the static `social_security_monthly` setting. It
takes year-to-date income minus expenses minus the generic deduction,
annualizes it, maps it onto the 15-bracket RETA table, and compares the
result with what the user actually pays to forecast the year-end
regularization. This is an estimate only, and the UI says so prominently.

Tax-panel integration is DISPLAY-ONLY (deductions=0, tax_total=0): the
tax_management module keep owning the SS money line, so no double counting.
"""

import json
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from contaautonomo.utils.constants import counts_as_income
from contaautonomo.core.module_manager import BaseModule

from .brackets import (BRACKET_TABLE_VERSION, GENERIC_DEDUCTION_PCT,
                       apply_values, brackets_for_year, contribution_rate_for_year,
                       fields as table_fields, validate as validate_table)
from .engine import project
from .yearly import confirmed_values, fallback_note, notice_html

from .table_checks import CHECK_RETA


class RetaAdvisorModule(BaseModule):
    """RETA quota advisor and regularization forecast (estimate only)."""

    @property
    def module_id(self):
        return 'reta_advisor'

    @property
    def name(self):
        return 'RETA Quota Advisor'

    @property
    def description(self):
        return ('Projects your RETA bracket from real income/expenses and '
                'forecasts the year-end regularization. Estimate, not advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def dependencies(self):
        # Actual Social Security payments (Tax Management provides them).
        return ['ss.payments_total']

    # --- Registry functions ------------------------------------------------ #

    def get_functions(self):
        from contaautonomo.core.functions import Function, Param
        return [Function(
            'reta.projection', self.projection,
            'RETA projection for the year of `today`: bracket, quota and the year-end '
            'regularization. The deltas and the quota are what-if changes; nothing '
            'is saved.',
            params=(Param('today', 'date', 'Reference day, default today'),
                    Param('income_delta', 'float', 'Added to year-to-date income, EUR'),
                    Param('expense_delta', 'float', 'Added to year-to-date expenses, EUR'),
                    Param('monthly_quota', 'float', 'Quota for the rest of the year, EUR')),
            returns='{"regularization_delta": amount (> 0 owed, < 0 refund), '
                    '"recommended_monthly_quota", "current_monthly_quota", '
                    '"contribution_rate_pct", ...}')]

    @property
    def nav_items(self):
        return [
            {'label': 'RETA Advisor', 'endpoint': 'reta_advisor.reta_index',
             'icon': '🪙', 'group': 'Taxes', 'order': 50}
        ]

    def register_models(self, db):
        self._db = db

        class RetaConfig(db.Model):
            __tablename__ = 'reta_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        self.Config = RetaConfig
        return {'RetaConfig': RetaConfig}

    # --- Config ------------------------------------------------------------- #

    def _get_config(self):
        out = {'deduction_pct': GENERIC_DEDUCTION_PCT}
        try:
            row = self.Config.query.filter_by(key='config').first()
            if row and row.value:
                out.update(json.loads(row.value))
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading reta config failed: %s', e)
        return out

    def _save_config(self, data):
        row = self.Config.query.filter_by(key='config').first()
        if not row:
            row = self.Config(key='config')
            self._db.session.add(row)
        row.value = json.dumps(data)
        self._db.session.commit()

    # --- Data --------------------------------------------------------------- #

    def _ytd_figures(self, today=None):
        """(income_eur, expenses_eur, months_elapsed) for the current year."""
        from contaautonomo.app import Invoice, Expense
        today = today or date.today()
        year = today.year

        income = 0.0
        for inv in Invoice.query.all():
            if (inv.invoice_date and inv.invoice_date.year == year
                    and inv.invoice_date <= today
                    and counts_as_income(inv)):
                income += inv.amount_eur or 0.0

        expenses = 0.0
        for exp in Expense.query.all():
            if (exp.expense_date and exp.expense_date.year == year
                    and exp.expense_date <= today):
                base = exp.net_amount if exp.net_amount is not None else exp.amount
                expenses += self._to_eur(base, exp.currency, exp.expense_date)

        return income, expenses, today.month

    def _to_eur(self, amount, currency, when=None):
        if not amount or currency == 'EUR':
            return amount or 0.0
        date_str = when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else None
        try:
            converted, _r, _d = self.core.currency_service.convert(
                amount, currency, 'EUR', date_str)
            if converted is not None:
                return converted
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Conversion %s->EUR failed: %s', currency, e)
        return amount

    def _paid_ytd(self, year):
        """Social Security recorded as paid in `year`, or None when nothing is
        recorded or nothing provides ss.payments_total."""
        functions = getattr(self.core, 'functions', None)
        if functions is None or not functions.available('ss.payments_total'):
            return None
        try:
            paid = functions.call('ss.payments_total', date_from=date(year, 1, 1),
                                  date_to=date(year, 12, 31))
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading SS payments failed: %s', type(e).__name__)
            return None
        return paid['total'] if paid['count'] else None

    def projection(self, today=None, income_delta=0.0, expense_delta=0.0,
                   monthly_quota=None):
        """The RETA projection for the year of `today` (see engine.project);
        `regularization_delta` > 0 is owed, < 0 is a refund.

        The what-if simulator passes changes: `income_delta` / `expense_delta`
        are added to the year-to-date figures and `monthly_quota` replaces the
        current quota for the rest of the year. Without them the result is the
        advisor's own. Reads only; nothing is saved. The result also carries
        `contribution_rate_pct` (base × rate = quota) and its table year.
        """
        return self._projection(today, income_delta, expense_delta, monthly_quota)

    def _projection(self, today=None, income_delta=0.0, expense_delta=0.0,
                    monthly_quota=None):
        today = today or date.today()
        income, expenses, months = self._ytd_figures(today)
        settings = self.core.get_settings()
        current_quota = (settings.social_security_monthly
                         if settings and settings.social_security_monthly else 0.0)
        if monthly_quota is not None:
            current_quota = monthly_quota
        cfg = self._get_config()
        result = project(
            income + income_delta, expenses + expense_delta, months,
            current_monthly_quota=current_quota,
            paid_ytd=self._paid_ytd(today.year),
            deduction_pct=float(cfg.get('deduction_pct', GENERIC_DEDUCTION_PCT)),
            year=today.year, table=self._brackets(today.year))
        rate_year, rate_pct, rate_fallback = self._rate(today.year)
        result.update(contribution_rate_pct=rate_pct, contribution_rate_year=rate_year,
                      contribution_rate_fallback=rate_fallback)
        return result

    # --- Yearly tables and the Tax Data module ------------------------------ #

    def _table(self, year):
        """(table_year, brackets, rate_pct, is_fallback): the year's tables,
        with the values the owner confirmed for it in Tax Data. A confirmed
        year is not a fallback."""
        table_year, brackets, fallback = brackets_for_year(year)
        _rate_year, rate, rate_fallback = contribution_rate_for_year(year)
        status = confirmed_values(getattr(self.core, 'functions', None), CHECK_RETA['id'], year)
        if status is None:
            return table_year, brackets, rate, fallback or rate_fallback
        brackets, rate = apply_values(brackets, rate, status.get('values') or {})
        return table_year, brackets, rate, False

    def _brackets(self, year):
        table_year, brackets, _rate, fallback = self._table(year)
        return table_year, brackets, fallback

    def _rate(self, year):
        rate_year, _rate, _fallback = contribution_rate_for_year(year)
        _ty, _brackets, rate, fallback = self._table(year)
        return rate_year, rate, fallback

    def get_capabilities(self):
        """The RETA table of a year, for the Tax Data module (`year_table`):
        the owner confirms it for a new year or enters the new values."""
        def action(year):
            table_year, brackets, fallback = brackets_for_year(year)
            _ry, rate, _rf = contribution_rate_for_year(year)
            return {'table_year': table_year, 'built_in': not fallback,
                    'fields': table_fields(brackets, rate), 'auto': None}

        def validate(year, values):
            _ty, brackets, _f = brackets_for_year(year)
            return validate_table(brackets, values)

        return [{'type': 'year_table', 'id': CHECK_RETA['id'], 'name': CHECK_RETA['name'],
                 'holds': CHECK_RETA['holds'], 'steps': CHECK_RETA['steps'],
                 'links': CHECK_RETA['links'], 'after_check': CHECK_RETA['after_check'],
                 'action': action, 'validate': validate}]

    # --- Routes ------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('reta_advisor', __name__,
                       template_folder='templates',
                       url_prefix='/reta-advisor')
        login_required = self.core.login_required
        module = self

        @bp.route('/', methods=['GET', 'POST'])
        @login_required
        def reta_index():
            if request.method == 'POST':
                try:
                    pct = float(request.form.get('deduction_pct', GENERIC_DEDUCTION_PCT))
                    if not 0 <= pct <= 100:
                        raise ValueError('deduction_pct out of range')
                    module._save_config({'deduction_pct': pct})
                    flash('Advisor settings saved.', 'success')
                except (TypeError, ValueError):
                    flash('Invalid deduction percentage.', 'danger')
                return redirect(url_for('reta_advisor.reta_index'))

            proj = module._projection()
            year = date.today().year
            _table_year, brackets, _fallback = module._brackets(year)
            notice = (notice_html(getattr(module.core, 'functions', None), CHECK_RETA['id'],
                                  year, proj['table_year'])
                      if proj['table_fallback'] else '')
            return render_template('reta_advisor.html',
                                   proj=proj,
                                   brackets=brackets,
                                   table_note=(fallback_note(proj['table_year'])
                                               if proj['table_fallback'] else ''),
                                   table_notice=notice,
                                   version=BRACKET_TABLE_VERSION,
                                   config=module._get_config(),
                                   current_year=datetime.now().year)

        app.register_blueprint(bp)

    # --- Tax panel (display-only, F6-D4 adapted) ----------------------------- #

    def get_tax_obligations(self, context):
        try:
            proj = self._projection()
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('RETA projection failed: %s', e)
            return None
        symbol = context.get('currency_symbol', '€')
        delta = proj['regularization_delta']
        direction = 'to pay' if delta > 0 else 'refund'
        notes = [
            f"RETA advisor (estimate): projected quota "
            f"{symbol}{proj['recommended_monthly_quota']:.2f}/month vs current "
            f"{symbol}{proj['current_monthly_quota']:.2f}/month — regularization "
            f"~{symbol}{abs(delta):.2f} {direction}. Table v{BRACKET_TABLE_VERSION}."
        ]
        if proj.get('table_fallback'):
            notes.append(f"RETA advisor: {fallback_note(proj['table_year'])}")
        return {
            'summary_columns': [],
            'breakdown_rows': [],
            'notes': notes,
            # Display-only: tax_management owns the SS money line.
            'deductions': 0,
            'tax_total': 0,
        }
