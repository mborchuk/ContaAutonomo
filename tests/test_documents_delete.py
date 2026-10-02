"""Documents module: deleting a document with children must not hit the
FOREIGN KEY constraint (document_file / document_history / document_note all
reference document.id and SQLite runs with foreign_keys=ON)."""
from datetime import datetime

import pytest


@pytest.fixture
def documents(loaded_modules):
    return loaded_modules.modules['documents']


def _document_with_children(dm, db, name, files=3, history=2, notes=1):
    doc = dm.Document(name=name, file_path='_multi_')
    db.session.add(doc)
    db.session.flush()
    for i in range(files):
        db.session.add(dm.DocumentFile(
            document_id=doc.id, file_path=f'documents_files/{name}_{i}.pdf',
            original_filename=f'{name}_{i}.pdf'))
    for i in range(history):
        db.session.add(dm.DocumentHistory(
            document_id=doc.id, action='file_added', details=f'file {i}'))
    for i in range(notes):
        db.session.add(dm.DocumentNote(
            document_id=doc.id, title=f'note {i}', text='body'))
    db.session.commit()
    return doc


def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_delete_document_with_files_history_and_notes(documents, client):
    from app import db

    doc = _document_with_children(documents, db, 'DELME', files=5)
    doc_id = doc.id
    _auth(client)

    resp = client.post(f'/documents/delete/{doc_id}', follow_redirects=False)
    assert resp.status_code == 302

    assert db.session.get(documents.Document, doc_id) is None
    assert documents.DocumentFile.query.filter_by(document_id=doc_id).count() == 0
    assert documents.DocumentHistory.query.filter_by(document_id=doc_id).count() == 0
    assert documents.DocumentNote.query.filter_by(document_id=doc_id).count() == 0


def test_delete_survives_missing_storage_files(documents, client, monkeypatch):
    """A file already gone from the storage backend (e.g. deleted in Google
    Drive) must not block deleting the database record."""
    from app import db

    doc = _document_with_children(documents, db, 'DELME2', files=2)
    doc_id = doc.id

    def _raise(key):
        raise FileNotFoundError(f'could not resolve key: {key}')
    monkeypatch.setattr(documents.core.storage, 'delete', _raise)

    _auth(client)
    resp = client.post(f'/documents/delete/{doc_id}', follow_redirects=False)
    assert resp.status_code == 302
    assert db.session.get(documents.Document, doc_id) is None


def test_bulk_delete_with_children(documents, client):
    from app import db

    a = _document_with_children(documents, db, 'BULK-A')
    b = _document_with_children(documents, db, 'BULK-B')
    ids = [a.id, b.id]
    _auth(client)

    resp = client.post('/documents/bulk', data={
        'bulk_action': 'delete',
        'doc_ids': [str(i) for i in ids],
    }, follow_redirects=False)
    assert resp.status_code == 302
    for doc_id in ids:
        assert db.session.get(documents.Document, doc_id) is None
        assert documents.DocumentHistory.query.filter_by(
            document_id=doc_id).count() == 0
