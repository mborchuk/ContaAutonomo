"""B-2 — fiscal calendar, Modelo 303/130 boxes and RETA brackets by year."""
from datetime import date

import pytest

from modules.fiscal_calendar.calendar_data import (
    all_deadlines,
    deadlines_for_year,
    is_verified,
    upcoming,
)
from modules.reta_advisor.brackets import BRACKET_TABLES, brackets_for_year
from modules.reta_advisor.engine import project
from modules.tax_es_forms.boxes import BOX_TABLES, boxes_for_year
from year_tables import resolve


# --- resolver -------------------------------------------------------------------

def test_resolve_exact_fallback_and_before_first():
    tables = {2026: 'a', 2028: 'b'}
    assert resolve(tables, 2026) == (2026, 'a', False)
    assert resolve(tables, 2027) == (2026, 'a', True)    # latest earlier
    assert resolve(tables, 2031) == (2028, 'b', True)
    assert resolve(tables, 2020) == (2026, 'a', True)    # before all tables


# --- calendar -------------------------------------------------------------------

@pytest.mark.parametrize('year', [2028, 2031, 2040])
def test_calendar_has_deadlines_for_any_year(year):
    entries = deadlines_for_year(year)
    forms = {(e['form'], e['period']) for e in entries}
    assert ('303', 'Q1') in forms and ('130', 'Q4') in forms and ('100', 'annual') in forms
    assert all(e['verified'] is False for e in entries)
    assert all(e['window_end'].weekday() < 5 for e in entries)


def test_verified_years_flagged():
    assert is_verified(2026) and all(e['verified'] for e in deadlines_for_year(2026))
    assert not is_verified(2028)


def test_upcoming_in_2028_is_not_empty():
    items = upcoming(60, today=date(2028, 3, 15), selected_forms={'303'})
    assert any(d['year'] == 2028 and d['quarter'] == 1 for d in items)


def test_default_window_follows_today():
    years = {e['year'] for e in all_deadlines(today=date(2030, 6, 1))}
    assert years == {2029, 2030, 2031}


# --- Modelo 303 / 130 boxes ---------------------------------------------------

def test_box_table_own_year_and_fallback():
    own_year = min(BOX_TABLES)
    assert boxes_for_year(own_year)[2] is False
    table_year, tables, fallback = boxes_for_year(own_year + 5)
    assert fallback and table_year == max(BOX_TABLES)
    assert '01' in tables['303'] and '07' in tables['130']


def test_draft_for_year_without_table_shows_note(loaded_modules):
    forms = loaded_modules.modules['tax_es_forms']
    own = forms._draft_303(min(BOX_TABLES), 1)
    assert own['table_fallback'] is False and own['table_note'] == ''
    later = forms._draft_303(max(BOX_TABLES) + 2, 1)
    assert later['table_fallback'] is True
    assert f'{max(BOX_TABLES)} table' in later['table_note']
    draft130 = forms._draft_130(max(BOX_TABLES) + 2, 1)
    assert draft130['table_fallback'] is True


def test_draft_page_renders_fallback_banner(loaded_modules, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    year = max(BOX_TABLES) + 2
    html = client.get(f'/tax-forms-draft/303/{year}/1').get_data(as_text=True)
    assert f'Figures use the {max(BOX_TABLES)} table' in html
    html = client.get(f'/tax-forms-draft/303/{min(BOX_TABLES)}/1').get_data(as_text=True)
    assert 'Figures use the' not in html


# --- RETA brackets ----------------------------------------------------------------

def test_reta_brackets_own_year_and_fallback():
    assert brackets_for_year(min(BRACKET_TABLES))[2] is False
    assert brackets_for_year(max(BRACKET_TABLES) + 3)[2] is True


def test_reta_projection_reports_table_used():
    own = project(24000, 0, 12, year=min(BRACKET_TABLES))
    assert own['table_year'] == min(BRACKET_TABLES) and own['table_fallback'] is False
    later = project(24000, 0, 12, year=max(BRACKET_TABLES) + 1)
    assert later['table_fallback'] is True
    assert later['recommended_monthly_quota'] == own['recommended_monthly_quota']
