"""E-2 — Invoice Attachments: upload a ready-made PDF; sealed PDFs on issued
invoices cannot be replaced; the attach action is hidden on locked invoices."""
import io
from datetime import date
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import FileStorage

from module_manager import LocalStorageBackend

PDF = b'%PDF-1.4\n% synthetic test pdf\n%%EOF\n'


@pytest.fixture
def attachments(loaded_modules, tmp_path, monkeypatch):
    core = loaded_modules.core
    monkeypatch.setattr(core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    inst = loaded_modules.discovered['invoice_attachments'](core)
    inst.register_models(core.db)
    return inst


def _invoice(number, status='draft', issued=False):
    from app import Invoice, db
    inv = Invoice.query.filter_by(invoice_number=number).first()
    if inv:
        db.session.delete(inv)
        db.session.commit()
    inv = Invoice(invoice_number=number, client_name='Attach SL', amount_usd=0.0,
                  amount_eur=100.0, exchange_rate=1.0, invoice_date=date(2026, 3, 1),
                  status=status, currency='EUR')
    if issued:
        from datetime import datetime
        inv.issued_at = datetime(2026, 3, 1)
    db.session.add(inv)
    db.session.commit()
    return inv


def _req(filename, data=PDF):
    return SimpleNamespace(files={'invoice_pdf': FileStorage(io.BytesIO(data), filename=filename)})


def test_upload_on_create_stores_pdf_and_hash(attachments, app, tmp_path):
    import hashlib
    inv = _invoice('ATT-1')
    with app.test_request_context():
        attachments.on_invoice_created(inv, _req('ready.pdf'))
    assert inv.pdf_hash == hashlib.sha256(PDF).hexdigest()
    assert (tmp_path / inv.pdf_storage_key).read_bytes() == PDF
    assert attachments.core.invoice_service.has_pdf(inv)


def test_non_pdf_is_rejected(attachments, app):
    inv = _invoice('ATT-2')
    with app.test_request_context():
        attachments.on_invoice_updated(inv, _req('notes.txt', b'hello'))
    assert not inv.pdf_hash


def test_sealed_pdf_on_issued_invoice_cannot_be_replaced(attachments, app):
    inv = _invoice('ATT-3')
    with app.test_request_context():
        attachments.on_invoice_created(inv, _req('first.pdf'))
    first_hash = inv.pdf_hash
    inv.status = 'issued'
    from datetime import datetime
    inv.issued_at = datetime(2026, 3, 2)
    attachments.core.db.session.commit()

    with pytest.raises(ValueError, match='Cannot replace'):
        attachments.core.invoice_service.attach_pdf(inv, b'%PDF-other', 'second.pdf')
    with app.test_request_context():   # the form hook reports it and keeps the PDF
        attachments.on_invoice_updated(inv, _req('second.pdf', b'%PDF-1.4 other'))
    assert inv.pdf_hash == first_hash


def test_attach_action_hidden_on_locked_invoice(attachments, app, monkeypatch):
    issued = _invoice('ATT-4', status='issued', issued=True)
    assert attachments.get_invoice_actions(issued) == []

    import flask
    monkeypatch.setattr(flask, 'render_template', lambda name, **ctx: f'<{name}>')
    draft = _invoice('ATT-5')
    with app.test_request_context():
        assert attachments.get_invoice_actions(draft) == ['<attach_button.html>']


def test_form_fields_rendered(attachments):
    assert 'name="invoice_pdf"' in attachments.get_create_form_html()
    inv = _invoice('ATT-6')
    assert 'Attach Invoice PDF' in attachments.get_edit_form_html(inv)
