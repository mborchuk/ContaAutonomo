"""Yearly tax tables say how to check them (the Tax Data module shows it), and the
box numbers the drafts use are compared with the official AEAT record layouts
the app ships — automatically, every test run."""
import pytest

from modules.tax_es_forms import table_checks as tc
from modules.tax_es_forms.aeat_files import LAYOUT_FILES
from modules.tax_es_forms.boxes import (BOX_TABLES, MODELO_130_BOXES, MODELO_303_BOXES,
                                        MODELO_390_BOXES, MODELO_390_TABLES,
                                        MODELO_347_TABLES)
from modules.reta_advisor.table_checks import CHECK_RETA


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
    assert check['id'].startswith('es.') and check['name']
    assert check['steps'] and check['holds'] and check['after_check']
    assert check['links'] and all(url.startswith('https://') for _label, url in check['links'])


def test_the_automatic_check_compares_with_the_newest_layout():
    year = max(BOX_TABLES)
    ok, message = tc.CHECK_303_130['auto'](year)
    assert ok is True and 'Checked automatically' in message
    ok, message = tc.CHECK_390['auto'](2099)   # no 2099 layout: says which one it used
    assert 'newest official AEAT record layout the app has (from 2026)' in message
    assert 'newer layout for 2099' in message


def test_a_failing_automatic_check_never_breaks_the_page():
    from modules.tax_es_forms.index import _auto

    def broken(year):
        raise RuntimeError('boom')
    ok, message = _auto({'auto': broken}, 2099)
    assert ok is None and 'could not run (RuntimeError)' in message
