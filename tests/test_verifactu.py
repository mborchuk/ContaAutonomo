"""F1 — Verifactu: chain math, record writing on lifecycle events, tampering."""
import json
from datetime import date

import pytest

from modules.verifactu.chain import (
    GENESIS_HASH,
    record_hash,
    sign_hash,
    verify_chain,
)


# --- pure chain math ---------------------------------------------------------

def test_record_hash_deterministic_and_order_insensitive_keys():
    payload = {'b': 2, 'a': 1}
    same = {'a': 1, 'b': 2}
    assert record_hash(GENESIS_HASH, payload) == record_hash(GENESIS_HASH, same)
    assert record_hash('x' * 64, payload) != record_hash(GENESIS_HASH, payload)


def test_verify_chain_accepts_valid_and_detects_tamper():
    key = 'test-secret'
    payload1 = {'invoice': 'A-1', 'total': 100}
    h1 = record_hash(GENESIS_HASH, payload1)
    payload2 = {'invoice': 'A-2', 'total': 200}
    h2 = record_hash(h1, payload2)
    records = [
        {'payload': json.dumps(payload1, sort_keys=True, separators=(',', ':')),
         'prev_hash': GENESIS_HASH, 'record_hash': h1,
         'signature': sign_hash(h1, key)},
        {'payload': json.dumps(payload2, sort_keys=True, separators=(',', ':')),
         'prev_hash': h1, 'record_hash': h2,
         'signature': sign_hash(h2, key)},
    ]
    ok, bad, reason = verify_chain(records, key)
    assert ok and bad is None

    # Tamper with the first payload -> chain breaks at index 0.
    records[0]['payload'] = json.dumps({'invoice': 'A-1', 'total': 999},
                                       sort_keys=True, separators=(',', ':'))
    ok, bad, reason = verify_chain(records, key)
    assert not ok and bad == 0 and 'mismatch' in reason


def test_verify_chain_detects_broken_linkage():
    payload = {'x': 1}
    h = record_hash(GENESIS_HASH, payload)
    records = [
        {'payload': json.dumps(payload, sort_keys=True, separators=(',', ':')),
         'prev_hash': 'f' * 64,     # wrong link
         'record_hash': h, 'signature': ''},
    ]
    ok, bad, reason = verify_chain(records)
    assert not ok and bad == 0 and 'link' in reason


def test_verify_chain_detects_bad_signature():
    payload = {'x': 1}
    h = record_hash(GENESIS_HASH, payload)
    records = [{'payload': json.dumps(payload, sort_keys=True, separators=(',', ':')),
                'prev_hash': GENESIS_HASH, 'record_hash': h,
                'signature': 'deadbeef'}]
    ok, bad, reason = verify_chain(records, 'key')
    assert not ok and 'signature' in reason


# --- lifecycle integration ----------------------------------------------------

@pytest.fixture
def verifactu(loaded_modules):
    return loaded_modules.modules['verifactu']


def _issue_one(db, number):
    from app import Customer, Invoice, _issue_invoice
    cust = Customer(name=f'{number} SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    inv = Invoice(invoice_number=number, client_name=cust.name, amount_usd=0.0,
                  amount_eur=100.0, exchange_rate=1.0,
                  invoice_date=date(2026, 3, 1), status='draft',
                  currency='EUR', customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()
    _issue_invoice(inv)
    return inv


def test_issue_writes_alta_record_and_chain_verifies(verifactu):
    from app import db

    inv = _issue_one(db, 'VF-A')
    recs = verifactu.VerifactuRecord.query.filter_by(invoice_id=inv.id).all()
    assert len(recs) == 1
    assert recs[0].record_type == 'alta'
    payload = json.loads(recs[0].payload)
    assert payload['invoice_number'] == inv.invoice_number
    assert payload['vat_amount'] == 21.0      # snapshot: 21% of 100

    _records, (ok, bad, reason) = verifactu._verify_all()
    assert ok, f'chain broken at {bad}: {reason}'


def test_annul_appends_anulacion_record(verifactu, client):
    from app import db

    inv = _issue_one(db, 'VF-B')
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.post(f'/annul/{inv.id}', follow_redirects=False)
    assert resp.status_code == 302
    types = [r.record_type for r in verifactu.VerifactuRecord.query.filter_by(
        invoice_id=inv.id).order_by(verifactu.VerifactuRecord.id)]
    assert types == ['alta', 'anulacion']


def test_qr_payload_contains_invoice_fields(verifactu):
    from app import db

    inv = _issue_one(db, 'VF-C')
    url = verifactu.qr_payload(inv)
    assert f'numserie={inv.invoice_number}' in url
    assert 'importe=100.00' in url


# --- C-4: dedicated signing key ------------------------------------------------

def test_verify_chain_accepts_per_record_key_resolver():
    payload = {'x': 1}
    h = record_hash(GENESIS_HASH, payload)
    rec = {'payload': json.dumps(payload, sort_keys=True, separators=(',', ':')),
           'prev_hash': GENESIS_HASH, 'record_hash': h,
           'signature': sign_hash(h, 'k1'), 'key_id': 'a'}
    assert verify_chain([rec], lambda r: {'a': 'k1'}.get(r['key_id']))[0]
    ok, bad, reason = verify_chain([rec], lambda r: None)
    assert not ok and bad == 0 and 'unknown key' in reason


def test_new_records_are_signed_with_verifactu_key_not_secret_key(verifactu):
    from app import db
    from modules.verifactu.chain import key_id

    inv = _issue_one(db, 'VF-K1')
    rec = verifactu.VerifactuRecord.query.filter_by(invoice_id=inv.id).one()
    assert rec.key_id == key_id('test-verifactu-key')
    assert rec.signature == sign_hash(rec.record_hash, 'test-verifactu-key')
    assert rec.signature != sign_hash(rec.record_hash, verifactu._legacy_secret())


def test_rotating_secret_key_keeps_chain_valid(verifactu, monkeypatch):
    from app import db

    _issue_one(db, 'VF-K2')
    monkeypatch.setitem(verifactu.core.app.config, 'SECRET_KEY', 'rotated-session-key')
    _records, (ok, bad, reason) = verifactu._verify_all()
    assert ok, f'chain broken at {bad}: {reason}'


def _append_legacy_record(verifactu, signature_key):
    """Append a record the way the module wrote them before VERIFACTU_KEY."""
    from app import db
    from modules.verifactu.chain import canonical_payload

    last = verifactu.VerifactuRecord.query.order_by(
        verifactu.VerifactuRecord.id.desc()).first()
    prev = last.record_hash if last else GENESIS_HASH
    payload = {'invoice_number': f'LEGACY-{prev[:6]}', 'total': 50}
    h = record_hash(prev, payload)
    rec = verifactu.VerifactuRecord(
        record_type='alta', invoice_id=0, invoice_number=payload['invoice_number'],
        payload=canonical_payload(payload), prev_hash=prev, record_hash=h,
        signature=sign_hash(h, signature_key), key_id=None)
    db.session.add(rec)
    db.session.commit()
    return rec


def test_legacy_records_verify_with_secret_key_then_migrate(verifactu):
    from modules.verifactu.chain import key_id

    rec = _append_legacy_record(verifactu, verifactu._legacy_secret())
    _records, (ok, _bad, _reason) = verifactu._verify_all()
    assert ok, 'legacy record must verify with SECRET_KEY before migration'

    verifactu._migrate_signing_key()
    assert rec.key_id == key_id('test-verifactu-key')
    assert rec.signature == sign_hash(rec.record_hash, 'test-verifactu-key')
    _records, (ok, bad, reason) = verifactu._verify_all()
    assert ok, f'chain broken at {bad}: {reason}'

    verifactu._migrate_signing_key()  # idempotent
    assert rec.key_id == key_id('test-verifactu-key')


def test_migration_leaves_records_with_bad_signature_alone(verifactu):
    from app import db

    rec = _append_legacy_record(verifactu, 'some-other-key')
    verifactu._migrate_signing_key()
    assert rec.key_id is None
    _records, (ok, _bad, reason) = verifactu._verify_all()
    assert not ok and 'signature' in reason
    db.session.delete(rec)
    db.session.commit()


def test_status_page_renders_with_dedicated_key(verifactu, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get('/verifactu/')
    assert resp.status_code == 200


def test_deployment_key_prefers_env_then_creates_private_file(loaded_modules,
                                                              tmp_path, monkeypatch):
    import os
    import stat

    core = loaded_modules.modules['verifactu'].core
    key_file = tmp_path / 'instance' / 'verifactu.key'
    monkeypatch.setitem(core.app.config, 'VERIFACTU_KEY_FILE', str(key_file))

    monkeypatch.setenv('VERIFACTU_KEY', 'from-env')
    assert core.deployment_key('VERIFACTU_KEY', 'VERIFACTU_KEY_FILE', 'test') == 'from-env'
    assert not key_file.exists()

    monkeypatch.delenv('VERIFACTU_KEY')
    first = core.deployment_key('VERIFACTU_KEY', 'VERIFACTU_KEY_FILE', 'test')
    assert len(first) >= 32
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert core.deployment_key('VERIFACTU_KEY', 'VERIFACTU_KEY_FILE', 'test') == first
