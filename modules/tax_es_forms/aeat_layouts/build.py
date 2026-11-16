#!/usr/bin/env python3
"""
Rebuild a layout JSON from an official AEAT "diseño de registro" spreadsheet.

Maintenance tool, not used by the app. It needs `openpyxl` (xlsx) or `xlrd`
(xls), which are not app dependencies: install them in a throwaway
environment. Example (Modelo 303, exercise 2026):

    python build.py DR303e26v101.xlsx 303 2026 1.01 <source URL> \
        00=DP30300 01=DP30301 03=DP30303 DID=DP303DID > dr303_2026.json

Each page argument is <page id>=<sheet name>. A field row is kept when its
first four cells are number, position, length and type. The JSON lists per
page [number, position, length, type, casilla, constant, description]: the
casilla is the first "[NN]" of the description outside a "( … )" formula,
the constant is the quoted value of "Constante \"…\"" when its length fits.
Review the result against the spreadsheet (test_aeat_files.py checks that
positions are contiguous and every page is tagged).
"""

import json
import re
import sys
from datetime import date

CONST_RE = re.compile(r'Constante\s+"([^"]*)"', re.I)
QUOTED_RE = re.compile(r'^"([^"]*)"$')
BOX_RE = re.compile(r'\[\s*(\d+)\s*\]')


def _rows(path, sheet):
    if path.endswith('.xls'):
        import xlrd
        s = xlrd.open_workbook(path).sheet_by_name(sheet)
        return [s.row_values(r) for r in range(s.nrows)]
    import openpyxl
    return [list(r) for r in openpyxl.load_workbook(path, data_only=True)[sheet]
            .iter_rows(values_only=True)]


def fields(rows):
    out = []
    for row in rows:
        if len(row) < 5:
            continue
        try:
            n, pos, length = int(float(row[0])), int(float(row[1])), int(float(row[2]))
        except (TypeError, ValueError):
            continue
        rest = [re.sub(r'\s+', ' ', str(c)).strip() for c in row[4:] if c not in (None, '')]
        desc, rule = (rest[0] if rest else ''), ' '.join(rest[1:])
        m = CONST_RE.search(rule) or CONST_RE.search(desc) or QUOTED_RE.match(rule.strip())
        const = m.group(1) if m and len(m.group(1)) == length else None
        boxes = BOX_RE.findall(re.sub(r'\([^()]*\)', ' ', desc)) or BOX_RE.findall(desc)
        box = boxes[0].lstrip('0').zfill(2) if boxes else None
        out.append([n, pos, length, str(row[3]).strip(), box, const, desc[:90]])
    return out


def main(argv):
    path, model, valid_from, version, source, *pages = argv
    layout = {'model': model, 'layout': path.rsplit('/', 1)[-1].rsplit('.', 1)[0],
              'version': version, 'valid_from': int(valid_from), 'source': source,
              'checked_on': date.today().isoformat(), 'pages': {}}
    for spec in pages:
        page_id, sheet = spec.split('=', 1)
        layout['pages'][page_id] = fields(_rows(path, sheet))
    json.dump(layout, sys.stdout, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main(sys.argv[1:])
