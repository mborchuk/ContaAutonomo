"""Documents: the text of stored PDFs is read in the background through
pdf.extract_text and found by the Documents search (FTS5), synthetic files."""
import io

import pytest
from sqlalchemy import text as sql

from functions import Function, Param
from module_manager import BaseModule
from modules.documents import text_index


def _pdf(*pages):
    """One page per argument: a string draws text, None only a rectangle."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for body in pages:
        if body is None:
            c.rect(50, 50, 200, 200, fill=1)
        else:
            c.drawString(50, 750, body)
        c.showPage()
    c.save()
    return buf.getvalue()


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


@pytest.fixture
def docs(app, loaded_modules, client, monkeypatch):
    from app import db
    module = loaded_modules.modules['documents']
    module._ensure_text_index()
    storage = MemoryStorage()
    for name in ('save', 'get', 'exists', 'delete'):
        monkeypatch.setattr(loaded_modules.core.storage, name, getattr(storage, name))
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    module.storage = storage
    yield module
    if text_index.available(db.session):
        db.session.execute(sql(f"INSERT INTO {text_index.FTS}({text_index.FTS}) "
                               "VALUES('delete-all')"))
    for model in (module.DocumentText, module.DocumentNote, module.DocumentHistory,
                  module.DocumentFileVersion, module.DocumentFile, module.Document):
        model.query.delete()
    db.session.commit()
    db.session.expunge_all()


def _create(client, data, filename='letter.pdf', name='Synthetic letter'):
    client.post('/documents/create', content_type='multipart/form-data',
                data={'name': name, 'status': 'active',
                      'files': (io.BytesIO(data), filename)})


def _search(client, q):
    return client.get('/documents/', query_string={'q': q}).get_data(as_text=True)


# --- the query ------------------------------------------------------------ #

@pytest.mark.parametrize('typed, query', [
    ('factura', '"factura"*'),
    ('  Factura   2026-0001 ', '"Factura"* "2026"* "0001"*'),
    ('póliza "seguro" OR NEAR(', '"póliza"* "seguro"* "OR"* "NEAR"*'),
    ('!!! -- ', None),
    ('', None),
])
def test_what_the_user_types_becomes_words_only(typed, query):
    assert text_index.match_query(typed) == query


def test_at_most_ten_words():
    assert text_index.match_query(' '.join(f'w{i}' for i in range(15))).count('*') == 10


# --- reading and searching -------------------------------------------------- #

def test_new_pdf_is_read_in_the_background_and_found(docs, client):
    _create(client, _pdf('Factura electrónica 2026-0042 del proveedor Acme'), 'factura.pdf',
            name='Proveedor March')
    assert 'Proveedor March' not in _search(client, 'electronica')     # not read yet
    key = docs.DocumentFile.query.one().file_path
    assert text_index.available(docs._db.session)                       # FTS5, not the fallback
    assert docs.read_pending_text() == {key: 'ok'}
    assert docs.read_pending_text() == {}                               # read once
    html = _search(client, 'electronica')                               # accent folded
    assert 'Proveedor March' in html and '<mark>electrónica</mark>' in html
    assert 'Proveedor March' in _search(client, 'fact acme')            # prefixes, all words
    assert 'Proveedor March' not in _search(client, 'acme invoice')
    row = docs.DocumentText.query.one()
    assert (row.status, row.page_count, row.method) == ('ok', 1, 'pypdf')
    view = client.get(f'/documents/view/{docs.Document.query.one().id}').get_data(as_text=True)
    assert 'Text: searchable, 1 page(s)' in view


def test_snippet_is_escaped(docs, client):
    _create(client, _pdf('Nota <script>alert(1)</script> factura'), name='Escape test')
    docs.read_pending_text()
    html = _search(client, 'factura')
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html


def test_a_scan_is_recorded_and_shown(docs, client):
    _create(client, _pdf(None), 'scan.pdf', name='Scanned receipt')
    assert list(docs.read_pending_text().values()) == ['no_text_layer']
    assert docs.read_pending_text() == {}                               # not retried
    view = client.get(f'/documents/view/{docs.Document.query.one().id}').get_data(as_text=True)
    assert 'No text layer (a scan: needs OCR)' in view


def test_only_pdfs_are_read(docs, client):
    _create(client, b'plain text', 'note.txt', name='Plain note')
    assert docs.read_pending_text() == {}
    assert docs.DocumentText.query.count() == 0


def test_an_unreachable_file_is_tried_three_times(docs, client):
    _create(client, _pdf('Recibo'), name='Lost file')
    key = docs.DocumentFile.query.one().file_path
    docs.storage.files.pop(key)
    for _ in range(3):
        assert docs.read_pending_text() == {key: 'error'}
    assert docs.read_pending_text() == {}
    assert docs.DocumentText.query.one().attempts == 3


def test_deleting_the_document_removes_its_text(docs, client):
    _create(client, _pdf('Contrato de alquiler'), name='Rental contract')
    docs.read_pending_text()
    doc = docs.Document.query.one()
    client.post(f'/documents/delete/{doc.id}')
    assert docs.DocumentText.query.count() == 0
    assert 'Rental contract' not in _search(client, 'alquiler')
    assert text_index.search(docs._db.session, 'alquiler') == {}


def test_a_module_that_replaces_the_reader_is_used(docs, client, loaded_modules, monkeypatch):
    class Ocr(BaseModule):
        module_id = 'zz_ocr'
        name = 'OCR'

        def get_functions(self):
            return [Function('pdf.extract_text',
                             lambda pdf_bytes, max_pages=None: {
                                 'status': 'ok', 'text': 'texto reconocido por OCR',
                                 'pages': ['texto reconocido por OCR'], 'page_count': 1,
                                 'empty_pages': [], 'method': 'ocr'},
                             'OCR.', params=(Param('pdf_bytes', 'bytes', required=True),
                                             Param('max_pages', 'int', minimum=1)))]

    monkeypatch.setitem(loaded_modules.modules, 'zz_ocr', Ocr(loaded_modules.core))
    _create(client, _pdf(None), 'scan.pdf', name='Scan read by OCR')
    docs.read_pending_text()
    assert docs.DocumentText.query.one().method == 'ocr'
    assert 'Scan read by OCR' in _search(client, 'reconocido')


def test_nothing_is_read_without_a_provider(docs, client, loaded_modules, monkeypatch):
    monkeypatch.setattr(loaded_modules.functions, 'available', lambda name: False)
    _create(client, _pdf('Recibo'), name='No reader')
    assert docs.read_pending_text() == {}
    assert docs.DocumentText.query.count() == 0


def test_substring_search_without_fts5(docs, client, monkeypatch):
    _create(client, _pdf('Seguro de responsabilidad civil'), name='Liability insurance')
    docs.read_pending_text()
    monkeypatch.setattr(text_index, 'available', lambda session: False)
    html = _search(client, 'responsabilidad')
    assert 'Liability insurance' in html and '<mark>responsabilidad</mark>' in html


def test_an_index_created_later_indexes_stored_texts(docs, client):
    _create(client, _pdf('Certificado de residencia fiscal'), name='Tax residence')
    docs.read_pending_text()
    with docs._db.engine.begin() as conn:
        text_index.drop(conn)
    assert not text_index.available(docs._db.session)
    docs._ensure_text_index()
    assert 'Tax residence' in _search(client, 'residencia')


def test_the_job_is_scheduled(docs, loaded_modules):
    job = loaded_modules.core.scheduler._jobs['documents.read_text']
    assert job['type'] == 'interval' and job['func'] == docs.read_pending_text


def test_backup_leaves_out_the_index_and_restore_rebuilds_it(docs, client, loaded_modules):
    import json
    _create(client, _pdf('Escritura de compraventa'), name='Deed')
    docs.read_pending_text()
    backup = loaded_modules.modules['backup']
    dump = json.loads(backup._dump_db_json())
    assert 'document_text' in dump['tables']
    assert not [t for t in dump['tables'] if t.startswith(text_index.FTS)]
    ok, _ = backup._restore_db_from_json(dump)
    assert ok and not text_index.available(docs._db.session)    # dropped by the restore
    docs._ensure_text_index()                                    # what the restart does
    assert 'Deed' in _search(client, 'compraventa')
