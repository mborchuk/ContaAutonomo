"""G-8 — installable app: manifest, service worker, capture page, share target."""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'static'


def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_manifest_is_public_and_installable(loaded_modules, client):
    resp = client.get('/manifest.webmanifest')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/manifest+json'
    m = json.loads(resp.get_data(as_text=True))
    assert m['name'] and m['short_name'] and m['start_url'] == '/'
    assert m['display'] == 'standalone' and m['scope'] == '/'
    sizes = {i['sizes'] for i in m['icons'] if i['type'] == 'image/png'}
    assert {'192x192', '512x512'} <= sizes
    for icon in m['icons']:                          # every icon exists and is a PNG
        path = STATIC / icon['src'].split('/static/', 1)[1]
        assert path.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'


def test_manifest_registers_the_share_target(loaded_modules, client):
    m = json.loads(client.get('/manifest.webmanifest').get_data(as_text=True))
    target = m['share_target']
    assert target['action'] == '/expenses/capture/share'
    assert target['method'] == 'POST' and target['enctype'] == 'multipart/form-data'
    assert target['params']['files'][0]['name'] == 'receipt'
    assert any(s['url'] == '/expenses/capture' for s in m['shortcuts'])


def test_manifest_without_expenses_has_no_share_target(loaded_modules, client, monkeypatch):
    real = loaded_modules.provider_of
    monkeypatch.setattr(loaded_modules, 'provider_of',
                        lambda role: None if role == 'expenses' else real(role))
    m = json.loads(client.get('/manifest.webmanifest').get_data(as_text=True))
    assert 'share_target' not in m and 'shortcuts' not in m


def test_service_worker_is_served_from_the_root(loaded_modules, client):
    resp = client.get('/sw.js')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/javascript'
    assert resp.headers['Service-Worker-Allowed'] == '/'
    assert 'no-cache' in resp.headers['Cache-Control']


def test_service_worker_caches_nothing():
    source = (STATIC / 'js' / 'sw.js').read_text(encoding='utf-8')
    code = re.sub(r'/\*.*?\*/', '', source, flags=re.S)       # ignore comments
    code = re.sub(r'//[^\n]*', '', code)
    for forbidden in ('caches.', 'cache.put', 'cache.add', 'indexedDB', 'localStorage'):
        assert forbidden not in code, forbidden
    assert "'/expenses/capture/share'" in code


def test_scripts_are_external_files_for_the_csp(loaded_modules, client):
    _auth(client)
    html = client.get('/expenses/capture').get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'src="/static/js/pwa.js"' in html and 'src="/static/js/capture.js"' in html
    for name in ('sw.js', 'pwa.js', 'capture.js'):
        assert (STATIC / 'js' / name).exists()


def test_capture_page_requires_login(loaded_modules, client):
    resp = client.get('/expenses/capture')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_capture_page_shows_photo_inputs_and_the_form(loaded_modules, client):
    _auth(client)
    html = client.get('/expenses/capture').get_data(as_text=True)
    assert 'id="capture-camera"' in html and 'capture="environment"' in html
    assert 'id="capture-gallery"' in html
    assert 'id="expense-form"' in html and 'Capture a receipt' in html


def test_capture_form_saves_like_the_create_form(loaded_modules, app, client):
    from app import Expense
    _auth(client)
    resp = client.post('/expenses/capture', data={
        'amount': '12.10', 'currency': 'EUR', 'category': 'Meals',
        'description': 'Synthetic lunch', 'expense_date': '2032-05-05',
        'vat_rate': '10', 'deductible': 'on', 'deductible_pct': '100'})
    assert resp.status_code == 302
    assert Expense.query.filter_by(description='Synthetic lunch').count() == 1


def test_share_post_without_service_worker_changes_nothing(loaded_modules, app, client):
    from app import Expense
    app.config['WTF_CSRF_ENABLED'] = True     # a share from another app has no token
    try:
        _auth(client)
        before = Expense.query.count()
        resp = client.post('/expenses/capture/share', data={},
                           content_type='multipart/form-data')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/expenses/capture')
        assert Expense.query.count() == before
    finally:
        app.config['WTF_CSRF_ENABLED'] = False


def test_share_post_requires_login(loaded_modules, client):
    resp = client.post('/expenses/capture/share')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']
