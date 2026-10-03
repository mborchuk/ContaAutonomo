"""Build and CI configuration guards: locked dependencies, one Python version,
security gates that can fail, the health check on /health."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(name):
    return (ROOT / name).read_text()


def _requirements(name):
    """(name, specifier line) for each requirement in a pip-compile lock."""
    reqs = []
    for line in _read(name).splitlines():
        if line and not line.startswith((' ', '#', '-')):
            reqs.append(line.rstrip(' \\'))
    return reqs


def test_lock_files_pin_every_package_with_hashes():
    for name in ('requirements.txt', 'requirements-dev.txt'):
        reqs = _requirements(name)
        assert reqs, f'{name} is empty'
        unpinned = [r for r in reqs if '==' not in r]
        assert not unpinned, f'{name}: not pinned: {unpinned}'
        assert _read(name).count('--hash=sha256:') >= len(reqs)


def test_runtime_lock_covers_direct_dependencies():
    locked = {r.split('==')[0].lower() for r in _requirements('requirements.txt')}
    for line in _read('requirements.in').splitlines():
        line = line.split('#')[0].strip()
        if line:
            pkg = re.split(r'[\[<>=!~ ]', line, 1)[0].lower()
            assert pkg in locked, f'{pkg} missing from requirements.txt'
    assert 'sentry-sdk' in locked and 'gunicorn' in locked


def test_image_installs_the_lock_with_hashes_on_a_pinned_base():
    dockerfile = _read('Dockerfile')
    assert re.search(r'^FROM python:3\.14-slim@sha256:[0-9a-f]{64}$', dockerfile, re.M)
    assert '--require-hashes -r requirements.txt' in dockerfile


def test_one_python_version_everywhere():
    for wf in ('tests.yml', 'security.yml'):
        versions = set(re.findall(r'python-version: "([\d.]+)"',
                                  _read(f'.github/workflows/{wf}')))
        assert versions == {'3.14'}, (wf, versions)
    assert 'Python 3.14' in _read('README.md')


def test_security_gates_can_fail():
    workflow = _read('.github/workflows/security.yml')
    gates = [l for l in workflow.splitlines()
             if re.search(r'trivy (fs|image) .*--exit-code 1', l)]
    assert len(gates) == 2
    assert all('--severity CRITICAL,HIGH' in g and '--ignore-unfixed' in g for g in gates)
    assert 'bandit -q -r . ' in workflow and '-lll -ii' in workflow
    assert 'Fail on fixable vulnerabilities' in workflow
    assert 'safety' not in workflow
    assert 'sha256sum --check' in workflow


def test_compose_health_check_uses_health_endpoint():
    compose = _read('docker-compose.yml')
    assert "localhost:5000/health'" in compose
    assert '/auth/login' not in compose


def test_docker_context_excludes_local_and_runtime_data():
    ignored = set(_read('.dockerignore').split())
    for entry in ('.env', 'instance/', 'backups/', '.venv/', 'build/', 'dist/',
                  'pdf_signature_files/', 'invoice_logos/'):
        assert entry in ignored, entry
