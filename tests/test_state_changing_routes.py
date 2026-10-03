"""Routes that change state accept POST only and are CSRF-protected."""
import pytest


def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


@pytest.fixture
def rows(app):
    from app import Bank, Customer, db

    customers = [Customer(name='ACME Synthetic SL'), Customer(name='Beta Synthetic SL')]
    banks = [Bank(name='Synthetic Bank A', iban='XX00SYNTHETIC0001'),
             Bank(name='Synthetic Bank B', iban='XX00SYNTHETIC0002')]
    db.session.add_all(customers + banks)
    db.session.commit()
    return {'customer': customers[0].id, 'bank': banks[0].id}


ROUTES = [
    ('/customers/{customer}/delete', 'customer'),
    ('/settings/customers/{customer}/set-default', 'customer'),
    ('/settings/banks/{bank}/delete', 'bank'),
    ('/settings/banks/{bank}/set-default', 'bank'),
]


def _state(kind, row_id):
    from app import Bank, Customer, db

    db.session.expire_all()
    obj = db.session.get(Customer if kind == 'customer' else Bank, row_id)
    return None if obj is None else bool(obj.is_default)


@pytest.mark.parametrize('path,kind', ROUTES)
def test_get_does_not_change_state(loaded_modules, client, rows, path, kind):
    _auth(client)
    before = _state(kind, rows[kind])
    resp = client.get(path.format(**rows))
    assert resp.status_code in (302, 405)  # 405 handled; never executed
    assert _state(kind, rows[kind]) == before


@pytest.mark.parametrize('path,kind', ROUTES)
def test_post_without_csrf_token_is_rejected(loaded_modules, app, client, rows, path, kind):
    app.config['WTF_CSRF_ENABLED'] = True
    _auth(client)
    before = _state(kind, rows[kind])
    client.post(path.format(**rows))
    assert _state(kind, rows[kind]) == before


@pytest.mark.parametrize('path,kind', ROUTES)
def test_post_changes_state(loaded_modules, client, rows, path, kind):
    _auth(client)
    before = _state(kind, rows[kind])
    resp = client.post(path.format(**rows))
    assert resp.status_code == 302
    assert _state(kind, rows[kind]) != before


def test_logout_requires_post(loaded_modules, client):
    _auth(client)
    client.get('/logout')
    with client.session_transaction() as sess:
        assert sess.get('authenticated') is True
    client.post('/logout')
    with client.session_transaction() as sess:
        assert not sess.get('authenticated')
