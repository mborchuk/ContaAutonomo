"""Modelo 130 from 2027 (Real Decreto-ley 43/2026): 19 % below 250,000 EUR of
net turnover two years before, no Modelo 130 below 85,000 EUR when all that
year's payments were refunded, and the option for AEAT-calculated payments.
Synthetic figures throughout."""
from datetime import date

import pytest

from modules.tax_es_irpf.engine import (IrpfEngine, Modelo130Input, modelo130_plan,
                                        reference_year)
from modules.tax_es_irpf.params_store import load_params


@pytest.fixture
def params_2027():
    return load_params(2027)        # no 2027 file: the 2026 file, with its 2027 rules


def _m130(**kw):
    defaults = dict(year=2027, quarter=1, ytd_income=10000.0, ytd_deductible_expenses=0.0,
                    apply_dificil_justif=False)
    defaults.update(kw)
    return Modelo130Input(**defaults)


# ── Pure rules ───────────────────────────────────────────────────────────

def test_the_reference_is_two_years_before():
    """The 2026 renta closes on 30 June 2027, after the first 2027 window."""
    assert reference_year(2027) == 2025


def test_2026_keeps_20pct_whatever_the_turnover():
    plan = modelo130_plan(load_params(2026), 2026, ref_turnover=1000.0, ref_refunded=True)
    assert plan['mode'] == 'file' and plan['rate'] == 0.20 and plan['notes'] == []


@pytest.mark.parametrize('turnover, rate', [(249999.99, 0.19), (250000.0, 0.20),
                                            (0.0, 0.19)])
def test_19pct_below_250000(params_2027, turnover, rate):
    plan = modelo130_plan(params_2027, 2027, ref_turnover=turnover)
    assert plan['mode'] == 'file' and plan['rate'] == rate


def test_unknown_turnover_keeps_20pct_and_says_so(params_2027):
    plan = modelo130_plan(params_2027, 2027, ref_turnover=None)
    assert plan['rate'] == 0.20
    assert 'Enter it in Settings → IRPF' in plan['notes'][0]


def test_no_130_below_85000_only_when_all_payments_were_refunded(params_2027):
    assert modelo130_plan(params_2027, 2027, ref_turnover=60000.0,
                          ref_refunded=True)['mode'] == 'exempt'
    assert modelo130_plan(params_2027, 2027, ref_turnover=60000.0,
                          ref_refunded=False)['mode'] == 'file'
    assert modelo130_plan(params_2027, 2027, ref_turnover=85000.0,
                          ref_refunded=True)['mode'] == 'file'


def test_aeat_mode_from_2027_only(params_2027):
    assert modelo130_plan(params_2027, 2027, ref_turnover=100000.0,
                          aeat_mode=True)['mode'] == 'aeat'
    assert modelo130_plan(load_params(2026), 2026, aeat_mode=True)['mode'] == 'file'


def test_engine_applies_the_plan(params_2027):
    engine = IrpfEngine(params_2027)
    result = engine.compute_modelo130(_m130(ref_turnover=100000.0))
    assert result.summary['modelo130_due'] == 1900.0       # 10.000 × 19 %
    assert result.summary['rate'] == 0.19
    assert any(line['label'] == '× 19%' for line in result.lines)

    exempt = engine.compute_modelo130(_m130(ref_turnover=60000.0, ref_refunded=True))
    assert exempt.summary['modelo130_due'] == 0.0 and exempt.summary['mode'] == 'exempt'

    aeat = engine.compute_modelo130(_m130(ref_turnover=100000.0, aeat_mode=True,
                                          aeat_amount=321.0))
    assert aeat.summary['modelo130_due'] == 321.0 and aeat.summary['mode'] == 'aeat'
    estimate = engine.compute_modelo130(_m130(ref_turnover=100000.0, aeat_mode=True))
    assert estimate.summary['modelo130_due'] == 1900.0
    assert any('own estimate' in n for n in estimate.notes)


def test_tax_data_values_change_the_rules(params_2027):
    from modules.tax_es_irpf import yearly
    keys = [f['key'] for f in yearly.fields(params_2027, 2027)]
    assert {'modelo130_reduced_rate', 'modelo130_reduced_below',
            'modelo130_exempt_below'} <= set(keys)
    assert 'modelo130_reduced_rate' not in [f['key'] for f in
                                            yearly.fields(load_params(2026), 2026)]
    params = load_params(2027, override=yearly.override(
        {'modelo130_reduced_rate': 18.0, 'modelo130_exempt_below': 50000.0}))
    assert modelo130_plan(params, 2027, ref_turnover=100000.0)['rate'] == 0.18
    assert modelo130_plan(params, 2027, ref_turnover=60000.0,
                          ref_refunded=True)['mode'] == 'file'
    assert yearly.validate({'modelo130_exempt_below': 0}) == \
        'A turnover limit must be above zero.'


# ── Module, on synthetic data ────────────────────────────────────────────

@pytest.fixture
def irpf(app, loaded_modules, monkeypatch):
    from contaautonomo.app import Customer, Invoice, Settings, db
    module = loaded_modules.modules['tax_es_irpf']
    # The years Settings offers move with today's date; pin them.
    monkeypatch.setattr(module, '_m130_years', lambda today=None: [2027, 2028])
    if not Settings.query.first():
        db.session.add(Settings())
    customer = Customer(name='Synthetic Client SL', tax_type='standard')
    db.session.add(customer)
    db.session.flush()
    for number, day, amount in (('S-1', date(2025, 3, 10), 30000.0),
                                ('S-2', date(2025, 9, 10), 30000.0),
                                ('S-3', date(2027, 2, 10), 10000.0)):
        db.session.add(Invoice(invoice_number=number, client_name=customer.name,
                               customer_id=customer.id, amount_usd=amount,
                               amount_eur=amount, exchange_rate=1.0, currency='EUR',
                               invoice_date=day, status='paid'))
    db.session.commit()
    return module


def test_turnover_comes_from_the_invoices_of_the_reference_year(irpf):
    assert irpf._ref_turnover(2027, irpf._get_profile()) == (60000.0, 'invoices')
    plan = irpf.modelo130_plan(2027)
    assert plan['rate'] == 0.19 and plan['source'] == 'invoices'


def test_turnover_of_a_year_started_midway_is_raised_to_the_year(irpf):
    profile = irpf._get_profile()
    profile.start_date = date(2025, 7, 1)          # S-1 falls before: not counted
    turnover, source = irpf._ref_turnover(2027, profile)
    assert source == 'invoices'
    assert turnover == pytest.approx(30000.0 * 365 / 184, abs=0.01)
    profile.start_date = date(2026, 2, 1)          # no activity in 2025
    assert irpf._ref_turnover(2027, profile) == (0.0, 'no activity')


def test_settings_answers_count_for_their_year_only(irpf, client):
    from contaautonomo.app import db
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    profile = irpf._get_profile()
    irpf._save_m130(profile, {'irpf_m130_year': '2027', 'irpf_m130_ref_turnover': '70000',
                              'irpf_m130_refunded': 'on'})
    db.session.commit()
    assert irpf.modelo130_plan(2027)['mode'] == 'exempt'
    boxes, rate = irpf.modelo130_boxes(2027, 1)
    assert boxes['07'] == 0.0 and rate == 0.19
    assert irpf.modelo130_plan(2028)['mode'] == 'file'        # answers were for 2027
    # Tax Forms shows why nothing is due.
    html = client.get('/tax-forms-draft/130/2027/1').get_data(as_text=True)
    assert 'No Modelo 130 is due for 2027' in html
    # Back to the invoices: an empty field.
    irpf._save_m130(profile, {'irpf_m130_year': '2027', 'irpf_m130_ref_turnover': ''})
    db.session.commit()
    assert irpf.modelo130_plan(2027)['mode'] == 'file'


def test_aeat_amount_is_the_payment_and_feeds_the_renta(irpf):
    from contaautonomo.app import db
    profile = irpf._get_profile()
    irpf._save_m130(profile, {'irpf_m130_year': '2027', 'irpf_m130_aeat': 'on',
                              'irpf_m130_aeat_amount': '250,00'})
    db.session.commit()
    boxes, _rate = irpf.modelo130_boxes(2027, 1)
    assert boxes['07'] == 250.0
    m100 = irpf._build_modelo100_input(2027, profile)
    assert m100.pagos_fraccionados == 1000.0               # 4 × 250


def test_the_years_offered_start_in_2027(loaded_modules):
    module = loaded_modules.modules['tax_es_irpf']
    assert module._m130_years(date(2026, 11, 25)) == [2027]
    assert module._m130_years(date(2027, 12, 1)) == [2027, 2028]


def test_a_year_outside_the_choices_is_not_saved(irpf):
    profile = irpf._get_profile()
    irpf._save_m130(profile, {'irpf_m130_year': '2020', 'irpf_m130_refunded': 'on'})
    assert profile.m130_year is None


def test_settings_tab_shows_the_questions_and_the_result(irpf, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/settings').get_data(as_text=True)
    assert 'Modelo 130 from 2027 (Real Decreto-ley 43/2026)' in html
    assert 'name="irpf_m130_ref_turnover"' in html
    assert 'From the invoices in the app: 60000.00' in html
    assert 'file the Modelo 130 at 19 %' in html
