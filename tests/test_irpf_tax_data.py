"""IRPF figures in Tax Data: the gastos de difícil justificación percentage
and cap and the Modelo 130 rate are a yearly table the owner confirms or
changes; the IRPF Estimator uses what was saved for that year (synthetic
values throughout)."""
import pytest

YEAR = 2099          # no params file: uses the 2026 figures
TABLE = 'es.irpf_figures'


@pytest.fixture
def owner(app, loaded_modules, client):
    from contaautonomo.app import db
    entry = loaded_modules.modules['tax_data'].Entry
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    yield client
    entry.query.delete()
    db.session.commit()
    db.session.expunge_all()


def _irpf(loaded_modules):
    return loaded_modules.modules['tax_es_irpf']


def test_tax_data_lists_the_irpf_figures_with_the_2026_values(owner):
    html = owner.get('/tax-data/?year=2026').get_data(as_text=True)
    assert 'IRPF figures (estimación directa simplificada)' in html
    assert 'BOE-A-2026-24701' in html
    for name, value in (('v_dificil_pct', '10'), ('v_dificil_cap', '3000'),
                        ('v_modelo130_rate', '20')):
        assert f'name="{name}"' in html
        assert value in html.split(f'name="{name}"')[1].split('>')[0]


def test_a_year_on_earlier_figures_warns_until_confirmed(owner, loaded_modules):
    html = owner.get(f'/irpf-es/?year={YEAR}').get_data(as_text=True)
    assert 'confirm it or enter the new values in Tax Data' in html
    html = owner.get(f'/irpf-es/modelo130/{YEAR}/1').get_data(as_text=True)
    assert 'confirm it or enter the new values in Tax Data' in html

    owner.post(f'/tax-data/{TABLE}/{YEAR}')
    html = owner.get(f'/irpf-es/?year={YEAR}').get_data(as_text=True)
    assert 'confirm it or enter the new values in Tax Data' not in html


def test_a_year_with_its_own_figures_does_not_warn(owner):
    html = owner.get('/irpf-es/?year=2026').get_data(as_text=True)
    assert 'confirm it or enter the new values in Tax Data' not in html


def test_new_figures_are_used_for_that_year_only(owner, loaded_modules):
    irpf = _irpf(loaded_modules)
    owner.post(f'/tax-data/{TABLE}/{YEAR}',
               data={'v_dificil_pct': '12', 'v_dificil_cap': '3.500,00',
                     'v_modelo130_rate': '19'})
    params = irpf._params(YEAR)
    assert params['eds']['dificil_justificacion_pct'] == pytest.approx(0.12)
    assert params['eds']['dificil_justificacion_cap'] == 3500.0
    assert params['modelo130']['rate'] == pytest.approx(0.19)
    assert params['_confirmed_on']
    assert irpf._params(2026)['eds']['dificil_justificacion_pct'] == pytest.approx(0.10)
    # The shipped file is not changed by the override.
    from modules.tax_es_irpf.params_store import load_params
    assert load_params(YEAR)['eds']['dificil_justificacion_pct'] == pytest.approx(0.10)

    from modules.tax_es_irpf.engine import Modelo130Input
    result = irpf._engine(YEAR).compute_modelo130(Modelo130Input(
        year=YEAR, quarter=1, ytd_income=10000.0, ytd_deductible_expenses=0.0))
    # 10.000 − 1.200 (12 %) = 8.800; × 19 % = 1.672.
    assert result.summary['modelo130_due'] == 1672.0


@pytest.mark.parametrize('data, problem', [
    ({'v_dificil_cap': '0'}, 'The annual cap must be above zero.'),
    ({'v_modelo130_rate': '100'}, 'The Modelo 130 rate must be between 0 and 100 %.'),
    ({'v_dificil_pct': '100'}, 'The difícil justificación percentage must be below 100 %.'),
])
def test_impossible_figures_are_refused(owner, loaded_modules, data, problem):
    response = owner.post(f'/tax-data/{TABLE}/{YEAR}', data=data, follow_redirects=True)
    assert problem in response.get_data(as_text=True)
    status = loaded_modules.functions.call('taxdata.year_values', table=TABLE, year=YEAR)
    assert status['confirmed'] is False


def test_settings_say_the_year_percentage(owner):
    html = owner.get('/settings').get_data(as_text=True)
    assert 'Apply the gastos de difícil justificación (' in html
    assert 'Taxes → Tax Data' in html


def test_without_tax_data_the_shipped_figures_are_used(owner, loaded_modules, monkeypatch):
    functions = loaded_modules.functions
    real = functions.available
    monkeypatch.setattr(functions, 'available',
                        lambda name: not name.startswith('taxdata.') and real(name))
    params = _irpf(loaded_modules)._params(YEAR)
    assert params['_fallback_from'] == 2026 and '_confirmed_on' not in params
    html = owner.get(f'/irpf-es/?year={YEAR}').get_data(as_text=True)
    assert 'Traceback' not in html
