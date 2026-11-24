"""Documents carry Dublin Core metadata (ISO 15836): a unique identifier,
creator, language and rights, exported as oai_dc XML and through the API
(synthetic data)."""
import io
import xml.etree.ElementTree as ET
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from modules.documents import dublin_core as dc

DC = '{http://purl.org/dc/elements/1.1/}'


def _doc(**kw):
    base = dict(name='Synthetic insurance policy', creator='Synthetic Insurer SA',
                category='Insurance', tags='car,#2026', description='Car <policy> & cover',
                counterparty='Synthetic Broker SL', document_date=date(2026, 1, 15),
                expiry_date=date(2027, 1, 14), identifier='DOC-2026-0007',
                reference_number='POL-123', language='es', rights='Confidential')
    base.update(kw)
    return SimpleNamespace(**base)


def test_record_maps_every_field_to_its_element():
    pairs = dc.record(_doc(), ['pdf', 'jpg', 'pdf'])
    assert dc.as_dict(pairs) == {
        'title': ['Synthetic insurance policy'], 'creator': ['Synthetic Insurer SA'],
        'subject': ['Insurance', 'car', '2026'], 'description': ['Car <policy> & cover'],
        'contributor': ['Synthetic Broker SL'], 'date': ['2026-01-15'],
        'type': ['Text', 'StillImage'], 'format': ['application/pdf', 'image/jpeg'],
        'identifier': ['DOC-2026-0007', 'POL-123'], 'language': ['es'],
        'coverage': ['2026-01-15/2027-01-14'], 'rights': ['Confidential']}


def test_empty_values_are_left_out():
    pairs = dc.record(_doc(creator=None, tags=None, description='', counterparty=None,
                           expiry_date=None, reference_number=None, language=None,
                           rights=None), [])
    assert [e for e, _ in pairs] == ['title', 'subject', 'date', 'identifier']


def test_xml_is_oai_dc_and_escaped():
    root = ET.fromstring(dc.to_xml(dc.record(_doc(), ['xlsx'])))
    assert root.tag == '{http://www.openarchives.org/OAI/2.0/oai_dc/}dc'
    assert root.find(f'{DC}description').text == 'Car <policy> & cover'
    assert root.find(f'{DC}type').text == 'Dataset'
    assert [e.text for e in root.findall(f'{DC}identifier')] == ['DOC-2026-0007', 'POL-123']


@pytest.mark.parametrize('raw, clean', [('es', 'es'), ('EN', 'en'), ('en-gb', 'en-GB'),
                                        ('', None), ('spanish', None), ('e', None)])
def test_language_is_an_iso_639_1_code(raw, clean):
    assert dc.clean_language(raw) == clean


def test_next_identifier_counts_within_the_year():
    assert dc.next_identifier(2026, []) == 'DOC-2026-0001'
    assert dc.next_identifier(2026, ['DOC-2026-0009', 'DOC-2025-0042', None]) == 'DOC-2026-0010'


# --- In the app ----------------------------------------------------------- #

@pytest.fixture
def documents(app, loaded_modules, client, monkeypatch):
    from contaautonomo.app import db
    module = loaded_modules.modules['documents']
    saved = {}

    def save(file, key):
        saved[key] = file.read() if hasattr(file, 'read') else file
        return key
    monkeypatch.setattr(loaded_modules.core.storage, 'save', save)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    yield module
    for model in (module.DocumentNote, module.DocumentHistory, module.DocumentFile,
                  module.Document):
        model.query.delete()
    db.session.commit()
    db.session.expunge_all()


def _create(client, **fields):
    data = {'name': 'Synthetic contract', 'status': 'active', 'language': 'es',
            'files': (io.BytesIO(b'%PDF-1.4 synthetic'), 'contract.pdf'), **fields}
    return client.post('/documents/create', data=data, content_type='multipart/form-data')


def test_a_new_document_gets_the_next_identifier_and_its_metadata(documents, client):
    year = datetime.now().year
    _create(client, creator='Synthetic Landlord SL', rights='Confidential')
    _create(client, name='Second', language='EN-gb')
    first, second = documents.Document.query.order_by(documents.Document.id).all()
    assert first.identifier == f'DOC-{year}-0001' and second.identifier == f'DOC-{year}-0002'
    assert (first.creator, first.language, first.rights) == (
        'Synthetic Landlord SL', 'es', 'Confidential')
    assert second.language == 'en-GB'
    history = documents.DocumentHistory.query.filter_by(document_id=first.id).first()
    assert f'DOC-{year}-0001' in history.details


def test_edit_and_duplicate_keep_identifiers_unique(documents, client):
    _create(client)
    doc = documents.Document.query.one()
    client.post(f'/documents/edit/{doc.id}', data={'name': doc.name, 'status': 'active',
                                                    'creator': 'Synthetic Author',
                                                    'language': 'xx-invalid'})
    doc = documents.Document.query.one()
    assert doc.creator == 'Synthetic Author' and doc.language is None
    changes = [h.details for h in documents.DocumentHistory.query.filter_by(
        document_id=doc.id, action='updated')]
    assert any('Creator' in c for c in changes)
    client.post(f'/documents/duplicate/{doc.id}')
    identifiers = {d.identifier for d in documents.Document.query.all()}
    assert len(identifiers) == 2 and doc.identifier in identifiers


def test_existing_documents_get_identifiers_once(documents):
    from contaautonomo.app import db
    for year, name in ((2024, 'old'), (2025, 'older-added-later'), (2024, 'old-2')):
        db.session.add(documents.Document(name=name, file_path='_multi_',
                                          created_at=datetime(year, 3, 1)))
    db.session.commit()
    documents._backfill_identifiers()
    got = {d.name: d.identifier for d in documents.Document.query.all()}
    assert got == {'old': 'DOC-2024-0001', 'old-2': 'DOC-2024-0002',
                   'older-added-later': 'DOC-2025-0001'}
    documents._backfill_identifiers()           # nothing changes the second time
    assert {d.name: d.identifier for d in documents.Document.query.all()} == got


def test_dublin_core_xml_and_api(documents, client, loaded_modules):
    _create(client, creator='Synthetic Issuer', rights='Internal')
    doc = documents.Document.query.one()
    resp = client.get(f'/documents/view/{doc.id}/dublin-core.xml')
    assert resp.status_code == 200 and resp.mimetype == 'application/xml'
    assert f'{doc.identifier}-dc.xml' in resp.headers['Content-Disposition']
    root = ET.fromstring(resp.data)
    assert root.find(f'{DC}creator').text == 'Synthetic Issuer'
    assert root.find(f'{DC}format').text == 'application/pdf'

    body, status = documents._api_dublin_core(None, doc_id=doc.id)
    assert status == 200 and body['identifier'] == [doc.identifier]
    serialized = documents._api_serialize_document(doc)
    assert serialized['identifier'] == doc.identifier and serialized['rights'] == 'Internal'


def test_search_finds_the_identifier(documents, client):
    _create(client)
    doc = documents.Document.query.one()
    html = client.get(f'/documents/?q={doc.identifier}').get_data(as_text=True)
    assert f'/documents/view/{doc.id}' in html
    page = client.get(f'/documents/view/{doc.id}').get_data(as_text=True)
    assert doc.identifier in page and 'Dublin Core' in page
