#!/usr/bin/env python3
"""
Bank statement parsers — pure functions, no Flask, no DB.

CAVEMAN NOTE: two format only (per PM scope): Norma 43 / AEB43 (Spanish bank
standard, fixed-width, latin-1) and generic CSV with a user column mapping
(every bank CSV different). Parser return plain dicts; dedup hash computed
here so importer stay dumb.

N43 layout implemented (standard AEB43):
  11 = account header (currency at pos 47-49 as ISO 4217 numeric)
  22 = movement: fecha operación pos 10-16 (YYMMDD), debe/haber flag pos 27
       (1=debit, 2=credit), importe pos 28-42 (14 digits, 2 implied decimals)
  23 = concept lines (description, appended to the previous 22)
  33/88 = footers (ignored)
Verify against fixture files from the user's actual banks (F12-D0/D2 AC).
"""

import csv
import hashlib
import io
from datetime import datetime

# ISO 4217 numeric -> alpha for the currencies this app tracks.
_CURRENCY_NUM = {'978': 'EUR', '840': 'USD', '826': 'GBP', '203': 'CZK',
                 '980': 'UAH', '985': 'PLN'}


def movement_hash(date_iso, amount, description):
    """Dedup key: same date+amount+description = same movement."""
    raw = f'{date_iso}|{amount:.2f}|{(description or "").strip()}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def parse_n43(data):
    """Parse Norma 43 (AEB43) content. Accepts bytes (latin-1) or str.

    Returns (movements, errors): movements are dicts with date (date),
    amount (float, signed: credits +, debits -), currency, description, hash.
    Bad lines are collected into errors, good ones still import (F12-X1
    decision: import-good-skip-bad with report).
    """
    if isinstance(data, bytes):
        text = data.decode('latin-1', errors='replace')
    else:
        text = data

    movements, errors = [], []
    currency = 'EUR'
    current = None

    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        rtype = line[0:2]
        try:
            if rtype == '11':
                num = line[47:50].strip()
                currency = _CURRENCY_NUM.get(num, 'EUR')
            elif rtype == '22':
                if current:
                    movements.append(current)
                fecha = line[10:16]
                dh = line[27]
                importe = line[28:42]
                amount = int(importe) / 100.0
                if dh == '1':          # debe = debit
                    amount = -amount
                current = {
                    'date': datetime.strptime(fecha, '%y%m%d').date(),
                    'amount': amount,
                    'currency': currency,
                    'description': line[52:].strip() or '',
                    'counterparty': '',
                }
            elif rtype == '23' and current is not None:
                extra = line[4:].strip()
                if extra:
                    current['description'] = (
                        current['description'] + ' ' + extra).strip()
            # 33 / 88 footers: nothing to extract
        except (ValueError, IndexError) as e:
            errors.append(f'line {lineno}: {e}')
            current = None
    if current:
        movements.append(current)

    for m in movements:
        m['hash'] = movement_hash(m['date'].isoformat(), m['amount'],
                                  m['description'])
    return movements, errors


def parse_csv(data, mapping):
    """Parse a bank CSV using a column mapping.

    mapping keys: date, amount, description (column names, required);
    counterparty (optional); date_format (default %d/%m/%Y);
    decimal_comma (bool, '1.234,56' style amounts).
    Returns (movements, errors) — good rows import, bad rows are reported.
    """
    if isinstance(data, bytes):
        text = data.decode('utf-8-sig', errors='replace')
    else:
        text = data

    date_col = mapping.get('date')
    amount_col = mapping.get('amount')
    desc_col = mapping.get('description')
    if not (date_col and amount_col and desc_col):
        raise ValueError('mapping needs date, amount and description columns')
    cp_col = mapping.get('counterparty')
    date_format = mapping.get('date_format') or '%d/%m/%Y'
    decimal_comma = bool(mapping.get('decimal_comma'))
    currency = mapping.get('currency') or 'EUR'

    movements, errors = [], []
    reader = csv.DictReader(io.StringIO(text))
    for lineno, row in enumerate(reader, 2):   # 1 = header
        try:
            raw_amount = (row[amount_col] or '').strip()
            if decimal_comma:
                raw_amount = raw_amount.replace('.', '').replace(',', '.')
            amount = float(raw_amount.replace('€', '').replace(' ', ''))
            m = {
                'date': datetime.strptime(row[date_col].strip(), date_format).date(),
                'amount': amount,
                'currency': currency,
                'description': (row[desc_col] or '').strip(),
                'counterparty': (row.get(cp_col) or '').strip() if cp_col else '',
            }
            m['hash'] = movement_hash(m['date'].isoformat(), m['amount'],
                                      m['description'])
            movements.append(m)
        except (KeyError, TypeError, ValueError) as e:
            errors.append(f'row {lineno}: {e}')
    return movements, errors
