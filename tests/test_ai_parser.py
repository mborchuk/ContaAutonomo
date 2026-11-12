"""E-2 — AI Parser: response mapping per provider. External AI services are
never called: every provider SDK is replaced by a stub module."""
import io
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from modules.ai_parser.index import (
    AnthropicProvider,
    GoogleDocAIProvider,
    OpenAIProvider,
    PROVIDERS,
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


def test_anthropic_maps_json_and_mime(monkeypatch):
    calls = []
    _fake_anthropic(monkeypatch, json.dumps(EXPENSE), calls)
    provider = AnthropicProvider({'anthropic_api_key': 'k'})
    assert provider.parse_document(b'\x89PNG', 'r.png', 'expense') == EXPENSE
    assert calls[0]['messages'][0]['content'][0]['source']['media_type'] == 'image/png'


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
def ai_module(loaded_modules):
    from app import db
    inst = loaded_modules.discovered['ai_parser'](loaded_modules.core)
    inst.register_models(db)
    db.create_all()
    return inst


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
