"""Read the box values of a filed AEAT form from its PDF receipt.

The PDF that the AEAT Sede returns after filing Modelo 130, 303 or 349 is the
official form with every filled box printed next to its box number. This
module finds each amount and the nearest box number to its left on the same
line, so the app knows what was actually filed (box 05 of the next Modelo
130, the VAT credit carried to the next Modelo 303).

Pure: bytes in, dict out. No Flask, no DB. Anything that is not a recognised
AEAT form returns None — the caller keeps the upload and stores no boxes.
"""
import io
import re

FORMS = ('130', '303', '349')

_MONEY = re.compile(r'^-?\d{1,3}(?:\.\d{3})*,\d{2}$')
_BOX = re.compile(r'^\d{2,3}$')
_PERIOD = re.compile(r'\b(20\d{2})\s+([1-4])T\b')
_FORM = re.compile(r'Modelo\s*\n?\s*(\d{3})')

# Printed rates and percentages, not amounts (Modelo 303 tipo % / atribución).
_NOT_AMOUNTS = {
    '303': {'02', '05', '08', '17', '20', '23', '151', '154', '157', '166',
            '169', '65'},
}
_SAME_LINE = 6.0  # points between a box number and its value on one row


def _amount(text):
    return float(text.replace('.', '').replace(',', '.'))


def _page_items(page):
    items = []

    def visit(text, _cm, tm, _font, _size):
        text = text.strip()
        if text:
            items.append((text, tm[4], tm[5]))

    page.extract_text(visitor_text=visit)
    return items


def read_filed_form(pdf_bytes):
    """Return {'form', 'year', 'quarter', 'boxes'} or None.

    `boxes` maps box number (string, as printed: '01', '110') to a float.
    """
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = list(reader.pages)
        text = '\n'.join(p.extract_text() or '' for p in pages)
    except Exception:
        return None

    form = next((m.group(1) for m in _FORM.finditer(text)
                 if m.group(1) in FORMS), None)
    if form is None:
        return None
    period = _PERIOD.search(text)

    skip = _NOT_AMOUNTS.get(form, set())
    boxes = {}
    for page in pages:
        items = _page_items(page)
        numbers = [(t, x, y) for t, x, y in items if _BOX.match(t)]
        for text_item, x, y in items:
            if not _MONEY.match(text_item):
                continue
            left = [(x - bx, box) for box, bx, by in numbers
                    if abs(by - y) < _SAME_LINE and bx < x]
            if not left:
                continue
            box = min(left)[1]
            if box not in skip and box not in boxes:
                boxes[box] = _amount(text_item)
    if not boxes:
        return None
    return {
        'form': form,
        'year': int(period.group(1)) if period else None,
        'quarter': int(period.group(2)) if period else None,
        'boxes': boxes,
    }
