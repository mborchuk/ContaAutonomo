#!/usr/bin/env python3
"""
What-if simulator (G-3) — the year's figures with some inputs changed.

Pure: no Flask, no database, nothing saved. The module builds a `Baseline`
from the real data (the same inputs the Modelo 100 estimate uses) and this
file runs the existing engines on it twice — once unchanged, once with the
user's `Scenario` — so "no changes" gives exactly the real figures and any
difference comes from the engines alone. No new tax rules.

Inputs the user can change: income, deductible expenses (RETA excluded),
comunidad autónoma and the RETA monthly base.

Results, all for the year of the baseline:
  - irpf_year:        Modelo 100 cuota líquida (the IRPF of the year)
  - m100_result:      Modelo 100 cuota diferencial after retenciones and the
                      year's Modelo 130 payments (> 0 to pay, < 0 refund)
  - m130_remaining:   Modelo 130 still to pay this year, open quarter
                      included: the cumulative 4T payment over the year's
                      figures minus the payments of the closed quarters
  - regularization:   RETA regularización (None when the RETA advisor is off)
  - net_income:       income − expenses − RETA − regularización − IRPF

A RETA base change applies from the next month to December: it changes the
RETA deducted in IRPF and the RETA paid in the regularización projection.
Not modelled (flagged): tarifa plana, pluriactividad.
"""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from .engine import Modelo130Input


def _round2(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


@dataclass
class Baseline:
    """The real figures of one year, as the IRPF estimator sees them."""

    year: int
    m100_input: Any                  # engine.Modelo100Input built from the data
    region: str
    reta_in_irpf: float              # RETA inside m100_input.deductible_expenses
    paid_130_closed: float           # Modelo 130 of the quarters already closed
    months_remaining: int            # months after the current one, this year
    monthly_quota: float             # RETA quota paid now
    contribution_rate_pct: float | None = None   # None: RETA advisor off
    reta_cap: float | None = None    # mutualidad cap on the RETA deduction
    eds_eligible: bool = True
    apply_dificil_justif_m130: bool = False
    low_income_deduction: bool = False

    @property
    def income(self):
        return self.m100_input.income

    @property
    def expenses(self):
        """Deductible expenses without RETA (what the user edits)."""
        return self.m100_input.deductible_expenses - self.reta_in_irpf

    @property
    def reta_base(self):
        if not self.contribution_rate_pct:
            return None
        return _round2(self.monthly_quota / (self.contribution_rate_pct / 100.0))


@dataclass
class Scenario:
    income: float
    expenses: float
    region: str
    monthly_quota: float


def unchanged(base):
    """The scenario that reproduces the real figures."""
    return Scenario(income=base.income, expenses=base.expenses, region=base.region,
                    monthly_quota=base.monthly_quota)


def quota_from_base(base_eur, rate_pct):
    return _round2(base_eur * rate_pct / 100.0)


def run(engine, base, scenario, minimo_fn: Callable, regional_deductions_fn: Callable,
        reta_fn: Callable | None):
    """Results for one scenario.

    engine: IrpfEngine for the year. minimo_fn(region) -> (estatal,
    autonómico). regional_deductions_fn(region) -> amount. reta_fn(income
    delta, expense delta, monthly quota) -> RETA projection dict, or None
    when the RETA advisor is not available. Raises ValueError for a foral
    comunidad (the engine does not model it).
    """
    quota_delta = scenario.monthly_quota - base.monthly_quota
    reta = base.reta_in_irpf + quota_delta * base.months_remaining
    if base.reta_cap is not None:
        reta = min(reta, base.reta_cap)
    reta = max(0.0, reta)
    deductible = scenario.expenses + reta

    m130 = engine.compute_modelo130(Modelo130Input(
        year=base.year, quarter=4, ytd_income=scenario.income,
        ytd_deductible_expenses=deductible,
        ytd_retenciones=base.m100_input.retenciones,
        prior_payments=base.paid_130_closed,
        eds_eligible=base.eds_eligible,
        apply_dificil_justif=base.apply_dificil_justif_m130,
        low_income_deduction=base.low_income_deduction))
    m130_remaining = m130.summary['modelo130_due']

    if scenario.region == base.region:
        minimo_estatal = base.m100_input.minimo_estatal
        minimo_autonomico = base.m100_input.minimo_autonomico
        regional_deductions = base.m100_input.regional_deductions_amount
    else:
        minimo_estatal, minimo_autonomico = minimo_fn(scenario.region)
        regional_deductions = regional_deductions_fn(scenario.region)
    m100 = engine.compute_modelo100(replace(
        base.m100_input, income=scenario.income, deductible_expenses=deductible,
        minimo_estatal=minimo_estatal, minimo_autonomico=minimo_autonomico,
        regional_deductions_amount=regional_deductions,
        pagos_fraccionados=base.paid_130_closed + m130_remaining), scenario.region)

    regularization = None
    if reta_fn is not None:
        projection = reta_fn(scenario.income - base.income,
                             scenario.expenses - base.expenses, scenario.monthly_quota)
        regularization = projection['regularization_delta']

    irpf_year = m100.summary['cuota_liquida']
    net = scenario.income - scenario.expenses - reta - (regularization or 0.0) - irpf_year
    return {
        'irpf_year': _round2(irpf_year),
        'm100_result': _round2(m100.summary['modelo100_diferencial']),
        'm130_remaining': _round2(m130_remaining),
        'regularization': None if regularization is None else _round2(regularization),
        'net_income': _round2(net),
        'reta_in_irpf': _round2(reta),
        'region_used': m100.region,
        'notes': list(m100.notes),
    }


RESULT_KEYS = ('irpf_year', 'm100_result', 'm130_remaining', 'regularization', 'net_income')


def compare(real, scenario):
    """Scenario − real for every result (None when either side is missing)."""
    out = {}
    for key in RESULT_KEYS:
        a, b = real.get(key), scenario.get(key)
        out[key] = None if a is None or b is None else _round2(b - a)
    return out
