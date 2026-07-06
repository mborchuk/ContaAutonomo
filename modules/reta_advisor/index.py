#!/usr/bin/env python3
"""
RETA Quota Advisor Module (F6) — project real-income bracket + regularization.

CAVEMAN NOTE: replace guesswork around the static `social_security_monthly`
setting. Count YTD income − expenses − generic deduction, annualize, map onto
the 15-bracket RETA table, compare with what user actually pay → forecast the
year-end regularization. ESTIMATE ONLY, big letters.

Tax-panel integration is DISPLAY-ONLY (deductions=0, tax_total=0): the
tax_management module keep owning the SS money line, so no double counting.
"""

import json
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash

from module_manager import BaseModule

from .brackets import BRACKETS, BRACKET_TABLE_VERSION, GENERIC_DEDUCTION_PCT
from .engine import project


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
    def nav_items(self):
        return [
            {'label': 'RETA Advisor', 'endpoint': 'reta_advisor.reta_index',
             'icon': '🪙', 'group': 'System'}
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
        from app import Invoice, Expense
        today = today or date.today()
        year = today.year

        income = 0.0
        for inv in Invoice.query.all():
            if (inv.invoice_date and inv.invoice_date.year == year
                    and inv.invoice_date <= today
                    and inv.status != 'cancelled'):
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
        """Actual SS payments this year via tax_management, if enabled."""
        tm = self.core.module_manager.modules.get('tax_management')
        if not tm or not hasattr(tm, 'SSPayment'):
            return None
        try:
            payments = tm.SSPayment.query.filter(
                self._db.extract('year', tm.SSPayment.payment_date) == year).all()
            return sum(p.amount for p in payments) if payments else None
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading SS payments failed: %s', e)
            return None

    def _projection(self, today=None):
        today = today or date.today()
        income, expenses, months = self._ytd_figures(today)
        settings = self.core.get_settings()
        current_quota = (settings.social_security_monthly
                         if settings and settings.social_security_monthly else 0.0)
        cfg = self._get_config()
        return project(
            income, expenses, months,
            current_monthly_quota=current_quota,
            paid_ytd=self._paid_ytd(today.year),
            deduction_pct=float(cfg.get('deduction_pct', GENERIC_DEDUCTION_PCT)))

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
            return render_template('reta_advisor.html',
                                   proj=proj,
                                   brackets=BRACKETS,
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
        return {
            'summary_columns': [],
            'breakdown_rows': [],
            'notes': notes,
            # Display-only: tax_management owns the SS money line.
            'deductions': 0,
            'tax_total': 0,
        }
