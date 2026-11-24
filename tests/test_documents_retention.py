"""Documents: records and retention (ISO 15489). A declared record is fixed
until its retention date; then it is reviewed (kept longer or disposed of),
and every deletion is logged after the document is gone (synthetic data)."""
import io
from datetime import date

import pytest

from modules.documents import integrity, retention


def test_retention_runs_to_the_end_of_the_year():
    assert retention.retain_until(6, date(2026, 3, 10)) == date(2032, 12, 31)
    assert retention.retain_until(4, None, declared_on=date(2027, 1, 2)) == date(2031, 12, 31)
    assert retention.retain_until(None, date(2026, 3, 10)) is None


@pytest.mark.parametrize('raw, years', [('6', 6), (' 4 ', 4), ('0', None), ('51', None),
                                        ('', None), ('six', None), (None, None)])
def test_years_are_whole_and_bounded(raw, years):
    assert retention.clean_years(raw) == years


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


@pytest.fixture
def docs(app, loaded_modules, client, monkeypatch):
    from contaautonomo.app import Message, db
    module = loaded_modules.modules['documents']
    storage = MemoryStorage()
    for name in ('save', 'get', 'exists', 'delete'):
        monkeypatch.setattr(loaded_modules.core.storage, name, getattr(storage, name))
    module.storage = storage
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    yield module
    for model in (module.DocumentNote, module.DocumentHistory, module.DocumentFileVersion,
                  module.DocumentFile, module.Document, module.DocumentDisposal):
        model.query.delete()
    module.DocumentCategory.query.filter(module.DocumentCategory.name.like('Synthetic%')).delete(
        synchronize_session=False)
    Message.query.filter_by(source='documents').delete()
    db.session.commit()
    db.session.expunge_all()


def _create(client, docs, category='Synthetic Tax', document_date='2026-03-10'):
    client.post('/documents/create', content_type='multipart/form-data', data={
        'name': 'Synthetic return', 'status': 'active', 'category': category,
        'document_date': document_date,
        'files': (io.BytesIO(b'%PDF-1.4 synthetic return'), 'return.pdf')})
    return docs.Document.query.order_by(docs.Document.id.desc()).first()


def _set_period(client, docs, name, years):
    cat = docs.DocumentCategory.query.filter_by(name=name).one()
    return client.post(f'/documents/categories/{cat.id}/retention',
                       data={'retention_years': years}, follow_redirects=True)


def test_category_period_is_set_cleared_and_checked(docs, client):
    _create(client, docs)
    _set_period(client, docs, 'Synthetic Tax', '6')
    assert docs.DocumentCategory.query.filter_by(name='Synthetic Tax').one().retention_years == 6
    resp = _set_period(client, docs, 'Synthetic Tax', '99')
    assert 'whole years from 1 to 50' in resp.get_data(as_text=True)
    assert docs.DocumentCategory.query.filter_by(name='Synthetic Tax').one().retention_years == 6
    _set_period(client, docs, 'Synthetic Tax', '')
    assert docs.DocumentCategory.query.filter_by(name='Synthetic Tax').one().retention_years is None


def test_declaring_uses_the_period_and_fixes_the_record(docs, client):
    doc = _create(client, docs)
    resp = client.post(f'/documents/view/{doc.id}/declare', follow_redirects=True)
    assert 'Set how many years' in resp.get_data(as_text=True)      # no period anywhere
    assert docs.Document.query.filter_by(id=doc.id).one().record_declared_at is None

    _set_period(client, docs, 'Synthetic Tax', '6')
    client.post(f'/documents/view/{doc.id}/declare')
    doc = docs.Document.query.filter_by(id=doc.id).one()
    assert doc.record_declared_at and doc.retain_until == date(2032, 12, 31)
    assert docs.DocumentHistory.query.filter_by(action='declared_record').count() == 1

    df = docs.DocumentFile.query.filter_by(document_id=doc.id).one()
    for resp in (client.get(f'/documents/edit/{doc.id}', follow_redirects=True),
                 client.post(f'/documents/edit/{doc.id}', data={'name': 'changed'},
                             follow_redirects=True),
                 client.post(f'/documents/delete/{doc.id}', follow_redirects=True),
                 client.post(f'/documents/file/{df.id}/delete', follow_redirects=True),
                 client.post(f'/documents/file/{df.id}/replace', follow_redirects=True,
                             content_type='multipart/form-data',
                             data={'file': (io.BytesIO(b'x'), 'x.pdf')})):
        assert 'This document is a record' in resp.get_data(as_text=True)
    doc = docs.Document.query.filter_by(id=doc.id).one()
    assert doc.name == 'Synthetic return' and docs.DocumentFile.query.count() == 1
    assert docs.DocumentFileVersion.query.count() == 0

    client.post(f'/documents/note/add/{doc.id}', data={'note_title': 'Filed', 'note_text': 'ok'})
    assert docs.DocumentNote.query.filter_by(document_id=doc.id).count() == 1


def test_bulk_actions_leave_records_alone(docs, client):
    record = _create(client, docs)
    other = _create(client, docs)
    client.post(f'/documents/view/{record.id}/declare', data={'retention_years': '4'})
    resp = client.post('/documents/bulk', data={'bulk_action': 'delete',
                                                'doc_ids': [record.id, other.id]},
                       follow_redirects=True)
    assert '1 record(s) left unchanged' in resp.get_data(as_text=True)
    assert [d.id for d in docs.Document.query.all()] == [record.id]
    logged = docs.DocumentDisposal.query.one()
    assert logged.action == 'deleted' and logged.identifier == other.identifier


def test_declaring_takes_missing_fingerprints(docs, client):
    from contaautonomo.app import db
    doc = _create(client, docs)
    df = docs.DocumentFile.query.filter_by(document_id=doc.id).one()
    df.sha256 = None
    db.session.commit()
    client.post(f'/documents/view/{doc.id}/declare', data={'retention_years': '4'})
    assert docs.DocumentFile.query.filter_by(id=df.id).one().sha256 == integrity.sha256(
        b'%PDF-1.4 synthetic return')


def test_record_status_is_removed_only_with_a_reason(docs, client):
    doc = _create(client, docs)
    client.post(f'/documents/view/{doc.id}/declare', data={'retention_years': '4'})
    client.post(f'/documents/view/{doc.id}/undeclare', data={'reason': ''})
    assert docs.Document.query.filter_by(id=doc.id).one().record_declared_at is not None
    client.post(f'/documents/view/{doc.id}/undeclare', data={'reason': 'Declared by mistake'})
    doc = docs.Document.query.filter_by(id=doc.id).one()
    assert doc.record_declared_at is None and doc.retain_until is None
    released = docs.DocumentHistory.query.filter_by(action='record_released').one()
    assert 'Declared by mistake' in released.details


def test_review_keeps_or_disposes_and_the_log_remains(docs, client):
    from contaautonomo.app import db
    due = _create(client, docs, document_date='2015-02-01')
    later = _create(client, docs, document_date='2026-02-01')
    for doc in (due, later):
        client.post(f'/documents/view/{doc.id}/declare', data={'retention_years': '4'})
    page = client.get('/documents/retention').get_data(as_text=True)
    assert due.identifier in page.split('Due in the next 12 months')[0]

    resp = client.post(f'/documents/retention/{later.id}/dispose', follow_redirects=True)
    assert 'Only a record past its retention date' in resp.get_data(as_text=True)

    client.post(f'/documents/retention/{due.id}/keep', data={'years': '2'})
    kept = docs.Document.query.filter_by(id=due.id).one()
    assert kept.retain_until == date(date.today().year + 2, 12, 31)
    kept.retain_until = date(2019, 12, 31)      # back in the past for the next step
    db.session.commit()

    identifier = due.identifier
    client.post(f'/documents/retention/{due.id}/dispose', data={'reason': 'Period ended'})
    assert docs.Document.query.filter_by(id=due.id).first() is None
    assert docs.DocumentFile.query.filter_by(document_id=due.id).count() == 0
    entry = docs.DocumentDisposal.query.one()
    assert (entry.action, entry.identifier, entry.was_record, entry.reason) == (
        'disposed', identifier, True, 'Period ended')
    assert integrity.sha256(b'%PDF-1.4 synthetic return') in entry.files
    assert identifier in client.get('/documents/retention').get_data(as_text=True)


def test_records_past_their_date_get_one_message(docs, client):
    from contaautonomo.app import Message
    doc = _create(client, docs, document_date='2015-02-01')
    client.post(f'/documents/view/{doc.id}/declare', data={'retention_years': '4'})
    assert docs.post_retention_messages() == 1
    assert docs.post_retention_messages() == 0
    message = Message.query.filter_by(source='documents').one()
    assert '/documents/retention' in message.text and '31/12/2019' in message.text
