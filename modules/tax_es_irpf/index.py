#!/usr/bin/env python3
"""Tax ES IRPF Module — Modelo 130 + Modelo 100 estimator.

Caveman summary: this module guess two numbers for Spanish autónomo.
  - Modelo 130 = how much IRPF to prepay this quarter (cumulative YTD).
  - Modelo 100 = what final year IRPF look like after prepays + retenciones.

Both modelos live in ONE module because they share the same money path
(net profit, expense handling, 5% difícil justificación cap). The actual math
is in `engine.py` — pure, no Flask/DB — so it is easy to test. This file only
does the I/O: pull numbers from invoices/expenses/SS, call the engine, render.

EVERY figure is an ESTIMATE, not tax advice. Confirm with AEAT before filing.

It complements `tax_management` (which stores uploaded Modelo PDFs); this one
adds the calculation. Region scales + rates are year-versioned JSON data, never
hardcoded — see params/ and engine.py.
"""

import json
from datetime import date, datetime

from flask import Blueprint, render_template, request

from module_manager import BaseModule

from .engine import (
    IrpfEngine,
    Modelo100Input,
    Modelo130Input,
    default_retencion_rate,
    minimo_for_region,
)
from .params_store import available_years, load_params

# Quarter → last calendar day (month, day), used for cumulative YTD windows.
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


class TaxEsIrpfModule(BaseModule):
    """Spanish IRPF estimator: Modelo 130 (quarterly) + Modelo 100 (annual)."""

    @property
    def module_id(self):
        return 'tax_es_irpf'

    @property
    def name(self):
        return 'IRPF Estimator (España)'

    @property
    def description(self):
        return ('Estimate Modelo 130 (quarterly) and Modelo 100 (annual) IRPF '
                'for Spanish autónomos in estimación directa simplificada. '
                'Estimate only — not tax advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'IRPF Estimator', 'endpoint': 'tax_es_irpf.index',
             'icon': '🧮', 'group': 'Taxes'}
        ]

    @property
    def settings_tab(self):
        return {'id': 'irpf_es', 'label': 'IRPF (España)'}

    # ── Models ───────────────────────────────────────────────────────────

    def register_models(self, db):
        self._db = db

        class IrpfProfile(db.Model):
            __tablename__ = 'irpfes_profile'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            region = db.Column(db.String(40), default='valenciana')
            regime = db.Column(db.String(20), default='eds')  # estimación directa simpl.
            is_professional = db.Column(db.Boolean, default=True)
            start_date = db.Column(db.Date)  # alta date — proration + 7%/15% window
            age = db.Column(db.Integer, default=0)
            descendientes = db.Column(db.Integer, default=0)
            descendientes_menor3 = db.Column(db.Integer, default=0)
            ascendientes65 = db.Column(db.Integer, default=0)
            ascendientes75 = db.Column(db.Integer, default=0)
            uses_mutualidad = db.Column(db.Boolean, default=False)
            low_income_deduction = db.Column(db.Boolean, default=False)
            # 5% difícil justificación inside M130 box 02 (default ON per AEAT).
            apply_dificil_justif_m130 = db.Column(db.Boolean, default=True)
            eds_eligible = db.Column(db.Boolean, default=True)  # prior INCN ≤ limit
            inicio_reduction_eligible = db.Column(db.Boolean, default=False)
            regional_deduction_inputs_json = db.Column(db.Text, default='{}')
            declaration_mode = db.Column(db.String(20), default='individual')
            family_unit = db.Column(db.String(20), default='biparental')
            spouse_income = db.Column(db.Float, default=0.0)
            spouse_deductible_expenses = db.Column(db.Float, default=0.0)
            spouse_retenciones = db.Column(db.Float, default=0.0)
            spouse_pagos_fraccionados = db.Column(db.Float, default=0.0)

        class IrpfQuarter(db.Model):
            __tablename__ = 'irpfes_quarter'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            year = db.Column(db.Integer, nullable=False)
            quarter = db.Column(db.Integer, nullable=False)
            income = db.Column(db.Float, default=0.0)
            expenses = db.Column(db.Float, default=0.0)
            dificil_just = db.Column(db.Float, default=0.0)
            retenciones = db.Column(db.Float, default=0.0)
            payment_due = db.Column(db.Float, default=0.0)
            computed_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.IrpfProfile = IrpfProfile
        self.IrpfQuarter = IrpfQuarter
        return {'IrpfProfile': IrpfProfile, 'IrpfQuarter': IrpfQuarter}

    # Core models are read lazily (like tax_poland imports Settings) to avoid
    # redefining mapped classes that already live in app.py / tax_management.

    @property
    def Invoice(self):
        from app import Invoice
        return Invoice

    @property
    def Expense(self):
        from app import Expense
        return Expense

    def _ss_model(self):
        """tax_management's SSPayment model, if that module is enabled."""
        mm = getattr(self.core, 'module_manager', None)
        if mm is None:
            return None
        tm = mm.modules.get('tax_management')
        return getattr(tm, 'SSPayment', None) if tm else None

    def on_enable(self):
        """Idempotent guarded migrations: new columns on invoice + expense."""
        from sqlalchemy import inspect as sa_inspect, text

        migrations = {
            'invoice': [
                ('irpf_retention_pct', 'FLOAT DEFAULT 0'),
                ('irpf_retention_amount', 'FLOAT DEFAULT 0'),
            ],
            'expense': [
                ('deductible', 'BOOLEAN DEFAULT 1'),
                ('deductible_pct', 'FLOAT DEFAULT 100'),
            ],
            'irpfes_profile': [
                ('apply_dificil_justif_m130', 'BOOLEAN DEFAULT 1'),
            ],
        }
        inspector = sa_inspect(self._db.engine)
        for table, cols in migrations.items():
            try:
                existing = {c['name'] for c in inspector.get_columns(table)}
            except Exception as e:  # table not present yet — skip quietly
                self.logger.warning('IRPF migrate: cannot inspect %s: %s', table, e)
                continue
            for col, typedef in cols:
                if col in existing:
                    continue
                try:
                    with self._db.engine.connect() as conn:
                        conn.execute(text(
                            f'ALTER TABLE {table} ADD COLUMN {col} {typedef}'))
                        conn.commit()
                    self.logger.info('IRPF migrate: added %s.%s', table, col)
                except Exception as e:
                    self.logger.error('IRPF migrate: %s.%s failed: %s', table, col, e)

    # ── Profile + params helpers ─────────────────────────────────────────

    def _get_profile(self):
        profile = self.IrpfProfile.query.first()
        if not profile:
            profile = self.IrpfProfile()
            self._db.session.add(profile)
            self._db.session.commit()
        return profile

    def _profile_dict(self, profile):
        """Plain dict for the pure engine's mínimo helper."""
        return {
            'age': profile.age or 0,
            'descendientes': profile.descendientes or 0,
            'descendientes_menor3': profile.descendientes_menor3 or 0,
            'ascendientes65': profile.ascendientes65 or 0,
            'ascendientes75': profile.ascendientes75 or 0,
        }

    def _params(self, year):
        override = None  # DB param override hook — not wired in v1 UI yet
        return load_params(year, override=override)

    def _engine(self, year):
        return IrpfEngine(self._params(year))

    # ── Data collectors (DB → plain numbers for the engine) ──────────────

    def _year_start(self, year, profile):
        """1 Jan, or alta date if registered mid-year (proration)."""
        start = date(year, 1, 1)
        if profile.start_date and profile.start_date.year == year:
            return max(start, profile.start_date)
        return start

    def _sum_income(self, year, profile, end_dt):
        """Sum paid-invoice income (EUR) and IRPF retenciones in a window."""
        start_dt = self._year_start(year, profile)
        invoices = self.Invoice.query.filter(
            self.Invoice.status == 'paid',
            self.Invoice.invoice_date >= start_dt,
            self.Invoice.invoice_date <= end_dt,
        ).all()
        income = sum((inv.amount_eur or 0.0) for inv in invoices)
        retenciones = 0.0
        for inv in invoices:
            amount = getattr(inv, 'irpf_retention_amount', None)
            pct = getattr(inv, 'irpf_retention_pct', None)
            if amount:
                retenciones += amount
            elif pct:
                retenciones += (inv.amount_eur or 0.0) * (pct / 100.0)
        return income, retenciones

    def _sum_expenses(self, year, profile, end_dt):
        """Sum deductible expenses (EUR) in a window, honouring deductible_pct."""
        start_dt = self._year_start(year, profile)
        expenses = self.Expense.query.filter(
            self.Expense.expense_date >= start_dt,
            self.Expense.expense_date <= end_dt,
        ).all()
        total = 0.0
        for exp in expenses:
            deductible = getattr(exp, 'deductible', True)
            if deductible is False:
                continue
            pct = getattr(exp, 'deductible_pct', None)
            pct = 100.0 if pct is None else pct
            amount_eur = self._to_eur(exp.amount or 0.0, exp.currency or 'EUR',
                                      exp.expense_date)
            total += amount_eur * (pct / 100.0)
        return total

    def _sum_reta(self, year, profile, end_dt):
        """RETA (Seguridad Social) paid in window — 100% deductible expense.

        Falls back to Settings.social_security_monthly × elapsed months when no
        SSPayment rows exist. Mutualidad alternativa caps the deduction.
        """
        start_dt = self._year_start(year, profile)
        ss_model = self._ss_model()
        reta = 0.0
        if ss_model is not None:
            payments = ss_model.query.filter(
                ss_model.payment_date >= start_dt,
                ss_model.payment_date <= end_dt,
            ).all()
            reta = sum((p.amount or 0.0) for p in payments)
        if reta == 0:
            settings = self.core.get_settings()
            monthly = getattr(settings, 'social_security_monthly', 0.0) or 0.0
            months = end_dt.month - start_dt.month + 1
            reta = monthly * max(0, months)
        if profile.uses_mutualidad:
            cap = self._params(year)['reta'].get('mutualidad_deduction_cap')
            if cap is not None:
                reta = min(reta, cap)
        return reta

    def _to_eur(self, amount, currency, when):
        if not amount or currency == 'EUR':
            return amount
        date_str = when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else None
        try:
            converted, _rate, _d = self.core.currency_service.convert(
                amount, currency, 'EUR', date_str)
            if converted is not None:
                return converted
        except Exception as e:
            self.logger.warning('IRPF currency %s->EUR failed: %s', currency, e)
        return amount

    def _regional_deductions_amount(self, year, profile):
        """Sum data-driven autonomic deductions for the profile's region."""
        params = self._params(year)
        region = profile.region or params.get('default_region', 'valenciana')
        deductions = params.get('regional_deductions', {}).get(region, [])
        try:
            inputs = json.loads(profile.regional_deduction_inputs_json or '{}')
        except (ValueError, TypeError):
            inputs = {}
        total = 0.0
        for d in deductions:
            if d.get('type') != 'percent_of':
                continue
            value = float(inputs.get(d['input'], 0) or 0)
            if value <= 0:
                continue
            amount = value * d['pct']
            if d.get('max') is not None:
                amount = min(amount, d['max'])
            total += amount
        return total

    def _build_modelo130_input(self, year, quarter, profile):
        month, day = _QUARTER_END[quarter]
        end_dt = date(year, month, day)
        income, retenciones = self._sum_income(year, profile, end_dt)
        expenses = self._sum_expenses(year, profile, end_dt)
        expenses += self._sum_reta(year, profile, end_dt)

        prior = 0.0
        for q in range(1, quarter):
            prior_input = self._build_modelo130_input(year, q, profile)
            prior += self._engine(year).compute_modelo130(
                prior_input).summary['modelo130_due']

        return Modelo130Input(
            year=year,
            quarter=quarter,
            ytd_income=income,
            ytd_deductible_expenses=expenses,
            ytd_retenciones=retenciones,
            prior_payments=prior,
            eds_eligible=bool(profile.eds_eligible),
            apply_dificil_justif=bool(profile.apply_dificil_justif_m130),
            low_income_deduction=bool(profile.low_income_deduction),
        )

    def _build_modelo100_input(self, year, profile):
        end_dt = date(year, 12, 31)
        income, retenciones = self._sum_income(year, profile, end_dt)
        expenses = self._sum_expenses(year, profile, end_dt)
        expenses += self._sum_reta(year, profile, end_dt)

        pagos = 0.0
        for q in range(1, 5):
            q_input = self._build_modelo130_input(year, q, profile)
            pagos += self._engine(year).compute_modelo130(
                q_input).summary['modelo130_due']

        region = profile.region or self._params(year).get('default_region', 'valenciana')
        minimo_estatal, minimo_autonomico = minimo_for_region(
            self._profile_dict(profile), self._params(year), region)

        return Modelo100Input(
            year=year,
            income=income,
            deductible_expenses=expenses,
            minimo_estatal=minimo_estatal,
            minimo_autonomico=minimo_autonomico,
            retenciones=retenciones,
            pagos_fraccionados=pagos,
            eds_eligible=bool(profile.eds_eligible),
            inicio_reduction_eligible=bool(profile.inicio_reduction_eligible),
            regional_deductions_amount=self._regional_deductions_amount(year, profile),
            declaration_mode=profile.declaration_mode or 'individual',
            family_unit=profile.family_unit or 'biparental',
            spouse_income=profile.spouse_income or 0.0,
            spouse_deductible_expenses=profile.spouse_deductible_expenses or 0.0,
            spouse_retenciones=profile.spouse_retenciones or 0.0,
            spouse_pagos_fraccionados=profile.spouse_pagos_fraccionados or 0.0,
        )

    def _current_quarter(self, today=None):
        today = today or date.today()
        return (today.month - 1) // 3 + 1

    # ── Routes ───────────────────────────────────────────────────────────

    def register_routes(self, app):
        bp = Blueprint('tax_es_irpf', __name__,
                       template_folder='templates', url_prefix='/irpf-es')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def index():
            year = request.args.get('year', type=int) or date.today().year
            return module._render_overview(year)

        @bp.route('/modelo130/<int:year>/<int:quarter>')
        @login_required
        def modelo130(year, quarter):
            return module._render_modelo130(year, quarter)

        @bp.route('/modelo100/<int:year>')
        @login_required
        def modelo100(year):
            return module._render_modelo100(year)

        app.register_blueprint(bp)

    def _render_overview(self, year):
        profile = self._get_profile()
        region = profile.region or 'valenciana'
        quarter = self._current_quarter()
        try:
            m130 = self._engine(year).compute_modelo130(
                self._build_modelo130_input(year, quarter, profile))
            m100 = self._engine(year).compute_modelo100(
                self._build_modelo100_input(year, profile), region)
            error = None
        except ValueError as e:  # e.g. foral region
            m130 = m100 = None
            error = str(e)
        return render_template(
            'irpf_overview.html', year=year, quarter=quarter, region=region,
            m130=m130.as_dict() if m130 else None,
            m100=m100.as_dict() if m100 else None,
            years=available_years(), error=error)

    def _render_modelo130(self, year, quarter):
        profile = self._get_profile()
        result = self._engine(year).compute_modelo130(
            self._build_modelo130_input(year, quarter, profile))
        return render_template('irpf_detail.html', title=f'Modelo 130 — {quarter}T {year}',
                               result=result.as_dict(), year=year)

    def _render_modelo100(self, year):
        profile = self._get_profile()
        region = profile.region or 'valenciana'
        try:
            recommendation = self._engine(year).recommend_declaration(
                self._build_modelo100_input(year, profile), region)
        except ValueError as e:
            return render_template('irpf_detail.html', title=f'Modelo 100 — {year}',
                                   result=None, year=year, error=str(e))
        return render_template('irpf_modelo100.html', title=f'Modelo 100 — {year}',
                               year=year, recommendation=recommendation)

    # ── Dashboard / tax hooks ────────────────────────────────────────────

    def get_tax_obligations(self, context):
        """Contribute the current quarter Modelo 130 estimate to the panel."""
        year = context['current_year']
        symbol = context.get('currency_symbol', '€')
        try:
            profile = self._get_profile()
            quarter = self._current_quarter()
            result = self._engine(year).compute_modelo130(
                self._build_modelo130_input(year, quarter, profile))
        except Exception as e:
            self.logger.error('IRPF tax_obligations failed: %s', e)
            return None
        due = result.summary['modelo130_due']
        return {
            'summary_columns': [
                {'label': f'Modelo 130 ({quarter}T)', 'value': due},
            ],
            'breakdown_rows': [
                {'label': f'IRPF pago fraccionado {quarter}T (estimate)', 'amount': due},
            ],
            'notes': [
                f'Modelo 130 {quarter}T estimate: {symbol}{due:.2f}. '
                'Estimate only — confirm with AEAT.'
            ],
            'deductions': 0,        # do not perturb the dashboard's taxable income
            'tax_total': 0,         # 130 is a prepayment, not an extra obligation
        }

    def calculate_income_tax(self, context):
        """Replace default IRPF with the Modelo 100 annual estimate."""
        year = date.today().year
        try:
            profile = self._get_profile()
            region = profile.region or 'valenciana'
            result = self._engine(year).compute_modelo100(
                self._build_modelo100_input(year, profile), region)
        except Exception as e:
            self.logger.error('IRPF calculate_income_tax failed: %s', e)
            return None

        summary = result.summary
        # Map the engine's line list onto the dashboard's breakdown shape.
        breakdown = []
        for line in result.lines:
            breakdown.append({
                'bracket': line['label'],
                'rate': '',
                'amount': line['amount'],
                'tax': line['amount'],
                'active': bool(line.get('total')),
            })
        verb = 'a ingresar' if summary['a_ingresar'] else 'a devolver'
        return {
            'income_tax': max(0.0, summary['cuota_liquida']),
            'irpf_breakdown': breakdown,
            'label': f'IRPF Modelo 100 estimate ({verb})',
        }

    def get_report_sections(self):
        return [
            {'id': 'irpf_modelo130', 'title': 'Modelo 130 (1T–4T) estimate',
             'description': 'Quarterly IRPF fractional-payment estimate per quarter.',
             'query_fn': self._report_modelo130},
            {'id': 'irpf_modelo100', 'title': 'Modelo 100 (annual) estimate',
             'description': 'Annual IRPF settlement estimate, line by line.',
             'query_fn': self._report_modelo100},
        ]

    def _report_modelo130(self, start_date, end_date):
        profile = self._get_profile()
        year = start_date.year
        rows = []
        for quarter in range(1, 5):
            result = self._engine(year).compute_modelo130(
                self._build_modelo130_input(year, quarter, profile))
            s = result.summary
            rows.append({
                'quarter': f'{quarter}T',
                'rendimiento_neto': round(s['rendimiento_neto_ytd'], 2),
                'payment_due': round(s['modelo130_due'], 2),
            })
        return rows

    def _report_modelo100(self, start_date, end_date):
        profile = self._get_profile()
        year = start_date.year
        region = profile.region or 'valenciana'
        result = self._engine(year).compute_modelo100(
            self._build_modelo100_input(year, profile), region)
        return [{'label': line['label'], 'amount': round(line['amount'], 2)}
                for line in result.lines]

    # ── Invoice form: capture retención ──────────────────────────────────

    def get_create_form_html(self):
        profile = self._get_profile()
        params = self._params(date.today().year)
        alta_year = profile.start_date.year if profile.start_date else date.today().year
        rate = default_retencion_rate(
            is_professional=bool(profile.is_professional),
            alta_year=alta_year, invoice_year=date.today().year, params=params)
        return (
            '<div class="form-group">'
            '<label for="irpf_retention_pct">IRPF retención % (estimate)</label>'
            f'<input type="number" step="0.01" min="0" max="100" '
            f'id="irpf_retention_pct" name="irpf_retention_pct" '
            f'value="{rate * 100:.0f}">'
            '<small>Pre-filled from your alta date; override per invoice '
            '(e.g. 0 for private clients).</small></div>'
        )

    def on_invoice_created(self, invoice, request):
        self._store_retention(invoice, request)

    def get_edit_form_html(self, invoice):
        current = getattr(invoice, 'irpf_retention_pct', 0) or 0
        return (
            '<div class="form-group">'
            '<label for="irpf_retention_pct">IRPF retención % (estimate)</label>'
            f'<input type="number" step="0.01" min="0" max="100" '
            f'id="irpf_retention_pct" name="irpf_retention_pct" value="{current}">'
            '</div>'
        )

    def on_invoice_updated(self, invoice, request):
        self._store_retention(invoice, request)

    def _store_retention(self, invoice, request):
        raw = request.form.get('irpf_retention_pct')
        if raw is None or raw == '':
            return
        try:
            pct = float(raw)
        except (TypeError, ValueError):
            return
        try:
            inv = self.Invoice.query.get(invoice.id)
            if inv is None:
                return
            inv.irpf_retention_pct = pct
            inv.irpf_retention_amount = (inv.amount_eur or 0.0) * (pct / 100.0)
            self._db.session.commit()
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('IRPF store retención failed: %s', e)

    # ── Settings tab ─────────────────────────────────────────────────────

    def get_settings_html(self, settings):
        profile = self._get_profile()
        params = self._params(date.today().year)
        regions = [r for r in params.get('regional_scales', {}) if r != 'supletoria']
        options = ''.join(
            f'<option value="{r}"{" selected" if profile.region == r else ""}>'
            f'{r.title()}</option>' for r in regions)
        low_income = 'checked' if profile.low_income_deduction else ''
        inicio = 'checked' if profile.inicio_reduction_eligible else ''
        mutualidad = 'checked' if profile.uses_mutualidad else ''
        dificil = 'checked' if profile.apply_dificil_justif_m130 else ''
        start = profile.start_date.isoformat() if profile.start_date else ''
        return f'''
        <h3>🧮 IRPF Estimator (España)</h3>
        <p style="color:#666;">Estimate only — not tax advice. Confirm with AEAT.</p>
        <div class="form-group">
            <label for="irpf_region">Comunidad autónoma</label>
            <select id="irpf_region" name="irpf_region">{options}</select>
        </div>
        <div class="form-group">
            <label for="irpf_start_date">Alta date (start of activity)</label>
            <input type="date" id="irpf_start_date" name="irpf_start_date" value="{start}">
        </div>
        <div class="form-group">
            <label for="irpf_age">Age</label>
            <input type="number" id="irpf_age" name="irpf_age" min="0" value="{profile.age or 0}">
        </div>
        <div class="form-group">
            <label for="irpf_descendientes">Descendientes (children)</label>
            <input type="number" id="irpf_descendientes" name="irpf_descendientes" min="0" value="{profile.descendientes or 0}">
        </div>
        <div class="form-group">
            <label><input type="checkbox" name="irpf_inicio" {inicio}> Eligible for 20% inicio-actividad reduction</label>
        </div>
        <div class="form-group">
            <label><input type="checkbox" name="irpf_dificil_m130" {dificil}> Apply 5% difícil justificación inside Modelo 130 box 02 (default ON, per AEAT)</label>
        </div>
        <div class="form-group">
            <label><input type="checkbox" name="irpf_low_income" {low_income}> Apply art. 110.3.c low-income deduction (Modelo 130)</label>
        </div>
        <div class="form-group">
            <label><input type="checkbox" name="irpf_mutualidad" {mutualidad}> Uses mutualidad alternativa (cap RETA deduction)</label>
        </div>
        '''

    def save_settings(self, settings, form):
        if 'irpf_region' not in form:
            return
        profile = self._get_profile()
        profile.region = form.get('irpf_region', 'valenciana')
        start = form.get('irpf_start_date', '')
        if start:
            try:
                profile.start_date = datetime.strptime(start, '%Y-%m-%d').date()
            except ValueError:
                pass
        for field_name, attr in [('irpf_age', 'age'),
                                 ('irpf_descendientes', 'descendientes')]:
            try:
                setattr(profile, attr, int(form.get(field_name, 0) or 0))
            except (TypeError, ValueError):
                pass
        profile.inicio_reduction_eligible = 'irpf_inicio' in form
        profile.low_income_deduction = 'irpf_low_income' in form
        profile.uses_mutualidad = 'irpf_mutualidad' in form
        profile.apply_dificil_justif_m130 = 'irpf_dificil_m130' in form
        self._db.session.commit()
        self.logger.info('IRPF settings saved: region=%s', profile.region)
