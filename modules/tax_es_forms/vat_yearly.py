"""The yearly VAT return for small businesses and professionals (from 2027).

Pure rules, no Flask or database. Real Decreto-ley 43/2026, art. 3
(BOE-A-2026-24701), adds the "régimen especial de declaración anual para
pequeños empresarios y profesionales" to the Ley del IVA (arts. 163 novovicies
to 163 duotricies) and its Reglamento (arts. 33.1, 61 octodecies, 71.3, 71.10,
disposición transitoria séptima):

- Voluntary, for individuals (and entities under attribution of income made
  of individuals) whose volume of operations in the previous calendar year
  did not exceed 85,000 EUR (raised to the year when they started that year;
  a new activity can apply at once). Above it, out from the next year.
- Not with: régimen simplificado, the monthly refund register (REDEME), the
  group regime, cash accounting (criterio de caja: in March 2027 one can
  leave it for this regime), the cuenta corriente tributaria.
- One self-assessment for the calendar year, filed in the first 30 days of
  January, instead of four quarterly Modelo 303 returns.
- Three advance payments (pagos a cuenta), debited from the account given
  on 20 April, 20 July and 20 October. Each is a quarter of the result of
  the previous year's yearly return, or, in the first year, of the sum of
  that year's quarterly 303 results. Zero when that reference is not
  positive, and when the activity starts. The AEAT shows each amount in the
  Sede in the first five days of the month.
- The advances are deducted in the yearly return: what is left is paid, or
  refunded or carried forward.
- Option by declaración censal in December for the next year, or when the
  activity starts; for 2027, in March 2027. Renouncing it binds for three
  years.

The form for the yearly return and the advances is not published yet (an
Orden of the Ministerio de Hacienda will set it), so the app shows concepts,
not box numbers, and makes no AEAT file for it.
"""

from datetime import date

FROM_YEAR = 2027
VOLUME_LIMIT = 85000.0
ADVANCE_MONTHS = (4, 7, 10)
DEBIT_DAY = 20
SOURCE = ('Real Decreto-ley 43/2026, art. 3 (BOE-A-2026-24701): Ley del IVA arts. 163 '
          'novovicies–163 duotricies; Reglamento del IVA arts. 33.1, 61 octodecies, 71')


def in_regime(config, year):
    """True when the owner files VAT yearly in `year`: `config` is
    {'from': first year, 'until': last year or None}."""
    first = (config or {}).get('from')
    last = (config or {}).get('until')
    if not first or year < max(int(first), FROM_YEAR):
        return False
    return last is None or year <= int(last)


def advance_amount(reference):
    """One advance payment: a quarter of a positive reference, else 0."""
    if reference is None or reference <= 0:
        return 0.0
    return round(reference / 4, 2)


def advance_dates(year):
    """The three debit days of `year`."""
    return [date(year, month, DEBIT_DAY) for month in ADVANCE_MONTHS]


def advances_debited(amount, year, today):
    """How much of the year's advances was debited by `today`."""
    return round(sum(amount for day in advance_dates(year) if day <= today), 2)


def yearly_result(quarters, advance, today=None):
    """The yearly return from the four quarterly 303 drafts of the year
    (their `boxes`): VAT charged (box 27), VAT deductible (45), result (46,
    before any compensation between quarters, which this regime does not
    have), the three advances and what is left. `due_so_far` counts the
    advances debited by `today` (None: all three) against the result so far.
    """
    charged = round(sum(q.get('27', 0.0) for q in quarters), 2)
    deductible = round(sum(q.get('45', 0.0) for q in quarters), 2)
    result = round(sum(q.get('46', 0.0) for q in quarters), 2)
    advances = round(advance * len(ADVANCE_MONTHS), 2)
    return {
        'charged': charged, 'deductible': deductible, 'result': result,
        'advance': advance, 'advances': advances,
        'to_settle': round(result - advances, 2),
    }


def volume_check(volume, year, started_in=None):
    """(ok, sentence) for the volume of operations of the year before `year`.
    `started_in` is the date the activity started, when known."""
    previous = year - 1
    if started_in is not None and started_in.year >= year:
        return True, f'The activity starts in {year}: the regime can be applied at once.'
    if volume is None:
        return None, f'The volume of operations of {previous} is not known.'
    if started_in is not None and started_in.year == previous:
        active = (date(previous, 12, 31) - started_in).days + 1
        whole = (date(previous, 12, 31) - date(previous, 1, 1)).days + 1
        volume = round(volume * whole / active, 2)
    if volume > VOLUME_LIMIT:
        return False, (f'The volume of operations of {previous} ({volume:,.2f} EUR) is above '
                       f'{VOLUME_LIMIT:,.0f} EUR: the yearly VAT return cannot be applied '
                       f'in {year}.')
    return True, (f'The volume of operations of {previous} ({volume:,.2f} EUR) is within '
                  f'{VOLUME_LIMIT:,.0f} EUR.')
