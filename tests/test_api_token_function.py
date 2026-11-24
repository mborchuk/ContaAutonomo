"""Reports' API route is token-protected through api.check_token."""
import pytest

from contaautonomo.core.functions import Function, InvalidArguments, Param
from contaautonomo.core.module_manager import BaseModule


@pytest.fixture
def token(loaded_modules):
    from contaautonomo.app import db, Settings
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()
    api = loaded_modules.modules['api']
    api.on_enable()          # idempotent: adds the token column, mints a token
    return api._get_token()


def test_check_token(loaded_modules, token):
    call = loaded_modules.functions.call
    assert call('api.check_token', token=token) is True
    assert call('api.check_token', token=token[:-1] + 'x') is False
    assert call('api.check_token', token='nón-ascii') is False
    with pytest.raises(InvalidArguments):
        call('api.check_token', token='')


@pytest.mark.parametrize('sent,status', [(None, 401), ('wrong', 401), ('x' * 600, 401),
                                         ('TOKEN', 400)])
def test_reports_route_checks_the_token(loaded_modules, client, token, sent, status):
    headers = {'X-API-Token': token if sent == 'TOKEN' else sent} if sent else {}
    resp = client.post('/api/v1/reports/generate', json={}, headers=headers)
    assert resp.status_code == status      # 400: authorised, but no sections


def test_reports_route_refuses_without_a_token_checker(loaded_modules, client, token,
                                                       monkeypatch):
    monkeypatch.delitem(loaded_modules.modules, 'api')
    resp = client.post('/api/v1/reports/generate', json={}, headers={'X-API-Token': token})
    assert resp.status_code == 401


def test_reports_route_uses_any_token_checker(loaded_modules, client, monkeypatch):
    class Checker(BaseModule):
        module_id = 'zz_checker'
        name = 'Checker'

        def get_functions(self):
            return [Function('api.check_token', lambda token: token == 'other-secret',
                             'Check.', params=(Param('token', 'str', required=True),))]

    monkeypatch.delitem(loaded_modules.modules, 'api')
    monkeypatch.setitem(loaded_modules.modules, 'zz_checker', Checker(loaded_modules.core))
    resp = client.post('/api/v1/reports/generate', json={},
                       headers={'X-API-Token': 'other-secret'})
    assert resp.status_code == 400
