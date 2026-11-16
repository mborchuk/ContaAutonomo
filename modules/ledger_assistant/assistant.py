#!/usr/bin/env python3
"""
Ledger assistant — the pure parts: what may be read, what is sent, what is
shown. No Flask, no database, no network.

How a question is answered (index.py runs the steps):
  1. PLAN.   The provider receives the question and the catalogue — the
             registry functions whose contract allows the assistant
             (read-only, nothing sent out) and that an enabled module or the
             core implements. No records.
  2. READ.   `validate_plan` keeps only catalogue calls whose parameters
             pass the contract. The module runs them through the registry,
             with every database write blocked.
  3. ANSWER. `minimise` keeps only the contract's `assistant_fields`; those
             records and the question go to the provider, which answers and
             cites the record ids it used.
  4. SHOW.   `records_used` lists the cited records (all sent records when it
             cites none), with a deterministic total, and the page adds the
             "not tax advice" wording.

The catalogue has no function that writes or sends data out (contracts.py
refuses to offer one), so a plan cannot ask for one; anything not in it is
dropped and reported.
"""

import json
from datetime import date

MAX_CALLS = 5
MAX_RECORDS = 500          # per call, across pages
PAGE_SIZE = 100

DISCLAIMER = ('Answered from your records by an AI model — it can be wrong. '
              'Check the records listed below. Not tax advice.')

# The assistant pages through results itself; a plan never sets these.
PAGING = ('page', 'per_page')
DESCRIPTION_MAX = 80


def catalogue_from(contracts):
    """{name: contract} for the contracts the assistant may call."""
    return {c.name: c for c in contracts if c.assistant and c.read_only and not c.external}


def sent_fields(catalogue):
    """{record kind: fields that may be sent}, for the settings panel."""
    return {c.record_kind: c.assistant_fields for c in catalogue.values()}


def _signature(contract):
    params = [p.name + ('' if p.required else '?') for p in contract.params
              if p.name not in PAGING]
    return f'{contract.name}({", ".join(params)})'


def plan_prompt(catalogue, today):
    lines = [f'- {_signature(c)}: {c.summary}' for c in catalogue.values()]
    return (
        'You plan read-only lookups in a small business ledger (a Spanish '
        f'autónomo). Today is {today.isoformat()}. Quarters are calendar quarters '
        '(Q1 = January-March). Dates are YYYY-MM-DD; "?" marks an optional '
        'parameter. Available calls:\n' + '\n'.join(lines) + '\n'
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

    A call runs only when it is in the catalogue and its parameters pass the
    contract. Each call is {'call', 'params', 'kind', 'paged'}; params are the
    checked values, dates as YYYY-MM-DD.
    """
    calls, rejected = [], []
    raw = plan.get('calls') if isinstance(plan, dict) else None
    if not isinstance(raw, list):
        return [], (['the plan was not understood'] if plan is not None else [])
    for item in raw[:MAX_CALLS]:
        name = item.get('call') if isinstance(item, dict) else None
        contract = catalogue.get(name) if isinstance(name, str) else None
        if contract is None:
            rejected.append(f'{name!r} is not a read-only call the assistant may use')
            continue
        params = item.get('params') or {}
        if not isinstance(params, dict):
            rejected.append(f'{name}: parameters were not understood')
            continue
        paging = sorted(set(params) & set(PAGING))
        if paging:
            rejected.append(f'{name}: parameter {paging[0]!r} is not allowed')
            continue
        clean, problem = contract.validate(params)
        if problem:
            rejected.append(f'{name}: {problem}')
            continue
        calls.append({'call': name,
                      'params': {k: v.isoformat() if hasattr(v, 'isoformat') else v
                                 for k, v in clean.items()},
                      'kind': contract.record_kind, 'paged': 'page' in contract.param_names})
    if len(raw) > MAX_CALLS:
        rejected.append(f'only the first {MAX_CALLS} calls were run')
    return calls, rejected


def minimise(contract, row, draft_id=None):
    """The fields of one record that may be sent to the provider: the
    contract's `assistant_fields`, and the record id."""
    out = {key: row.get(key) for key in contract.assistant_fields}
    if contract.record_kind == 'draft':
        out = {'id': draft_id, **out}
    if contract.record_kind == 'expense' and isinstance(out.get('description'), str):
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
