"""Records and retention (ISO 15489) — pure functions, no Flask.

A document becomes a *record* when the owner declares it one: from then on it
is fixed (its details and files cannot be changed or deleted; notes can still
be added) and it is kept until its retention date. Each category can carry a
retention period in years. The period runs to 31 December of the year of the
document date (or of the declaration, for a document without a date) plus the
period, as Spanish keeping periods are counted from the end of a year.

When a record reaches its retention date it is due for review: keep it for
more years, or dispose of it. Every deletion and disposal is written to a log
that stays after the document is gone.

The app does not set periods: the owner does, per category (with the advice
of their gestor). The Categories page names common Spanish periods as a
reminder, with their source.
"""
from datetime import date

MAX_YEARS = 50

# Shown on the Categories page as a reminder; the owner decides.
COMMON_PERIODS = (
    ('6 years', 'accounting books, invoices and supporting documents of a business '
                '(Código de Comercio, art. 30)'),
    ('4 years', 'the period in which the tax authority can review a tax return '
                '(Ley General Tributaria, art. 66); it starts when the filing period ends'),
    ('4 years', 'Social Security contributions (Ley General de la Seguridad Social, art. 24)'),
)


def retain_until(years, document_date=None, declared_on=None):
    """31 December of (the year of the document date, or of the declaration)
    + `years`; None without a period."""
    if not years:
        return None
    base = document_date or declared_on or date.today()
    return date(base.year + int(years), 12, 31)


def clean_years(value):
    """A retention period in whole years (1..MAX_YEARS), or None."""
    try:
        years = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return years if 1 <= years <= MAX_YEARS else None


def is_due(doc, today=None):
    """A declared record whose retention date has passed."""
    until = getattr(doc, 'retain_until', None)
    return bool(getattr(doc, 'record_declared_at', None) and until
                and until < (today or date.today()))
