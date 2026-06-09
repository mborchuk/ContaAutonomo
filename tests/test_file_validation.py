"""Magic-byte file validation tests."""
from file_validation import content_matches_ext, sniff_kind


def test_pdf_accepted():
    assert content_matches_ext(b'%PDF-1.7\n...', 'pdf')


def test_renamed_text_as_pdf_rejected():
    assert not content_matches_ext(b'just some text', 'pdf')


def test_xlsx_is_zip():
    assert content_matches_ext(b'PK\x03\x04rest', 'xlsx')


def test_legacy_doc_is_ole():
    assert content_matches_ext(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'doc')


def test_unknown_extension_rejected():
    assert not content_matches_ext(b'%PDF', 'exe')


def test_sniff_png_and_jpeg():
    assert sniff_kind(b'\x89PNG\r\n\x1a\n') == 'png'
    assert sniff_kind(b'\xff\xd8\xff\xe0') == 'jpeg'
    assert sniff_kind(b'random') is None
