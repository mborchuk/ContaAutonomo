"""An invoice number is unique: the create and edit forms refuse a used one
with a message instead of an error page, and the forms can ask before sending."""
import pytest

from contaautonomo.core.module_manager import LocalStorageBackend


@pytest.fixture
def owner(loaded_modules, client, tmp_path, monkeypatch):
    monkeypatch.setattr(loaded_modules.core, '_storage_backend', LocalStorageBackend(str(tmp_path)))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


def _create(client, number, client_name='Unique Number SL'):
    return client.post('/create', data={
        'invoice_number': number, 'client_name': client_name, 'invoice_date': '2026-11-19',
        'currency': 'EUR', 'items[0][description]': 'Work',
        'items[0][quantity]': '1', 'items[0][unit_price]': '100'})


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _, m in sess.get('_flashes', [])]


def _count(number):
    from contaautonomo.app import Invoice
    return Invoice.query.filter_by(invoice_number=number).count()


@pytest.mark.parametrize('again', ['UNQ-1', ' UNQ-1 '])
def test_create_with_a_used_number_is_refused_with_a_message(owner, again):
    from contaautonomo.app import Customer
    _create(owner, 'UNQ-1')
    _flashes(owner)                                    # clear
    customers = Customer.query.count()
    resp = _create(owner, again, client_name='Brand New Customer SL')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/create')
    assert any('"UNQ-1" is already used by the invoice for Unique Number SL (19/11/2026)' in m
               for m in _flashes(owner))
    assert _count('UNQ-1') == 1 and Customer.query.count() == customers


def test_create_stores_the_number_without_surrounding_spaces(owner):
    _create(owner, '  UNQ-2  ')
    assert _count('UNQ-2') == 1


def test_edit_to_a_used_number_is_refused(owner):
    from contaautonomo.app import Invoice, db
    _create(owner, 'UNQ-3')
    _create(owner, 'UNQ-4')
    inv = Invoice.query.filter_by(invoice_number='UNQ-4').one()
    resp = owner.post(f'/edit/{inv.id}', data={
        'invoice_number': 'UNQ-3', 'client_name': 'Unique Number SL',
        'invoice_date': '2026-11-19', 'currency': 'EUR',
        'items[0][description]': 'Work', 'items[0][quantity]': '1',
        'items[0][unit_price]': '100'})
    assert resp.status_code == 302 and resp.headers['Location'].endswith(f'/edit/{inv.id}')
    db.session.refresh(inv)
    assert inv.invoice_number == 'UNQ-4'
    assert any('"UNQ-3" is already used' in m for m in _flashes(owner))


def test_check_number_says_taken_except_for_the_invoice_itself(owner):
    from contaautonomo.app import Invoice
    _create(owner, 'UNQ-5')
    inv = Invoice.query.filter_by(invoice_number='UNQ-5').one()
    taken = owner.post('/invoices/check-number', json={'invoice_number': ' UNQ-5 '}).get_json()
    assert taken['taken'] is True and 'Unique Number SL' in taken['message']
    assert owner.post('/invoices/check-number',
                      json={'invoice_number': 'UNQ-5', 'exclude_id': inv.id}).get_json() == {
        'taken': False, 'message': ''}
    assert owner.post('/invoices/check-number',
                      json={'invoice_number': 'UNQ-free'}).get_json()['taken'] is False


def test_check_number_needs_login(client):
    resp = client.post('/invoices/check-number', json={'invoice_number': 'X'})
    assert resp.status_code in (302, 401)


def test_forms_ask_before_sending(owner):
    from contaautonomo.app import Invoice
    assert 'invoice-number-taken' in owner.get('/create').get_data(as_text=True)
    _create(owner, 'UNQ-6')
    inv = Invoice.query.filter_by(invoice_number='UNQ-6').one()
    html = owner.get(f'/edit/{inv.id}').get_data(as_text=True)
    assert 'invoice-number-taken' in html and f'const excludeId = {inv.id};' in html
