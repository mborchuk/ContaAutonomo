"""E-2 — AI Parser: response mapping per provider. External AI services are
never called: every provider SDK is replaced by a stub module."""
import io
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from modules.ai_parser import catalog
from modules.ai_parser.catalog import DEFAULT_MODELS
from modules.ai_parser.index import (
    AnthropicProvider,
    GoogleDocAIProvider,
    OpenAIProvider,
    PROVIDERS,
    describe_error,
)

INVOICE = {'invoice_number': 'A-1', 'invoice_date': '2026-03-02', 'client_name': 'ACME SL',
           'currency': 'EUR', 'items': [{'description': 'Work', 'quantity': 2,
                                         'unit_price': 50}], 'total': 100}
EXPENSE = {'expense_date': '2026-03-03', 'contractor_name': 'Shop', 'amount': 121,
           'currency': 'EUR', 'net_amount': 100, 'vat_rate': 21, 'vat_amount': 21}


def _fake_openai(monkeypatch, reply, calls):
    mod = ModuleType('openai')

    class Client:
        def __init__(self, api_key):
            calls.append(('key', api_key))
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            calls.append(('request', kw))
            msg = SimpleNamespace(content=reply)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    mod.OpenAI = Client
    monkeypatch.setitem(sys.modules, 'openai', mod)


def _fake_anthropic(monkeypatch, reply, calls):
    mod = ModuleType('anthropic')

    class Client:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(text=reply)])
    mod.Anthropic = Client
    monkeypatch.setitem(sys.modules, 'anthropic', mod)


def test_registry_lists_three_providers():
    assert set(PROVIDERS) == {'openai', 'anthropic', 'google_docai'}


@pytest.mark.parametrize('doc_type,payload', [('invoice', INVOICE), ('expense', EXPENSE)])
def test_openai_maps_fenced_json_and_picks_prompt(monkeypatch, doc_type, payload):
    calls = []
    _fake_openai(monkeypatch, '```json\n' + json.dumps(payload) + '\n```', calls)
    provider = OpenAIProvider({'openai_api_key': 'k', 'openai_model': 'm'})
    assert provider.parse_document(b'%PDF-1.4', 'doc.pdf', doc_type) == payload
    request = next(c[1] for c in calls if c[0] == 'request')
    content = request['messages'][0]['content']
    prompt = content[0]['text']
    assert ('client_name' in prompt) == (doc_type == 'invoice')
    assert content[1]['image_url']['url'].startswith('data:application/pdf;base64,')
    assert request['model'] == 'm'
    assert 'max_tokens' not in request and 'temperature' not in request
    assert request['max_completion_tokens'] > 0


def test_anthropic_maps_json_and_mime(monkeypatch):
    calls = []
    _fake_anthropic(monkeypatch, json.dumps(EXPENSE), calls)
    provider = AnthropicProvider({'anthropic_api_key': 'k'})
    assert provider.parse_document(b'\x89PNG', 'r.png', 'expense') == EXPENSE
    assert calls[0]['messages'][0]['content'][0]['source']['media_type'] == 'image/png'
    assert calls[0]['model'] == DEFAULT_MODELS['anthropic']


def test_unconfigured_providers():
    assert not OpenAIProvider({}).is_configured()
    assert not AnthropicProvider({'anthropic_api_key': ''}).is_configured()
    assert not GoogleDocAIProvider({'google_project_id': 'p'}).is_configured()


def _fake_docai(monkeypatch, entities):
    mod = ModuleType('google.cloud.documentai_v1')

    class Client:
        def processor_path(self, *parts):
            return '/'.join(parts)

        def process_document(self, request):
            return SimpleNamespace(document=SimpleNamespace(entities=entities))
    mod.DocumentProcessorServiceClient = Client
    mod.RawDocument = lambda **kw: kw
    mod.ProcessRequest = lambda **kw: kw
    monkeypatch.setitem(sys.modules, 'google.cloud.documentai_v1', mod)
    import google.cloud
    monkeypatch.setattr(google.cloud, 'documentai_v1', mod, raising=False)


def _ent(type_, text, props=()):
    return SimpleNamespace(type_=type_, mention_text=text, properties=list(props))


def test_google_docai_invoice_mapping(monkeypatch):
    line = _ent('line_item', '', [_ent('line_item/description', 'Consulting'),
                                  _ent('line_item/quantity', '2'),
                                  _ent('line_item/unit_price', '50,00 €')])
    _fake_docai(monkeypatch, [_ent('invoice_id', 'F-7'), _ent('invoice_date', '02/03/2026'),
                              _ent('receiver_name', 'ACME SL'), _ent('total_amount', '100,00'),
                              line])
    provider = GoogleDocAIProvider({'google_project_id': 'p', 'google_processor_id': 'x'})
    out = provider.parse_document(b'%PDF', 'f.pdf', 'invoice')
    assert out['invoice_number'] == 'F-7'
    assert out['invoice_date'] == '2026-03-02'
    assert out['client_name'] == 'ACME SL'
    assert out['total'] == 100.0
    assert out['items'] == [{'description': 'Consulting', 'quantity': 2.0, 'unit_price': 50.0}]


def test_google_docai_expense_vat_split(monkeypatch):
    _fake_docai(monkeypatch, [_ent('supplier_name', 'Shop'), _ent('invoice_date', '2026-03-03'),
                              _ent('total_amount', '121'), _ent('total_tax_amount', '21')])
    provider = GoogleDocAIProvider({'google_project_id': 'p', 'google_processor_id': 'x'})
    out = provider.parse_document(b'%PDF', 'f.pdf', 'expense')
    assert out['amount'] == 121.0 and out['vat_amount'] == 21.0 and out['net_amount'] == 100.0
    assert out['vat_rate'] is None and out['contractor_name'] == 'Shop'


def test_google_helpers():
    p = GoogleDocAIProvider({})
    assert p._parse_date('31.12.2026') == '2026-12-31'
    assert p._parse_date('not a date') == 'not a date'
    assert p._parse_amount('1.5 £') == 1.5 and p._parse_amount('x') == 0


# --- module API handler with a stub provider ----------------------------------

@pytest.fixture
def ai_module(standalone_module):
    return standalone_module('ai_parser')


def _request(filename, data, doc_type='invoice'):
    from werkzeug.datastructures import FileStorage
    return SimpleNamespace(files={'file': FileStorage(io.BytesIO(data), filename=filename)},
                           form={'doc_type': doc_type})


def test_api_parse_returns_provider_result(ai_module, monkeypatch, app):
    stub = SimpleNamespace(name='stub', display_name='Stub',
                           parse_document=lambda data, name, kind: {'kind': kind, 'size': len(data)})
    monkeypatch.setattr(ai_module, '_get_provider', lambda: stub)
    with app.test_request_context():
        body, status = ai_module._api_parse(_request('a.pdf', b'12345', 'expense'))
    assert status == 200 and body == {'data': {'kind': 'expense', 'size': 5}}


def test_api_parse_without_provider_is_400(ai_module, monkeypatch, app):
    from modules.api.index import ApiError
    monkeypatch.setattr(ai_module, '_get_provider', lambda: None)
    with app.test_request_context(), pytest.raises(ApiError) as err:
        ai_module._api_parse(_request('a.pdf', b'1'))
    assert err.value.status == 400


def test_api_parse_provider_failure_is_502(ai_module, monkeypatch, app):
    from modules.api.index import ApiError

    def boom(*_a):
        raise RuntimeError('provider down')
    monkeypatch.setattr(ai_module, '_get_provider',
                        lambda: SimpleNamespace(name='s', display_name='S', parse_document=boom))
    with app.test_request_context(), pytest.raises(ApiError) as err:
        ai_module._api_parse(_request('a.pdf', b'1'))
    assert err.value.status == 502


def test_get_provider_requires_configuration(ai_module):
    from app import db
    cfg = ai_module._get_config()
    cfg.provider = 'openai'
    cfg.openai_api_key = ''
    db.session.commit()
    assert ai_module._get_provider() is None
    cfg.openai_api_key = 'sk-test'
    db.session.commit()
    assert ai_module._get_provider().name == 'openai'
    cfg.openai_api_key = ''
    db.session.commit()


# --- model catalog: live listing, cache, retired models ----------------------

def _fake_model_lists(monkeypatch, anthropic_models=(), openai_models=(), error=None):
    seen = {}
    anth = ModuleType('anthropic')

    class AnthropicClient:
        def __init__(self, api_key):
            seen['anthropic_key'] = api_key
            self.models = SimpleNamespace(list=self._list)

        def _list(self, limit):
            if error:
                raise error
            return iter(SimpleNamespace(id=i, display_name=n) for i, n in anthropic_models)
    anth.Anthropic = AnthropicClient
    oai = ModuleType('openai')

    class OpenAIClient:
        def __init__(self, api_key):
            seen['openai_key'] = api_key
            self.models = SimpleNamespace(list=lambda: iter(
                SimpleNamespace(id=i, created=c) for i, c in openai_models))
    oai.OpenAI = OpenAIClient
    monkeypatch.setitem(sys.modules, 'anthropic', anth)
    monkeypatch.setitem(sys.modules, 'openai', oai)
    return seen


def test_list_models_anthropic(monkeypatch):
    _fake_model_lists(monkeypatch, anthropic_models=[('claude-opus-5-5', 'Claude Opus 5.5')])
    assert catalog.list_models('anthropic', 'k') == [
        {'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5'}]


def test_list_models_openai_keeps_chat_models_newest_first(monkeypatch):
    _fake_model_lists(monkeypatch, openai_models=[
        ('gpt-6-luna', 10), ('text-embedding-3-large', 30), ('gpt-6.1-terra', 20),
        ('gpt-6-realtime', 40), ('whisper-1', 50), ('o5-mini', 15)])
    ids = [m['id'] for m in catalog.list_models('openai', 'k')]
    assert ids == ['gpt-6.1-terra', 'o5-mini', 'gpt-6-luna']


def test_list_models_rejects_other_providers():
    with pytest.raises(ValueError):
        catalog.list_models('google_docai', 'k')


def test_choices_fallback_live_and_unlisted_current():
    fallback = catalog.choices('anthropic', '', DEFAULT_MODELS['anthropic'])
    assert not fallback['live'] and fallback['models'] == catalog.FALLBACK_MODELS['anthropic']
    custom = catalog.choices('openai', 'not json', 'gpt-custom')
    assert custom['models'][0] == {'id': 'gpt-custom', 'name': 'gpt-custom'}
    raw = catalog.store_cache('', 'anthropic', [{'id': 'claude-x', 'name': 'X'}])
    live = catalog.choices('anthropic', raw, 'claude-gone')
    assert live['live'] and live['fetched_at']
    assert live['models'] == [{'id': 'claude-gone', 'name': 'claude-gone (not in the list)'},
                              {'id': 'claude-x', 'name': 'X'}]


def test_retired_models_are_not_defaults_or_fallbacks():
    for provider, retired in catalog.RETIRED_MODELS.items():
        assert DEFAULT_MODELS[provider] not in retired
        assert not retired & {m['id'] for m in catalog.FALLBACK_MODELS[provider]}


@pytest.mark.parametrize('status,needle', [(404, 'model is not available'),
                                           (401, 'rejected the API key'),
                                           (429, 'rate limit'), (500, None)])
def test_describe_error(status, needle):
    message = describe_error(SimpleNamespace(status_code=status))
    assert (message is None) if needle is None else (needle in message)


def test_refresh_models_caches_the_list(ai_module, monkeypatch):
    from app import db
    seen = _fake_model_lists(monkeypatch, anthropic_models=[('claude-new', 'New')])
    cfg = ai_module._get_config()
    cfg.anthropic_api_key = 'stored-key'
    cfg.anthropic_model = 'claude-new'
    db.session.commit()
    payload, status = ai_module.refresh_models('anthropic')
    assert status == 200 and seen['anthropic_key'] == 'stored-key'
    assert payload['live'] and payload['models'] == [{'id': 'claude-new', 'name': 'New'}]
    assert catalog.load_cache(cfg.models_cache)['anthropic']['models'][0]['id'] == 'claude-new'

    ai_module.refresh_models('anthropic', api_key=' typed-key ')
    assert seen['anthropic_key'] == 'typed-key'
    cfg.anthropic_api_key = ''
    cfg.models_cache = ''
    db.session.commit()


def test_refresh_models_errors(ai_module, monkeypatch):
    from app import db
    cfg = ai_module._get_config()
    cfg.openai_api_key = ''
    db.session.commit()
    assert ai_module.refresh_models('google_docai')[1] == 404
    assert ai_module.refresh_models('openai')[1] == 400
    auth_error = type('AuthenticationError', (Exception,), {'status_code': 401})
    _fake_model_lists(monkeypatch, error=auth_error())
    payload, status = ai_module.refresh_models('anthropic', api_key='bad')
    assert status == 502 and 'rejected the API key' in payload['error']


def test_migrate_moves_retired_model_to_default(ai_module):
    from app import db
    cfg = ai_module._get_config()
    cfg.anthropic_model = 'claude-sonnet-4-20250514'
    cfg.openai_model = 'gpt-custom'
    db.session.commit()
    ai_module._migrate()
    db.session.expire_all()
    cfg = ai_module._get_config()
    assert cfg.anthropic_model == DEFAULT_MODELS['anthropic']
    assert cfg.openai_model == 'gpt-custom'
    ai_module._migrate()  # idempotent
    cfg.openai_model = DEFAULT_MODELS['openai']
    db.session.commit()


def test_settings_panel_lists_models(ai_module, app):
    with app.test_request_context('/settings'):
        html = ai_module.get_settings_html(None)
    assert 'Refresh list' in html
    for provider in catalog.LISTABLE:
        assert f'value="{DEFAULT_MODELS[provider]}"' in html
    assert 'claude-sonnet-4-20250514' not in html
