"""G-7 — read-only ledger assistant: off by default, cannot write, sends only
the records needed, lists them, and explains a missing provider. It reads and
asks through registry functions, so the tests give it a stand-in text module
instead of the AI Agent — what a third-party module replacing it would do."""
import json
from datetime import date

import pytest

from functions import Function, Param
from module_manager import BaseModule
from modules.ledger_assistant import assistant as steps

TODAY = date(2035, 11, 10)


class FakeTextModule(BaseModule):
    """Any module declaring ai.text_status and ai.complete_text will do; it
    declares them itself, as a module from outside the repository would."""

    module_id = 'fake_text_ai'
    name = 'Fake text AI'

    def __init__(self, core, replies=(), status=None):
        super().__init__(core)
        self.replies = list(replies)
        self.status = status or {'available': True, 'provider': 'Synthetic AI', 'reason': None}
        self.seen = []

    def get_functions(self):
        return [
            Function('ai.text_status', lambda: self.status, 'Whether text works.'),
            Function('ai.complete_text', self.complete, 'Reply to the message.',
                     params=(Param('system', 'str', required=True, max_length=20_000),
                             Param('user', 'str', required=True, max_length=400_000),
                             Param('max_tokens', 'int', minimum=1, maximum=16_000)),
                     external=True),
            Function('ai.summarise', lambda text: text[:10], 'An extra function of its own.',
                     params=(Param('text', 'str', required=True),)),
        ]

    def complete(self, system, user, max_tokens=1500):
        self.seen.append({'system': system, 'user': user, 'max_tokens': max_tokens})
        return self.replies.pop(0) if self.replies else ''


@pytest.fixture
def la(loaded_modules):
    return loaded_modules.modules['ledger_assistant']


@pytest.fixture
def text_ai(loaded_modules, monkeypatch):
    def install(plan=None, answer=None, status=None):
        replies = [json.dumps(r) for r in (plan, answer) if r is not None]
        fake = FakeTextModule(loaded_modules.core, replies, status)
        monkeypatch.setitem(loaded_modules.modules, 'fake_text_ai', fake)
        return fake
    return install


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


def test_it_depends_on_functions_not_modules(la, loaded_modules):
    assert la.dependencies == ['ledger.list_invoices']
    assert loaded_modules.missing_dependencies('ledger_assistant') == []   # the core


def test_without_a_text_module_it_says_what_is_missing(la, ledger):
    out = la.ask('How much did I invoice ACME in Q3?', TODAY)
    assert 'Enable the AI Agent module' in out['missing']
    assert out['answer'] is None and out['calls'] == []


def test_a_second_text_module_is_not_used_and_says_who_owns_it(la, text_ai, loaded_modules,
                                                                monkeypatch):
    first = text_ai(plan={'calls': []})
    second = FakeTextModule(loaded_modules.core, [json.dumps({'calls': []})])
    monkeypatch.setitem(loaded_modules.modules, 'other_text_ai', second)
    la.ask('Anything?', TODAY)
    assert len(first.seen) == 1 and second.seen == []
    assert loaded_modules.functions.status('other_text_ai')['conflicts'] == [
        {'function': 'ai.complete_text', 'owner': 'fake_text_ai'},
        {'function': 'ai.summarise', 'owner': 'fake_text_ai'},
        {'function': 'ai.text_status', 'owner': 'fake_text_ai'}]
    monkeypatch.delitem(loaded_modules.modules, 'fake_text_ai')   # owner disabled
    second.replies = [json.dumps({'calls': []})]
    la.ask('Anything?', TODAY)
    assert len(second.seen) == 1                                     # the next one takes over


def test_a_failing_text_module_does_not_break_the_page(la, text_ai, client, monkeypatch):
    fake = text_ai()

    def broken():
        raise RuntimeError('third-party bug')
    monkeypatch.setattr(fake, 'status', None)
    monkeypatch.setattr(type(fake), 'get_functions', lambda self: [
        Function('ai.text_status', broken, 'Broken status.'),
        Function('ai.complete_text', self.complete, 'Reply.',
                 params=(Param('system', 'str', required=True, max_length=20_000),
                         Param('user', 'str', required=True, max_length=400_000),
                         Param('max_tokens', 'int', minimum=1, maximum=16_000)),
                 external=True)])
    assert 'could not report its status' in la.missing()
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get('/assistant/')
    assert resp.status_code == 200 and 'could not report its status' in resp.get_data(as_text=True)


def test_the_text_module_reason_is_shown(la, text_ai):
    text_ai(status={'available': False, 'provider': 'Google Document AI',
                    'reason': 'CLI agents are off on this server.'})
    assert la.missing() == 'CLI agents are off on this server.'


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


def test_catalogue_holds_only_read_only_internal_functions(la, loaded_modules):
    catalogue = la._catalogue()
    assert {'ledger.list_invoices', 'ledger.list_customers', 'ledger.list_expenses',
            'tax.draft_303', 'tax.draft_130'} <= set(catalogue)
    for contract in catalogue.values():
        assert contract.read_only and not contract.external and contract.assistant_fields
    assert 'ai.complete_text' not in catalogue


def test_it_cannot_create_change_or_delete(la, ledger, text_ai, loaded_modules, monkeypatch):
    from app import db
    text_ai(plan={'calls': [
        {'call': 'ledger.list_invoices', 'params': {}},
        {'call': 'ledger.list_expenses', 'params': {}},
        {'call': 'ledger.list_customers'},
        {'call': 'delete_invoice', 'params': {'id': 1}},                         # unknown
        {'call': 'ledger.list_invoices', 'params': {'id': 1, 'status': 'paid'}},  # not allowed
    ]}, answer={'answer': 'Synthetic.', 'record_ids': {}})
    # An implementation with a side effect is stopped by the guard, too.
    import dataclasses
    declared = loaded_modules.functions._core['ledger.list_customers']

    def writing_customers():
        from app import Customer
        db.session.add(Customer(name='Injected Synthetic'))
        db.session.commit()
        return declared.call()
    monkeypatch.setitem(loaded_modules.functions._core, 'ledger.list_customers',
                        dataclasses.replace(declared, call=writing_customers))

    before = _snapshot(db)
    out = la.ask('Show everything', TODAY)
    assert _snapshot(db) == before
    assert any('delete_invoice' in r for r in out['rejected'])
    assert any("'id' is not allowed" in r for r in out['rejected'])
    assert 'ledger.list_customers: refused: the assistant can only read data' in out['errors']


# --- data minimisation and the answer -------------------------------------- #

def test_sends_only_the_records_and_fields_needed(la, ledger, text_ai, loaded_modules):
    fake = text_ai(plan={'calls': [{'call': 'ledger.list_invoices', 'params': {
        'client': 'ACME', 'date_from': '2035-07-01', 'date_to': '2035-09-30'}}]},
        answer={'answer': 'You invoiced 1,500.00 EUR.', 'record_ids': {'invoice': []}})
    out = la.ask('How much did I invoice ACME in Q3?', TODAY)

    assert fake.seen[0]['user'] == 'How much did I invoice ACME in Q3?'   # plan: question only
    assert 'LA-' not in fake.seen[0]['system'] + fake.seen[0]['user']
    assert 'ledger.list_invoices(date_from?, date_to?, client?, status?)' in fake.seen[0]['system']
    sent = json.loads(fake.seen[1]['user'])['records']
    numbers = sorted(r['invoice_number'] for r in sent['invoice'])
    assert numbers == ['LA-1', 'LA-2']                                     # Q3, ACME only
    fields = loaded_modules.functions.get('ledger.list_invoices').assistant_fields
    for row in sent['invoice']:
        assert set(row) == set(fields)
    payload = fake.seen[1]['user']
    for secret in ('billing@example.invalid', 'B00000000', 'internal note'):
        assert secret not in payload
    assert out['totals']['invoices_eur'] == 1500.0
    assert out['calls'][0]['params'] == {'date_from': '2035-07-01', 'date_to': '2035-09-30',
                                         'client': 'ACME'}


def test_answer_lists_the_records_it_used(la, ledger, text_ai):
    plan = {'calls': [{'call': 'ledger.list_invoices', 'params': {'client': 'ACME'}}]}
    text_ai(plan=plan, answer={'answer': 'x', 'record_ids': {'invoice': []}})
    first = la.ask('ACME invoices', TODAY)
    target = next(r for r in first['used']['invoice'] if r['invoice_number'] == 'LA-3')

    text_ai(plan=plan, answer={'answer': 'The October invoice is 900.00 EUR.',
                               'record_ids': {'invoice': [target['id']]}})
    out = la.ask('Which ACME invoice is from October?', TODAY)
    assert [r['invoice_number'] for r in out['used']['invoice']] == ['LA-3']
    assert out['totals'] == {'invoices_eur': 900.0}


def test_expense_descriptions_are_cut_to_one_line(la, ledger, text_ai):
    fake = text_ai(plan={'calls': [{'call': 'ledger.list_expenses', 'params': {}}]},
                   answer={'answer': 'ok', 'record_ids': {}})
    la.ask('My expenses', TODAY)
    sent = json.loads(fake.seen[1]['user'])['records']['expense']
    assert sent[0]['description'] == 'Synthetic tool'
    assert 'notes' not in sent[0]


def test_tax_drafts_are_read_through_their_function(la, ledger, text_ai):
    fake = text_ai(plan={'calls': [{'call': 'tax.draft_303', 'params': {'year': 2035,
                                                                         'quarter': 3}}]},
                   answer={'answer': 'ok', 'record_ids': {}})
    out = la.ask('My Q3 VAT', TODAY)
    draft = json.loads(fake.seen[1]['user'])['records']['draft'][0]
    assert draft['id'] == '303-2035-3' and set(draft) == {'id', 'form', 'year', 'quarter', 'boxes'}
    assert out['errors'] == []


def test_plan_validation_rejects_bad_parameters(la):
    catalogue = la._catalogue()
    calls, rejected = steps.validate_plan({'calls': [
        {'call': 'tax.draft_303', 'params': {'year': 2035, 'quarter': 3}},
        {'call': 'tax.draft_303', 'params': {'year': 2035, 'quarter': 7}},
        {'call': 'ledger.list_invoices', 'params': {'date_from': 'yesterday'}},
        {'call': 'tax.draft_130', 'params': {'year': 'x', 'quarter': 1}},
        {'call': 'ledger.list_invoices', 'params': {'page': 3}},
    ]}, catalogue)
    assert calls == [{'call': 'tax.draft_303', 'params': {'year': 2035, 'quarter': 3},
                      'kind': 'draft', 'paged': False}]
    assert rejected == [
        'tax.draft_303: quarter must be between 1 and 4',
        'ledger.list_invoices: date_from must be a date (YYYY-MM-DD)',
        'tax.draft_130: year must be a whole number',
        "ledger.list_invoices: parameter 'page' is not allowed",
    ]
    _, rejected = steps.validate_plan(
        {'calls': [{'call': 'tax.draft_130', 'params': {'year': 2035}}] * 6}, catalogue)
    assert rejected == ['tax.draft_130: quarter is required'] * 5 + [
        'only the first 5 calls were run']


def test_a_disabled_data_module_leaves_the_catalogue(la, loaded_modules, monkeypatch):
    monkeypatch.delitem(loaded_modules.modules, 'tax_es_forms')
    assert 'tax.draft_303' not in la._catalogue()
    assert 'ledger.list_invoices' in la._catalogue()


# --- page ------------------------------------------------------------------- #

def _auth(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_page_requires_login(loaded_modules, client):
    resp = client.get('/assistant/')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_page_explains_a_missing_provider(la, client):
    _auth(client)
    html = client.get('/assistant/').get_data(as_text=True)
    assert 'Enable the AI Agent module' in html and 'Not tax advice' in html


def test_page_shows_answer_records_and_disclaimer(la, ledger, text_ai, client):
    text_ai(plan={'calls': [{'call': 'ledger.list_invoices', 'params': {'client': 'ACME'}}]},
            answer={'answer': 'Three invoices.', 'record_ids': {}})
    _auth(client)
    html = client.post('/assistant/', data={'question': 'ACME invoices?'}).get_data(as_text=True)
    assert 'Three invoices.' in html and 'Records used' in html and 'LA-3' in html
    assert 'ledger.list_invoices' in html
    assert steps.DISCLAIMER.split(' — ')[0] in html


def test_settings_panel_says_what_is_sent(la, app, text_ai):
    text_ai()
    with app.test_request_context('/settings'):
        html = la.get_settings_html(None)
    assert 'your question only' in html and 'Never sent' in html
    assert 'Synthetic AI' in html and 'invoice_number' in html


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
        with pytest.raises(Exception) as err:   # ApiError
            api._list_invoices()
    assert type(err.value).__name__ == 'ApiError' and err.value.status == 400


def test_api_expenses_answer_404_without_an_expenses_module(loaded_modules, app, monkeypatch):
    api = loaded_modules.modules['api']
    monkeypatch.delitem(loaded_modules.modules, 'expenses')
    with app.test_request_context('/api/v1/expenses'):
        with pytest.raises(Exception) as err:
            api._list_expenses()
    assert err.value.status == 404
