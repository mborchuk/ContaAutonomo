"""Pick the data table for a fiscal year (task B-2).

Tax data that changes by year (Modelo 303/130 box tables, RETA brackets) is
keyed by year. A year without its own table uses the latest table from an
earlier year — or the earliest table for a year before all tables — and the
caller shows "figures use the <year> table — verify".
"""


def resolve(tables, year):
    """Return (table_year, table, is_fallback) for `year` from {year: table}."""
    if not tables:
        raise ValueError('no tables defined')
    if year in tables:
        return year, tables[year], False
    earlier = [y for y in tables if y < year]
    table_year = max(earlier) if earlier else min(tables)
    return table_year, tables[table_year], True


def fallback_note(table_year):
    return f'Figures use the {table_year} table — verify against the current official values.'
