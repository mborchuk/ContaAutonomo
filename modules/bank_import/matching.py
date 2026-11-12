#!/usr/bin/env python3
"""
Movement ↔ invoice matching heuristics — pure functions.

Ranks candidates only and never matches automatically. Confidence order
(F12-D3 acceptance criteria): amount + invoice number in the description,
then amount + close date, then amount only. The user always confirms.
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


def suggest_expenses_for_movement(movement, expenses):
    """Rank expenses for a debit movement: [(score, expense, reason), ...].

    movement: dict/obj with amount (debits negative), date, description and
    optional counterparty.
    expenses: iterable of candidates exposing amount (in the movement's
    currency), expense_date, contractor_name, invoice_number. Only debit
    movements (money out) suggest expenses; the amount must match. Nothing is
    linked without the user's confirmation.
    """
    get = movement.get if isinstance(movement, dict) else \
        lambda k, _m=movement: getattr(_m, k, None)
    amount = get('amount') or 0
    if amount >= 0:
        return []
    text = ' '.join(filter(None, [get('description'), get('counterparty')])).lower()
    mdate = get('date')

    out = []
    for exp in expenses:
        if exp.amount is None or abs(abs(amount) - exp.amount) > AMOUNT_TOLERANCE:
            continue
        score = 50
        reasons = ['amount matches']
        number = (exp.invoice_number or '').lower()
        if len(number) >= 3 and number in text:
            score += 40
            reasons.append('invoice number in description')
        if mdate and exp.expense_date:
            days = abs((mdate - exp.expense_date).days)
            if days <= DATE_WINDOW_DAYS:
                score += 20 + max(0, 10 - days // 7)
                reasons.append(f'{days}d from expense date')
        contractor = (exp.contractor_name or '').lower()
        if contractor and contractor in text:
            score += 25
            reasons.append('contractor name in description')
        out.append((score, exp, ', '.join(reasons)))

    out.sort(key=lambda t: (-t[0], abs((mdate - t[1].expense_date).days)
                            if mdate and t[1].expense_date else 0))
    return out
