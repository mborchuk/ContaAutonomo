"""Documents: each file carries its SHA-256 fingerprint, Verify integrity
compares, replacing or signing a file keeps the earlier version, and a PDF's
declared PDF/A level is shown (synthetic files)."""
import hashlib
import io

import pytest

from modules.documents import integrity

PDFA = (b'%PDF-1.7\n<x:xmpmeta><rdf:Description pdfaid:part="2" '
        b'pdfaid:conformance="B"/></x:xmpmeta>\n%%EOF')


@pytest.mark.parametrize('data, claim', [
    (PDFA, 'PDF/A-2B'),
    (b'%PDF-1.4 <pdfaid:part>1</pdfaid:part><pdfaid:conformance>a</pdfaid:conformance>', 'PDF/A-1A'),
    (b'%PDF-2.0 pdfaid:part="4"', 'PDF/A-4'),
    (b'%PDF-1.4 plain', None),
    (b'GIF89a pdfaid:part="2"', None),
    (b'', None)])
def test_pdfa_claim_from_the_xmp_metadata(data, claim):
    assert integrity.pdfa_claim(data) == claim


def test_sha256_and_short_form():
    assert integrity.sha256(b'abc') == hashlib.sha256(b'abc').hexdigest()
    assert integrity.short(integrity.sha256(b'abc')) == 'ba7816bf8f01'


# --- In the app ----------------------------------------------------------- #

class MemoryStorage:
    def __init__(self):
        self.files = {}

    def save(self, data, key):
        self.files[key] = data if isinstance(data, bytes) else data.read()
        return key

    def get(self, key):
        return (self.files[key], key) if key in self.files else None

    def exists(self, key):
        return key in self.files

    def delete(self, key):
        self.files.pop(key, None)

    def send(self, key, download_name=None):
        from flask import Response
        return Response(self.files[key], headers={
            'Content-Disposition': f'attachment; filename="{download_name}"'})


@pytest.fixture
def docs(app, loaded_modules, client, monkeypatch):
    from app import db
    module = loaded_modules.modules['documents']
    storage = MemoryStorage()
    for name in ('save', 'get', 'exists', 'delete', 'send'):
        monkeypatch.setattr(loaded_modules.core.storage, name, getattr(storage, name))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    module.storage = storage
    yield module
    for model in (module.DocumentNote, module.DocumentHistory, module.DocumentFileVersion,
                  module.DocumentFile, module.Document):
        model.query.delete()
    db.session.commit()
    db.session.expunge_all()


def _create(client, data=PDFA, filename='policy.pdf'):
    client.post('/documents/create', content_type='multipart/form-data',
                data={'name': 'Synthetic policy', 'status': 'active',
                      'files': (io.BytesIO(data), filename)})


def _flash(response):
    return response.get_data(as_text=True)


def test_upload_records_fingerprint_and_pdfa(docs, client):
    _create(client)
    df = docs.DocumentFile.query.one()
    assert df.sha256 == integrity.sha256(PDFA) and df.version == 1 and df.pdfa == 'PDF/A-2B'
    assert docs.storage.files[df.file_path] == PDFA
    page = client.get(f'/documents/view/{df.document_id}').get_data(as_text=True)
    assert 'PDF/A-2B' in page and integrity.short(df.sha256) in page


def test_replace_keeps_the_earlier_version(docs, client):
    _create(client)
    df = docs.DocumentFile.query.one()
    first_key = df.file_path
    client.post(f'/documents/file/{df.id}/replace', content_type='multipart/form-data',
                data={'file': (io.BytesIO(b'%PDF-1.4 new'), 'policy-2.pdf')})
    df = docs.DocumentFile.query.one()
    assert (df.version, df.original_filename, df.pdfa) == (2, 'policy-2.pdf', None)
    assert df.file_path != first_key and docs.storage.files[df.file_path] == b'%PDF-1.4 new'
    old = docs.DocumentFileVersion.query.one()
    assert (old.version, old.file_path, old.sha256, old.reason) == (
        1, first_key, integrity.sha256(PDFA), 'replaced')
    resp = client.get(f'/documents/file-version/{old.id}/download')
    assert resp.data == PDFA and 'policy_v1.pdf' in resp.headers['Content-Disposition']
    assert docs.DocumentHistory.query.filter_by(action='file_replaced').count() == 1


def test_signing_keeps_the_unsigned_file(docs, client, loaded_modules, monkeypatch):
    _create(client, data=b'%PDF-1.4 unsigned')
    df = docs.DocumentFile.query.one()
    signer = {'type': 'pdf_sign', 'name': 'Synthetic signer',
              'action': lambda pdf: pdf + b'\n%signed'}
    monkeypatch.setattr(loaded_modules, 'find_capabilities',
                        lambda kind, **_: [signer] if kind == 'pdf_sign' else [])
    client.post(f'/documents/file/{df.id}/sign/0')
    df = docs.DocumentFile.query.one()
    assert docs.storage.files[df.file_path] == b'%PDF-1.4 unsigned\n%signed' and df.version == 2
    old = docs.DocumentFileVersion.query.one()
    assert docs.storage.files[old.file_path] == b'%PDF-1.4 unsigned' and old.reason == 'signed'


def test_verify_reports_unchanged_changed_missing_and_records_new(docs, client):
    from app import db
    _create(client)
    df = docs.DocumentFile.query.one()
    resp = client.post(f'/documents/view/{df.document_id}/verify', follow_redirects=True)
    assert '1 of 1 file(s) unchanged' in _flash(resp)

    docs.storage.files[df.file_path] = PDFA + b'tampered'
    resp = client.post(f'/documents/view/{df.document_id}/verify', follow_redirects=True)
    assert 'changed since stored: policy.pdf' in _flash(resp)

    del docs.storage.files[df.file_path]
    resp = client.post(f'/documents/view/{df.document_id}/verify', follow_redirects=True)
    assert 'missing: policy.pdf' in _flash(resp)

    docs.storage.files[df.file_path] = PDFA
    df.sha256 = None                       # stored before fingerprints existed
    db.session.commit()
    resp = client.post(f'/documents/view/{df.document_id}/verify', follow_redirects=True)
    assert 'fingerprint recorded now: policy.pdf' in _flash(resp)
    assert docs.DocumentFile.query.one().sha256 == integrity.sha256(PDFA)
    assert docs.DocumentHistory.query.filter_by(action='integrity_checked').count() == 4


def test_delete_removes_versions_but_not_a_file_a_duplicate_uses(docs, client):
    _create(client)
    df = docs.DocumentFile.query.one()
    shared_key = df.file_path
    client.post(f'/documents/duplicate/{df.document_id}')
    client.post(f'/documents/file/{df.id}/replace', content_type='multipart/form-data',
                data={'file': (io.BytesIO(b'%PDF-1.4 v2'), 'policy.pdf')})
    current_key = docs.DocumentFile.query.filter_by(id=df.id).one().file_path
    client.post(f'/documents/delete/{df.document_id}')
    assert docs.DocumentFileVersion.query.count() == 0
    assert current_key not in docs.storage.files        # only this document used it
    assert shared_key in docs.storage.files             # the duplicate still does
    copy = docs.DocumentFile.query.one()
    assert copy.file_path == shared_key and copy.sha256 == integrity.sha256(PDFA)
