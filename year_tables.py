"""Pick the data table for a fiscal year (task B-2).

Tax data that changes by year (Modelo 303/130 box tables, RETA brackets) is
keyed by year. A year without its own table uses the latest table from an
earlier year — or the earliest table for a year before all tables — and the
caller shows "figures use the <year> table — verify".

A module describes how a person checks one of its tables (`TableCheck`): what
the table holds, the steps, the official sources, what to change once it is
checked and, when the app has an official source to compare with, an automatic
check. `fallback_notice` turns that into the warning the page shows
(`templates/_year_table_notice.html`). The core knows no module's tables.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


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


@dataclass(frozen=True)
class TableCheck:
    """How a person checks one yearly table against the official source."""
    title: str                    # 'Modelo 390 casillas'
    holds: str                    # what the table holds and what could change
    steps: tuple                  # numbered steps, plain sentences
    links: tuple = ()             # ((label, url), ...) official sources
    update: str = ''              # what to change once checked
    # year -> (ok, message): an automatic comparison with an official source
    # the app has, or None when there is nothing to compare with.
    auto: Optional[Callable] = field(default=None, compare=False)


def fallback_notice(year, table_year, check=None):
    """The warning for a page that shows `year` with the `table_year` table:
    {'text', 'title', 'holds', 'steps', 'links', 'after_check', 'auto'} where
    `auto` is {'ok', 'message'} or None. Plain values (pages and the API)."""
    notice = {'text': f'Figures for {year} use the {table_year} table — check that the '
                      f'official values did not change for {year}.',
              'title': '', 'holds': '', 'steps': [], 'links': [], 'after_check': '',
              'auto': None}
    if check is None:
        return notice
    notice.update(title=check.title, holds=check.holds, steps=list(check.steps),
                  links=[{'label': label, 'url': url} for label, url in check.links],
                  after_check=check.update)
    if check.auto is not None:
        try:
            ok, message = check.auto(year)
        except Exception as exc:  # an automatic check never breaks the page
            ok, message = None, f'The automatic check could not run ({type(exc).__name__}).'
        if message:
            notice['auto'] = {'ok': ok, 'message': message}
    return notice
