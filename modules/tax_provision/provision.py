#!/usr/bin/env python3
"""
Tax provision — add up the parts the tax engines already calculate.

Pure: no Flask, no database, no tax rules. Each part comes from its own engine
(Modelo 303 draft, Modelo 130 draft, Modelo 100 estimate, RETA projection);
this file only sums them and labels the sign. A part whose module is disabled
or failed has `amount` None: it is left out of the total and listed as
missing, so the total never pretends to be complete.

Sign convention for every part: > 0 is owed, < 0 is money coming back (a
refund, or a VAT credit to compensate in a later quarter). A negative part
reduces the total.
"""

from decimal import ROUND_HALF_UP, Decimal


def round2(value):
    """Half-up to cents, as the drafts round."""
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def direction(amount, negative='refund'):
    """'pay', 'nil', or the word for a negative amount ('refund' or
    'compensate')."""
    if amount is None:
        return None
    if amount > 0:
        return 'pay'
    if amount < 0:
        return negative
    return 'nil'


def summarise(parts):
    """Total of the available parts.

    `parts` is a list of dicts with at least `amount` (float or None).
    Returns {'parts', 'total', 'missing', 'complete'}: `total` is the sum of
    every part with an amount, rounded to cents; `missing` lists the parts
    without one.
    """
    available = [p for p in parts if p.get('amount') is not None]
    missing = [p for p in parts if p.get('amount') is None]
    total = round2(sum(p['amount'] for p in available)) if available else 0.0
    return {
        'parts': parts,
        'total': total,
        'total_direction': direction(total),
        'missing': missing,
        'complete': not missing,
    }
