"""E-2 — PDF Verify: signature details from a signed synthetic PDF; nothing
from an unsigned one. The certificate is generated per test run."""
import io
from datetime import datetime, timedelta, timezone

import pytest

from modules.pdf_verify.index import _parse_pdf_date, extract_signatures

pytest.importorskip('pyhanko')


def _unsigned_pdf():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, 'Synthetic test document')
    c.save()
    return buf.getvalue()


def _self_signed(common_name):
    from asn1crypto import keys as asn1_keys, x509 as asn1_x509
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Test Org')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(424242)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    der_cert = cert.public_bytes(serialization.Encoding.DER)
    der_key = key.private_bytes(serialization.Encoding.DER,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return asn1_x509.Certificate.load(der_cert), asn1_keys.PrivateKeyInfo.load(der_key)


def _signed_pdf():
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    cert, key = _self_signed('Test Signer')
    signer = signers.SimpleSigner(signing_cert=cert, signing_key=key,
                                  cert_registry=SimpleCertificateStore.from_certs([cert]))
    writer = IncrementalPdfFileWriter(io.BytesIO(_unsigned_pdf()))
    meta = signers.PdfSignatureMetadata(field_name='Signature1', reason='Test reason',
                                        location='Valencia')
    out = signers.sign_pdf(writer, meta, signer=signer)
    return out.getvalue()


def test_unsigned_pdf_has_no_signatures():
    assert extract_signatures(_unsigned_pdf()) == []


def test_garbage_is_not_a_pdf():
    assert extract_signatures(b'not a pdf') == []


def test_signed_pdf_reports_signer_and_metadata():
    sigs = extract_signatures(_signed_pdf())
    assert len(sigs) == 1
    sig = sigs[0]
    assert 'Test Signer' in sig['signer']
    assert sig['reason'] == 'Test reason' and sig['location'] == 'Valencia'
    assert sig['issuer'] == sig['signer']          # self-signed
    assert sig['filter'] == 'Adobe.PPKLite'
    assert sig['serial'] == '424242'
    assert sig['algorithm'] == 'SHA-256'
    assert sig['signed_at'] and sig['valid_from'] < sig['valid_to']


def test_pdf_date_parsing():
    parsed = _parse_pdf_date("D:20260302101500+01'00'")
    assert parsed and parsed.startswith('2026-03-02')
    assert _parse_pdf_date('') is None
