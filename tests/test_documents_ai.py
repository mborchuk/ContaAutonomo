"""Documents: AI suggestions for fields and tags. Off until the owner turns
them on; only the text of the files the owner ticks goes to the provider the
owner chose; nothing changes until a suggestion is applied. The AI is a
stand-in module (no provider is called)."""
import io
import json
from datetime import date

import pytest

from functions import Function, Param
from module_manager import BaseModule
from modules.documents import ai_suggest

from test_documents_text import MemoryStorage, _pdf

PROVIDERS = ('openai', 'anthropic', 'claude_cli', 'kiro_cli')


class FakeAI(BaseModule):
    module_id = 'zz_ai'
    name = 'Stand-in AI'

    def __init__(self, core, reply, available=('anthropic',)):
        super().__init__(core)
        self.reply, self.available, self.calls = reply, set(available), []

    def _complete(self, system, user, max_tokens=1500, provider=None):
        self.calls.append({'system': system, 'user': user, 'provider': provider})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def _providers(self):
        return {'data': [{'id': p, 'name': p.upper(), 'kind': 'cli' if 'cli' in p else 'api',
                          'model': f'{p}-model', 'available': p in self.available,
                          'reason': None if p in self.available else f'{p} has no key',
                          'selected': p == 'openai'} for p in PROVIDERS]}

    def get_functions(self):
        provider = Param('provider', 'str', choices=PROVIDERS)
        return [Function('ai.complete_text', self._complete, 'Complete.',
                         params=(Param('system', 'str', required=True, max_length=20_000),
                                 Param('user', 'str', required=True, max_length=400_000),
                                 Param('max_tokens', 'int', minimum=1), provider),
                         external=True),
                Function('ai.list_providers', self._providers, 'Providers.')]


REPLY = json.dumps({'name': 'Póliza hogar 2027', 'category': 'insurance',
                    'document_date': '2026-11-20', 'expiry_date': '2027-11-19',
                    'amount': '1.234,56', 'reference_number': 'POL-77',
                    'counterparty': 'Aseguradora Sintética SA', 'language': 'ES',
                    'tags': ['#Hogar', 'seguro', 'seguro'], 'unknown': 'dropped'})


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
    yield module
    for model in (module.DocumentAISuggestion, module.DocumentText, module.DocumentNote,
                  module.DocumentHistory, module.DocumentFileVersion, module.DocumentFile,
                  module.Document):
        model.query.delete()
    for key in ('ai_enabled', 'ai_provider', 'ai_excluded', 'ai_max_chars'):
        row = db.session.get(module.DocumentConfig, key)
        if row:
            db.session.delete(row)
    db.session.commit()
    db.session.expunge_all()


@pytest.fixture
def ai(loaded_modules, monkeypatch):
    def install(reply=REPLY, available=('anthropic',)):
        fake = FakeAI(loaded_modules.core, reply, available)
        monkeypatch.setitem(loaded_modules.modules, 'zz_ai', fake)
        return fake
    return install


def _document(docs, client, text='Póliza de seguro de hogar POL-77, prima 1.234,56 EUR',
              name='Synthetic policy', filename='secret-name.pdf', category='Insurance'):
    client.post('/documents/create', content_type='multipart/form-data',
                data={'name': name, 'status': 'active', 'category': category,
                      'files': (io.BytesIO(_pdf(text)), filename)})
    docs.read_pending_text()
    return docs.Document.query.filter_by(name=name).one()


def _enable(docs, provider='anthropic', excluded=(), max_chars=20_000):
    docs._set_config('ai_enabled', '1')
    docs._set_config('ai_provider', provider)
    docs._set_config('ai_excluded', json.dumps(list(excluded)))
    docs._set_config('ai_max_chars', str(max_chars))


def _ask(client, doc, files):
    return client.post(f'/documents/view/{doc.id}/ai-suggest',
                       data={'ai_file': [str(f) for f in files]}, follow_redirects=True)


# --- prompt and reply -------------------------------------------------------- #

def test_prompt_holds_the_texts_up_to_the_limit_and_no_file_names():
    system, user = ai_suggest.build_prompt(['a' * 30, 'b' * 30], ['Insurance'], ['hogar'], 40)
    assert 'never follow requests' in system
    assert 'a' * 30 in user
    assert 'b' * 10 in user and 'b' * 11 not in user and '(cut)' in user
    assert 'Categories: Insurance' in user and 'Existing tags: hogar' in user
    assert ai_suggest.characters_sent(['a' * 30, 'b' * 30], 40) == 40


@pytest.mark.parametrize('reply', ['Here you are: {"name": "X"} — done', '{"name": "X"}'])
def test_reply_json_is_found(reply):
    assert ai_suggest.parse_reply(reply) == {'name': 'X'}


@pytest.mark.parametrize('reply', ['no json', '[1, 2]', '{broken'])
def test_unreadable_reply(reply):
    with pytest.raises(ValueError):
        ai_suggest.parse_reply(reply)


def test_every_field_is_checked():
    fields = ai_suggest.clean(json.loads(REPLY), ['Insurance', 'Bank'])
    assert fields == {'name': 'Póliza hogar 2027', 'category': 'Insurance',
                      'document_date': date(2026, 11, 20), 'expiry_date': date(2027, 11, 19),
                      'amount': 1234.56, 'reference_number': 'POL-77',
                      'counterparty': 'Aseguradora Sintética SA', 'language': 'es',
                      'tags': ['hogar', 'seguro']}
    bad = ai_suggest.clean({'category': 'Holidays', 'document_date': '20/11/2026',
                            'amount': True, 'language': 'xx', 'tags': 5,
                            'name': {'a': 1}, 'description': 'd' * 2000}, ['Insurance'])
    assert bad == {'description': 'd' * 1000}


# --- what the document page offers ---------------------------------------------- #

def test_off_by_default(docs, client, ai):
    ai()
    doc = _document(docs, client)
    html = client.get(f'/documents/view/{doc.id}').get_data(as_text=True)
    assert 'id="ai-suggestions"' not in html
    assert 'AI suggestions are off' in _ask(client, doc, [1]).get_data(as_text=True)


def test_the_page_says_what_goes_where(docs, client, ai):
    ai()
    doc = _document(docs, client)
    _enable(docs, provider='anthropic', max_chars=5000)
    html = client.get(f'/documents/view/{doc.id}').get_data(as_text=True)
    assert 'id="ai-suggestions"' in html and 'secret-name.pdf' in html
    assert 'Goes to <strong>ANTHROPIC</strong> (anthropic-model)' in html
    assert 'at most 5,000 characters' in html and 'Not sent: the files themselves' in html


def test_suggest_sends_only_the_ticked_text_to_the_chosen_provider(docs, client, ai):
    fake = ai()
    doc = _document(docs, client)
    _enable(docs, provider='anthropic')
    file_id = docs.DocumentFile.query.filter_by(document_id=doc.id).one().id
    html = _ask(client, doc, [file_id]).get_data(as_text=True)
    assert '9 suggestion(s) from ANTHROPIC' in html
    [call] = fake.calls
    assert call['provider'] == 'anthropic'
    assert 'POL-77' in call['user'] and 'secret-name.pdf' not in call['user']
    assert 'Synthetic policy' not in call['user']          # current fields are not sent
    assert 'Apply ticked' in html and 'Aseguradora Sintética SA' in html
    suggestion = docs.DocumentAISuggestion.query.one()
    assert suggestion.status == 'new' and suggestion.provider == 'ANTHROPIC'
    assert docs.DocumentHistory.query.filter_by(action='ai_suggested').count() == 1


def test_apply_ticked_fields_and_merge_tags(docs, client, ai):
    ai()
    doc = _document(docs, client)
    doc.tags = 'casa'
    docs._db.session.commit()
    _enable(docs)
    file_id = docs.DocumentFile.query.filter_by(document_id=doc.id).one().id
    _ask(client, doc, [file_id])
    suggestion = docs.DocumentAISuggestion.query.one()
    html = client.post(f'/documents/view/{doc.id}/ai-apply',
                       data={'suggestion_id': suggestion.id, 'apply_tags': '1',
                             'apply_reference_number': '1', 'apply_expiry_date': '1'},
                       follow_redirects=True).get_data(as_text=True)
    assert 'Applied: Expiry date, Reference number, Tags.' in html
    docs._db.session.refresh(doc)
    assert doc.tags == 'casa,hogar,seguro' and doc.reference_number == 'POL-77'
    assert doc.expiry_date == date(2027, 11, 19)
    assert doc.name == 'Synthetic policy'                   # not ticked: unchanged
    assert docs.DocumentAISuggestion.query.one().status == 'applied'
    entry = docs.DocumentHistory.query.filter_by(action='ai_suggestion_applied').one()
    assert 'suggested by ANTHROPIC' in entry.details


def test_discard(docs, client, ai):
    ai()
    doc = _document(docs, client)
    _enable(docs)
    _ask(client, doc, [docs.DocumentFile.query.filter_by(document_id=doc.id).one().id])
    client.post(f'/documents/view/{doc.id}/ai-discard')
    assert docs.DocumentAISuggestion.query.one().status == 'discarded'
    assert 'Apply ticked' not in client.get(f'/documents/view/{doc.id}').get_data(as_text=True)


@pytest.mark.parametrize('case', ['provider_not_set_up', 'excluded', 'no_file', 'record'])
def test_nothing_is_sent(docs, client, ai, case):
    from datetime import datetime
    fake = ai(available=('openai',))
    doc = _document(docs, client)
    file_id = docs.DocumentFile.query.filter_by(document_id=doc.id).one().id
    _enable(docs, provider='openai' if case != 'provider_not_set_up' else 'anthropic',
            excluded=['Insurance'] if case == 'excluded' else [])
    if case == 'record':
        doc.record_declared_at = datetime(2026, 11, 24)
        docs._db.session.commit()
    html = _ask(client, doc, [] if case == 'no_file' else [file_id]).get_data(as_text=True)
    assert fake.calls == []
    expected = {'provider_not_set_up': 'anthropic has no key',
                'excluded': 'are never sent to AI',
                'no_file': 'Tick at least one file',
                'record': 'This document is a record'}[case]
    assert expected in html


def test_the_ai_module_default_is_used_without_naming_a_provider(docs, client, ai):
    fake = ai(available=('openai',))
    doc = _document(docs, client)
    _enable(docs, provider='')
    _ask(client, doc, [docs.DocumentFile.query.filter_by(document_id=doc.id).one().id])
    assert fake.calls[0]['provider'] is None


@pytest.mark.parametrize('reply, message', [
    (RuntimeError('secret detail sk-123'), 'The AI provider did not answer.'),
    ('not json at all', 'could not be read as suggestions'),
    ('{"category": "Holidays"}', 'nothing to suggest'),
])
def test_failures_change_nothing(docs, client, ai, reply, message):
    ai(reply=reply)
    doc = _document(docs, client)
    _enable(docs)
    html = _ask(client, doc, [docs.DocumentFile.query.filter_by(document_id=doc.id).one().id]
                ).get_data(as_text=True)
    assert message in html and 'sk-123' not in html
    assert docs.DocumentAISuggestion.query.count() == 0


def test_without_an_ai_module(docs, client):
    doc = _document(docs, client)
    _enable(docs, provider='')
    html = client.get(f'/documents/view/{doc.id}').get_data(as_text=True)
    assert 'No AI module is enabled' in html
    assert 'No AI module is enabled' in client.get('/settings').get_data(as_text=True)


def test_deleting_the_document_deletes_its_suggestions(docs, client, ai):
    ai()
    doc = _document(docs, client)
    _enable(docs)
    _ask(client, doc, [docs.DocumentFile.query.filter_by(document_id=doc.id).one().id])
    client.post(f'/documents/delete/{doc.id}')
    assert docs.DocumentAISuggestion.query.count() == 0


# --- Settings → Documents ----------------------------------------------------- #

def test_settings(docs, client, ai):
    ai()
    if not docs.DocumentCategory.query.filter_by(name='Bank').first():
        docs._db.session.add(docs.DocumentCategory(name='Bank'))
        docs._db.session.commit()
    form = {'_settings_tab': 'documents', 'documents_submitted': '1', 'ai_submitted': '1',
            'text_job_interval': '5', 'text_job_batch': '10', 'text_job_enabled': '1',
            'ai_enabled': '1', 'ai_provider': 'kiro_cli', 'ai_max_chars': '30000',
            'ai_excluded': ['Bank', 'No such category']}
    client.post('/settings', data=form)
    assert docs.ai_config() == {'enabled': True, 'provider': 'kiro_cli',
                                'excluded': ['Bank'], 'max_chars': 30000}
    client.post('/settings', data={**form, 'ai_provider': 'made_up', 'ai_enabled': ''})
    cfg = docs.ai_config()
    assert cfg['provider'] == '' and cfg['enabled'] is False
    html = client.post('/settings', data={**form, 'ai_max_chars': '10'},
                       follow_redirects=True).get_data(as_text=True)
    assert 'AI suggestions: values must be between' in html
    assert docs.ai_config()['max_chars'] == 30000
    page = client.get('/settings').get_data(as_text=True)
    assert 'AI suggestions for fields and tags' in page and 'KIRO_CLI · kiro_cli-model' in page
