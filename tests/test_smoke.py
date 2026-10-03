"""Smoke tests: the app boots and core endpoints respond."""


def test_app_boots(app):
    assert app is not None


def test_health_endpoint(client):
    resp = client.get('/health')
    assert resp.status_code in (200, 503)
    data = resp.get_json()
    assert 'db' in data and 'storage' in data


def test_login_page_reachable(client):
    # The auth blueprint has no URL prefix. First run redirects to /setup;
    # otherwise the login page renders.
    resp = client.get('/login')
    assert resp.status_code == 200 or resp.headers['Location'].endswith('/setup')


def test_dashboard_requires_auth(client):
    # Unauthenticated dashboard access redirects to login.
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code in (302, 308)


def test_max_content_length_configured(app):
    assert app.config['MAX_CONTENT_LENGTH'] == 50 * 1024 * 1024
