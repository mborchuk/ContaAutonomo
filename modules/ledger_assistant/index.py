#!/usr/bin/env python3
"""
Ledger Assistant Module (G-7) — read-only questions over the books.

Opt-in (off by default, like every module but Backup). Answers questions such
as "how much did I invoice this customer in Q3" through registry functions
(functions.py): it reads with the functions whose declaration allows the
assistant, and asks with `ai.complete_text`. It names no module, so any
module declaring those functions works. The steps and the data sent are
in assistant.py.

Read-only by construction and by guard:
  - the catalogue holds only functions declared read-only that send nothing
    out, and a plan can name nothing else;
  - while the calls run, every SQL statement other than a SELECT is refused
    (`read_only()`), and the session is rolled back afterwards, so not even an
    implementation with a side effect can change the database;
  - the module has no tables and writes no activity-log entry;
  - it never sees an API key: `ai.complete_text` returns only the reply.

Only the question goes into the planning request; only the declared
`assistant_fields` of the records those calls return go into the answering
request. The settings panel says so. Every answer lists the records it used
and carries the "not tax advice" wording.
"""

import contextvars
import json
from contextlib import contextmanager

from flask import Blueprint, render_template, request
from sqlalchemy import event

from contaautonomo.core.functions import FunctionUnavailable
from contaautonomo.core.module_manager import BaseModule

from . import assistant as core_steps

MAX_QUESTION = 500
NO_TEXT_PROVIDER = ('No enabled module answers text questions. Enable the AI Agent '
                    'module in Settings → Modules and choose OpenAI or Anthropic with an '
                    'API key, or Claude Code CLI or Kiro CLI, in Settings → AI Agent.')
NOTHING_TO_READ = 'No enabled module provides data the assistant can read.'
TEXT_STATUS_FAILED = ('The module that answers text questions could not report its status. '
                      'Check the server log.')

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
                'language. Read-only; asks through the AI Agent and sends only the '
                'records the question needs. Not tax advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def dependencies(self):
        # Functions, not modules: the core implements the invoice list. The
        # text function is optional — without it the page explains what to enable.
        return ['ledger.list_invoices']

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

    # --- Registry ------------------------------------------------------- #

    @property
    def _functions(self):
        return self.core.functions

    def text_status(self):
        """ai.text_status, or None when no module implements text questions.
        A provider that fails or answers nonsense makes the assistant
        unavailable instead of breaking its page."""
        if not self._functions.available('ai.text_status'):
            return None
        try:
            status = self._functions.call('ai.text_status')
        except Exception as e:
            self.logger.warning('ai.text_status failed: %s', type(e).__name__)
            status = None
        if not isinstance(status, dict):
            return {'available': False, 'provider': None, 'reason': TEXT_STATUS_FAILED}
        return status

    def missing(self):
        """What has to be configured before a question can be answered, or None."""
        status = self.text_status()
        if status is None or not self._functions.available('ai.complete_text'):
            return NO_TEXT_PROVIDER
        if not status.get('available'):
            return status.get('reason') or NO_TEXT_PROVIDER
        if not self._catalogue():
            return NOTHING_TO_READ
        return None

    def _catalogue(self):
        return core_steps.catalogue_from(self._functions.find(assistant=True))

    def _complete(self, system, user):
        return self._functions.call('ai.complete_text', system=system, user=user,
                                    max_tokens=1500)

    # --- Reading ------------------------------------------------------------ #

    @staticmethod
    def _call_problem(exc):
        """A fixed message for a failed read; the details go to the log."""
        if isinstance(exc, ReadOnlyViolation):
            return 'refused: the assistant can only read data'
        if isinstance(exc, FunctionUnavailable):
            return 'not available: no enabled module provides it'
        return 'the data could not be read'

    def run_calls(self, calls, catalogue):
        """({kind: [minimised record]}, errors, truncated) for validated calls."""
        sent, errors, truncated = {}, [], False
        with self.read_only():
            for call in calls:
                contract = catalogue[call['call']]
                try:
                    if call['kind'] == 'draft':
                        body = self._functions.call(call['call'], **call['params'])
                        draft_id = '-'.join(str(body.get(k, call['params'].get(k, '')))
                                            for k in ('form', 'year', 'quarter'))
                        sent.setdefault('draft', []).append(
                            core_steps.minimise(contract, body, draft_id))
                        continue
                    rows, page = [], 1
                    while True:
                        params = dict(call['params'])
                        if call['paged']:
                            params.update(page=page, per_page=core_steps.PAGE_SIZE)
                        body = self._functions.call(call['call'], **params)
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
                            bucket.append(core_steps.minimise(contract, row))
                            seen.add(row.get('id'))
                except Exception as e:
                    self.logger.warning('Assistant call %s failed: %s',
                                        call['call'], type(e).__name__)
                    errors.append(f'{call["call"]}: {self._call_problem(e)}')
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
        catalogue = self._catalogue()
        try:
            plan = core_steps.parse_json(self._complete(
                core_steps.plan_prompt(catalogue, today), question))
            calls, rejected = core_steps.validate_plan(plan, catalogue)
            out['calls'], out['rejected'] = calls, rejected
            if not calls:
                out['answer'] = ('This question cannot be answered from the invoices, '
                                 'expenses, customers and tax drafts the assistant can read.')
                return out
            sent, errors, truncated = self.run_calls(calls, catalogue)
            out['errors'], out['truncated'] = errors, truncated
            reply = self._complete(core_steps.ANSWER_PROMPT, json.dumps(
                {'question': question, 'records': sent}, default=str, ensure_ascii=False))
        except FunctionUnavailable:
            out['missing'] = self.missing() or NO_TEXT_PROVIDER
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
        from contaautonomo.app import limiter

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
        status = self.text_status()
        return self.render_settings_template(
            'ledger_assistant_settings.html',
            provider=(status or {}).get('provider'),
            missing=self.missing(),
            sent_fields=core_steps.sent_fields(self._catalogue()))
