"""Login and setup are rate limited per client address (5/min and 3/min)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def auth_client(loaded_modules, app, client, tmp_path, monkeypatch):
    from auth import auth_manager

    def reset_counters():
        for limiter in app.extensions.get('limiter', ()):
            limiter.reset()

    monkeypatch.setattr(auth_manager, 'config_file', str(tmp_path / 'auth_config.json'))
    reset_counters()
    yield client
    reset_counters()


def test_login_post_limited_to_five_per_minute(auth_client):
    from auth import auth_manager

    auth_manager.setup_password('synthetic-login-pass')
    codes = [auth_client.post('/login', data={'password': 'wrong-synthetic'}).status_code
             for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert auth_client.get('/login').status_code == 200  # page itself not limited


def test_setup_post_limited_to_three_per_minute(auth_client):
    codes = [auth_client.post('/setup', data={'password': 'short'}).status_code
             for _ in range(4)]
    assert codes == [200] * 3 + [429]


def test_limits_are_per_client_address(auth_client):
    for _ in range(3):
        auth_client.post('/setup', data={'password': 'short'})
    assert auth_client.post('/setup', data={'password': 'short'}).status_code == 429
    other = auth_client.post('/setup', data={'password': 'short'},
                             environ_base={'REMOTE_ADDR': '198.51.100.20'})
    assert other.status_code == 200


@pytest.mark.parametrize('count,expected', [('0', 'False'), ('1', 'True')])
def test_trusted_proxy_count_enables_proxy_fix(count, expected):
    env = dict(os.environ, TRUSTED_PROXY_COUNT=count, FLASK_DEBUG='1',
               DATABASE_URL='sqlite:///:memory:')
    script = ('from werkzeug.middleware.proxy_fix import ProxyFix; import app; '
              'print(isinstance(app.app.wsgi_app, ProxyFix))')
    out = subprocess.run(
        [sys.executable, '-c', script],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == expected, out.stderr[-2000:]
