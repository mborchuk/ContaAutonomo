"""F2 — Invoice lifecycle hardening tests: issue, sequence, snapshot, lock,
rectificative, annul, and issue-blocking hooks."""
import json
from datetime import date
from types import SimpleNamespace

import pytest


def _draft(db, Invoice, Customer, amount_eur=1000.0, tax_type='standard',
           invoice_date=date(2026, 2, 1), number='DRAFT-1'):
    customer = Customer(name='ACME', tax_type=tax_type)
    db.session.add(customer)
    db.session.flush()
    inv = Invoice(invoice_number=number, client_name='ACME', amount_usd=0.0,
                  amount_eur=amount_eur, exchange_rate=1.0, invoice_date=invoice_date,
                  status='draft', currency='EUR', customer_id=customer.id)
    db.session.add(inv)
    db.session.flush()
    return inv


def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


# --- F2-D2 / F2-D4: issue assigns sequence + freezes snapshot ---------------

def test_issue_assigns_series_sequence_and_snapshot(app):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)

    assert inv.status == 'issued'
    assert inv.series == '2026'
    assert inv.sequence_number == 1
    assert inv.invoice_number == '2026/0001'
    assert inv.issued_at is not None
    # Snapshot frozen: standard customer -> 21% of 1000.
    assert inv.snap_vat_rate == 21.0
    assert inv.snap_taxable_base == 1000.0
    assert inv.snap_vat_amount == 210.0
    snap = json.loads(inv.snap_customer)
    assert snap['tax_type'] == 'standard'


def test_snapshot_survives_customer_change(app):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)
    # Change the customer AFTER issue — snapshot must not move.
    inv.customer.tax_type = 'non_eu'
    db.session.commit()
    assert inv.snap_vat_rate == 21.0
    assert inv.snap_vat_amount == 210.0


def test_sequence_increments_per_series(app):
    from app import db, Invoice, Customer, _issue_invoice

    a = _draft(db, Invoice, Customer, number='DRAFT-A')
    _issue_invoice(a, request_obj=None)
    b = _draft(db, Invoice, Customer, number='DRAFT-B')
    _issue_invoice(b, request_obj=None)
    assert a.invoice_number == '2026/0001'
    assert b.invoice_number == '2026/0002'


def test_non_standard_customer_has_zero_vat_snapshot(app):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer, tax_type='eu_b2b')
    _issue_invoice(inv, request_obj=None)
    assert inv.snap_vat_rate == 0.0
    assert inv.snap_vat_amount == 0.0


# --- F2-D1: lock enforcement -----------------------------------------------

def test_locked_helper():
    from app import _invoice_locked
    assert _invoice_locked(SimpleNamespace(status='issued')) is True
    assert _invoice_locked(SimpleNamespace(status='paid')) is True
    assert _invoice_locked(SimpleNamespace(status='draft')) is False
    assert _invoice_locked(SimpleNamespace(status='pending')) is False
    assert _invoice_locked(None) is False


def test_edit_route_blocks_issued_invoice(app, client):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)
    db.session.commit()
    _auth(client)
    resp = client.get(f'/edit/{inv.id}', follow_redirects=False)
    assert resp.status_code == 302
    assert f'/view/{inv.id}' in resp.headers['Location']


def test_delete_route_blocks_issued_invoice(app, client):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)
    db.session.commit()
    _auth(client)
    resp = client.post(f'/delete/{inv.id}', follow_redirects=False)
    assert resp.status_code == 302
    # Still present.
    assert db.session.get(Invoice, inv.id) is not None


def test_invoice_service_locks_issued(app):
    from app import db, Invoice, Customer, _issue_invoice
    from module_manager import InvoiceService

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)
    svc = InvoiceService(SimpleNamespace(db=db))
    assert svc.is_locked(inv) is True


# --- F2-D3: rectify + annul -------------------------------------------------

def test_rectify_creates_linked_draft_without_mutating_original(app, client):
    from app import db, Invoice, InvoiceItem, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    db.session.add(InvoiceItem(invoice_id=inv.id, description='Work', quantity=1,
                               unit_price_usd=1000, subtotal_usd=1000))
    db.session.commit()
    _issue_invoice(inv, request_obj=None)
    db.session.commit()
    original_number = inv.invoice_number

    _auth(client)
    resp = client.post(f'/rectify/{inv.id}', data={'rectification_type': 'sustitucion'},
                       follow_redirects=False)
    assert resp.status_code == 302
    draft = Invoice.query.filter_by(rectifies_invoice_id=inv.id).first()
    assert draft is not None
    assert draft.status == 'draft'
    assert draft.rectification_type == 'sustitucion'
    assert len(draft.items) == 1
    # Original untouched.
    assert inv.invoice_number == original_number
    assert inv.status == 'issued'


def test_annul_marks_cancelled_and_retains(app, client):
    from app import db, Invoice, Customer, _issue_invoice

    inv = _draft(db, Invoice, Customer)
    _issue_invoice(inv, request_obj=None)
    db.session.commit()
    _auth(client)
    resp = client.post(f'/annul/{inv.id}', follow_redirects=False)
    assert resp.status_code == 302
    refreshed = db.session.get(Invoice, inv.id)
    assert refreshed is not None
    assert refreshed.status == 'cancelled'


# --- F2-D5: issue-blocking hooks -------------------------------------------

def test_issue_hook_veto_aborts(app, monkeypatch):
    import app as appmod
    from app import db, Invoice, Customer, _issue_invoice

    class Vetoer:
        def on_invoice_issued(self, invoice, request):
            raise ValueError('compliance veto')

    class FakeManager:
        def on_invoice_issued(self, invoice, request):
            Vetoer().on_invoice_issued(invoice, request)

    monkeypatch.setattr(appmod, 'module_manager', FakeManager())
    inv = _draft(db, Invoice, Customer)
    with pytest.raises(ValueError):
        _issue_invoice(inv, request_obj=None)
    db.session.rollback()
    # Transition aborted — not left issued after rollback.
    refreshed = db.session.get(Invoice, inv.id)
    assert refreshed is None or refreshed.status != 'issued'
