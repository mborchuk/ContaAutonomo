"""pdf.extract_text: the core's pypdf default and a module replacing it."""
import io

import pytest

from contaautonomo.core import pdf_text
from contaautonomo.core.functions import Function, Param
from contaautonomo.core.module_manager import BaseModule


def _pdf(*pages):
    """A PDF with one page per argument: a string draws text, None draws only a
    rectangle (like a scan: no text layer)."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages:
        if text is None:
            c.rect(50, 50, 200, 200, fill=1)
        else:
            c.drawString(50, 750, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_text_pages_and_empty_pages():
    out = pdf_text.extract(_pdf('Factura 2026-0001', None, 'Total 121,00 EUR'))
    assert out['status'] == 'ok' and out['method'] == 'pypdf'
    assert out['page_count'] == 3 and out['empty_pages'] == [2]
    assert out['pages'][0] == 'Factura 2026-0001' and out['pages'][1] == ''
    assert out['text'] == 'Factura 2026-0001\n\nTotal 121,00 EUR'
    assert not out['truncated']


def test_a_scan_has_no_text_layer():
    out = pdf_text.extract(_pdf(None, None))
    assert out['status'] == 'no_text_layer' and out['text'] == ''
    assert out['page_count'] == 2 and out['empty_pages'] == [1, 2]


@pytest.mark.parametrize('data', [b'', b'not a pdf', b'%PDF-1.7 broken'])
def test_garbage_is_unreadable(data):
    out = pdf_text.extract(data)
    assert out['status'] == 'unreadable' and out['text'] == '' and out['page_count'] == 0


def test_encrypted_pdf():
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_pdf('Secret text'))))
    writer.encrypt(user_password='pw', owner_password='owner')
    buf = io.BytesIO()
    writer.write(buf)
    assert pdf_text.extract(buf.getvalue())['status'] == 'encrypted'


def test_empty_user_password_is_opened():
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_pdf('Open text'))))
    writer.encrypt(user_password='', owner_password='owner')
    buf = io.BytesIO()
    writer.write(buf)
    out = pdf_text.extract(buf.getvalue())
    assert out['status'] == 'ok' and 'Open text' in out['text']


def test_page_cap():
    out = pdf_text.extract(_pdf('one', 'two', 'three'), max_pages=2)
    assert out['pages'] == ['one', 'two'] and out['page_count'] == 3 and out['truncated']


def test_too_large(monkeypatch):
    monkeypatch.setattr(pdf_text, 'MAX_BYTES', 10)
    assert pdf_text.extract(_pdf('x'))['status'] == 'too_large'


def test_control_characters_and_unmapped_glyphs():
    assert pdf_text.clean_page('Total\x00 121\x07,00\n') == 'Total 121,00'
    assert pdf_text.clean_page('\x00\x01\x02\x03 12 \x00\x00\x00') == ''
    assert pdf_text.clean_page(None) == ''


# --- through the registry ---------------------------------------------------- #

def test_the_core_provides_it_as_a_default(loaded_modules):
    functions = loaded_modules.functions
    assert functions.provider('pdf.extract_text') == 'core'
    assert functions.is_core_default('pdf.extract_text')
    out = functions.call('pdf.extract_text', pdf_bytes=_pdf('Recibo 42'), max_pages='5')
    assert out['status'] == 'ok' and out['text'] == 'Recibo 42'
    assert functions.problem_with('pdf.extract_text', {'pdf_bytes': 'text'}) == \
        'pdf_bytes must be bytes'


def test_a_module_replaces_the_default(loaded_modules, standalone_module, monkeypatch):
    class OtherReader(BaseModule):
        module_id = 'zz_pdf_reader'
        name = 'Other PDF reader'

        def get_functions(self):
            return [Function('pdf.extract_text',
                             lambda pdf_bytes, max_pages=None: {'status': 'ok', 'text': 'OCR'},
                             'Text of a PDF, with OCR.',
                             params=(Param('pdf_bytes', 'bytes', required=True),
                                     Param('max_pages', 'int', minimum=1)))]

    monkeypatch.setitem(loaded_modules.modules, 'zz_pdf_reader',
                        OtherReader(loaded_modules.core))
    functions = loaded_modules.functions
    assert functions.provider('pdf.extract_text') == 'zz_pdf_reader'
    assert functions.status('zz_pdf_reader')['replaces_core'] == ['pdf.extract_text']
    assert functions.status('zz_pdf_reader')['conflicts'] == []
    assert loaded_modules._function_state('zz_pdf_reader')['function_replaces_core'] == \
        ['pdf.extract_text']
    # The AI Parser reads PDFs through the function, so it follows the module.
    assert standalone_module('ai_parser')._extract_pdf_text(b'%PDF') == 'OCR'
    monkeypatch.delitem(loaded_modules.modules, 'zz_pdf_reader')
    assert functions.provider('pdf.extract_text') == 'core'


def test_ai_parser_reads_pdfs_with_the_core_default(standalone_module):
    assert standalone_module('ai_parser')._extract_pdf_text(_pdf('Ticket 7')) == 'Ticket 7'
