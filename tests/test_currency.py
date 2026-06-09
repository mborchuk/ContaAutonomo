"""Currency conversion + ECB feed cache tests."""
import currency_converter as cc

SAMPLE_XML = (
    b'<gesmes:Envelope '
    b'xmlns:gesmes="http://www.gesmes.org/xml/2002-09-01" '
    b'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
    b'<Cube><Cube time="2026-03-17">'
    b'<Cube currency="USD" rate="1.0850"/>'
    b'<Cube currency="GBP" rate="0.8500"/>'
    b'</Cube></Cube></gesmes:Envelope>'
)


class _FakeResp:
    content = SAMPLE_XML

    def raise_for_status(self):
        pass


def _reset_cache():
    cc._ecb_cache['content'] = None
    cc._ecb_cache['ts'] = 0.0


def test_ecb_feed_is_cached(monkeypatch):
    calls = {'n': 0}

    def fake_get(url, timeout=10):
        calls['n'] += 1
        return _FakeResp()

    monkeypatch.setattr(cc.requests, 'get', fake_get)
    _reset_cache()

    r1, d1 = cc.get_exchange_rate('2026-03-17')
    r2, d2 = cc.get_exchange_rate('2026-03-18')

    # USD->EUR = 1 / 1.0850
    assert round(r1, 4) == round(1 / 1.0850, 4)
    assert d1 == '2026-03-17'
    # Two lookups, but only one network fetch thanks to the cache.
    assert calls['n'] == 1


def test_multiple_rates_use_cache(monkeypatch):
    calls = {'n': 0}

    def fake_get(url, timeout=10):
        calls['n'] += 1
        return _FakeResp()

    monkeypatch.setattr(cc.requests, 'get', fake_get)
    _reset_cache()

    cc.get_exchange_rate('2026-03-17')
    rates = cc.get_multiple_exchange_rates('2026-03-17', ['USD', 'GBP'], base_currency='EUR')

    assert 'USD' in rates and 'GBP' in rates
    assert calls['n'] == 1  # shared cache across both code paths


def test_currency_symbol_fallback():
    assert cc.get_currency_symbol('EUR') == '€'
    assert cc.get_currency_symbol('XOF') == 'XOF'  # unknown -> code itself
