"""D-4 — Facturae XAdES-EPES signature with the PDF Signature certificate.
The certificate is a throwaway self-signed PFX generated per test run."""
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip('signxml')

from modules.einvoice import xades  # noqa: E402
from modules.einvoice.facturae import generate_facturae  # noqa: E402

PASSWORD = 'test-pfx-password'


def _pfx(password=PASSWORD):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Facturae Test Signer')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(777)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    data = pkcs12.serialize_key_and_certificates(
        b'test', key, cert, None, BestAvailableEncryption(password.encode()))
    return data, cert


@pytest.fixture
def issued(loaded_modules):
    from app import Customer, Invoice, Settings, _issue_invoice, db
    settings = Settings.query.first() or Settings()
    db.session.add(settings)
    settings.business_name, settings.vat_number = 'Mi Negocio SL', 'B12345678'
    settings.address, settings.postal_code = 'Calle Mayor 1', '12006'
    settings.city, settings.country = 'Castellón', 'Spain'
    cust = Customer(name='Firma Kunde GmbH', tax_type='eu_b2b', vat_number='DE987654321',
                    address='Hauptstr. 9', postal_code='10115', city='Berlin',
                    country='Germany')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number='XS-1', client_name=cust.name, amount_usd=0.0,
                  amount_eur=1200.0, exchange_rate=1.0, invoice_date=date(2026, 6, 1),
                  status='draft', currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)
    return inv, settings, cust


def test_sign_and_verify_with_facturae_policy(issued):
    inv, settings, cust = issued
    pfx, cert = _pfx()
    signed = xades.sign_facturae(generate_facturae(inv, settings, cust), pfx, PASSWORD)
    assert b'SignaturePolicyIdentifier' in signed
    assert xades.POLICY_DIGEST_SHA1.encode() in signed
    assert b'<xades:ClaimedRole>emisor</xades:ClaimedRole>' in signed
    assert xades.POLICY_IDENTIFIER.encode() in signed
    assert xades.verify_facturae(signed, cert)        # raises if invalid


def test_tampered_file_fails_verification(issued):
    from signxml.exceptions import InvalidSignature
    inv, settings, cust = issued
    pfx, cert = _pfx()
    signed = xades.sign_facturae(generate_facturae(inv, settings, cust), pfx, PASSWORD)
    tampered = signed.replace(b'1200.00', b'9200.00', 1)
    assert tampered != signed
    with pytest.raises(InvalidSignature):
        xades.verify_facturae(tampered, cert)


def test_wrong_password_raises_value_error():
    pfx, _cert = _pfx()
    with pytest.raises(ValueError):
        xades.load_pkcs12(pfx, 'wrong')


def test_module_export_signed_when_certificate_configured(issued, loaded_modules,
                                                         monkeypatch):
    inv, settings, cust = issued
    module = loaded_modules.modules['einvoice']
    pfx, cert = _pfx()
    monkeypatch.setattr(module, '_certificate', lambda: (pfx, PASSWORD))
    xml, signed = module._export(inv, settings, cust)
    assert signed and xades.verify_facturae(xml, cert)
    xml, signed = module._export(inv, settings, cust, unsigned=True)
    assert not signed and b'Signature' not in xml


def test_module_export_unsigned_without_certificate(issued, loaded_modules, client):
    inv, _settings, _cust = issued
    module = loaded_modules.modules['einvoice']
    assert module._certificate() is None          # pdf_signature not enabled in tests
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/einvoice/facturae/{inv.id}.xml')
    assert resp.status_code == 200
    assert resp.headers['Content-Disposition'].endswith('facturae_XS-1.xml') \
        or resp.headers['Content-Disposition'].endswith('.xml')
    assert b'SignaturePolicyIdentifier' not in resp.data


def test_download_route_serves_xsig_when_signed(issued, loaded_modules, client, monkeypatch):
    inv, _settings, _cust = issued
    module = loaded_modules.modules['einvoice']
    pfx, _cert = _pfx()
    monkeypatch.setattr(module, '_certificate', lambda: (pfx, PASSWORD))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get(f'/einvoice/facturae/{inv.id}.xml')
    assert resp.status_code == 200
    assert resp.headers['Content-Disposition'].endswith('.xsig')
    assert b'SignaturePolicyIdentifier' in resp.data
