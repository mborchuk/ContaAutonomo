"""Yearly tax tables say how to check them (year_tables.TableCheck), and the
box numbers the drafts use are compared with the official AEAT record layouts
the app ships — automatically, every test run."""
import pytest

from modules.tax_es_forms import table_checks as tc
from modules.tax_es_forms.aeat_files import LAYOUT_FILES
from modules.tax_es_forms.boxes import (BOX_TABLES, MODELO_130_BOXES, MODELO_303_BOXES,
                                        MODELO_390_BOXES, MODELO_390_TABLES,
                                        MODELO_347_TABLES)
from modules.reta_advisor.table_checks import CHECK_RETA
from year_tables import fallback_notice


@pytest.mark.parametrize('model, boxes', [('303', MODELO_303_BOXES),
                                          ('130', MODELO_130_BOXES),
                                          ('390', MODELO_390_BOXES)])
def test_every_box_the_drafts_use_is_in_the_official_layout(model, boxes):
    """For every exercise the app has a layout for: a renumbered box fails here."""
    for year in LAYOUT_FILES[model]:
        ok, message = tc.boxes_in_layout(model, boxes, year)
        assert ok is True, message


def test_every_year_table_of_390_matches_its_layout():
    for year, table in MODELO_390_TABLES.items():
        if year >= min(LAYOUT_FILES['390']):
            assert tc.boxes_in_layout('390', table, year)[0] is True


def test_2026_has_its_own_390_and_347_tables():
    assert 2026 in MODELO_390_TABLES and 2026 in MODELO_347_TABLES
    assert MODELO_347_TABLES[2026]['threshold'] == 3005.06


def test_a_renumbered_box_is_reported():
    ok, message = tc.boxes_in_layout('390', {**MODELO_390_BOXES, '9999': 'not a casilla'}, 2026)
    assert ok is False and '9999' in message and 'do not use these figures' in message


def test_no_layout_for_the_year_says_so():
    ok, message = tc.boxes_in_layout('390', MODELO_390_BOXES, 2020)
    assert ok is None and 'no official Modelo 390 record layout for 2020' in message


@pytest.mark.parametrize('check', [tc.CHECK_303_130, tc.CHECK_390, tc.CHECK_347, CHECK_RETA])
def test_each_check_tells_a_person_what_to_do(check):
    notice = fallback_notice(2099, 2026, check)
    assert notice['text'].startswith('Figures for 2099 use the 2026 table')
    assert notice['steps'] and notice['holds'] and notice['after_check']
    assert notice['links'] and all(l['url'].startswith('https://') for l in notice['links'])


def test_notice_shows_the_automatic_result():
    year = max(BOX_TABLES)
    notice = fallback_notice(year, year - 1, tc.CHECK_303_130)
    assert notice['auto']['ok'] is True and 'Checked automatically' in notice['auto']['message']
    notice = fallback_notice(2099, 2026, tc.CHECK_390)
    message = notice['auto']['message']   # no 2099 layout: says which one it compared with
    assert 'newest official AEAT record layout the app has (from 2026)' in message
    assert 'newer layout for 2099' in message


def test_a_failing_automatic_check_never_breaks_the_page():
    from year_tables import TableCheck

    def broken(year):
        raise RuntimeError('boom')
    notice = fallback_notice(2099, 2026, TableCheck('x', 'y', ('z',), auto=broken))
    assert notice['auto']['ok'] is None and 'could not run' in notice['auto']['message']


def test_draft_pages_render_the_steps_and_links(loaded_modules, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/tax-forms-draft/347/2099').get_data(as_text=True)
    assert 'How to check the Modelo 347 threshold' in html
    assert 'https://www.boe.es/buscar/act.php?id=BOE-A-2007-15984#a33' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'Unchanged: add the year to MODELO_347_TABLES' in html and 'built-in method' not in html
    html = client.get('/tax-forms-draft/347/2026').get_data(as_text=True)
    assert 'How to check the Modelo 347 threshold' not in html      # 2026 has its table
