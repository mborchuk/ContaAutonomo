"""PDF signing and verification through the function registry."""
from datetime import date

import pytest

from contaautonomo.core.functions import Function, Param
from contaautonomo.core.module_manager import BaseModule, LocalStorageBackend


def _replacement(mm, mid, *functions):
    class Replacement(BaseModule):
        module_id = mid
        name = 'Replacement'

        def get_functions(self):
            return list(functions)
    return Replacement(mm.core)


@pytest.fixture
def pdf_signature(loaded_modules, standalone_module, monkeypatch):
    module = standalone_module('pdf_signature')
    monkeypatch.setitem(loaded_modules.modules, 'pdf_signature', module)
    return module


def _invoice(number):
    from contaautonomo.app import Invoice, db
    inv = Invoice(invoice_number=number, client_name='Sign Test SL', amount_usd=0.0,
                  amount_eur=100.0, exchange_rate=1.0, invoice_date=date(2026, 11, 12),
                  status='draft', currency='EUR')
    db.session.add(inv)
    db.session.commit()
    return inv


def test_extract_signatures_function(loaded_modules, standalone_module, monkeypatch):
    monkeypatch.setitem(loaded_modules.modules, 'pdf_verify', standalone_module('pdf_verify'))
    assert loaded_modules.functions.provider('pdf.extract_signatures') == 'pdf_verify'
    assert loaded_modules.functions.call('pdf.extract_signatures', pdf_bytes=b'not a pdf') == []


def test_sign_invoice_signs_only_what_the_form_asked_for(loaded_modules, pdf_signature,
                                                         monkeypatch):
    from contaautonomo.app import db
    calls = []
    monkeypatch.setattr(pdf_signature, '_sign_invoice_pdf',
                        lambda invoice, visual, digital: calls.append(
                            (invoice.invoice_number, visual, digital)) or True)
    asked, not_asked = _invoice('SIGN-1'), _invoice('SIGN-2')
    db.session.add(pdf_signature.PDFSignatureInvoice(invoice_id=asked.id, has_visual=True))
    db.session.commit()

    call = loaded_modules.functions.call
    assert call('pdf.sign_invoice', invoice_id=asked.id) == {'signed': True, 'requested': True}
    assert call('pdf.sign_invoice', invoice_id=not_asked.id) == \
        {'signed': False, 'requested': False}
    assert calls == [('SIGN-1', True, False)]


def test_signing_certificate_feeds_the_facturae_signature(loaded_modules, pdf_signature,
                                                          monkeypatch):
    from contaautonomo.app import db
    from modules.einvoice import xades
    einvoice = loaded_modules.modules['einvoice']
    monkeypatch.setattr(xades, 'available', lambda: True)
    assert loaded_modules.functions.call('pdf.signing_certificate') is None
    assert einvoice._certificate() is None

    cfg = pdf_signature._get_config()
    cfg.certificate_key, cfg.certificate_password = 'certs/test.pfx', 'secret-pw'
    db.session.commit()
    monkeypatch.setattr(loaded_modules.core.storage, 'get',
                        lambda key: (b'PFX', 'test.pfx') if key == 'certs/test.pfx' else None)
    assert einvoice._certificate() == (b'PFX', 'secret-pw')
    cfg.certificate_key = ''
    db.session.commit()


def test_invoice_badge_uses_any_signature_reader(loaded_modules, pdf_signature, monkeypatch):
    reader = Function('pdf.extract_signatures', lambda pdf_bytes: [{'signer': 'Test CA'}],
                      'Signatures.', params=(Param('pdf_bytes', 'bytes', required=True),))
    monkeypatch.setitem(loaded_modules.modules, 'zz_reader',
                        _replacement(loaded_modules, 'zz_reader', reader))
    monkeypatch.setattr(loaded_modules.core.invoice_service, 'get_pdf',
                        lambda invoice: (b'%PDF-1.4', 'x.pdf'))
    assert pdf_signature._check_real_pdf_signed(object()) == [{'signer': 'Test CA'}]
    monkeypatch.delitem(loaded_modules.modules, 'zz_reader')
    assert pdf_signature._check_real_pdf_signed(object()) == []


def test_new_invoice_pdf_is_signed_through_the_function(loaded_modules, client, tmp_path,
                                                        monkeypatch):
    from contaautonomo.app import Invoice
    seen = []
    signer = Function('pdf.sign_invoice',
                      lambda invoice_id: seen.append(invoice_id) or
                      {'signed': True, 'requested': True},
                      'Sign.', params=(Param('invoice_id', 'int', required=True),),
                      read_only=False)
    monkeypatch.setitem(loaded_modules.modules, 'zz_signer',
                        _replacement(loaded_modules, 'zz_signer', signer))
    monkeypatch.setattr(loaded_modules.core, '_storage_backend',
                        LocalStorageBackend(str(tmp_path)))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.post('/create', data={
        'invoice_number': 'SIGN-NEW', 'client_name': 'Sign Test SL',
        'invoice_date': '2026-11-12', 'currency': 'EUR',
        'items[0][description]': 'Work', 'items[0][quantity]': '1',
        'items[0][unit_price]': '100'})
    assert seen == [Invoice.query.filter_by(invoice_number='SIGN-NEW').one().id]
