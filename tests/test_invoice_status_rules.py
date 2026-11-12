"""A-1: an invoice reaches paid/cancelled only through the lifecycle actions.

Creating or editing an invoice (web form or API) can never set the status
directly, and an annulled invoice is read-only like an issued one.
"""
from datetime import date

import pytest


def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def _draft(number, status='draft'):
    from app import db, Invoice, Customer
    customer = Customer(name=f'Client {number}', tax_type='standard')
    db.session.add(customer)
    db.session.flush()
    inv = Invoice(invoice_number=number, client_name=customer.name,
                  amount_usd=0.0, amount_eur=100.0, exchange_rate=1.0,
                  invoice_date=date(2026, 3, 1), status=status,
                  currency='EUR', customer_id=customer.id)
    db.session.add(inv)
    db.session.commit()
    return inv


def _form(number, **extra):
    data = {'invoice_number': number, 'client_name': 'Form Client',
            'invoice_date': '2026-03-01', 'currency': 'EUR',
            'items[0][description]': 'Work', 'items[0][quantity]': '1',
            'items[0][unit_price]': '100'}
    data.update(extra)
    return data


@pytest.fixture
def api_headers(loaded_modules):
    from app import db, Settings
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()
    api = loaded_modules.modules['api']
    api.on_enable()          # idempotent: adds the token column, mints a token
    return {'X-API-Token': api._get_token()}


# --- web form ----------------------------------------------------------------

@pytest.mark.parametrize('status', ['paid', 'cancelled', 'issued', 'pending'])
def test_web_create_rejects_status(loaded_modules, client, status):
    from app import Invoice
    _auth(client)
    resp = client.post('/create', data=_form(f'WEB-{status}', status=status))
    assert resp.status_code == 302
    assert Invoice.query.filter_by(invoice_number=f'WEB-{status}').first() is None


def test_web_create_makes_a_draft(loaded_modules, client):
    from app import Invoice
    _auth(client)
    client.post('/create', data=_form('WEB-DRAFT'))
    assert Invoice.query.filter_by(invoice_number='WEB-DRAFT').one().status == 'draft'


@pytest.mark.parametrize('status', ['paid', 'cancelled'])
def test_web_edit_cannot_change_status(loaded_modules, client, status):
    from app import db, Invoice
    inv = _draft(f'EDIT-{status}')
    _auth(client)
    resp = client.post(f'/edit/{inv.id}', data=_form(inv.invoice_number, status=status))
    assert resp.status_code == 302
    assert db.session.get(Invoice, inv.id).status == 'draft'


def test_web_edit_without_status_still_saves(loaded_modules, client):
    from app import db, Invoice
    inv = _draft('EDIT-OK')
    _auth(client)
    client.post(f'/edit/{inv.id}', data=_form('EDIT-OK', notes='updated'))
    saved = db.session.get(Invoice, inv.id)
    assert saved.notes == 'updated'
    assert saved.status == 'draft'


def test_paid_only_after_issue(loaded_modules, client):
    from app import db, Invoice
    inv = _draft('FLOW-1')
    _auth(client)
    client.post(f'/mark-paid/{inv.id}')
    assert db.session.get(Invoice, inv.id).status == 'draft'
    client.post(f'/issue/{inv.id}')
    client.post(f'/mark-paid/{inv.id}')
    assert db.session.get(Invoice, inv.id).status == 'paid'


# --- annulled invoices are read-only -----------------------------------------

def test_annulled_invoice_cannot_be_edited_or_deleted(loaded_modules, client):
    from app import db, Invoice
    inv = _draft('ANNUL-1')
    _auth(client)
    client.post(f'/issue/{inv.id}')
    client.post(f'/annul/{inv.id}')
    assert db.session.get(Invoice, inv.id).status == 'cancelled'

    resp = client.get(f'/edit/{inv.id}')
    assert resp.status_code == 302 and f'/view/{inv.id}' in resp.headers['Location']
    client.post(f'/delete/{inv.id}')
    assert db.session.get(Invoice, inv.id) is not None


def test_legacy_cancelled_invoice_stays_editable(loaded_modules):
    from app import _invoice_locked
    inv = _draft('LEGACY-CANCEL', status='cancelled')
    assert inv.issued_at is None
    assert _invoice_locked(inv) is False


def test_view_marks_legacy_paid_invoice(loaded_modules, client):
    inv = _draft('LEGACY-PAID', status='paid')
    _auth(client)
    html = client.get(f'/view/{inv.id}').get_data(as_text=True)
    assert 'never issued through the app' in html


# --- API ---------------------------------------------------------------------

def _api_body(**extra):
    body = {'client_name': 'API Client', 'currency': 'EUR',
            'invoice_date': '2026-03-01',
            'items': [{'description': 'Work', 'quantity': 1, 'unit_price': 100}]}
    body.update(extra)
    return body


def test_api_create_defaults_to_draft(client, api_headers):
    resp = client.post('/api/v1/invoices', json=_api_body(), headers=api_headers)
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()['data']['status'] == 'draft'


@pytest.mark.parametrize('status', ['paid', 'pending', 'cancelled'])
def test_api_create_rejects_status(client, api_headers, status):
    resp = client.post('/api/v1/invoices', json=_api_body(status=status),
                       headers=api_headers)
    assert resp.status_code == 400


def test_api_patch_rejects_status(client, api_headers):
    inv = _draft('API-PATCH')
    resp = client.patch(f'/api/v1/invoices/{inv.id}', json={'status': 'paid'},
                        headers=api_headers)
    assert resp.status_code == 400


def test_api_issue_then_mark_paid(client, api_headers):
    inv = _draft('API-FLOW')
    early = client.post(f'/api/v1/invoices/{inv.id}/mark-paid', headers=api_headers)
    assert early.status_code == 409

    issued = client.post(f'/api/v1/invoices/{inv.id}/issue', headers=api_headers)
    assert issued.status_code == 200, issued.get_json()
    assert issued.get_json()['data']['status'] == 'issued'

    paid = client.post(f'/api/v1/invoices/{inv.id}/mark-paid', headers=api_headers)
    assert paid.status_code == 200
    assert paid.get_json()['data']['status'] == 'paid'

    again = client.post(f'/api/v1/invoices/{inv.id}/issue', headers=api_headers)
    assert again.status_code == 409
