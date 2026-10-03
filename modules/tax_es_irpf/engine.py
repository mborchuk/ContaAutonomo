"""Pure IRPF calc engine — Modelo 130 (quarterly) + Modelo 100 (annual).

Caveman rule for this file: NO Flask, NO DB, NO globals. Inputs in, numbers
out. Only stdlib. So engine easy to test and reuse from hooks + routes.

Both modelos share the same money path:
  income - deductible expenses = rendimiento neto previo
  - 5% difícil justificación (EDS only, annual cap)         -> rendimiento neto

Modelo 130 then: x20%, minus retenciones YTD, minus prior 130 payments,
floor at 0. Modelo 100 then: optional 20% inicio reduction, optional joint
reduction, two half-scales (state + region) with mínimo, minus credits.

EVERY number this engine makes is an ESTIMATE, not tax advice. Confirm with
AEAT before filing. Rates/brackets come from year-versioned params (see
params/es_irpf_<year>.json) — never hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DISCLAIMER = "ESTIMATE — not tax advice. Confirm with AEAT (Renta WEB) before filing."

# Money rounded to cents at the boundaries so reports match to the céntimo.
_CENTS = 2


def _round(amount: float) -> float:
    """Round money to cents. One place so rounding policy stays consistent."""
    return round(amount, _CENTS)


# ─────────────────────────────────────────────────────────────────────────
# Shared building blocks (used by BOTH modelos — write once, reuse)
# ─────────────────────────────────────────────────────────────────────────

def apply_scale(base: float, brackets: list[dict[str, Any]]) -> float:
    """Progressive tax on `base` over a bracket list.

    Each bracket = {"upto": <ceiling or None>, "rate": <fraction>}. Same code
    for any year, any region — the brackets are data, this is the engine.
    """
    if base <= 0:
        return 0.0
    tax = 0.0
    prev = 0.0
    for bracket in brackets:
        ceiling = bracket["upto"]
        rate = bracket["rate"]
        upper = base if ceiling is None else min(base, float(ceiling))
        if upper > prev:
            tax += (upper - prev) * rate
            prev = upper
        if ceiling is not None and base <= ceiling:
            break
    return tax


def net_profit_previo(income: float, deductible_expenses: float) -> float:
    """Rendimiento neto previo = income − deductible expenses (may be negative)."""
    return income - deductible_expenses


def dificil_justificacion(
    net_previo: float,
    params: dict[str, Any],
    *,
    eds_eligible: bool,
    already_applied: float = 0.0,
) -> float:
    """5% gastos de difícil justificación (EDS specialty).

    Only when EDS applies AND activity positive. Cap is ANNUAL, so the caller
    passes how much was already used earlier in the year (`already_applied`);
    the running total is clamped to the cap. For Modelo 130 the YTD recompute
    naturally handles this because `net_previo` is cumulative — pass
    already_applied=0 and the YTD 5% is clamped to the cap directly.
    """
    if not eds_eligible or net_previo <= 0:
        return 0.0
    eds = params["eds"]
    raw = eds["dificil_justificacion_pct"] * net_previo
    cap = eds["dificil_justificacion_cap"]
    remaining_cap = max(0.0, cap - already_applied)
    return _round(min(raw, remaining_cap))


def rendimiento_neto(
    income: float,
    deductible_expenses: float,
    params: dict[str, Any],
    *,
    eds_eligible: bool,
) -> tuple[float, float]:
    """Return (rendimiento_neto, dificil_justificacion_applied).

    Shared by 130 and 100 so the base is computed identically in both.
    """
    previo = net_profit_previo(income, deductible_expenses)
    dificil = dificil_justificacion(previo, params, eds_eligible=eds_eligible)
    return _round(previo - dificil), dificil


def default_retencion_rate(
    *,
    is_professional: bool,
    alta_year: int,
    invoice_year: int,
    params: dict[str, Any],
) -> float:
    """IRPF retención rate to pre-fill on a new invoice.

    Empresario (not profesional) → 0. New professional → reduced rate inside
    the alta + N-year window, else general rate. Overridable per invoice.
    """
    if not is_professional:
        return 0.0
    r = params["retencion"]
    window = range(alta_year, alta_year + r["new_window_years"])  # alta + 2
    return r["new_professional"] if invoice_year in window else r["general"]


def compute_minimo(profile: dict[str, Any], minimo: dict[str, Any]) -> float:
    """Mínimo personal y familiar from the personal/family situation.

    `minimo` is ONE mínimo set — either the state set or a region's autonomic
    set. IRPF uses two: the state scale subtracts the state mínimo, the
    autonomic scale subtracts the (higher, in CV) autonomic mínimo. Call this
    once per set. Source: real M100-2025 boxes 0519/0520 (see design §2.3/§4).

    profile keys (all optional): age (int), descendientes (int count),
    descendientes_menor3 (int count), ascendientes65 (int), ascendientes75 (int).
    """
    total = float(minimo["contribuyente"])

    age = profile.get("age") or 0
    if age >= 75:
        total += minimo.get("age65", 0) + minimo.get("age75", 0)
    elif age >= 65:
        total += minimo.get("age65", 0)

    # Descendientes: the per-child amounts escalate; the table lists 1st..4th+.
    table = minimo["descendientes"]
    n_desc = int(profile.get("descendientes") or 0)
    for i in range(n_desc):
        total += table[i] if i < len(table) else table[-1]
    total += int(profile.get("descendientes_menor3") or 0) * minimo.get(
        "descendiente_menor3", 0)

    total += int(profile.get("ascendientes65") or 0) * minimo.get("ascendiente65", 0)
    total += int(profile.get("ascendientes75") or 0) * (
        minimo.get("ascendiente65", 0) + minimo.get("ascendiente75", 0)
    )
    return _round(total)


def minimo_for_region(profile: dict[str, Any], params: dict[str, Any],
                      region: str) -> tuple[float, float]:
    """Return (mínimo estatal, mínimo autonómico) for a profile + region.

    Region without its own autonomic mínimo set falls back to the state set.
    """
    state_set = params["minimo_estatal"]
    region_sets = params.get("minimo_autonomico", {})
    auton_set = region_sets.get(region, state_set)
    return compute_minimo(profile, state_set), compute_minimo(profile, auton_set)


# ─────────────────────────────────────────────────────────────────────────
# Result container — carries BOTH output modes (summary + line-by-line)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class IrpfResult:
    """One engine run, both output modes.

    summary = quick numbers (cards/dashboard). lines = ordered line-by-line so
    the user can see every step and trust the figure.
    """

    summary: dict[str, float]
    lines: list[dict[str, Any]]
    params_year: int
    region: str | None = None
    notes: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "lines": self.lines,
            "params_year": self.params_year,
            "region": self.region,
            "notes": self.notes,
            "disclaimer": self.disclaimer,
        }


def _line(label: str, amount: float, *, total: bool = False) -> dict[str, Any]:
    return {"label": label, "amount": _round(amount), "total": total}


# ─────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Modelo130Input:
    """Cumulative (YTD, 1 Jan → end of quarter) figures for one quarter."""

    year: int
    quarter: int
    ytd_income: float
    ytd_deductible_expenses: float
    ytd_retenciones: float = 0.0
    prior_payments: float = 0.0  # 130 paid in earlier quarters, same year
    eds_eligible: bool = True
    # Apply the 5% difícil justificación inside M130 box 02. Default ON per the
    # official AEAT instructions (box 02 includes it). None = use params default.
    apply_dificil_justif: bool | None = None
    low_income_deduction: bool = False
    low_income_amount: float = 0.0  # art. 110.3.c, ≤ params cap


@dataclass
class Modelo100Input:
    """Whole-year figures for the annual return."""

    year: int
    income: float
    deductible_expenses: float
    minimo_estatal: float
    minimo_autonomico: float
    retenciones: float = 0.0
    pagos_fraccionados: float = 0.0  # sum of the 4 quarters' Modelo 130
    eds_eligible: bool = True
    inicio_reduction_eligible: bool = False
    regional_deductions_amount: float = 0.0  # already-summed autonomic deductions
    declaration_mode: str = "individual"  # 'individual' | 'conjunta'
    family_unit: str = "biparental"  # 'biparental' | 'monoparental'
    spouse_income: float = 0.0
    spouse_deductible_expenses: float = 0.0
    spouse_retenciones: float = 0.0
    spouse_pagos_fraccionados: float = 0.0


# ─────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────

class IrpfEngine:
    """Stateless calc engine. Construct with loaded year params, then compute."""

    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.year = int(params["year"])

    # ── Modelo 130 (quarterly fractional payment) ────────────────────────

    def compute_modelo130(self, inp: Modelo130Input) -> IrpfResult:
        params = self.params
        # 5% difícil justificación toggle: input override, else params default.
        apply_dificil = inp.apply_dificil_justif
        if apply_dificil is None:
            apply_dificil = params["modelo130"].get("apply_5pct_dificil_justif", True)
        net, dificil = rendimiento_neto(
            inp.ytd_income,
            inp.ytd_deductible_expenses,
            params,
            eds_eligible=inp.eds_eligible and apply_dificil,
        )

        rate = params["modelo130"]["rate"]
        gross = _round(max(0.0, net) * rate) if net > 0 else _round(net * rate)

        low_income = 0.0
        if inp.low_income_deduction and net > 0:
            cap = params["modelo130"]["low_income_deduction_max"]
            low_income = _round(min(inp.low_income_amount or cap, cap))

        result_before_floor = (
            gross - inp.ytd_retenciones - inp.prior_payments - low_income
        )
        payment_due = _round(max(0.0, result_before_floor))

        lines = [
            _line("Income YTD", inp.ytd_income),
            _line("Deductible expenses YTD", -inp.ytd_deductible_expenses),
        ]
        if dificil:
            cap = params["eds"]["dificil_justificacion_cap"]
            lines.append(
                _line(f"5% difícil justificación (cap {cap:.0f})", -dificil)
            )
        lines.append(_line("Rendimiento neto YTD", net))
        lines.append(_line(f"× {rate * 100:.0f}%", gross))
        if inp.ytd_retenciones:
            lines.append(_line("− Retenciones YTD", -inp.ytd_retenciones))
        if inp.prior_payments:
            lines.append(_line("− Modelo 130 prior quarters", -inp.prior_payments))
        if low_income:
            lines.append(_line("− Low-income deduction (art. 110.3.c)", -low_income))
        lines.append(_line("Payment due", payment_due, total=True))

        notes: list[str] = []
        if result_before_floor < 0:
            notes.append(
                "Negative result → payment is 0; the negative rendimiento "
                "carries forward to offset later quarters this year."
            )

        return IrpfResult(
            summary={
                "modelo130_due": payment_due,
                "rendimiento_neto_ytd": net,
                "quarter": inp.quarter,
            },
            lines=lines,
            params_year=self.year,
            notes=notes,
        )

    # ── Modelo 100 (annual return) ───────────────────────────────────────

    def compute_modelo100(
        self, inp: Modelo100Input, region: str
    ) -> IrpfResult:
        params = self.params
        state_scale = params["state_scale"]
        regional_scale, region_used, region_note = self._regional_scale(region)

        # 1-2. own activity net (income − expenses − difícil justificación)
        net, dificil = rendimiento_neto(
            inp.income, inp.deductible_expenses, params,
            eds_eligible=inp.eds_eligible,
        )

        # 3. 20% inicio de actividad reduction (Modelo 100 only, positive net)
        inicio = 0.0
        if inp.inicio_reduction_eligible and net > 0:
            ia = params["inicio_actividad"]
            inicio = _round(ia["reduction_pct"] * min(net, ia["base_cap"]))
        net_actividad = _round(net - inicio)

        # 4. base imponible general (+ spouse activity if joint)
        base_imponible = net_actividad
        spouse_net = 0.0
        if inp.declaration_mode == "conjunta":
            spouse_net, _ = rendimiento_neto(
                inp.spouse_income, inp.spouse_deductible_expenses, params,
                eds_eligible=inp.eds_eligible,
            )
            base_imponible = _round(base_imponible + spouse_net)

        # 5. joint reduction (not below 0)
        joint_reduction = 0.0
        if inp.declaration_mode == "conjunta":
            tc = params["tributacion_conjunta"]
            raw = tc["biparental"] if inp.family_unit == "biparental" else tc["monoparental"]
            joint_reduction = _round(min(raw, max(0.0, base_imponible)))

        # 6. base liquidable general
        base_liquidable = _round(max(0.0, base_imponible - joint_reduction))

        # 7-9. two half-scales, each minus ITS OWN mínimo (state mínimo on the
        # state scale, autonomic mínimo on the regional scale — CV's is higher).
        cuota_estatal = _round(
            apply_scale(base_liquidable, state_scale)
            - apply_scale(inp.minimo_estatal, state_scale)
        )
        cuota_autonomica = _round(
            apply_scale(base_liquidable, regional_scale)
            - apply_scale(inp.minimo_autonomico, regional_scale)
        )
        cuota_integra = _round(cuota_estatal + cuota_autonomica)

        # 10. cuota líquida (minus autonomic deductions, floor at 0)
        cuota_liquida = _round(
            max(0.0, cuota_integra - inp.regional_deductions_amount)
        )

        # 11. cuota diferencial (credit retenciones + pagos fraccionados)
        retenciones = inp.retenciones + (
            inp.spouse_retenciones if inp.declaration_mode == "conjunta" else 0.0
        )
        pagos = inp.pagos_fraccionados + (
            inp.spouse_pagos_fraccionados if inp.declaration_mode == "conjunta" else 0.0
        )
        cuota_diferencial = _round(cuota_liquida - retenciones - pagos)

        lines = [
            _line("Year income", inp.income),
            _line("Deductible expenses", -inp.deductible_expenses),
        ]
        if dificil:
            cap = params["eds"]["dificil_justificacion_cap"]
            lines.append(_line(f"5% difícil justificación (cap {cap:.0f})", -dificil))
        if inicio:
            lines.append(_line("− 20% reducción inicio actividad", -inicio))
        lines.append(_line("Rendimiento neto actividad", net_actividad))
        if inp.declaration_mode == "conjunta":
            lines.append(_line("+ Cónyuge (rendimiento neto)", spouse_net))
            lines.append(_line("Base imponible general", base_imponible))
            lines.append(_line(f"− Reducción conjunta ({inp.family_unit})", -joint_reduction))
        lines.append(_line("Base liquidable general", base_liquidable))
        lines.append(_line("Mínimo estatal", inp.minimo_estatal))
        lines.append(_line("Mínimo autonómico", inp.minimo_autonomico))
        lines.append(_line("Cuota estatal", cuota_estatal))
        lines.append(_line(f"Cuota autonómica ({region_used})", cuota_autonomica))
        lines.append(_line("Cuota íntegra", cuota_integra))
        if inp.regional_deductions_amount:
            lines.append(
                _line("− Deducciones autonómicas", -inp.regional_deductions_amount)
            )
            lines.append(_line("Cuota líquida", cuota_liquida))
        if retenciones:
            lines.append(_line("− Retenciones soportadas", -retenciones))
        if pagos:
            lines.append(_line("− Pagos fraccionados (Modelo 130)", -pagos))
        verb = "A ingresar" if cuota_diferencial >= 0 else "A devolver"
        lines.append(_line(f"Cuota diferencial ({verb})", cuota_diferencial, total=True))

        notes: list[str] = []
        if region_note:
            notes.append(region_note)

        return IrpfResult(
            summary={
                "modelo100_diferencial": cuota_diferencial,
                "cuota_integra": cuota_integra,
                "cuota_liquida": cuota_liquida,
                "base_liquidable": base_liquidable,
                "a_ingresar": cuota_diferencial >= 0,
            },
            lines=lines,
            params_year=self.year,
            region=region_used,
            notes=notes,
        )

    def recommend_declaration(
        self, inp: Modelo100Input, region: str
    ) -> dict[str, Any]:
        """Compute Modelo 100 both individual and joint, recommend the cheaper.

        Joint does not always win (two earners often pay less individually), so
        the estimator shows both and flags the lower cuota diferencial.
        """
        from dataclasses import replace

        individual = self.compute_modelo100(
            replace(inp, declaration_mode="individual"), region
        )
        joint = self.compute_modelo100(
            replace(inp, declaration_mode="conjunta"), region
        )
        ind_pay = individual.summary["modelo100_diferencial"]
        joint_pay = joint.summary["modelo100_diferencial"]
        # Lower diferencial = better (less to pay / bigger refund).
        recommended = "conjunta" if joint_pay < ind_pay else "individual"
        return {
            "individual": individual.as_dict(),
            "conjunta": joint.as_dict(),
            "recommended": recommended,
        }

    # ── helpers ──────────────────────────────────────────────────────────

    def _regional_scale(
        self, region: str
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        """Pick the comunidad scale; fall back to supletoria with a warning.

        Foral regions (País Vasco, Navarra) are NOT modellable by the
        state+autonomic engine — refuse rather than fake them with supletoria.
        """
        scales = self.params["regional_scales"]
        foral = self.params.get("foral_regions", [])
        if region in foral:
            raise ValueError(
                f"Region '{region}' is foral (régimen propio) and is not "
                "supported by the state+autonomic engine."
            )
        if region in scales and region != "supletoria":
            return scales[region], region, None
        return (
            scales["supletoria"],
            "supletoria",
            f"Region '{region}' scale not loaded — using state supletoria "
            "fallback; figure is approximate.",
        )
