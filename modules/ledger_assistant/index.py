#!/usr/bin/env python3
"""
Ledger Assistant Module (G-7) — read-only questions over the books.

Opt-in (off by default, like every module but Backup). Answers questions such
as "how much did I invoice this customer in Q3" through read-only calls to
the existing /api/v1 GET handlers, with the AI provider configured in the AI
Parser. The steps and the data sent are in assistant.py.

Read-only by construction and by guard:
  - the catalogue holds GET calls only, and a plan can name nothing else;
  - while the calls run, every SQL statement other than a SELECT is refused
    (`read_only()`), and the session is rolled back afterwards, so not even a
    handler with a side effect can change the database;
  - the module has no tables and writes no activity-log entry.

Only the question goes into the planning request; only the fields in
assistant.SENT_FIELDS of the records those calls return go into the
answering request. The settings panel says so. Every answer lists the
records it used and carries the "not tax advice" wording.
"""

import contextvars
import json
from contextlib import contextmanager

from flask import Blueprint, render_template, request
from sqlalchemy import event

from module_manager import BaseModule

from . import assistant as core_steps
from .llm import ProviderUnavailable, complete, missing_reason

MAX_QUESTION = 500

# GET handlers of the api module the catalogue paths map to. Module routes
# (/m/...) go through the api dispatcher with the GET method only.
API_HANDLERS = {
    '/invoices': '_list_invoices',
    '/expenses': '_list_expenses',
    '/customers': '_list_customers',
}

_read_only = contextvars.ContextVar('ledger_assistant_read_only', default=False)
_guarded_engines = set()


class ReadOnlyViolation(PermissionError):
    """A statement other than SELECT was attempted while reading."""


def _block_writes(conn, cursor, statement, parameters, context, executemany):
    if not _read_only.get():
        return
    words = (statement or '').lstrip().split(None, 1)
    if not words or words[0].upper() != 'SELECT':
        raise ReadOnlyViolation('The ledger assistant can only read data')


class CallFailed(Exception):
    pass


class LedgerAssistantModule(BaseModule):
    """Read-only AI questions over invoices, expenses and tax drafts."""

    @property
    def module_id(self):
        return 'ledger_assistant'

    @property
    def name(self):
        return 'Ledger Assistant'

    @property
    def description(self):
        return ('Ask questions about your invoices, expenses and tax drafts in plain '
                'language. Read-only; uses the AI Parser provider and sends only the '
                'records the question needs. Not tax advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def dependencies(self):
        # The answers are read through the REST API handlers. The AI Parser is
        # optional: without it the page explains what to enable.
        return ['api']

    @property
    def nav_items(self):
        return [{'label': 'Assistant', 'endpoint': 'ledger_assistant.assistant_index',
                 'icon': '💬'}]

    @property
    def settings_tab(self):
        return {'id': 'ledger_assistant', 'label': 'Assistant'}

    # --- Read-only guard ---------------------------------------------------- #

    @contextmanager
    def read_only(self):
        """Refuse every statement but SELECT inside the block, then roll the
        session back so nothing a handler staged can be committed later."""
        engine = self.core.db.engine
        if id(engine) not in _guarded_engines:
            event.listen(engine, 'before_cursor_execute', _block_writes)
            _guarded_engines.add(id(engine))
        token = _read_only.set(True)
        try:
            yield
        finally:
            _read_only.reset(token)
            self.core.db.session.rollback()

    # --- Providers ---------------------------------------------------------- #

    def _provider(self, module_id):
        mm = getattr(self.core, 'module_manager', None)
        return mm.provider_of(module_id) if mm else None

    def provider_settings(self):
        parser = self._provider('ai_parser')
        return parser.provider_settings() if parser is not None else None

    def missing(self):
        """What has to be configured before a question can be answered, or None."""
        if self._provider('api') is None:
            return 'The API module is not enabled. Enable it in Settings → Modules.'
        return missing_reason(self.provider_settings())

    def _catalogue(self):
        mm = getattr(self.core, 'module_manager', None)
        loaded = set(mm.modules) if mm else set()
        return core_steps.catalogue_for(loaded)

    # --- Reading ------------------------------------------------------------ #

    def _call_once(self, api, path, params):
        with self.core.app.test_request_context('/api/v1' + path, method='GET',
                                                query_string=params):
            if path.startswith('/m/'):
                parts = path.split('/')
                result = api._handle(api._dispatch_module_route, parts[2], '/'.join(parts[3:]))
            else:
                result = api._handle(getattr(api, API_HANDLERS[path]))
        response, status = result if isinstance(result, tuple) else (result, result.status_code)
        body = response.get_json(silent=True) or {}
        if status != 200:
            raise CallFailed(body.get('message') or f'HTTP {status}')
        return body

    def run_calls(self, calls):
        """({kind: [minimised record]}, errors, truncated) for validated calls."""
        api = self._provider('api')
        sent, errors, truncated = {}, [], False
        with self.read_only():
            for call in calls:
                try:
                    if call['kind'] == 'draft':
                        body = self._call_once(api, call['path'], call['params'])
                        draft_id = call['path'].rsplit('/draft/', 1)[-1].replace('/', '-')
                        sent.setdefault('draft', []).append(
                            core_steps.minimise('draft', body, draft_id))
                        continue
                    rows, page = [], 1
                    while True:
                        params = dict(call['params'])
                        if call['paged']:
                            params.update(page=page, per_page=core_steps.PAGE_SIZE)
                        body = self._call_once(api, call['path'], params)
                        rows.extend(body.get('data') or [])
                        if not call['paged'] or len(rows) >= (body.get('total') or 0):
                            break
                        if len(rows) >= core_steps.MAX_RECORDS:
                            truncated = True
                            break
                        page += 1
                    bucket = sent.setdefault(call['kind'], [])
                    seen = {r['id'] for r in bucket}
                    for row in rows[:core_steps.MAX_RECORDS]:
                        if row.get('id') not in seen:
                            bucket.append(core_steps.minimise(call['kind'], row))
                            seen.add(row.get('id'))
                except (CallFailed, ReadOnlyViolation) as e:
                    errors.append(f'{call["call"]}: {e}')
        return sent, errors, truncated

    # --- Asking ------------------------------------------------------------- #

    def ask(self, question, today=None):
        """Answer one question. Returns a dict for the page: answer, used
        records, totals, the calls run, and anything missing or rejected."""
        today = today or core_steps.default_today()
        question = (question or '').strip()[:MAX_QUESTION]
        out = {'question': question, 'answer': None, 'used': {}, 'totals': {},
               'calls': [], 'rejected': [], 'errors': [], 'truncated': False,
               'missing': None}
        if not question:
            out['errors'].append('Type a question first.')
            return out
        out['missing'] = self.missing()
        if out['missing']:
            return out
        settings = self.provider_settings()
        catalogue = self._catalogue()
        try:
            plan = core_steps.parse_json(complete(
                settings, core_steps.plan_prompt(catalogue, today), question))
            calls, rejected = core_steps.validate_plan(plan, catalogue)
            out['calls'], out['rejected'] = calls, rejected
            if not calls:
                out['answer'] = ('This question cannot be answered from the invoices, '
                                 'expenses, customers and tax drafts the assistant can read.')
                return out
            sent, errors, truncated = self.run_calls(calls)
            out['errors'], out['truncated'] = errors, truncated
            reply = complete(settings, core_steps.ANSWER_PROMPT, json.dumps(
                {'question': question, 'records': sent}, default=str, ensure_ascii=False))
        except ProviderUnavailable as e:
            out['missing'] = str(e)
            return out
        except Exception as e:  # the provider failed; never echo its payload
            self.logger.error('Ledger assistant provider call failed: %s', type(e).__name__)
            out['errors'].append('The AI provider did not answer. Try again later.')
            return out
        parsed = core_steps.parse_json(reply)
        if isinstance(parsed, dict) and isinstance(parsed.get('answer'), str):
            out['answer'] = parsed['answer']
        else:
            out['answer'] = (reply or '').strip()
            parsed = {}
        out['used'] = core_steps.records_used(sent, parsed)
        out['totals'] = core_steps.totals(out['used'])
        return out

    # --- Routes ------------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('ledger_assistant', __name__, template_folder='templates',
                       url_prefix='/assistant')
        login_required = self.core.login_required
        module = self
        from app import limiter

        def _rl(rule):
            def deco(f):
                return limiter.limit(rule)(f) if limiter else f
            return deco

        @bp.route('/', methods=['GET'])
        @login_required
        def assistant_index():
            return render_template('ledger_assistant.html', result=None,
                                   missing=module.missing(), disclaimer=core_steps.DISCLAIMER)

        @bp.route('/', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def assistant_ask():
            result = module.ask(request.form.get('question', ''))
            return render_template('ledger_assistant.html', result=result,
                                   missing=result['missing'],
                                   disclaimer=core_steps.DISCLAIMER)

        app.register_blueprint(bp)

    # --- Settings (information only) --------------------------------------- #

    def get_settings_html(self, settings):
        provider = self.provider_settings()
        return self.render_settings_template(
            'ledger_assistant_settings.html',
            provider=(provider or {}).get('display_name'),
            missing=self.missing(), sent_fields=core_steps.SENT_FIELDS)
