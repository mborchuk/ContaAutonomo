#!/usr/bin/env python3
"""
Ledger assistant — the pure parts: what may be read, what is sent, what is
shown. No Flask, no database, no network.

How a question is answered (index.py runs the steps):
  1. PLAN.   The provider receives the question and the catalogue below —
             no records — and returns the read-only calls it needs.
  2. READ.   `validate_plan` keeps only catalogue calls with valid
             parameters. The module runs them against the GET handlers of
             /api/v1, with every database write blocked.
  3. ANSWER. `minimise` keeps only the fields listed in SENT_FIELDS; those
             records and the question go to the provider, which answers and
             cites the record ids it used.
  4. SHOW.   `records_used` lists the cited records (all sent records when it
             cites none), with a deterministic total, and the page adds the
             "not tax advice" wording.

The catalogue has no write endpoint, so a plan cannot ask for one; anything
not in it is dropped and reported.
"""

import json
import re
from datetime import date

MAX_CALLS = 5
MAX_RECORDS = 500          # per call, across pages
PAGE_SIZE = 100

DISCLAIMER = ('Answered from your records by an AI model — it can be wrong. '
              'Check the records listed below. Not tax advice.')

# Read-only calls the assistant may plan. `path` is under /api/v1; `params`
# lists the query parameters it may set (all optional).
CATALOGUE = {
    'invoices': {
        'path': '/invoices', 'kind': 'invoice', 'paged': True,
        'params': ('date_from', 'date_to', 'client', 'status'),
        'description': 'Invoices issued to customers. Filters: date_from/date_to '
                       '(YYYY-MM-DD, invoice date), client (part of the customer '
                       'name), status (draft, issued, paid, cancelled).',
    },
    'expenses': {
        'path': '/expenses', 'kind': 'expense', 'paged': True,
        'params': ('date_from', 'date_to', 'category'),
        'description': 'Business expenses. Filters: date_from/date_to (YYYY-MM-DD), '
                       'category (exact name, e.g. Software, Travel).',
    },
    'customers': {
        'path': '/customers', 'kind': 'customer', 'paged': False, 'params': (),
        'description': 'Customer list (name, country, VAT treatment).',
    },
    'modelo303': {
        'path': '/m/tax_es_forms/draft/303/{year}/{quarter}', 'kind': 'draft',
        'paged': False, 'params': ('year', 'quarter'), 'module': 'tax_es_forms',
        'description': 'Modelo 303 (IVA) draft boxes for one quarter. Needs year and '
                       'quarter (1-4).',
    },
    'modelo130': {
        'path': '/m/tax_es_forms/draft/130/{year}/{quarter}', 'kind': 'draft',
        'paged': False, 'params': ('year', 'quarter'), 'module': 'tax_es_forms',
        'description': 'Modelo 130 (IRPF) draft boxes for one quarter. Needs year and '
                       'quarter (1-4).',
    },
}

# The only fields that leave the app, per record kind. Emails, VAT numbers,
# addresses, notes, files and amounts in other fields are never sent.
SENT_FIELDS = {
    'invoice': ('id', 'invoice_number', 'invoice_date', 'client_name', 'amount_eur',
                'currency', 'status'),
    'expense': ('id', 'expense_date', 'category', 'description', 'amount', 'currency'),
    'customer': ('id', 'name', 'country', 'tax_type'),
}
DESCRIPTION_MAX = 80

_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def catalogue_for(available_modules):
    """Catalogue entries whose module is loaded."""
    return {name: spec for name, spec in CATALOGUE.items()
            if spec.get('module') is None or spec['module'] in available_modules}


def plan_prompt(catalogue, today):
    lines = [f'- {name}: {spec["description"]}' for name, spec in catalogue.items()]
    return (
        'You plan read-only lookups in a small business ledger (a Spanish '
        f'autónomo). Today is {today.isoformat()}. Quarters are calendar quarters '
        '(Q1 = January-March). Available calls:\n' + '\n'.join(lines) + '\n'
        f'Return ONLY JSON: {{"calls": [{{"call": "<name>", "params": {{...}}}}]}} '
        f'with at most {MAX_CALLS} calls and the narrowest filters that answer the '
        'question. Use no call that is not listed. If the question cannot be '
        'answered from these calls, return {"calls": []}.')


ANSWER_PROMPT = (
    'Answer the question using ONLY the records given, in the language of the '
    'question, briefly. Amounts are EUR unless a currency is shown. Do not give '
    'tax advice. Return ONLY JSON: {"answer": "<text>", "record_ids": '
    '{"invoice": [ids], "expense": [ids], "customer": [ids], "draft": [ids]}} '
    'listing the records the answer is based on.')


def parse_json(text):
    """JSON from a model reply, with ``` fences removed. None if invalid."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
    try:
        return json.loads(cleaned.strip())
    except (ValueError, TypeError):
        return None


def validate_plan(plan, catalogue):
    """(calls, rejected): the calls that may run, and why others were dropped.

    A call runs only when it is in the catalogue and every parameter is
    allowed and well formed. Each call is {'call', 'params', 'path'}.
    """
    calls, rejected = [], []
    raw = plan.get('calls') if isinstance(plan, dict) else None
    if not isinstance(raw, list):
        return [], (['the plan was not understood'] if plan is not None else [])
    for item in raw[:MAX_CALLS]:
        name = item.get('call') if isinstance(item, dict) else None
        spec = catalogue.get(name)
        if spec is None:
            rejected.append(f'{name!r} is not a read-only call the assistant may use')
            continue
        params = item.get('params') or {}
        if not isinstance(params, dict):
            rejected.append(f'{name}: parameters were not understood')
            continue
        clean, problem = {}, None
        for key, value in params.items():
            if key not in spec['params']:
                problem = f'{name}: parameter {key!r} is not allowed'
                break
            if value in (None, ''):
                continue
            if key in ('date_from', 'date_to'):
                if not (isinstance(value, str) and _DATE.match(value)):
                    problem = f'{name}: {key} must be YYYY-MM-DD'
                    break
            elif key in ('year', 'quarter'):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    problem = f'{name}: {key} must be a number'
                    break
                if key == 'quarter' and value not in (1, 2, 3, 4):
                    problem = f'{name}: quarter must be 1-4'
                    break
                if key == 'year' and not 2000 <= value <= 2100:
                    problem = f'{name}: year out of range'
                    break
            else:
                value = str(value)[:100]
            clean[key] = value
        if problem is None and '{year}' in spec['path'] and not (
                'year' in clean and 'quarter' in clean):
            problem = f'{name}: year and quarter are required'
        if problem:
            rejected.append(problem)
            continue
        path = spec['path'].format(**clean) if '{' in spec['path'] else spec['path']
        query = {k: v for k, v in clean.items() if k not in ('year', 'quarter')}
        calls.append({'call': name, 'path': path, 'params': query, 'kind': spec['kind'],
                      'paged': spec['paged']})
    if len(raw) > MAX_CALLS:
        rejected.append(f'only the first {MAX_CALLS} calls were run')
    return calls, rejected


def minimise(kind, row, draft_id=None):
    """The fields of one record that may be sent to the provider."""
    if kind == 'draft':
        return {'id': draft_id, 'form': row.get('form'), 'year': row.get('year'),
                'quarter': row.get('quarter'), 'boxes': row.get('boxes', {})}
    out = {key: row.get(key) for key in SENT_FIELDS[kind]}
    if kind == 'expense' and isinstance(out.get('description'), str):
        out['description'] = out['description'].splitlines()[0][:DESCRIPTION_MAX] \
            if out['description'] else ''
    return out


def records_used(sent, answer):
    """The sent records the answer cites, grouped by kind; all of them when
    it cites none. `sent` is {kind: [record]}."""
    cited = answer.get('record_ids') if isinstance(answer, dict) else None
    out = {}
    any_cited = False
    if isinstance(cited, dict):
        for kind, ids in cited.items():
            if kind in sent and isinstance(ids, list):
                wanted = {str(i) for i in ids}
                rows = [r for r in sent[kind] if str(r.get('id')) in wanted]
                if rows:
                    out[kind] = rows
                    any_cited = True
    if not any_cited:
        out = {k: v for k, v in sent.items() if v}
    return out


def totals(used):
    """Deterministic sums of the listed records, per currency, so the user can
    check the model's arithmetic."""
    out = {}
    for row in used.get('invoice', []):
        out.setdefault('invoices_eur', 0.0)
        out['invoices_eur'] += row.get('amount_eur') or 0.0
    for row in used.get('expense', []):
        key = f"expenses_{(row.get('currency') or 'EUR').lower()}"
        out.setdefault(key, 0.0)
        out[key] += row.get('amount') or 0.0
    return {k: round(v, 2) for k, v in out.items()}


def default_today():
    return date.today()
