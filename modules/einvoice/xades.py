"""XAdES-EPES signature for Facturae 3.2.2 files (task D-4).

A legally valid Facturae needs an enveloped XAdES-EPES signature that names
the Facturae signature policy v3.1. The certificate is the PFX/P12 uploaded in
the PDF Signature module; nothing else is configured here.

Policy identifier, hash and signer role follow the official policy document
(facturae.gob.es, "Política de firma formato Facturae v3.1") and match the
values used by production Facturae libraries. Validate a signed file with the
official validator before relying on it (see the module README).
"""
from lxml import etree

POLICY_IDENTIFIER = ('http://www.facturae.es/politica_de_firma_formato_facturae/'
                     'politica_de_firma_formato_facturae_v3_1.pdf')
POLICY_DESCRIPTION = 'Política de Firma FacturaE v3.1'
POLICY_DIGEST_SHA1 = 'Ohixl6upD6av8N7pEvDABhEL6hM='
SIGNER_ROLE = 'emisor'


def available():
    """True when the XAdES library is installed."""
    try:
        import signxml.xades  # noqa: F401
    except ImportError:
        return False
    return True


def _policy():
    from signxml.algorithms import DigestAlgorithm
    from signxml.xades import XAdESSignaturePolicy
    return XAdESSignaturePolicy(Identifier=POLICY_IDENTIFIER, Description=POLICY_DESCRIPTION,
                                DigestMethod=DigestAlgorithm.SHA1,
                                DigestValue=POLICY_DIGEST_SHA1)


def load_pkcs12(pfx_bytes, password):
    """(private_key, [certificate, *chain]) from PFX/P12 bytes."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    key, cert, chain = pkcs12.load_key_and_certificates(
        pfx_bytes, (password or '').encode('utf-8') or None)
    if key is None or cert is None:
        raise ValueError('The certificate file has no private key or certificate')
    return key, [cert, *(chain or [])]


def sign_facturae(xml_bytes, pfx_bytes, password):
    """Return the Facturae XML with an enveloped XAdES-EPES signature."""
    from signxml.xades import XAdESDataObjectFormat, XAdESSigner
    key, certs = load_pkcs12(pfx_bytes, password)
    root = etree.fromstring(xml_bytes)
    signer = XAdESSigner(
        signature_policy=_policy(),
        claimed_roles=[SIGNER_ROLE],
        data_object_format=XAdESDataObjectFormat(Description='Factura electrónica',
                                                 MimeType='text/xml'))
    signed = signer.sign(root, key=key, cert=certs)
    return etree.tostring(signed, xml_declaration=True, encoding='UTF-8')


def verify_facturae(signed_bytes, cert):
    """Verify a signed Facturae against `cert` and the Facturae policy.
    Raises signxml.exceptions.InvalidSignature on failure."""
    from cryptography.hazmat.primitives.serialization import Encoding
    from signxml.xades import XAdESVerifier
    pem = cert.public_bytes(Encoding.PEM).decode('ascii')
    return XAdESVerifier().verify(signed_bytes, x509_cert=pem,
                                  expect_signature_policy=_policy())
