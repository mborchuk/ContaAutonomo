"""
Upload type detection for the AI Parser. The type comes from the file's
leading bytes, never from its name, and decides how a provider sends it:
PDFs as documents, everything else as images.
"""
import base64

from contaautonomo.security.file_validation import sniff_kind

PDF = 'application/pdf'
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp')
SUPPORTED = 'PDF, JPEG, PNG, GIF or WebP'
UNSUPPORTED_MESSAGE = f'Unsupported file type. Upload a {SUPPORTED} file.'


class UnsupportedFile(ValueError):
    """The upload is not a type the providers accept; the message says so."""


def detect_mime(data):
    head = bytes(data[:16])
    kind = sniff_kind(head)
    if kind == 'pdf':
        return PDF
    if kind in ('jpeg', 'png'):
        return f'image/{kind}'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    raise UnsupportedFile(UNSUPPORTED_MESSAGE)


def is_pdf(mime):
    return mime == PDF


def b64(data):
    return base64.b64encode(data).decode('ascii')
