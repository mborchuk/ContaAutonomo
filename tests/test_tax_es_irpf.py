"""Unit tests for the tax_es_irpf engine (Modelo 130 + Modelo 100).

Worked examples with synthetic figures, each box checked by hand against the
year's scales (the arithmetic is in the comments). Plus the edge cases the
design calls out: loss
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


# ── Worked example: Modelo 130, 1T 2026 (synthetic) ──────────────────────

def test_modelo130_worked_1t_2026(engine):
    # Box 02 given as a total (difícil justificación inside it), so the
    # engine's own deduction is off to avoid counting it twice.
    result = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1,
        ytd_income=15000.00, ytd_deductible_expenses=2500.00,
        apply_dificil_justif=False,
    ))
    assert result.summary['rendimiento_neto_ytd'] == 12500.00  # box 03: 15.000 − 2.500
    assert result.summary['modelo130_due'] == 2500.00          # 12.500 × 20 %


# ── Worked example: Modelo 130, 4T 2025 (synthetic) ──────────────────────

def test_modelo130_worked_4t_2025():
    # 60.000 − 4.000 = 56.000; 5 % = 2.800, capped at 2.000 (2025 figures)
    # → box 03 54.000; × 20 % = 10.800; − 8.000 paid in Q1–Q3 = 2.800.
    engine = IrpfEngine(load_params(2025))
    result = engine.compute_modelo130(Modelo130Input(
        year=2025, quarter=4,
        ytd_income=60000.00, ytd_deductible_expenses=4000.00,
        prior_payments=8000.00,
    ))
    assert result.summary['rendimiento_neto_ytd'] == 54000.00  # box 03
    assert result.summary['modelo130_due'] == 2800.00          # box 07


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


# ── Worked example: Modelo 100, 2025 conjunta (synthetic) ────────────────

def test_modelo100_worked_2025_conjunta():
    """Inicio 20 %, conjunta, difícil cap, split state/CV mínimos, CV scale,
    pagos-fraccionados credit — every line worked out by hand below.
    """
    engine = IrpfEngine(load_params(2025))
    result = engine.compute_modelo100(_m100(
        year=2025, income=50000.00, deductible_expenses=6000.00,
        minimo_estatal=7950, minimo_autonomico=8745,  # state / CV, one child
        retenciones=0, pagos_fraccionados=8000.00,
        inicio_reduction_eligible=True,
        declaration_mode='conjunta', family_unit='biparental',
    ), region='valenciana')

    by_label = {l['label']: l['amount'] for l in result.lines}
    # 50.000 − 6.000 = 44.000; 5 % = 2.200, capped at 2.000
    assert by_label['5% difícil justificación (cap 2000)'] == -2000.0
    # (44.000 − 2.000) × 20 % = 8.400 → 33.600
    assert by_label['− 20% reducción inicio actividad'] == -8400.00
    assert by_label['Rendimiento neto actividad'] == 33600.00
    assert by_label['− Reducción conjunta (biparental)'] == -3400.0
    assert by_label['Base liquidable general'] == 30200.00
    # State: 12.450 × 9,5 % + 7.750 × 12 % + 10.000 × 15 % = 3.612,75,
    # minus the mínimo 7.950 × 9,5 % = 755,25
    assert by_label['Cuota estatal'] == 2857.50
    # CV: 12.000 × 9 % + 10.000 × 12 % + 8.200 × 15 % = 3.510,00,
    # minus the mínimo 8.745 × 9 % = 787,05
    assert by_label['Cuota autonómica (valenciana)'] == 2722.95

    s = result.summary
    assert s['cuota_integra'] == 5580.45
    assert s['modelo100_diferencial'] == -2419.55      # 5.580,45 − 8.000
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
    """CV mínimo is the state block × 1.10 (the params' CV amounts)."""
    profile = {'descendientes': 1}  # contribuyente + one child
    estatal, autonomico = minimo_for_region(profile, params, 'valenciana')
    assert estatal == 7950.0        # 5.550 + 2.400
    assert autonomico == 8745.0     # 6.105 + 2.640


def test_minimo_autonomico_falls_back_to_state_for_unloaded_region(params):
    estatal, autonomico = minimo_for_region({}, params, 'madrid')
    assert estatal == autonomico == 5550.0


def test_minimo_applied_to_each_half_scale_not_base(engine):
    """Mínimo lowers each cuota via its own scale(mínimo); base is unchanged."""
    r0 = engine.compute_modelo100(
        _m100(minimo_estatal=0, minimo_autonomico=0), region='valenciana')
    r1 = engine.compute_modelo100(
        _m100(minimo_estatal=5550, minimo_autonomico=6105), region='valenciana')
    # 60.000 − 18.000 − 3.000 (10 %, capped at 3.000 in 2026)
    assert r0.summary['base_liquidable'] == r1.summary['base_liquidable'] == 39000.0
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

def test_dificil_justificacion_2026_is_10pct_capped_at_3000(params):
    """Real Decreto-ley 43/2026: 10 % and 3.000 for the 2026 renta."""
    assert dificil_justificacion(60000, params, eds_eligible=True) == 3000.0
    assert dificil_justificacion(22000, params, eds_eligible=True) == 2200.0


def test_dificil_justificacion_2025_is_5pct_capped_at_2000():
    params = load_params(2025)
    assert dificil_justificacion(60000, params, eds_eligible=True) == 2000.0
    assert dificil_justificacion(22000, params, eds_eligible=True) == 1100.0


def test_2026_modelo130_first_three_quarters_keep_5pct_and_2000(params):
    """The new figures count from the 4T 2026 pago fraccionado on."""
    for quarter in (1, 2, 3):
        assert dificil_justificacion(60000, params, eds_eligible=True,
                                     quarter=quarter) == 2000.0
        assert dificil_justificacion(22000, params, eds_eligible=True,
                                     quarter=quarter) == 1100.0
    assert dificil_justificacion(22000, params, eds_eligible=True, quarter=4) == 2200.0


def test_a_later_year_on_2026_figures_does_not_keep_the_quarter_rule():
    params = load_params(2099)          # no file: uses 2026's
    assert params['_fallback_from'] == 2026
    assert 'modelo130_earlier_quarters' not in params['eds']
    assert dificil_justificacion(22000, params, eds_eligible=True, quarter=1) == 2200.0
    assert 'modelo130_earlier_quarters' in load_params(2026)['eds']   # cache untouched


def test_dificil_justificacion_zero_when_not_eds(params):
    assert dificil_justificacion(22000, params, eds_eligible=False) == 0.0


def test_dificil_justificacion_zero_when_loss(params):
    assert dificil_justificacion(-5000, params, eds_eligible=True) == 0.0


def test_modelo130_5pct_toggle_default_on(engine):
    """Default ON (AEAT box 02): the 5% is applied without an explicit flag."""
    on = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=1, ytd_income=22000, ytd_deductible_expenses=0))
    # 1T 2026 still 5 %: 22.000 − 1.100 = 20.900; ×20% = 4.180.
    assert on.summary['modelo130_due'] == 4180.0
    assert any(l['label'] == '5% difícil justificación (cap 2000)' for l in on.lines)
    q4 = engine.compute_modelo130(Modelo130Input(
        year=2026, quarter=4, ytd_income=22000, ytd_deductible_expenses=0))
    # 4T 2026: 22.000 − 2.200 (10 %) = 19.800; ×20% = 3.960.
    assert q4.summary['modelo130_due'] == 3960.0
    assert any(l['label'] == '10% difícil justificación (cap 3000)' for l in q4.lines)


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
    # 60.000 − 18.000 − 3.000 (10 %, cap) = 39.000; − 20 % = 31.200
    assert by_label['− 20% reducción inicio actividad'] == -7800.0
    assert by_label['Rendimiento neto actividad'] == 31200.0


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


def test_params_year_cannot_shape_the_file_path():
    from modules.tax_es_irpf.params_store import _load_file
    with pytest.raises(ValueError):
        _load_file('../../../../etc/passwd')
