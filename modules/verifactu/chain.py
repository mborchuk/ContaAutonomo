#!/usr/bin/env python3
"""
Verifactu record chain — pure functions, no Flask, no DB.

CAVEMAN NOTE: tamper-evidence come from two thing: every record carry
SHA-256 of (prev_hash + canonical payload), and an HMAC-SHA256 signature over
the record hash. Change any old record -> every later hash break -> chain
verify go red.

STANDING RULE (F1-D1, PENDING): the exact record field list, hash algorithm
input ordering, and signature requirements of RD 1007/2023 must be verified
against the official AEAT technical annexes before this module is used for
real compliance. The structures below are versioned (`CHAIN_SPEC_VERSION`) so
a later spec alignment is a data migration, not a redesign. NOT legal advice.
"""

import hashlib
import hmac
import json

CHAIN_SPEC_VERSION = "draft-1"

# First record in the chain links to this sentinel.
GENESIS_HASH = "0" * 64


def canonical_payload(payload):
    """Deterministic JSON for hashing: sorted keys, no whitespace drift."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)


def record_hash(prev_hash, payload):
    """SHA-256 over prev_hash + canonical payload."""
    data = (prev_hash or GENESIS_HASH) + canonical_payload(payload)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def sign_hash(rec_hash, secret_key):
    """HMAC-SHA256 signature of a record hash.

    Placeholder for the certificate-based signature the final AEAT spec may
    require (F1-D1) — key is derived from the app secret with a fixed context
    so it never doubles as a session key.
    """
    key = hashlib.sha256(f'verifactu:{secret_key}'.encode('utf-8')).digest()
    return hmac.new(key, rec_hash.encode('utf-8'), hashlib.sha256).hexdigest()


def verify_signature(rec_hash, signature, secret_key):
    return hmac.compare_digest(sign_hash(rec_hash, secret_key), signature or '')


def verify_chain(records, secret_key=None):
    """Walk the chain; return (ok, first_bad_index, reason).

    `records` is an ordered iterable of objects/dicts exposing payload (JSON
    str), prev_hash, record_hash, signature. Detects: broken linkage, payload
    tampering, signature mismatch (when secret_key given).
    """
    prev = GENESIS_HASH
    for idx, rec in enumerate(records):
        get = rec.get if isinstance(rec, dict) else lambda k, _r=rec: getattr(_r, k, None)
        payload_raw = get('payload')
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except (ValueError, TypeError):
            return False, idx, 'payload not valid JSON'
        if get('prev_hash') != prev:
            return False, idx, 'prev_hash does not link to previous record'
        expected = record_hash(prev, payload)
        if get('record_hash') != expected:
            return False, idx, 'record_hash mismatch (payload tampered?)'
        if secret_key is not None and not verify_signature(
                expected, get('signature'), secret_key):
            return False, idx, 'signature mismatch'
        prev = expected
    return True, None, None
