"""Lightweight file content validation by magic bytes — no external dependency.

Extension checks are trivially bypassed by renaming. These helpers sniff the
leading bytes so an upload's real type must match its claimed extension.
"""

_PDF_SIG = b'%PDF'
_ZIP_SIGS = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')   # xlsx/docx are zip
_OLE_SIG = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'              # legacy doc/xls
_JPEG_SIG = b'\xff\xd8\xff'
_PNG_SIG = b'\x89PNG\r\n\x1a\n'

# Map a file extension to the content "kind" its bytes must match.
EXT_KIND = {
    'pdf': 'pdf',
    'xlsx': 'zip', 'docx': 'zip', 'pptx': 'zip',
    'xls': 'ole', 'doc': 'ole', 'ppt': 'ole',
    'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
}


def sniff_kind(head):
    """Return a coarse content kind from the leading bytes, or None if unknown."""
    if head.startswith(_PDF_SIG):
        return 'pdf'
    if any(head.startswith(s) for s in _ZIP_SIGS):
        return 'zip'
    if head.startswith(_OLE_SIG):
        return 'ole'
    if head.startswith(_JPEG_SIG):
        return 'jpeg'
    if head.startswith(_PNG_SIG):
        return 'png'
    return None


def content_matches_ext(head, ext):
    """True if the sniffed content kind matches the expected kind for `ext`."""
    expected = EXT_KIND.get((ext or '').lower())
    if expected is None:
        return False
    return sniff_kind(head) == expected


def validate_filestorage(file_storage, ext):
    """Validate a Werkzeug FileStorage by reading its first bytes, then rewind.
    Returns True if the content matches the extension."""
    stream = file_storage.stream
    pos = stream.tell()
    head = stream.read(2048)
    stream.seek(pos)
    return content_matches_ext(head, ext)
