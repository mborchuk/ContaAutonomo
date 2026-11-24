"""G-3 — what-if simulator: unchanged = real, changes move results as the
engines say, and the database is never written."""
from dataclasses import replace
from datetime import date

import pytest

from modules.tax_es_irpf.engine import Modelo130Input, minimo_for_region
from modules.tax_es_irpf.simulator import (
    RESULT_KEYS, Baseline, Scenario, compare, quota_from_base, run, unchanged)

YEAR = 2034
TODAY = date(YEAR, 6, 15)       # Q2 open, 6 months left


@pytest.fixture
def synthetic_year(loaded_modules, app):
    from contaautonomo.app import Customer, Expense, Invoice, Settings, db
    settings = Settings.query.first()
    if settings is None:
        settings = Settings()
        db.session.add(settings)
    settings.social_security_monthly = 300.0
    cust = Customer(name='Simulator Test SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    for number, d, amount in (('SIM-1', date(YEAR, 1, 20), 9000.0),
                              ('SIM-2', date(YEAR, 3, 10), 6000.0),
                              ('SIM-3', date(YEAR, 5, 5), 7000.0)):
        db.session.add(Invoice(invoice_number=number, client_name=cust.name,
                               customer_id=cust.id, amount_usd=0.0, amount_eur=amount,
                               exchange_rate=1.0, invoice_date=d, status='draft',
                               currency='EUR'))
    db.session.add(Expense(amount=1210.0, currency='EUR', category='Software',
                           description='Synthetic tools', expense_date=date(YEAR, 2, 2),
                           net_amount=1000.0, vat_rate=21.0, vat_amount=210.0,
                           deductible=True, deductible_pct=100.0))
    db.session.commit()
    yield loaded_modules
    settings.social_security_monthly = 0.0
    db.session.commit()


def _irpf(mm):
    return mm.modules['tax_es_irpf']


def test_no_changes_gives_the_real_figures(synthetic_year):
    out = _irpf(synthetic_year).simulate(None, TODAY)
    assert out['simulated'] == out['real']
    assert all(out['difference'][k] == 0.0 for k in RESULT_KEYS)


def test_real_figures_match_the_estimator_and_the_reta_advisor(synthetic_year):
    out = _irpf(synthetic_year).simulate(None, TODAY)
    irpf = _irpf(synthetic_year)
    m100 = irpf.modelo100_result(YEAR)['summary']
    assert out['real']['irpf_year'] == pytest.approx(m100['cuota_liquida'])
    reta = synthetic_year.modules['reta_advisor'].projection(TODAY)
    assert out['real']['regularization'] == pytest.approx(reta['regularization_delta'])


def test_income_change_moves_each_result_as_the_engines_say(synthetic_year):
    irpf = _irpf(synthetic_year)
    base, _reta_fn = irpf.simulator_baseline(TODAY)
    scen = replace(unchanged(base), income=base.income + 1000.0)
    out = irpf.simulate(scen, TODAY)
    engine = irpf._engine(YEAR)

    expected_m130 = engine.compute_modelo130(Modelo130Input(
        year=YEAR, quarter=4, ytd_income=base.income + 1000.0,
        ytd_deductible_expenses=base.m100_input.deductible_expenses,
        ytd_retenciones=base.m100_input.retenciones, prior_payments=base.paid_130_closed,
        eds_eligible=base.eds_eligible, apply_dificil_justif=base.apply_dificil_justif_m130,
        low_income_deduction=base.low_income_deduction)).summary['modelo130_due']
    expected_m100 = engine.compute_modelo100(replace(
        base.m100_input, income=base.income + 1000.0,
        pagos_fraccionados=base.paid_130_closed + expected_m130), base.region).summary
    expected_reta = synthetic_year.modules['reta_advisor'].projection(
        TODAY, 1000.0, 0.0, base.monthly_quota)['regularization_delta']

    sim = out['simulated']
    assert sim['m130_remaining'] == pytest.approx(expected_m130)
    assert sim['irpf_year'] == pytest.approx(expected_m100['cuota_liquida'])
    assert sim['m100_result'] == pytest.approx(expected_m100['modelo100_diferencial'])
    assert sim['regularization'] == pytest.approx(expected_reta)
    diff = out['difference']
    assert diff['irpf_year'] > 0 and diff['m130_remaining'] > 0
    assert diff['net_income'] == pytest.approx(
        1000.0 - diff['irpf_year'] - diff['regularization'], abs=0.02)


def test_region_change_uses_that_comunidad(synthetic_year):
    irpf = _irpf(synthetic_year)
    base, _ = irpf.simulator_baseline(TODAY)
    out = irpf.simulate(replace(unchanged(base), region='madrid'), TODAY)
    params = irpf._params(YEAR)
    estatal, auton = minimo_for_region(irpf._profile_dict(irpf._read_profile()), params, 'madrid')
    expected = irpf._engine(YEAR).compute_modelo100(replace(
        base.m100_input, minimo_estatal=estatal, minimo_autonomico=auton,
        regional_deductions_amount=0.0,
        pagos_fraccionados=base.paid_130_closed + out['simulated']['m130_remaining']),
        'madrid')
    assert out['simulated']['irpf_year'] == pytest.approx(expected.summary['cuota_liquida'])
    assert out['simulated']['notes'] == expected.notes   # says which scale was used


def test_reta_base_change_applies_to_the_months_left(synthetic_year):
    irpf = _irpf(synthetic_year)
    base, _ = irpf.simulator_baseline(TODAY)
    assert base.months_remaining == 6 and base.contribution_rate_pct == 31.5
    new_quota = quota_from_base(base.reta_base + 200.0, base.contribution_rate_pct)
    out = irpf.simulate(replace(unchanged(base), monthly_quota=new_quota), TODAY)
    delta = (new_quota - base.monthly_quota) * 6
    assert out['simulated']['reta_in_irpf'] == pytest.approx(out['real']['reta_in_irpf'] + delta)
    assert out['difference']['irpf_year'] < 0     # more RETA deducted, less IRPF


def test_without_reta_advisor_regularization_is_missing(synthetic_year, monkeypatch):
    monkeypatch.delitem(synthetic_year.modules, 'reta_advisor')
    out = _irpf(synthetic_year).simulate(None, TODAY)
    assert out['real']['regularization'] is None
    assert out['difference']['regularization'] is None


def test_pure_run_with_stub_engine_inputs():
    """run() only calls the engines it is given: a stub proves the plumbing."""
    class Engine:
        def compute_modelo130(self, inp):
            return type('R', (), {'summary': {'modelo130_due': inp.ytd_income * 0.1}})()

        def compute_modelo100(self, inp, region):
            return type('R', (), {'summary': {'cuota_liquida': inp.income * 0.2,
                                              'modelo100_diferencial': 1.0},
                                  'region': region, 'notes': []})()
    from modules.tax_es_irpf.engine import Modelo100Input
    m100 = Modelo100Input(year=2034, income=100.0, deductible_expenses=30.0,
                          minimo_estatal=0, minimo_autonomico=0)
    base = Baseline(year=2034, m100_input=m100, region='x', reta_in_irpf=10.0,
                    paid_130_closed=0.0, months_remaining=0, monthly_quota=0.0)
    out = run(Engine(), base, Scenario(100.0, 20.0, 'x', 0.0), None, None, None)
    assert out['irpf_year'] == 20.0 and out['m130_remaining'] == 10.0
    assert out['net_income'] == 100.0 - 20.0 - 10.0 - 20.0
    assert compare(out, out)['net_income'] == 0.0


# --- the page and the database --------------------------------------------- #

def _snapshot(db):
    from sqlalchemy import text
    tables = sorted(db.metadata.tables)
    out = {}
    with db.engine.connect() as conn:
        names = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))}
        for name in tables:
            if name in names:
                out[name] = sorted(map(repr, conn.execute(text(f'SELECT * FROM "{name}"'))))
    return out


def test_database_is_unchanged_after_use(synthetic_year, client):
    from contaautonomo.app import db
    irpf = _irpf(synthetic_year)
    irpf.IrpfProfile.query.delete()     # a first visit must not create a profile
    db.session.commit()
    before = _snapshot(db)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    for query in ('', '?income=50000&expenses=2000&region=madrid&reta_base=1500',
                  '?income=abc&reta_base=-5'):
        assert client.get('/irpf-es/simulator' + query).status_code == 200
    irpf.simulate(None, TODAY)
    db.session.commit()
    assert _snapshot(db) == before


def test_page_renders_scenario_and_says_nothing_is_saved(synthetic_year, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/irpf-es/simulator?income=12345&region=madrid').get_data(as_text=True)
    assert 'Nothing is saved' in html and 'Estimate, not tax advice.' in html
    assert 'value="12345.00"' in html
    assert 'tarifa plana' in html
    bad = client.get('/irpf-es/simulator?income=abc').get_data(as_text=True)
    assert 'must be a number' in bad


def test_simulator_requires_login(loaded_modules, client):
    resp = client.get('/irpf-es/simulator')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']
