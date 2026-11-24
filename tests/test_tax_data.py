"""Tax Data module: the owner confirms a yearly tax table for a new year, or
enters its new values, in the app; the modules that use the table stop warning
and use those values (synthetic values throughout)."""
import pytest

YEAR = 2099          # no module has tables for it: every table is a fallback


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


def _values(loaded_modules, table):
    return loaded_modules.functions.call('taxdata.year_values', table=table, year=YEAR)


def test_page_lists_every_declared_table_with_how_to_check_it(owner):
    html = owner.get(f'/tax-data/?year={YEAR}').get_data(as_text=True)
    for name in ('Modelo 303 and 130 boxes', 'Modelo 390 casillas',
                 'Modelo 347 threshold', 'RETA brackets and contribution rate'):
        assert name in html
    assert 'Uses the 2026 table — not confirmed' in html
    assert 'Confirm unchanged for 2099' in html          # box tables: confirm only
    assert 'name="v_threshold"' in html and 'name="v_quota_0"' in html
    assert 'https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984#a33' in html


def test_confirming_unchanged_stops_the_warning(owner, loaded_modules):
    assert _values(loaded_modules, 'es.boxes_303_130')['confirmed'] is False
    html = owner.get(f'/tax-forms-draft/303/{YEAR}/1').get_data(as_text=True)
    assert 'confirm it or enter the new values in Tax Data' in html

    response = owner.post(f'/tax-data/es.boxes_303_130/{YEAR}')
    assert response.status_code == 302
    status = _values(loaded_modules, 'es.boxes_303_130')
    assert status['confirmed'] is True and status['values'] == {}
    draft = loaded_modules.modules['tax_es_forms']._draft_303(YEAR, 1)
    assert draft['table_fallback'] is False and draft['table_confirmed_on']
    html = owner.get(f'/tax-forms-draft/303/{YEAR}/1').get_data(as_text=True)
    assert 'Tax Data' not in html.split('Non-dismissible')[0].split('</h1>')[-1]


def test_a_new_347_threshold_is_used_for_that_year(owner, loaded_modules):
    owner.post(f'/tax-data/es.threshold_347/{YEAR}', data={'v_threshold': '4.000,00'})
    assert _values(loaded_modules, 'es.threshold_347')['values'] == {'threshold': 4000.0}
    draft = loaded_modules.modules['tax_es_forms']._draft_347(YEAR)
    assert draft['table_fallback'] is False
    assert draft['threshold'] == 4000.0 and 'threshold entered in Tax Data' in draft['source']
    # Another year keeps the threshold the app has.
    assert loaded_modules.modules['tax_es_forms']._draft_347(2026)['table_fallback'] is False


def test_new_reta_values_are_used_and_checked(owner, loaded_modules):
    reta = loaded_modules.modules['reta_advisor']
    _ty, brackets, rate, fallback = reta._table(YEAR)
    assert fallback is True
    owner.post(f'/tax-data/es.reta/{YEAR}',
               data={'v_quota_0': '210,50', 'v_rate': '32', 'v_upper_0': f'{brackets[0][1]:.2f}'})
    _ty, new, new_rate, fallback = reta._table(YEAR)
    assert fallback is False and new_rate == 32.0
    assert new[0][2] == 210.5 and new[1][2] == brackets[1][2]
    assert _values(loaded_modules, 'es.reta')['values'] == {'quota_0': 210.5, 'rate': 32.0}


def test_impossible_reta_values_are_refused(owner, loaded_modules):
    reta = loaded_modules.modules['reta_advisor']
    _ty, brackets, _rate, _f = reta._table(YEAR)
    response = owner.post(f'/tax-data/es.reta/{YEAR}',
                          data={'v_upper_1': f'{brackets[0][1] - 1:.2f}'},
                          follow_redirects=True)
    assert 'Each bracket must end above the one before it' in response.get_data(as_text=True)
    assert _values(loaded_modules, 'es.reta')['confirmed'] is False
    owner.post(f'/tax-data/es.threshold_347/{YEAR}', data={'v_threshold': 'abc'})
    assert _values(loaded_modules, 'es.threshold_347')['confirmed'] is False


def test_the_confirmation_can_be_removed(owner, loaded_modules):
    owner.post(f'/tax-data/es.casillas_390/{YEAR}')
    assert _values(loaded_modules, 'es.casillas_390')['confirmed'] is True
    owner.post(f'/tax-data/es.casillas_390/{YEAR}/undo')
    assert _values(loaded_modules, 'es.casillas_390')['confirmed'] is False


def test_unknown_table_or_year_is_not_found(owner, loaded_modules):
    """The app answers 404 with 'Page not found' and the dashboard."""
    for response in (owner.post(f'/tax-data/es.nothing/{YEAR}', follow_redirects=True),
                     owner.get('/tax-data/?year=1900', follow_redirects=True)):
        assert 'Page not found' in response.get_data(as_text=True)
    assert _values(loaded_modules, 'es.nothing')['confirmed'] is False


def test_without_tax_data_the_pages_keep_the_short_warning(owner, loaded_modules, monkeypatch):
    functions = loaded_modules.functions
    real = functions.available
    monkeypatch.setattr(functions, 'available',
                        lambda name: not name.startswith('taxdata.') and real(name))
    html = owner.get(f'/tax-forms-draft/347/{YEAR}').get_data(as_text=True)
    assert 'Figures use the 2026 table — verify' in html
    assert 'Tax Data' not in html.split('</h1>')[1].split('Non-dismissible')[0]
    html = owner.get('/reta-advisor/').get_data(as_text=True)
    assert 'Traceback' not in html


@pytest.mark.parametrize('text, number', [('3005,06', 3005.06), ('3.005,06', 3005.06),
                                          ('3,005.06', 3005.06), ('3005.06', 3005.06),
                                          ('4000', 4000.0), ('abc', None), ('', None)])
def test_amounts_are_read_with_either_decimal_separator(text, number):
    from modules.tax_data.index import _number
    assert _number(text) == number


def test_log_lines_cannot_be_forged_from_the_url():
    from modules.tax_data.index import _for_log
    assert _for_log('es.x\r\nINFO forged entry') == 'es.x  INFO forged entry'
    assert len(_for_log('x' * 500)) == 100
