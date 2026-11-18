#!/usr/bin/env python3
"""Tax ES IRPF Module — Modelo 130 + Modelo 100 estimator.

Summary: this module estimates two figures for a Spanish autónomo.
  - Modelo 130: how much IRPF to prepay this quarter (cumulative year to date).
  - Modelo 100: what the final annual IRPF looks like after the prepayments
    and withholdings (retenciones).

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

from constants import NON_INCOME_INVOICE_STATUSES
from module_manager import BaseModule

from .engine import (
    IrpfEngine,
    Modelo100Input,
    Modelo130Input,
    default_retencion_rate,
    minimo_for_region,
)
from .depreciation import depreciation_in_year
from .params_store import available_years, load_params
from db_helpers import db_get
from timeutil import utcnow

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
    def dependencies(self):
        # Expenses feed box 02; filed returns and Social Security payments come
        # from whatever provides these functions (Tax Management).
        return ['expenses', 'tax.filed_boxes', 'ss.payments_total']

    @property
    def nav_items(self):
        return [
            {'label': 'IRPF Estimator', 'endpoint': 'tax_es_irpf.index',
             'icon': '🧮', 'group': 'Taxes', 'order': 40}
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
            # 5% difícil justificación inside M130 box 02. Default OFF: the
            # owner's gestor applies it only in the annual Modelo 100.
            apply_dificil_justif_m130 = db.Column(db.Boolean, default=False)
            eds_eligible = db.Column(db.Boolean, default=True)  # prior INCN ≤ limit
            inicio_reduction_eligible = db.Column(db.Boolean, default=False)
            regional_deduction_inputs_json = db.Column(db.Text, default='{}')
            declaration_mode = db.Column(db.String(20), default='individual')
            family_unit = db.Column(db.String(20), default='biparental')
            spouse_income = db.Column(db.Float, default=0.0)
            spouse_deductible_expenses = db.Column(db.Float, default=0.0)
            spouse_retenciones = db.Column(db.Float, default=0.0)
            spouse_pagos_fraccionados = db.Column(db.Float, default=0.0)
            # Equipment (bienes de inversión): 'depreciate' or 'expense'.
            equipment_method = db.Column(db.String(20), default='depreciate')
            depreciation_rate = db.Column(db.Float, default=25.0)   # % per year
            equipment_threshold = db.Column(db.Float, default=300.0)  # EUR net
            equipment_categories = db.Column(db.String(200), default='Equipment')

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
            computed_at = db.Column(db.DateTime, default=utcnow)

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

    def _function(self, name):
        """The registry, if something provides `name`; else None."""
        functions = getattr(self.core, 'functions', None)
        return functions if functions is not None and functions.available(name) else None

    def _filed_boxes(self, form_type, year, quarter):
        functions = self._function('tax.filed_boxes')
        if functions is None:
            return {}
        return functions.call('tax.filed_boxes', form_type=form_type, year=year,
                              quarter=quarter)

    def _ss_paid(self, date_from, date_to):
        """Social Security recorded as paid in the window; 0 without a provider."""
        functions = self._function('ss.payments_total')
        if functions is None:
            return 0.0
        return functions.call('ss.payments_total', date_from=date_from,
                              date_to=date_to)['total']

    def on_enable(self):
        """Idempotent guarded migrations for this module's own columns."""
        from sqlalchemy import inspect as sa_inspect, text

        # Core columns this module reads (invoice.irpf_retention_*,
        # expense.deductible*) are core schema: schema_migrations.CORE_COLUMNS.
        migrations = {
            'irpfes_profile': [
                ('apply_dificil_justif_m130', 'BOOLEAN DEFAULT 0'),
                ('equipment_method', "VARCHAR(20) DEFAULT 'depreciate'"),
                ('depreciation_rate', 'FLOAT DEFAULT 25.0'),
                ('equipment_threshold', 'FLOAT DEFAULT 300.0'),
                ('equipment_categories', "VARCHAR(200) DEFAULT 'Equipment'"),
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
        """Sum income (EUR) and IRPF retenciones in a window.

        Devengo: every invoice dated in the window counts, paid or not, except
        cancelled ones (owner decision 2026-10-03).
        """
        start_dt = self._year_start(year, profile)
        invoices = self.Invoice.query.filter(
            self.Invoice.status.notin_(NON_INCOME_INVOICE_STATUSES),
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

    def _equipment_rule(self, profile):
        """(depreciate?, annual rate %, threshold EUR, category names)."""
        categories = {c.strip().lower()
                      for c in (profile.equipment_categories or 'Equipment').split(',')
                      if c.strip()}
        return ((profile.equipment_method or 'depreciate') == 'depreciate',
                profile.depreciation_rate if profile.depreciation_rate is not None else 25.0,
                profile.equipment_threshold if profile.equipment_threshold is not None else 300.0,
                categories)

    def _expense_net_eur(self, exp):
        """VAT-excluded, deductible share of an expense in EUR (0 if not deductible).

        VAT already deducted on Modelo 303 is not an IRPF expense, so the net
        amount is used; legacy rows without VAT data fall back to the amount.
        """
        if getattr(exp, 'deductible', True) is False:
            return 0.0
        pct = getattr(exp, 'deductible_pct', None)
        pct = 100.0 if pct is None else pct
        net = exp.net_amount if getattr(exp, 'net_amount', None) is not None else exp.amount
        return (self._to_eur(net or 0.0, exp.currency or 'EUR', exp.expense_date)
                or 0.0) * (pct / 100.0)

    def _sum_expenses(self, year, profile, end_dt):
        """Deductible expenses (EUR) from the year start to `end_dt`.

        Equipment at or above the threshold is depreciated (when the profile
        says so) instead of deducted in full; its yearly share is added for
        assets bought this year or earlier.
        """
        start_dt = self._year_start(year, profile)
        quarter = (end_dt.month - 1) // 3 + 1
        depreciate, rate, threshold, categories = self._equipment_rule(profile)

        total = 0.0
        for exp in self.Expense.query.filter(
                self.Expense.expense_date <= end_dt).all():
            net_eur = self._expense_net_eur(exp)
            is_asset = (depreciate and net_eur >= threshold
                        and (exp.category or '').strip().lower() in categories)
            if is_asset:
                total += depreciation_in_year(net_eur, exp.expense_date,
                                              year, quarter, rate)
            elif exp.expense_date >= start_dt:
                total += net_eur
        return total

    def _sum_reta(self, year, profile, end_dt):
        """RETA (Seguridad Social) paid in window — 100% deductible expense.

        Falls back to Settings.social_security_monthly × elapsed months when no
        SSPayment rows exist. Mutualidad alternativa caps the deduction.
        """
        start_dt = self._year_start(year, profile)
        reta = self._ss_paid(start_dt, end_dt)
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

        # Box 05: what was paid in earlier quarters — the filed box 07 when
        # the AEAT PDF is in Tax Forms, else this module's own estimate.
        prior = 0.0
        for q in range(1, quarter):
            filed = self._filed_boxes('130', year, q)
            if '07' in filed:
                prior += max(0.0, filed['07'])
                continue
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

    def modelo130_boxes(self, year, quarter):
        """Modelo 130 boxes 01–07 for one quarter (used by Tax Drafts)."""
        profile = self._get_profile()
        inp = self._build_modelo130_input(year, quarter, profile)
        result = self._engine(year).compute_modelo130(inp)
        net = result.summary['rendimiento_neto_ytd']
        rate = self._params(year)['modelo130']['rate']
        expenses = inp.ytd_income - net  # includes 5% difícil justificación
        return {
            '01': round(inp.ytd_income, 2),
            '02': round(expenses, 2),
            '03': round(net, 2),
            '04': round(max(0.0, net) * rate, 2),
            '05': round(inp.prior_payments, 2),
            '06': round(inp.ytd_retenciones, 2),
            '07': result.summary['modelo130_due'],
        }, rate

    def modelo100_result(self, year):
        """Modelo 100 estimate for `year` with the profile's comunidad, over
        the year's data recorded so far (`IrpfResult.as_dict()`). The result
        to pay (> 0) or refund (< 0) is `summary['modelo100_diferencial']`;
        it already credits retenciones and the year's Modelo 130 payments.
        Raises ValueError for a foral region."""
        profile = self._get_profile()
        region = profile.region or self._params(year).get('default_region', 'valenciana')
        return self._engine(year).compute_modelo100(
            self._build_modelo100_input(year, profile), region).as_dict()

    def equipment_rule(self):
        """What counts as equipment (bien de inversión) in the profile; Modelo
        303 boxes 30/31 use the same rule."""
        _, _, threshold, categories = self._equipment_rule(self._read_profile())
        return {'threshold': threshold, 'categories': sorted(categories)}

    def _modelo130(self, year, quarter):
        boxes, rate = self.modelo130_boxes(year, quarter)
        return {'boxes': boxes, 'rate': rate}

    # ── Registry functions ───────────────────────────────────────────────

    def get_functions(self):
        from functions import Function, Param
        year = Param('year', 'int', 'Tax year', required=True, minimum=2000, maximum=2100)
        quarter = Param('quarter', 'int', 'Quarter, 1-4', required=True, minimum=1, maximum=4)
        return [
            Function('tax.modelo130_boxes', self._modelo130,
                     'Modelo 130 (IRPF) boxes 01-07 for one quarter, year to date, '
                     'from the IRPF profile.',
                     params=(year, quarter),
                     returns='{"boxes": {"01".."07": amount}, "rate": fraction}'),
            Function('tax.modelo100_result', self.modelo100_result,
                     'Modelo 100 (renta) estimate for the year so far. Fails with '
                     'ValueError for a foral region (not modelled).',
                     params=(year,),
                     returns='IrpfResult.as_dict(): "summary"."modelo100_diferencial" '
                             '> 0 to pay, < 0 refund; ...'),
            Function('tax.equipment_rule', self.equipment_rule,
                     'What counts as equipment (bien de inversión): net amount at or '
                     'above the threshold, in one of the categories.',
                     returns='{"threshold": amount, "categories": [lowercase name]}'),
        ]

    # ── What-if simulator (G-3) — reads only, saves nothing ─────────────

    def _read_profile(self):
        """The saved profile, or an unsaved default one: unlike _get_profile
        this never writes, so the simulator leaves the database untouched."""
        return self.IrpfProfile.query.first() or self.IrpfProfile(
            region='valenciana', regime='eds', is_professional=True, age=0,
            descendientes=0, descendientes_menor3=0, ascendientes65=0,
            ascendientes75=0, uses_mutualidad=False, low_income_deduction=False,
            apply_dificil_justif_m130=False, eds_eligible=True,
            inicio_reduction_eligible=False, regional_deduction_inputs_json='{}',
            declaration_mode='individual', family_unit='biparental',
            spouse_income=0.0, spouse_deductible_expenses=0.0,
            spouse_retenciones=0.0, spouse_pagos_fraccionados=0.0,
            equipment_method='depreciate', depreciation_rate=25.0,
            equipment_threshold=300.0, equipment_categories='Equipment')

    # Comunidades of the common regime. Those without a scale in the year's
    # params are computed with the state supletoria scale, and the engine says
    # so in a note; foral regions are not offered (not modelled).
    COMMON_REGIME_REGIONS = (
        'andalucia', 'aragon', 'asturias', 'baleares', 'canarias', 'cantabria',
        'castilla_la_mancha', 'castilla_y_leon', 'cataluna', 'ceuta', 'extremadura',
        'galicia', 'la_rioja', 'madrid', 'melilla', 'murcia', 'valenciana')

    def simulator_regions(self, year):
        params = self._params(year)
        loaded = {r for r in params.get('regional_scales', {}) if r != 'supletoria'}
        foral = set(params.get('foral_regions', []))
        return sorted((loaded | set(self.COMMON_REGIME_REGIONS)) - foral)

    def simulator_baseline(self, today=None):
        """The real figures of today's year for the simulator, and the RETA
        hook (None when the RETA advisor is not enabled)."""
        from .simulator import Baseline
        today = today or date.today()
        year = today.year
        profile = self._read_profile()
        params = self._params(year)
        m100_input = self._build_modelo100_input(year, profile)
        quarter = self._current_quarter(today)
        closed = self._build_modelo130_input(year, quarter, profile).prior_payments

        settings = self.core.get_settings()
        quota = (getattr(settings, 'social_security_monthly', 0.0) or 0.0) if settings else 0.0
        functions = self._function('reta.projection')
        rate = None
        reta_fn = None
        if functions is not None:
            rate = functions.call('reta.projection', today=today)['contribution_rate_pct']

            def reta_fn(income_delta, expense_delta, monthly_quota):
                return functions.call('reta.projection', today=today,
                                      income_delta=income_delta,
                                      expense_delta=expense_delta,
                                      monthly_quota=monthly_quota)

        cap = None
        if profile.uses_mutualidad:
            cap = params['reta'].get('mutualidad_deduction_cap')
        base = Baseline(
            year=year, m100_input=m100_input,
            region=profile.region or params.get('default_region', 'valenciana'),
            reta_in_irpf=self._sum_reta(year, profile, date(year, 12, 31)),
            paid_130_closed=closed, months_remaining=12 - today.month,
            monthly_quota=quota, contribution_rate_pct=rate, reta_cap=cap,
            eds_eligible=bool(profile.eds_eligible),
            apply_dificil_justif_m130=bool(profile.apply_dificil_justif_m130),
            low_income_deduction=bool(profile.low_income_deduction))
        return base, reta_fn

    def simulate(self, scenario=None, today=None):
        """Real and scenario results plus their difference. `scenario` None
        means no changes. Raises ValueError for a foral comunidad."""
        from dataclasses import replace as dc_replace
        from .simulator import compare, run, unchanged
        base, reta_fn = self.simulator_baseline(today)
        profile = self._read_profile()
        params = self._params(base.year)
        engine = self._engine(base.year)

        def minimo_fn(region):
            return minimo_for_region(self._profile_dict(profile), params, region)

        def regional_deductions_fn(region):
            # A transient copy: the saved profile is never changed.
            other = self.IrpfProfile(
                region=region,
                regional_deduction_inputs_json=profile.regional_deduction_inputs_json)
            return self._regional_deductions_amount(base.year, other)

        real = run(engine, base, unchanged(base), minimo_fn, regional_deductions_fn, reta_fn)
        scen = scenario or unchanged(base)
        result = run(engine, base, scen, minimo_fn, regional_deductions_fn, reta_fn)
        return {
            'base': base, 'scenario': dc_replace(scen), 'real': real,
            'simulated': result, 'difference': compare(real, result),
        }

    def _render_simulator(self, args):
        from .simulator import Scenario, quota_from_base, unchanged
        today = date.today()
        year = today.year
        regions = self.simulator_regions(year)
        errors = []
        try:
            base, _reta_fn = self.simulator_baseline(today)
        except ValueError as e:  # foral comunidad in the profile
            return render_template('irpf_simulator.html', year=year, error=str(e),
                                   regions=regions)
        scenario = unchanged(base)

        def number(name, current):
            raw = (args.get(name) or '').strip().replace(',', '.')
            if raw == '':
                return current
            try:
                value = float(raw)
            except ValueError:
                errors.append(f'{name.replace("_", " ").capitalize()} must be a number.')
                return current
            if value < 0 or value > 100_000_000:
                errors.append(f'{name.replace("_", " ").capitalize()} must be between 0 '
                              'and 100,000,000.')
                return current
            return value

        scenario.income = number('income', base.income)
        scenario.expenses = number('expenses', base.expenses)
        region = (args.get('region') or base.region).strip()
        if region not in regions and region != base.region:
            errors.append('Unknown comunidad autónoma.')
            region = base.region
        scenario.region = region
        if base.contribution_rate_pct:
            reta_base = number('reta_base', base.reta_base)
            if reta_base != base.reta_base:
                scenario.monthly_quota = quota_from_base(reta_base, base.contribution_rate_pct)
        try:
            outcome = self.simulate(Scenario(**vars(scenario)), today)
            error = None
        except ValueError as e:
            outcome, error = None, str(e)
        return render_template('irpf_simulator.html', year=year, regions=regions,
                               base=base, scenario=scenario, outcome=outcome,
                               errors=errors, error=error, today=today)

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

        @bp.route('/simulator')
        @login_required
        def simulator():
            # GET only: a scenario is never saved.
            return module._render_simulator(request.args)

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
            inv = db_get(self.Invoice, invoice.id)
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
        depreciate, rate, threshold, _ = self._equipment_rule(profile)
        return self.render_settings_template(
            'tax_es_irpf_settings_panel.html',
            profile=profile, regions=regions, depreciate=depreciate, rate=rate,
            threshold=threshold,
            start=profile.start_date.isoformat() if profile.start_date else '')

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
                pass  # malformed date from the form: keep the stored value
        for field_name, attr in [('irpf_age', 'age'),
                                 ('irpf_descendientes', 'descendientes')]:
            try:
                setattr(profile, attr, int(form.get(field_name, 0) or 0))
            except (TypeError, ValueError):
                pass  # non-numeric input: keep the stored value
        profile.inicio_reduction_eligible = 'irpf_inicio' in form
        profile.low_income_deduction = 'irpf_low_income' in form
        profile.uses_mutualidad = 'irpf_mutualidad' in form
        profile.apply_dificil_justif_m130 = 'irpf_dificil_m130' in form
        method = form.get('irpf_equipment_method', 'depreciate')
        profile.equipment_method = method if method in ('depreciate', 'expense') else 'depreciate'
        for field_name, attr, low, high in [
                ('irpf_depreciation_rate', 'depreciation_rate', 0.0, 100.0),
                ('irpf_equipment_threshold', 'equipment_threshold', 0.0, None)]:
            try:
                value = float(form.get(field_name, ''))
            except (TypeError, ValueError):
                continue  # empty or non-numeric: keep the stored value
            if value >= low and (high is None or value <= high):
                setattr(profile, attr, value)
        categories = (form.get('irpf_equipment_categories') or '').strip()
        if categories:
            profile.equipment_categories = categories[:200]
        self._db.session.commit()
        self.logger.info('IRPF settings saved: region=%s', profile.region)
