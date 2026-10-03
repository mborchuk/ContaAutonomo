"""The session cookie is signed, not encrypted: it must carry no key material."""
import base64
import json
import zlib


def _cookie_payload(client):
    value = client.get_cookie('session').value
    compressed = value.startswith('.')
    payload = value.lstrip('.').split('.')[0]
    data = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
    return json.loads(zlib.decompress(data) if compressed else data)


def test_login_stores_no_key_in_session_cookie(loaded_modules, client, tmp_path, monkeypatch):
    from auth import auth_manager

    monkeypatch.setattr(auth_manager, 'config_file', str(tmp_path / 'auth_config.json'))
    auth_manager.setup_password('synthetic-login-pass')

    resp = client.post('/login', data={'password': 'synthetic-login-pass'})
    assert resp.status_code == 302
    payload = _cookie_payload(client)
    assert payload.get('authenticated') is True
    assert '_enc_token' not in payload and '_password' not in payload
