#!/usr/bin/env python3
"""
Movement ↔ invoice matching heuristics — pure functions.

CAVEMAN NOTE: rank only, never auto-match. Confidence order (F12-D3 AC):
amount + invoice number in description  >  amount + close date  >  amount only.
User always confirm.
"""

AMOUNT_TOLERANCE = 0.01
DATE_WINDOW_DAYS = 45


def _amount_matches(movement_amount, invoice_amount):
    return abs(abs(movement_amount) - (invoice_amount or 0)) <= AMOUNT_TOLERANCE


def suggest_for_movement(movement, invoices):
    """Return ranked suggestions [(score, invoice, reason), ...] best first.

    movement: dict/obj with amount (credit +), date, description.
    invoices: iterable of unpaid invoice-like objects (amount_eur,
    invoice_number, invoice_date, client_name).
    Only credit movements (money in) suggest invoices.
    """
    get = movement.get if isinstance(movement, dict) else \
        lambda k, _m=movement: getattr(_m, k, None)
    amount = get('amount') or 0
    if amount <= 0:
        return []
    desc = (get('description') or '').lower()
    mdate = get('date')

    out = []
    for inv in invoices:
        if not _amount_matches(amount, inv.amount_eur):
            continue
        score = 50
        reasons = ['amount matches']
        number = (inv.invoice_number or '').lower()
        if number and number in desc:
            score += 40
            reasons.append('invoice number in description')
        if mdate and inv.invoice_date:
            days = abs((mdate - inv.invoice_date).days)
            if days <= DATE_WINDOW_DAYS:
                score += max(0, 10 - days // 7)
                reasons.append(f'{days}d from invoice date')
        client = (inv.client_name or '').lower()
        if client and client in desc:
            score += 15
            reasons.append('client name in description')
        out.append((score, inv, ', '.join(reasons)))

    out.sort(key=lambda t: -t[0])
    return out
