"""File integrity for documents — pure functions, no Flask.

A record keeps its evidential value only while it is shown to be unchanged
(ISO 15489: authenticity and integrity). Each stored file therefore carries
its SHA-256 fingerprint, taken when the file is stored; *Verify integrity*
reads the file back and compares.

PDF/A (ISO 19005) is the PDF profile (ISO 32000) meant for long-term
archiving. A PDF says which PDF/A part and conformance level it follows in its
XMP metadata (`pdfaid:part`, `pdfaid:conformance`); the app shows that claim.
It does not validate it: that needs a full validator such as veraPDF.
"""
import hashlib
import re

_PART = re.compile(rb'pdfaid:part\s*(?:=\s*["\']|>)\s*([1-4])')
_CONFORMANCE = re.compile(rb'pdfaid:conformance\s*(?:=\s*["\']|>)\s*([ABUabu])')


def sha256(data):
    """Hex SHA-256 of `data` (bytes)."""
    return hashlib.sha256(data or b'').hexdigest()


def pdfa_claim(data):
    """'PDF/A-2B' (part and conformance the PDF declares), 'PDF/A-4' (part 4
    has no letter), or None for a PDF that declares none."""
    if not data or not data.startswith(b'%PDF'):
        return None
    part = _PART.search(data)
    if not part:
        return None
    level = f'PDF/A-{part.group(1).decode()}'
    conformance = _CONFORMANCE.search(data)
    if conformance:
        level += conformance.group(1).decode().upper()
    return level


def short(fingerprint):
    """The first 12 hex characters, for display."""
    return (fingerprint or '')[:12]
