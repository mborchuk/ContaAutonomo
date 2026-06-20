"""Unit tests for the tax_es_irpf engine (Modelo 130 + Modelo 100).

The worked examples in autonomos_md/MODELOS_100_130.md are REAL filings from
the user (two tax years), reproduced box-by-box. We assert the engine matches
each AEAT box to the cent. Plus the edge cases the design calls out: loss
quarter, EDS difícil-justificación cap + M130 box-02 toggle, split state/CV
mínimos, 20% inicio, conjunta, region split + supletoria fallback, foral
refusal, retención window, year-versioned params.

The engine is pure, so these tests import it directly — no Flask/DB needed.
"""
import pytest

from modules.tax_es_irpf.engine import (
    IrpfEngine,
    Modelo100Input,
    Modelo130Input,
    apply_scale,
    compute_minimo,
    default_retencion_rate,
    dificil_justificacion,
    minimo_for_region,
)
from modules.tax_es_irpf.params_store import available_years, load_params


@pytest.fixture
def params():
    return load_params(2026)


@pytest.fixture
def engine(params):
    return IrpfEngine(params)


def _m100(**kw):
    """Modelo100Input with sensible single-person defaults; override per test."""
    defaults = dict(year=2026, income=60000, deductible_expenses=18000,
                    minimo_estatal=5550, minimo_autonomico=6105)
    defaults.update(kw)
    return Modelo100Input(**defaults)


# ── Real filing: Modelo 130, 1T 2026 (design §3, worked example A) ─────────

def test_modelo130_real_1t_2026(engine):
    # box 02 already bundles real gastos + the 1T share of the 5%, so we feed
    # the box-02 total and turn the engine's own 5% off to avoid double count.
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1,
        ytd_income=16923.78, ytd_deductible_expenses=2097.81,
        apply_dificil_justif=False,
    ))
    assert result.summary['rendimiento_neto_ytd'] == 14825.97  # box 03
    assert result.summary['modelo130_due'] == 2965.19          # box 19 ✓ filing


# ── Real filing: Modelo 130, 4T 2024 (design §3, worked example A2) ────────

def test_modelo130_real_4t_2024(engine):
    # box 02 = 3.145,98 real gastos + 2.000 difícil (cap). Feed real gastos and
    # let the engine add the 5% (clamped to the 2.000 annual cap).
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=4,
        ytd_income=72373.81, ytd_deductible_expenses=3145.98,
        prior_payments=9843.01,
    ))
    assert result.summary['rendimiento_neto_ytd'] == 67227.83  # box 03
    assert result.summary['modelo130_due'] == 3602.56          # box 19 ✓ filing


# ── Illustrative example B (design §3) — 5% toggle ON ─────────────────────

def test_modelo130_illustrative_example_b(engine):
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=2,
        ytd_income=30000, ytd_deductible_expenses=8000,
        ytd_retenciones=1800, prior_payments=500,
    ))
    assert result.summary['rendimiento_neto_ytd'] == 20900.0
    assert result.summary['modelo130_due'] == 1880.0
    assert result.lines[-1]['total'] is True


# ── Real filing: Modelo 100, 2025 conjunta (design §4) ────────────────────

def test_modelo100_real_2025_conjunta(engine):
    """Reproduces M100-2025 box-by-box: inicio 20%, conjunta, RETA, difícil
    cap, split state/CV mínimos, CV scale, pagos-fraccionados credit.
    """
    result = engine.compute_modelo100(_m100(
        year=2025, income=68008.33, deductible_expenses=6742.81,
        minimo_estatal=10650, minimo_autonomico=11715,  # state / CV, 2 kids
        retenciones=0, pagos_fraccionados=12253.10,
        inicio_reduction_eligible=True,
        declaration_mode='conjunta', family_unit='biparental',
    ), region='valenciana')

    by_label = {l['label']: l['amount'] for l in result.lines}
    assert by_label['5% difícil justificación (cap 2000)'] == -2000.0   # 0222
    assert by_label['− 20% reducción inicio actividad'] == -11853.10    # 0234
    assert by_label['Rendimiento neto actividad'] == 47412.42           # 0235/0435
    assert by_label['− Reducción conjunta (biparental)'] == -3400.0     # 0491
    assert by_label['Base liquidable general'] == 44012.42              # 0500
    assert by_label['Cuota estatal'] == 4981.30                         # 0532
    assert by_label['Cuota autonómica (valenciana)'] == 4878.13         # 0533

    s = result.summary
    assert s['cuota_integra'] == 9859.43                                # 0587
    assert s['modelo100_diferencial'] == -2393.67                       # 0610 ✓
    assert s['a_ingresar'] is False  # a devolver (refund)


# ── apply_scale / split mínimo method ─────────────────────────────────────

def test_apply_scale_matches_doc_state_and_cv(params):
    assert round(apply_scale(40000, params['state_scale']), 2) == 5250.75
    assert round(apply_scale(5550, params['state_scale']), 2) == 527.25
    cv = params['regional_scales']['valenciana']
    assert round(apply_scale(40000, cv), 2) == 5180.0
    assert round(apply_scale(5550, cv), 2) == 499.5


def test_apply_scale_zero_and_negative(params):
    assert apply_scale(0, params['state_scale']) == 0.0
    assert apply_scale(-100, params['state_scale']) == 0.0


def test_minimo_split_state_vs_autonomic(params):
    """CV mínimo is the state block × 1.10 — verified vs the 2025 filing."""
    profile = {'descendientes': 2}  # contribuyente + 2 kids
    estatal, autonomico = minimo_for_region(profile, params, 'valenciana')
    assert estatal == 10650.0       # 5.550 + 2.400 + 2.700
    assert autonomico == 11715.0    # 6.105 + 2.640 + 2.970


def test_minimo_autonomico_falls_back_to_state_for_unloaded_region(params):
    estatal, autonomico = minimo_for_region({}, params, 'madrid')
    assert estatal == autonomico == 5550.0


def test_minimo_applied_to_each_half_scale_not_base(engine):
    """Mínimo lowers each cuota via its own scale(mínimo); base is unchanged."""
    r0 = engine.compute_modelo100(
        _m100(minimo_estatal=0, minimo_autonomico=0), region='valenciana')
    r1 = engine.compute_modelo100(
        _m100(minimo_estatal=5550, minimo_autonomico=6105), region='valenciana')
    assert r0.summary['base_liquidable'] == r1.summary['base_liquidable'] == 40000.0
    assert r1.summary['cuota_integra'] < r0.summary['cuota_integra']


# ── Edge: loss / negative quarter ─────────────────────────────────────────

def test_modelo130_loss_quarter_floors_at_zero(engine):
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1,
        ytd_income=5000, ytd_deductible_expenses=9000,  # net negative
    ))
    assert result.summary['modelo130_due'] == 0.0
    assert any('carries forward' in n for n in result.notes)


def test_modelo130_retenciones_exceed_gross_floors_at_zero(engine):
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1,
        ytd_income=10000, ytd_deductible_expenses=2000,
        ytd_retenciones=5000,
    ))
    assert result.summary['modelo130_due'] == 0.0


# ── Edge: difícil-justificación cap, EDS eligibility, M130 box-02 toggle ──

def test_dificil_justificacion_capped_at_2000(params):
    assert dificil_justificacion(60000, params, eds_eligible=True) == 2000.0


def test_dificil_justificacion_under_cap(params):
    assert dificil_justificacion(22000, params, eds_eligible=True) == 1100.0


def test_dificil_justificacion_zero_when_not_eds(params):
    assert dificil_justificacion(22000, params, eds_eligible=False) == 0.0


def test_dificil_justificacion_zero_when_loss(params):
    assert dificil_justificacion(-5000, params, eds_eligible=True) == 0.0


def test_modelo130_5pct_toggle_default_on(engine):
    """Default ON (AEAT box 02): the 5% is applied without an explicit flag."""
    on = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=22000, ytd_deductible_expenses=0))
    # 22.000 − 1.100 (5%) = 20.900; ×20% = 4.180.
    assert on.summary['modelo130_due'] == 4180.0


def test_modelo130_5pct_toggle_off(engine):
    off = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=22000, ytd_deductible_expenses=0,
        apply_dificil_justif=False))
    # No 5%: 22.000 ×20% = 4.400.
    assert off.summary['modelo130_due'] == 4400.0


def test_modelo100_no_dificil_when_eds_ineligible(engine):
    """Prior-year INCN > 600.000 € → EDS (and its 5%) does not apply."""
    result = engine.compute_modelo100(_m100(eds_eligible=False), region='valenciana')
    labels = [l['label'] for l in result.lines]
    assert not any('difícil' in l for l in labels)
    assert result.summary['base_liquidable'] == 42000.0


# ── Edge: 20% inicio-actividad reduction (Modelo 100 only) ────────────────

def test_inicio_actividad_reduction_applied(engine):
    result = engine.compute_modelo100(
        _m100(inicio_reduction_eligible=True), region='valenciana')
    by_label = {l['label']: l['amount'] for l in result.lines}
    assert by_label['− 20% reducción inicio actividad'] == -8000.0
    assert by_label['Rendimiento neto actividad'] == 32000.0


def test_inicio_actividad_base_cap(engine):
    result = engine.compute_modelo100(_m100(
        income=150000, deductible_expenses=0, eds_eligible=False,
        inicio_reduction_eligible=True), region='valenciana')
    by_label = {l['label']: l['amount'] for l in result.lines}
    assert by_label['− 20% reducción inicio actividad'] == -20000.0


def test_inicio_not_applied_in_modelo130(engine):
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=60000, ytd_deductible_expenses=18000))
    labels = [l['label'] for l in result.lines]
    assert not any('inicio' in l for l in labels)


# ── Edge: region split + supletoria fallback + foral refusal ──────────────

def test_supletoria_fallback_for_unloaded_region(engine):
    result = engine.compute_modelo100(_m100(), region='madrid')
    assert result.region == 'supletoria'
    assert any('supletoria' in n for n in result.notes)


def test_supletoria_combined_matches_19_to_47_shortcut(engine):
    """Supletoria autonomic mirrors the state half → combined ≈ 19/24/30/37%."""
    result = engine.compute_modelo100(_m100(
        minimo_estatal=0, minimo_autonomico=0, eds_eligible=False,
    ), region='supletoria')
    state = engine.params['state_scale']
    expected = round(2 * apply_scale(42000, state), 2)
    assert result.summary['cuota_integra'] == expected


def test_foral_region_refused(engine):
    with pytest.raises(ValueError, match='foral'):
        engine.compute_modelo100(_m100(), region='navarra')


# ── Edge: joint vs individual ─────────────────────────────────────────────

def test_joint_reduction_applied(engine):
    result = engine.compute_modelo100(_m100(
        declaration_mode='conjunta', family_unit='biparental'),
        region='valenciana')
    by_label = {l['label']: l['amount'] for l in result.lines}
    assert by_label['− Reducción conjunta (biparental)'] == -3400.0


def test_joint_monoparental_reduction(engine):
    result = engine.compute_modelo100(_m100(
        declaration_mode='conjunta', family_unit='monoparental'),
        region='valenciana')
    by_label = {l['label']: l['amount'] for l in result.lines}
    assert by_label['− Reducción conjunta (monoparental)'] == -2150.0


def test_recommend_declaration_returns_both_and_picks_cheaper(engine):
    inp = _m100(retenciones=5000, pagos_fraccionados=4000, family_unit='biparental')
    rec = engine.recommend_declaration(inp, region='valenciana')
    assert set(rec) == {'individual', 'conjunta', 'recommended'}
    ind = rec['individual']['summary']['modelo100_diferencial']
    joint = rec['conjunta']['summary']['modelo100_diferencial']
    assert joint < ind
    assert rec['recommended'] == 'conjunta'


def test_joint_reduction_cannot_make_base_negative(engine):
    result = engine.compute_modelo100(_m100(
        income=2000, deductible_expenses=0, eds_eligible=False,
        declaration_mode='conjunta', family_unit='biparental'),
        region='valenciana')
    assert result.summary['base_liquidable'] == 0.0


# ── mínimo personal y familiar (single-set helper) ────────────────────────

def test_minimo_base_single(params):
    assert compute_minimo({}, params['minimo_estatal']) == 5550.0


def test_minimo_with_children(params):
    assert compute_minimo({'descendientes': 2}, params['minimo_estatal']) == \
        5550.0 + 2400 + 2700


def test_minimo_with_child_under_3(params):
    # design §2.3 / 2024 filing: descendientes block 7.900 = 5.100 + 2.800 (<3
    # increment); full state mínimo adds the 5.550 contribuyente = 13.450.
    assert compute_minimo({'descendientes': 2, 'descendientes_menor3': 1},
                          params['minimo_estatal']) == 13450.0


def test_minimo_cv_block_is_state_times_1_1(params):
    cv = params['minimo_autonomico']['valenciana']
    # 2024 filing: CV descendientes block 8.690 = 7.900 × 1.10; full CV mínimo
    # adds the 6.105 contribuyente = 14.795.
    assert compute_minimo({'descendientes': 2, 'descendientes_menor3': 1}, cv) == 14795.0


def test_minimo_with_age_and_ascendiente(params):
    assert compute_minimo({'age': 67, 'ascendientes65': 1},
                          params['minimo_estatal']) == 5550 + 1150 + 1150


# ── retención default helper (7% new-professional window vs 15%) ──────────

def test_retencion_new_professional_window(params):
    for yr in (2026, 2027, 2028):
        assert default_retencion_rate(is_professional=True, alta_year=2026,
                                      invoice_year=yr, params=params) == 0.07


def test_retencion_general_after_window(params):
    assert default_retencion_rate(is_professional=True, alta_year=2026,
                                  invoice_year=2029, params=params) == 0.15


def test_retencion_zero_for_empresario(params):
    assert default_retencion_rate(is_professional=False, alta_year=2026,
                                  invoice_year=2026, params=params) == 0.0


# ── Year-versioned params (next year = data, not code) ────────────────────

def test_params_are_year_versioned():
    assert load_params(2025)['year'] == 2025
    assert load_params(2026)['year'] == 2026
    assert 2025 in available_years()
    assert 2026 in available_years()


def test_params_carry_split_minimo():
    p = load_params(2026)
    assert 'minimo_estatal' in p
    assert 'valenciana' in p['minimo_autonomico']
    assert p['modelo130']['apply_5pct_dificil_justif'] is True


def test_params_unknown_year_falls_back_to_latest():
    p = load_params(2099)
    assert p['_fallback_from'] == max(available_years())
    assert p['_requested_year'] == 2099


def test_low_income_deduction_optional(engine):
    base = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=20000, ytd_deductible_expenses=5000))
    with_ded = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=20000, ytd_deductible_expenses=5000,
        low_income_deduction=True))
    assert base.summary['modelo130_due'] - with_ded.summary['modelo130_due'] == 100.0
