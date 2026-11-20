#!/usr/bin/env python3
"""
AEAT import files for the model drafts (G-5) — pure, no Flask, no database.

Each function turns a draft (the dict the draft page shows) into the text file
the AEAT Sede accepts, following the official "diseño de registro" of the
year. Nothing here is a tax rule: the figures are the draft's, unchanged.

  - 303, 130, 390: the Sede form ("Importar") reads a file with the
    `<T…>` page records of the record layouts DR303e26v101, DR130e15v12 and
    dr390e2026. The page layouts are data in aeat_layouts/*.json, extracted
    from the official spreadsheets (positions, lengths, types, constants).
  - 349, 347: "Presentación mediante fichero" reads fixed 500-character
    records (type 1 declarant, type 2 per counterparty), per the AEAT
    "diseños lógicos" of Modelo 349 (Orden HAC/174/2020) and Modelo 347
    (exercise 2025 and later, Orden HAC/1431/2025).

None of the files has passed the AEAT validation from this code: the Sede
validates on import, with the taxpayer's certificate. Every file is marked
"not validated" in the app; see AEAT_FILES.md for what is filled, what the
user must complete in the form, and what is out of scope.
"""

import json
import os
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from year_tables import resolve

LAYOUT_DIR = os.path.join(os.path.dirname(__file__), 'aeat_layouts')

# {model: {first exercise the layout applies to: file}}.
LAYOUT_FILES = {
    '303': {2026: 'dr303_2026.json'},
    '130': {2015: 'dr130_2015.json'},
    '390': {2026: 'dr390_2026.json'},
}

# Fixed-width layouts of the informative returns: first exercise -> name.
RECORD_LAYOUTS = {
    '349': {2020: 'DR_Anexo_349 (Orden HAC/174/2020)'},
    '347': {2025: '347 — ejercicio 2025 y siguientes (Orden HAC/1431/2025)'},
}

RECORD_LENGTH = 500
RECORD_END = '\r\n'
ENCODING = 'iso-8859-1'


class UnsupportedYear(ValueError):
    """No official layout covers that exercise."""


# --- field formatting ------------------------------------------------------- #

def cents(value):
    return int(Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
               * 100)


def text(value, length):
    """Upper case, no accents (Ñ and Ç kept), letters, digits and blanks
    only, left aligned and padded with blanks."""
    out = []
    for ch in (value or '').upper():
        if ch in 'ÑÇ':
            out.append(ch)
            continue
        base = unicodedata.normalize('NFD', ch)[0]
        out.append(base if base.isascii() and (base.isalnum() or base == ' ') else ' ')
    cleaned = re.sub(r' +', ' ', ''.join(out)).strip()
    return cleaned[:length].ljust(length)


def digits(value, length):
    raw = re.sub(r'\D', '', str(value or ''))[-length:]
    return raw.rjust(length, '0')


def amount(value, length, signed):
    """Cents, right aligned with zeros; a negative signed field starts with N."""
    c = cents(value)
    if c < 0:
        if not signed:
            raise ValueError('negative amount in an unsigned field')
        return 'N' + str(-c).rjust(length - 1, '0')
    return str(c).rjust(length, '0')


def nif(value):
    """Spanish NIF without the ES prefix, 9 characters (empty if none)."""
    raw = re.sub(r'[^0-9A-Za-z]', '', value or '').upper()
    if raw.startswith('ES') and len(raw) == 11:
        raw = raw[2:]
    return raw


def nif_right(value):
    """NIF right aligned and zero padded to 9 (informative returns)."""
    raw = nif(value)
    return raw.rjust(9, '0') if raw else ' ' * 9


def split_name(full_name):
    """(surnames, given name) for an individual. "Surnames, Name" is
    split at the comma; otherwise the first word is taken as the name and the
    rest as the surnames — the user checks it in the form."""
    name = (full_name or '').strip()
    if ',' in name:
        surnames, given = name.split(',', 1)
        return surnames.strip(), given.strip()
    parts = name.split()
    if len(parts) < 2:
        return name, ''
    return ' '.join(parts[1:]), parts[0]


def surname_first(full_name):
    surnames, given = split_name(full_name)
    return f'{surnames} {given}'.strip()


# --- layouts with <T…> pages (303, 130, 390) ------------------------------- #

def load_layout(model, year):
    """The layout data for `model` and exercise `year`; UnsupportedYear when
    the year is before the first exercise the official layout covers."""
    table_year, filename, fallback = resolve(LAYOUT_FILES[model], year)
    if fallback and year < table_year:
        raise UnsupportedYear(
            f'No AEAT record layout for Modelo {model} of {year} in the app '
            f'(the layout starts with {table_year}).')
    with open(os.path.join(LAYOUT_DIR, filename), encoding='utf-8') as f:
        return json.load(f)


def render_page(fields, values=None, boxes=None, used=None):
    """One page record. `values` fills fields by number, `boxes` by casilla
    (the first field carrying that casilla in the model); constants come
    from the layout; anything else is blank (text) or zero (numbers). A
    numeric field never holds a blank: the Sede rejects the whole file
    ("Caracteres no válidos")."""
    values = values or {}
    boxes = boxes or {}
    used = used if used is not None else set()
    parts = []
    for n, _pos, length, typ, box, const, _desc in fields:
        numeric = typ in ('Num', 'N')
        if const is not None:
            s = const
        elif n in values:
            v = values[n]
            if not isinstance(v, str):
                s = amount(v, length, typ == 'N')
            elif not v.strip():
                if numeric:
                    raise ValueError(f'field {n} is numeric and cannot be blank')
                s = ' ' * length          # a text flag left blank on purpose
            elif numeric:
                s = digits(v, length)
            else:
                s = text(v, length)
        elif box and box in boxes and box not in used:
            s = amount(boxes[box], length, typ == 'N')
            used.add(box)
        else:
            s = '0' * length if numeric else ' ' * length
        if len(s) != length:
            raise ValueError(f'field {n} has {len(s)} characters, expected {length}')
        parts.append(s)
    return ''.join(parts)


def _envelope(layout, year, period, pages):
    model = layout['model']
    header = render_page(layout['pages']['00'], {4: str(year), 5: period})
    return header + ''.join(pages) + f'</T{model}0{year}{period}0000>'


# --- Modelo 303 -------------------------------------------------------------- #

# Draft box -> official casilla. The app's general-rate lines 01/03 are the
# 21 % lines, which the official 2026 model numbers [07]/[09].
_303_PAGE1 = {'01': '07', '03': '09', '10': '10', '11': '11', '12': '12', '13': '13',
              '27': '27', '28': '28', '29': '29', '30': '30', '31': '31',
              '36': '36', '37': '37', '38': '38', '39': '39', '45': '45', '46': '46'}


def declarant(settings):
    """(nif, name) of the declarant from Settings."""
    number = getattr(settings, 'vat_number', None) or getattr(settings, 'nie_number', None)
    name = getattr(settings, 'owner_name', None) or getattr(settings, 'business_name', None)
    return nif(number), name or ''


def modelo_303(draft, settings):
    """Import file for one 303 draft (exercise 2026 or later)."""
    year, quarter, b = draft['year'], draft['quarter'], draft['boxes']
    layout = load_layout('303', year)
    period = f'{quarter}T'
    last = quarter == 4
    number, name = declarant(settings)
    result = b.get('71', 0.0)
    kind = 'I' if cents(result) > 0 else ('N' if cents(result) == 0 else 'C')
    page1 = render_page(layout['pages']['01'], {
        6: kind, 7: number, 8: surname_first(name), 9: str(year), 10: period,
        11: '2', 12: '2', 13: '3', 14: '2', 15: '2', 16: '2',
        # Prorrata especial option and its revocation: the Sede takes only
        # 1 (yes) or 2 (no), in every period (E010171, E010181).
        17: '2', 18: '2', 19: '2',
        22: '2', 23: '2' if last else '0', 24: '1' if last else '0', 25: '0',
    }, {official: b.get(app, 0.0) for app, official in _303_PAGE1.items()})
    result_general = b.get('46', 0.0)
    page3 = render_page(layout['pages']['03'], {}, {
        '59': b.get('59', 0.0), '120': b.get('120', 0.0),
        '64': result_general, '65': 100.0, '66': result_general,
        '110': b.get('110', 0.0), '78': b.get('78', 0.0), '87': b.get('87', 0.0),
        '69': result_general - b.get('78', 0.0), '71': result,
    })
    did = render_page(layout['pages']['DID'], {11: '0'})
    return _envelope(layout, year, period, [page1, page3, did]), {
        'layout': layout['layout'], 'declaration_type': kind}


# --- Modelo 130 -------------------------------------------------------------- #

def modelo_130(draft, settings):
    year, quarter, b = draft['year'], draft['quarter'], draft['boxes']
    layout = load_layout('130', year)
    period = f'{quarter}T'
    number, name = declarant(settings)
    surnames, given = split_name(name)
    box07 = b.get('04', 0.0) - b.get('05', 0.0) - b.get('06', 0.0)
    # The draft's box 07 already includes the art. 110.3.c reduction (it is
    # floored at zero); the official model shows it in [13].
    reduction = max(0.0, max(0.0, box07) - b.get('07', 0.0))
    total = box07 - reduction
    kind = 'I' if cents(total) > 0 else 'N'
    page1 = render_page(layout['pages']['01'], {
        6: kind, 7: number, 8: surnames, 9: given, 10: str(year), 11: period,
    }, {
        '01': b.get('01', 0.0), '02': b.get('02', 0.0), '03': b.get('03', 0.0),
        '04': b.get('04', 0.0), '05': b.get('05', 0.0), '06': b.get('06', 0.0),
        '07': box07, '12': box07, '13': reduction, '14': total, '17': total, '19': total,
    })
    return _envelope(layout, year, period, [page1]), {
        'layout': layout['layout'], 'declaration_type': kind}


# --- Modelo 390 -------------------------------------------------------------- #

_390_BOXES = ('05', '06', '551', '552', '27', '28', '33', '34', '47', '48', '49', '50',
              '51', '597', '598', '58', '59', '64', '65', '84', '85', '86', '95', '97',
              '99', '103', '110', '108')
_390_PAGE_ORDER = ('01', '02', '02B', '03', '04', '05', '06', '07', '08')


def modelo_390(draft, settings, files_347=False):
    year, b = draft['year'], draft['boxes']
    layout = load_layout('390', year)
    number, name = declarant(settings)
    surnames, given = split_name(name)
    boxes = {k: b.get(k, 0.0) for k in _390_BOXES}
    used = set()
    pages = []
    for page_id in _390_PAGE_ORDER:
        values = {}
        if page_id == '01':
            values = {7: number, 8: surnames, 9: given, 10: str(year),
                      12: '0', 13: '0', 15: '0', 16: '0', 17: '0', 19: '2', 20: '2',
                      21: '2', 22: '2', 23: '0', 24: '0', 44: '1' if files_347 else '0'}
        pages.append(render_page(layout['pages'][page_id], values, boxes, used))
    return _envelope(layout, year, '0A', pages), {'layout': layout['layout']}


# --- fixed 500-character records (349, 347) --------------------------------- #

def _record(parts):
    rec = ''.join(parts)
    if len(rec) > RECORD_LENGTH:
        raise ValueError(f'record has {len(rec)} characters')
    return rec.ljust(RECORD_LENGTH)


def _check_record_year(model, year):
    first = min(RECORD_LAYOUTS[model])
    if year < first:
        raise UnsupportedYear(f'No AEAT record layout for Modelo {model} of {year} in the app.')
    return RECORD_LAYOUTS[model][max(y for y in RECORD_LAYOUTS[model] if y <= year)]


def _declarant_record(model, year, settings, sequence, tail):
    number, name = declarant(settings)
    phone = getattr(settings, 'phone', None) or ''
    return [
        '1', model, str(year), nif_right(number), text(surname_first(name), 40),
        'T' if model == '347' else ' ',
        digits(phone, 9), text(surname_first(name), 40),
        model + digits(sequence, 10), '  ', '0' * 13,
    ] + tail


def modelo_349(draft, settings):
    """Fixed-width file for one quarterly 349 draft (key S lines only)."""
    year, quarter = draft['year'], draft['quarter']
    layout = _check_record_year('349', year)
    number, _name = declarant(settings)
    lines = draft.get('operators', [])
    total = sum(op['base'] for op in lines)
    head = _record(_declarant_record('349', year, settings, f'{year}{quarter:06d}', [
        f'{quarter}T', digits(len(lines), 9), amount(total, 15, False),
        '0' * 9, '0' * 15, ' ']))
    records = [head]
    for op in lines:
        records.append(_record([
            '2', '349', str(year), nif_right(number), ' ' * 58,
            text(op.get('country'), 2), text(op.get('vat_number'), 15),
            text(op.get('name'), 40), op.get('key', 'S')[:1],
            amount(op['base'], 13, False)]))
    return RECORD_END.join(records) + RECORD_END, {'layout': layout}


_SPAIN = {'', 'ES', 'ESP', 'ESPANA', 'ESPAÑA', 'SPAIN'}


def _signed(value, length):
    c = cents(value)
    return ('N' if c < 0 else ' ') + str(abs(c)).rjust(length, '0')


def residence(line):
    """(province, country) codes for a 347 line, plus a warning or None."""
    country_text = text(line.get('country'), 20).strip()
    number = nif(line.get('nif'))
    if country_text in _SPAIN and re.fullmatch(r'[0-9A-Z]{9}', number or ''):
        province = (line.get('postal_code') or '').strip()[:2]
        if province.isdigit() and 1 <= int(province) <= 52:
            return province, '  ', None
        return '00', '  ', f'{line.get("name")}: no Spanish postal code — set the province in the form'
    prefix = (line.get('nif') or '')[:2].upper()
    code = prefix if prefix.isalpha() else ''
    warning = None if code else f'{line.get("name")}: country code unknown — set it in the form'
    return '99', (code or '  '), warning


def modelo_347(draft, settings):
    """Fixed-width file for the 347 check of one year."""
    year = draft['year']
    layout = _check_record_year('347', year)
    number, _name = declarant(settings)
    lines = draft.get('lines', [])
    total = sum(line['total'] for line in lines)
    head = _record(_declarant_record('347', year, settings, f'{year}000001', [
        digits(len(lines), 9), _signed(total, 15), '0' * 9, _signed(0, 15)]))
    records, warnings = [head], []
    for line in lines:
        province, country, warning = residence(line)
        if warning:
            warnings.append(warning)
        resident = province != '99'
        q = line['quarters']
        records.append(_record([
            '2', '347', str(year), nif_right(number),
            nif_right(line.get('nif')) if resident else ' ' * 9, ' ' * 9,
            text(line.get('name'), 40), 'D', province, country, ' ', line['key'],
            _signed(line['total'], 15), ' ', ' ', '0' * 15, _signed(0, 15), '0' * 4,
            _signed(q[0], 15), _signed(0, 15), _signed(q[1], 15), _signed(0, 15),
            _signed(q[2], 15), _signed(0, 15), _signed(q[3], 15), _signed(0, 15),
            ' ' * 17, ' ', 'X' if line.get('reverse_charge') else ' ', ' ',
            _signed(0, 15), '0' * 6]))
    return RECORD_END.join(records) + RECORD_END, {'layout': layout, 'warnings': warnings}


def encode(content):
    """Bytes for download: ISO-8859-1, as the AEAT layouts require."""
    return content.encode(ENCODING)
