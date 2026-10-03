"""API `_handle`: handler results must never be served as HTML."""
import io

import pytest
from flask import send_file


@pytest.fixture
def api(loaded_modules):
    # Instantiate without registering routes: _handle only needs core.
    return loaded_modules.discovered['api'](loaded_modules.core)


def test_tuple_result_is_json(api, app):
    with app.test_request_context('/api/v1/x'):
        resp, status = api._handle(lambda: ({'ok': True}, 201))
    assert status == 201
    assert resp.mimetype == 'application/json'


def test_bare_string_is_json_encoded_not_html(api, app):
    """A module handler returning a plain string built from request data must
    not be reflected as text/html (reflected XSS)."""
    payload = '<script>alert(1)</script>'
    with app.test_request_context('/api/v1/x'):
        resp, status = api._handle(lambda: payload)
    assert status == 200
    assert resp.mimetype == 'application/json'
    assert resp.get_json() == payload


def test_real_response_passes_through(api, app):
    with app.test_request_context('/api/v1/x'):
        pdf = send_file(io.BytesIO(b'%PDF-1.4'), mimetype='application/pdf')
        resp = api._handle(lambda: pdf)
    assert resp is pdf
    assert resp.mimetype == 'application/pdf'
