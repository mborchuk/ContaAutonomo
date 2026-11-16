"""G-7 — read-only ledger assistant: off by default, cannot write, sends only
the records needed, lists them, and explains a missing provider."""
import json
from datetime import date

import pytest

from modules.ledger_assistant import assistant as steps
from modules.ledger_assistant.llm import missing_reason

TODAY = date(2035, 11, 10)
SETTINGS = {'provider': 'openai', 'display_name': 'OpenAI (GPT-4o)',
            'api_key': 'sk-synthetic', 'model': 'gpt-test', 'configured': True}


@pytest.fixture
def la(loaded_modules):
    return loaded_modules.modules['ledger_assistant']


@pytest.fixture
def ledger(loaded_modules, app):
    from app import Customer, Expense, Invoice, db
    acme = Customer(name='ACME Synthetic SL', email='billing@example.invalid',
                    vat_number='B00000000', tax_type='standard')
    other = Customer(name='Other Synthetic SL', email='x@example.invalid',
                     tax_type='standard')
    db.session.add_all([acme, other])
    db.session.flush()
    rows = [('LA-1', acme, date(2035, 7, 3), 1000.0), ('LA-2', acme, date(2035, 9, 30), 500.0),
            ('LA-3', acme, date(2035, 10, 1), 900.0), ('LA-4', other, date(2035, 8, 8), 700.0)]
    for number, cust, d, amount in rows:
        db.session.add(Invoice(invoice_number=number, client_name=cust.name,
                               customer_id=cust.id, amount_usd=0.0, amount_eur=amount,
                               exchange_rate=1.0, invoice_date=d, status='issued',
                               currency='EUR', notes='internal note — never sent'))
    db.session.add(Expense(amount=50.0, currency='EUR', category='Software',
                           description='Synthetic tool\nsecond line', expense_date=date(2035, 8, 1),
                           notes='private'))
    db.session.commit()
    return acme


def _stub(monkeypatch, la, plan, answer, seen):
    def fake_complete(settings, system, user, max_tokens=1500):
        seen.append({'system': system, 'user': user})
        return json.dumps(plan) if len(seen) == 1 else json.dumps(answer)
    monkeypatch.setattr(la, 'provider_settings', lambda: SETTINGS)
    # The module imported `complete` by name; patch the name it calls.
    monkeypatch.setitem(type(la).ask.__globals__, 'complete', fake_complete)


def _snapshot(db):
    from sqlalchemy import text
    out = {}
    with db.engine.connect() as conn:
        names = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
        for name in sorted(names):
            out[name] = sorted(map(repr, conn.execute(text(f'SELECT * FROM "{name}"'))))
    return out


# --- opt-in and provider ---------------------------------------------------- #

def test_module_is_off_by_default(loaded_modules):
    cls = loaded_modules.discovered['ledger_assistant']
    assert getattr(cls, 'enabled_by_default', False) is False


@pytest.mark.parametrize('settings,fragment', [
    (None, 'AI Parser module is not enabled'),
    ({'provider': 'google_docai', 'display_name': 'Google Document AI', 'api_key': ''},
     'cannot answer questions'),
    ({'provider': 'anthropic', 'display_name': 'Anthropic (Claude)', 'api_key': ''},
     'No API key'),
])
def test_missing_provider_is_explained(settings, fragment):
    assert fragment in missing_reason(settings)


def test_ask_without_provider_says_what_is_missing(la, ledger, monkeypatch):
    monkeypatch.setattr(la, 'provider_settings', lambda: None)
    out = la.ask('How much did I invoice ACME in Q3?', TODAY)
    assert 'AI Parser module is not enabled' in out['missing']
    assert out['answer'] is None and out['calls'] == []


# --- read only -------------------------------------------------------------- #

def test_read_only_guard_refuses_writes(la, app):
    from app import Customer, db
    # The module loader runs index.py under its own name, so match by class name.
    with pytest.raises(PermissionError) as err:
        with la.read_only():
            db.session.add(Customer(name='Should Not Exist'))
            db.session.commit()
    assert type(err.value).__name__ == 'ReadOnlyViolation'
    assert Customer.query.filter_by(name='Should Not Exist').count() == 0


def test_only_get_list_handlers_are_reachable():
    from modules.ledger_assistant.index import API_HANDLERS
    assert set(API_HANDLERS.values()) == {'_list_invoices', '_list_expenses', '_list_customers'}
    assert all(':' not in spec['path'] for spec in steps.CATALOGUE.values())


def test_it_cannot_create_change_or_delete(la, ledger, monkeypatch):
    from app import db
    plan = {'calls': [
        {'call': 'invoices', 'params': {}},
        {'call': 'expenses', 'params': {}},
        {'call': 'customers'},
        {'call': 'delete_invoice', 'params': {'id': 1}},           # not in the catalogue
        {'call': 'invoices', 'params': {'id': 1, 'status': 'paid'}},  # parameter not allowed
    ]}
    answer = {'answer': 'Synthetic.', 'record_ids': {}}
    _stub(monkeypatch, la, plan, answer, [])
    # A handler with a side effect is stopped by the guard, too.
    api = la._provider('api')
    original = api._list_customers

    def writing_handler():
        from app import Customer
        db.session.add(Customer(name='Injected Synthetic'))
        db.session.commit()
        return original()
    monkeypatch.setattr(api, '_list_customers', writing_handler)

    before = _snapshot(db)
    out = la.ask('Show everything', TODAY)
    assert _snapshot(db) == before
    assert any('delete_invoice' in r for r in out['rejected'])
    assert any("'id' is not allowed" in r for r in out['rejected'])
    assert any(e.startswith('customers:') for e in out['errors'])


# --- data minimisation and the answer -------------------------------------- #

def test_sends_only_the_records_and_fields_needed(la, ledger, monkeypatch):
    plan = {'calls': [{'call': 'invoices', 'params': {
        'client': 'ACME', 'date_from': '2035-07-01', 'date_to': '2035-09-30'}}]}
    seen = []
    _stub(monkeypatch, la, plan, {'answer': 'You invoiced 1,500.00 EUR.',
                                  'record_ids': {'invoice': []}}, seen)
    out = la.ask('How much did I invoice ACME in Q3?', TODAY)

    assert seen[0]['user'] == 'How much did I invoice ACME in Q3?'   # plan: question only
    assert 'LA-' not in seen[0]['system'] + seen[0]['user']
    sent = json.loads(seen[1]['user'])['records']
    numbers = sorted(r['invoice_number'] for r in sent['invoice'])
    assert numbers == ['LA-1', 'LA-2']                                # Q3, ACME only
    for row in sent['invoice']:
        assert set(row) == set(steps.SENT_FIELDS['invoice'])
    payload = seen[1]['user']
    for secret in ('billing@example.invalid', 'B00000000', 'internal note'):
        assert secret not in payload
    assert out['totals']['invoices_eur'] == 1500.0


def test_answer_lists_the_records_it_used(la, ledger, monkeypatch):
    seen = []
    plan = {'calls': [{'call': 'invoices', 'params': {'client': 'ACME'}}]}
    _stub(monkeypatch, la, plan, {'answer': 'x', 'record_ids': {'invoice': []}}, seen)
    first = la.ask('ACME invoices', TODAY)
    target = next(r for r in first['used']['invoice'] if r['invoice_number'] == 'LA-3')

    seen.clear()
    _stub(monkeypatch, la, plan, {'answer': 'The October invoice is 900.00 EUR.',
                                  'record_ids': {'invoice': [target['id']]}}, seen)
    out = la.ask('Which ACME invoice is from October?', TODAY)
    assert [r['invoice_number'] for r in out['used']['invoice']] == ['LA-3']
    assert out['totals'] == {'invoices_eur': 900.0}


def test_expense_descriptions_are_cut_to_one_line(la, ledger, monkeypatch):
    seen = []
    _stub(monkeypatch, la, {'calls': [{'call': 'expenses', 'params': {}}]},
          {'answer': 'ok', 'record_ids': {}}, seen)
    la.ask('My expenses', TODAY)
    sent = json.loads(seen[1]['user'])['records']['expense']
    assert sent[0]['description'] == 'Synthetic tool'
    assert 'notes' not in sent[0]


def test_plan_validation_rejects_bad_parameters():
    cat = steps.catalogue_for({'tax_es_forms'})
    calls, rejected = steps.validate_plan({'calls': [
        {'call': 'modelo303', 'params': {'year': 2035, 'quarter': 3}},
        {'call': 'modelo303', 'params': {'year': 2035, 'quarter': 7}},
        {'call': 'invoices', 'params': {'date_from': 'yesterday'}},
        {'call': 'modelo130', 'params': {'year': 'x', 'quarter': 1}},
    ]}, cat)
    assert [c['path'] for c in calls] == ['/m/tax_es_forms/draft/303/2035/3']
    assert len(rejected) == 3
    assert 'modelo303' not in steps.catalogue_for(set())


# --- page ------------------------------------------------------------------- #

def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_page_requires_login(loaded_modules, client):
    resp = client.get('/assistant/')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_page_explains_a_missing_provider(la, client, monkeypatch):
    monkeypatch.setattr(la, 'provider_settings', lambda: None)
    _auth(client)
    html = client.get('/assistant/').get_data(as_text=True)
    assert 'AI Parser module is not enabled' in html and 'Not tax advice' in html


def test_page_shows_answer_records_and_disclaimer(la, ledger, client, monkeypatch):
    plan = {'calls': [{'call': 'invoices', 'params': {'client': 'ACME'}}]}
    _stub(monkeypatch, la, plan, {'answer': 'Three invoices.', 'record_ids': {}}, [])
    _auth(client)
    html = client.post('/assistant/', data={'question': 'ACME invoices?'}).get_data(as_text=True)
    assert 'Three invoices.' in html and 'Records used' in html and 'LA-3' in html
    assert steps.DISCLAIMER.split(' — ')[0] in html


def test_settings_panel_says_what_is_sent(la, app, monkeypatch):
    monkeypatch.setattr(la, 'provider_settings', lambda: SETTINGS)
    with app.test_request_context('/settings'):
        html = la.get_settings_html(None)
    assert 'your question only' in html and 'Never sent' in html
    assert 'OpenAI' in html


# --- API filters the assistant relies on ------------------------------------ #

def test_api_invoice_and_expense_filters(loaded_modules, ledger, app):
    api = loaded_modules.modules['api']
    with app.test_request_context('/api/v1/invoices?client=acme&date_from=2035-07-01'
                                  '&date_to=2035-09-30'):
        body, status = api._list_invoices()
    assert status == 200 and body['total'] == 2
    assert all('customer_id' in row for row in body['data'])
    with app.test_request_context('/api/v1/expenses?category=software&date_to=2035-08-01'):
        body, _ = api._list_expenses()
    assert body['total'] == 1
    with app.test_request_context('/api/v1/invoices?date_from=13/2035'):
        with pytest.raises(Exception) as err:   # ApiError, matched by name as _handle does
            api._list_invoices()
    assert type(err.value).__name__ == 'ApiError' and err.value.status == 400


# --- provider clients (SDKs stubbed; no network) ---------------------------- #

def test_openai_and_anthropic_text_calls(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from modules.ledger_assistant.llm import complete
    calls = []

    openai = ModuleType('openai')

    class OpenAIClient:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            calls.append(('openai', kw))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"calls": []}'))])
    openai.OpenAI = OpenAIClient
    anthropic = ModuleType('anthropic')

    class AnthropicClient:
        def __init__(self, api_key):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kw):
            calls.append(('anthropic', kw))
            return SimpleNamespace(content=[SimpleNamespace(text='hello')])
    anthropic.Anthropic = AnthropicClient
    monkeypatch.setitem(sys.modules, 'openai', openai)
    monkeypatch.setitem(sys.modules, 'anthropic', anthropic)

    assert complete(SETTINGS, 'sys', 'question') == '{"calls": []}'
    kw = calls[-1][1]
    assert kw['messages'][0] == {'role': 'system', 'content': 'sys'}
    assert 'temperature' not in kw and 'max_tokens' not in kw
    assert kw['max_completion_tokens'] == 6000
    assert complete({'provider': 'anthropic', 'api_key': 'k', 'model': 'm'},
                    'sys', 'q') == 'hello'
    assert calls[-1][1]['system'] == 'sys'
