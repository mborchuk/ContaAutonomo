"""Deleting a draft removes the rows modules keep for it (the database enforces
foreign keys) — before, a PDF Signature row made the delete fail with a 500."""
from datetime import date

import pytest


@pytest.fixture
def owner(loaded_modules, client, standalone_module, monkeypatch):
    # Load the two modules that keep rows per invoice, as in a real install.
    for module_id in ('pdf_signature', 'invoice_comments'):
        monkeypatch.setitem(loaded_modules.modules, module_id, standalone_module(module_id))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


def _draft(number):
    from app import Invoice, db
    inv = Invoice(invoice_number=number, client_name='Delete SL', amount_usd=0.0,
                  amount_eur=50.0, exchange_rate=1.0, invoice_date=date(2026, 11, 12),
                  status='draft', currency='EUR')
    db.session.add(inv)
    db.session.commit()
    return inv


def test_foreign_keys_are_enforced(app):
    from app import db
    from sqlalchemy import text
    assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1


def test_delete_draft_with_signature_and_comment_rows(owner, loaded_modules):
    from app import Invoice, db
    sig = loaded_modules.modules['pdf_signature']
    com = loaded_modules.modules['invoice_comments']
    inv = _draft('DEL-1')
    db.session.add(sig.PDFSignatureInvoice(invoice_id=inv.id))
    db.session.add(com.InvoiceComment(invoice_id=inv.id, text='note'))
    db.session.commit()
    inv_id = inv.id

    resp = owner.post(f'/delete/{inv_id}')
    assert resp.status_code == 302
    assert db.session.get(Invoice, inv_id) is None
    assert sig.PDFSignatureInvoice.query.filter_by(invoice_id=inv_id).count() == 0
    assert com.InvoiceComment.query.filter_by(invoice_id=inv_id).count() == 0


def test_blocked_delete_rolls_back_with_a_message(owner, loaded_modules, monkeypatch):
    from app import Invoice, db
    sig = loaded_modules.modules['pdf_signature']
    inv = _draft('DEL-2')
    db.session.add(sig.PDFSignatureInvoice(invoice_id=inv.id))
    db.session.commit()
    monkeypatch.setattr(sig, 'on_invoice_deleted', lambda invoice: None)  # a module that forgets

    resp = owner.post(f'/delete/{inv.id}')
    assert resp.status_code == 302 and resp.headers['Location'].endswith(f'/view/{inv.id}')
    with owner.session_transaction() as sess:
        flashes = [m for _cat, m in sess.get('_flashes', [])]
    assert any('could not be deleted' in m for m in flashes)
    assert db.session.get(Invoice, inv.id) is not None
