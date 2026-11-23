"""The table this module uses for a fiscal year.

Tax data that changes by year (box tables, the Modelo 347 threshold) is kept
in tables keyed by year. A year without its own table uses the latest table of
an earlier year (or the earliest table, for a year before all of them), and the
page warns "figures use the <year> table — verify".

The owner checks a table for a new year in the Tax Data module (Taxes → Tax
Data) and confirms it unchanged or enters the new values; this module asks it
through `taxdata.year_values`. Without the Tax Data module, the warning stays.
"""


def resolve(tables, year):
    """(table_year, table, is_fallback) for `year` from {year: table}."""
    if not tables:
        raise ValueError('no tables defined')
    if year in tables:
        return year, tables[year], False
    earlier = [y for y in tables if y < year]
    table_year = max(earlier) if earlier else min(tables)
    return table_year, tables[table_year], True


def fallback_note(table_year):
    return f'Figures use the {table_year} table — verify against the current official values.'


def confirmed_values(functions, table_id, year):
    """What the owner confirmed for `table_id` in `year` in the Tax Data
    module: {'confirmed_on', 'values'}, or None (not confirmed, or no Tax
    Data module)."""
    if functions is None or not functions.available('taxdata.year_values'):
        return None
    try:
        status = functions.call('taxdata.year_values', table=table_id, year=year)
    except Exception:  # never break a tax page over a confirmation lookup
        return None
    return status if status.get('confirmed') else None


def table_for_year(functions, table_id, tables, year, apply=None):
    """(table_year, table, is_fallback, confirmed_on): the table for `year`
    with the values the owner entered for that year applied
    (`apply(table, values)` returns the changed copy). A year the owner
    confirmed is not a fallback any more."""
    table_year, table, fallback = resolve(tables, year)
    status = confirmed_values(functions, table_id, year)
    if status is None:
        return table_year, table, fallback, None
    if status.get('values') and apply is not None:
        table = apply(table, status['values'])
    return table_year, table, False, status.get('confirmed_on')


def notice_html(functions, table_id, year, table_year):
    """The Tax Data module's warning for a page that shows `year` with the
    `table_year` table (how to check it, the automatic check, a link to
    confirm it), or '' without that module."""
    if functions is None or not functions.available('taxdata.year_notice'):
        return ''
    try:
        return functions.call('taxdata.year_notice', table=table_id, year=year,
                              table_year=table_year).get('html') or ''
    except Exception:
        return ''
