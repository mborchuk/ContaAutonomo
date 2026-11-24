"""
The text layer of a PDF, read with pypdf — the core's default for the
registry function `pdf.extract_text` (core_functions.py). A module that
declares the same function (PyMuPDF, OCR) replaces it.

No OCR: a scanned PDF has no text layer and comes back as `no_text_layer`,
so the caller can send it to something that reads images. Pure function, no
Flask, no database.
"""
import io
import logging
import re

from constants import MAX_CONTENT_LENGTH_BYTES

METHOD = 'pypdf'
DEFAULT_MAX_PAGES = 200
MAX_PAGES = 2000
MAX_BYTES = MAX_CONTENT_LENGTH_BYTES

OK = 'ok'
NO_TEXT_LAYER = 'no_text_layer'
ENCRYPTED = 'encrypted'
UNREADABLE = 'unreadable'
TOO_LARGE = 'too_large'
STATUSES = (OK, NO_TEXT_LAYER, ENCRYPTED, UNREADABLE, TOO_LARGE)

# Fonts without a Unicode map extract as NUL and other control characters.
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _result(status, pages=(), page_count=0, truncated=False):
    pages = list(pages)
    return {
        'status': status,
        'text': '\n\n'.join(p for p in pages if p),
        'pages': pages,
        'page_count': page_count,
        'empty_pages': [i for i, p in enumerate(pages, 1) if not p],
        'truncated': truncated,
        'method': METHOD,
    }


def clean_page(text):
    """Page text without control characters; '' when it is mostly unmapped
    glyphs (more control characters than letters and digits)."""
    text = text or ''
    if len(_CONTROL_RE.findall(text)) > sum(c.isalnum() for c in text):
        return ''
    return _CONTROL_RE.sub('', text).strip()


def extract(pdf_bytes, max_pages=None):
    """The text of the first `max_pages` pages (default 200, at most 2000).

    Returns {status, text, pages: [text per page], page_count, empty_pages:
    [1-based numbers of pages without text], truncated, method}. `status` is
    ok, no_text_layer (no page has text: a scan), encrypted (needs a
    password), unreadable (not a PDF, or broken) or too_large (over the
    upload limit). Never raises for a bad file.
    """
    if len(pdf_bytes) > MAX_BYTES:
        return _result(TOO_LARGE)
    limit = min(max_pages or DEFAULT_MAX_PAGES, MAX_PAGES)
    from pypdf import PdfReader
    # pypdf logs every oddity of a malformed file; the status says enough.
    pypdf_logger = logging.getLogger('pypdf')
    level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):
                    return _result(ENCRYPTED)
            except Exception:
                return _result(ENCRYPTED)
        page_count = len(reader.pages)
        pages = []
        for page in reader.pages[:limit]:
            try:
                pages.append(clean_page(page.extract_text()))
            except Exception:
                pages.append('')   # one broken page does not lose the rest
    except Exception:
        return _result(UNREADABLE)
    finally:
        pypdf_logger.setLevel(level)
    status = OK if any(pages) else (NO_TEXT_LAYER if page_count else UNREADABLE)
    return _result(status, pages, page_count, truncated=page_count > limit)
