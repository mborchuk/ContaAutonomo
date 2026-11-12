"""An issued invoice's PDF is rebuilt from the issued data: final number,
IVA snapshot and the Verifactu QR — not the PDF stored while it was a draft."""
import io

import pytest
from pypdf import PdfReader

from module_manager import LocalStorageBackend


def _text(data):
    return PdfReader(io.BytesIO(data)).pages[0].extract_text()


@pytest.fixture
def owner(loaded_modules, client, tmp_path, monkeypatch):
    monkeypatch.setattr(loaded_modules.core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


def _create(client, number, description='Consulting work'):
    from app import Invoice
    client.post('/create', data={
        'invoice_number': number, 'client_name': 'PDF Check SL', 'invoice_date': '2026-11-12',
        'currency': 'EUR', 'items[0][description]': description,
        'items[0][quantity]': '1', 'items[0][unit_price]': '100'})
    return Invoice.query.filter_by(invoice_number=number).one()


def test_new_invoice_gets_a_generated_draft_pdf(owner):
    inv = _create(owner, 'PDF-A')
    assert inv.status == 'draft' and inv.pdf_storage_key and inv.pdf_generated is True
    assert 'QR tributario' not in _text(owner.get(f'/preview-pdf/{inv.id}').data)


def test_issued_invoice_pdf_has_final_number_and_qr(owner):
    from app import db
    inv = _create(owner, 'PDF-B')
    old_key = inv.pdf_storage_key
    owner.post(f'/issue/{inv.id}')
    db.session.refresh(inv)
    assert inv.status == 'issued' and inv.pdf_storage_key is None   # draft PDF discarded
    for path in (f'/preview-pdf/{inv.id}', f'/generate-pdf/{inv.id}'):
        text = _text(owner.get(path).data)
        assert 'QR tributario:' in text, path
        assert inv.invoice_number in text and 'PDF-B' not in text, path
    assert inv.pdf_storage_key and inv.pdf_storage_key != old_key   # stored again, with QR
    assert 'QR tributario:' in _text(owner.get(f'/preview-pdf/{inv.id}').data)


def test_editing_a_draft_rebuilds_its_pdf(owner):
    from app import db
    inv = _create(owner, 'PDF-C', description='First wording')
    owner.post(f'/edit/{inv.id}', data={
        'invoice_number': 'PDF-C', 'client_name': 'PDF Check SL', 'invoice_date': '2026-11-12',
        'currency': 'EUR', 'items[0][description]': 'Second wording',
        'items[0][quantity]': '1', 'items[0][unit_price]': '100'})
    db.session.refresh(inv)
    text = _text(owner.get(f'/preview-pdf/{inv.id}').data)
    assert 'Second wording' in text and 'First wording' not in text


def test_attached_and_legacy_pdfs_are_kept_on_issue(owner, loaded_modules):
    from app import db, _issue_invoice
    svc = loaded_modules.core.invoice_service
    attached = _create(owner, 'PDF-D')
    svc.attach_pdf(attached, b'%PDF-1.4 user file', 'mine.pdf')
    legacy = _create(owner, 'PDF-E')
    legacy.pdf_generated = None          # stored before the flag existed
    db.session.commit()
    keys = {attached.id: attached.pdf_storage_key, legacy.id: legacy.pdf_storage_key}
    _issue_invoice(attached)
    _issue_invoice(legacy)
    assert attached.pdf_storage_key == keys[attached.id] and attached.pdf_generated is False
    assert legacy.pdf_storage_key == keys[legacy.id]


def test_regenerate_button_and_force_rebuild(owner):
    from app import db, _issue_invoice
    inv = _create(owner, 'PDF-F')
    inv.pdf_generated = None             # e.g. created before this fix
    db.session.commit()
    _issue_invoice(inv)
    view = owner.get(f'/view/{inv.id}').get_data(as_text=True)
    assert 'Regenerate PDF' in view and 'force=true' in view
    text = _text(owner.get(f'/generate-pdf/{inv.id}?force=true').data)
    assert 'QR tributario:' in text and inv.invoice_number in text
