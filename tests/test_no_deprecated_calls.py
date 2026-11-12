"""E-5 — no deprecated datetime.utcnow() or legacy Query.get() in app code."""
import re
import subprocess
from datetime import datetime, timezone

import pytest

from timeutil import utcnow

PATTERNS = {
    'datetime.utcnow': re.compile(r'\bdatetime\.utcnow\b'),
    'Query.get': re.compile(r'\.query\.get(_or_404)?\('),
}


def _app_sources():
    files = subprocess.run(['git', 'ls-files', '*.py'], capture_output=True,
                           text=True, check=True).stdout.split()
    return [f for f in files if not f.startswith('tests/')]


@pytest.mark.parametrize('name', sorted(PATTERNS))
def test_no_deprecated_pattern(name):
    offenders = []
    for path in _app_sources():
        with open(path, encoding='utf-8') as f:
            for no, line in enumerate(f, 1):
                if PATTERNS[name].search(line) and not line.lstrip().startswith(('#', '"', "'")):
                    if 'Replace' in line or 'legacy' in line:
                        continue  # documentation of the replacement
                    offenders.append(f'{path}:{no}')
    assert not offenders, f'{name} still used: {offenders}'


def test_utcnow_is_naive_utc():
    now = utcnow()
    assert now.tzinfo is None
    delta = abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds())
    assert delta < 5
