#!/usr/bin/env python3
"""
Fiscal Calendar Module (F3) — Spanish AEAT deadlines + reminders.

Uses local data (calendar_data.py), no external API. The upcoming filing
windows of the forms the user actually files, and reminders 14, 7 and 1 days
before a window closes, are messages in the core message center (top-bar bell)
posted through `messages.post`; each is posted once. A daily scheduler job and
the dashboard post them. Reminders also go through the `notify` capability
(email plugs in through F8) and are always written to the activity log.
"""

import json
from datetime import date

from flask import Blueprint, render_template

from contaautonomo.core.module_manager import BaseModule

from .calendar_data import (
    CALENDAR_VERSION,
    all_deadlines,
    apply_plan,
    deadlines_for_year,
    upcoming,
    available_forms,
)

# Reminder thresholds (days before the filing window closes).
REMINDER_THRESHOLDS = (14, 7, 1)
# How far ahead a filing window gets its message.
LOOKAHEAD_DAYS = 60
CALENDAR_LINK = '[fiscal calendar](/fiscal-calendar/)'



class FiscalCalendarModule(BaseModule):
    """Spanish fiscal calendar panel + deadline reminders."""

    @property
    def module_id(self):
        return 'fiscal_calendar'

    @property
    def name(self):
        return 'Fiscal Calendar'

    @property
    def description(self):
        return ('Spanish AEAT filing deadlines (Modelo 303/130/349/111/390/347/100) '
                'with messages and reminders in the message center. Local data, no external API.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Fiscal Calendar', 'endpoint': 'fiscal_calendar.calendar_index',
             'icon': '📅', 'group': 'Taxes', 'order': 60}
        ]

    @property
    def settings_tab(self):
        return {'id': 'fiscal_calendar', 'label': 'Fiscal Calendar'}

    def register_models(self, db):
        self._db = db

        class FiscalCalendarConfig(db.Model):
            __tablename__ = 'fiscal_calendar_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            key = db.Column(db.String(100), unique=True, nullable=False)
            value = db.Column(db.Text)

        self.Config = FiscalCalendarConfig
        return {'FiscalCalendarConfig': FiscalCalendarConfig}

    def on_enable(self):
        # Daily reminder job (no request context inside — activity log + notify).
        try:
            self.core.scheduler.add_job(
                job_id='fiscal_calendar.reminders',
                func=self._run_reminders,
                job_type='daily',
                time_str='08:00',
                description='Fiscal calendar deadline reminders',
            )
        except Exception as e:  # pragma: no cover - defensive
            self.logger.exception('Failed to register reminder job: %s', e)

    # --- Config: which forms does the user file --------------------------- #

    def _selected_forms(self):
        """Forms the user files. Default: inferred from TaxForm history, else all."""
        try:
            row = self.Config.query.filter_by(key='selected_forms').first()
            if row and row.value:
                data = json.loads(row.value)
                if isinstance(data, list) and data:
                    return set(data)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Reading selected_forms failed: %s', e)
        return self._default_forms()

    def _default_forms(self):
        """Smart default: distinct TaxForm.form_type values, else every form."""
        try:
            from contaautonomo.app import TaxForm
            rows = self._db.session.query(TaxForm.form_type).distinct().all()
            forms = {r[0] for r in rows if r[0]}
            # TaxForm.form_type may hold '303' etc; keep only known forms.
            known = set(available_forms())
            forms = {f for f in forms if f in known}
            if forms:
                return forms
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Inferring default forms failed: %s', e)
        return set(available_forms())

    # --- Filing plans of other modules ------------------------------------ #

    def _adjust(self, year, entries):
        """Apply the `filing_plan` capabilities of the enabled modules (for
        example the yearly VAT return) to the deadlines of `year`."""
        mgr = getattr(self.core, 'module_manager', None)
        try:
            caps = mgr.find_capabilities('filing_plan') if mgr else []
        except Exception:  # pragma: no cover - defensive
            return entries
        for cap in caps:
            try:
                plan = cap['action'](year)
            except Exception:
                self.logger.warning('A filing plan failed', exc_info=True)
                continue
            if plan:
                entries = apply_plan(year, entries, plan)
        return entries

    # --- Dashboard panel (F3-D2) ------------------------------------------ #

    def upcoming_deadlines(self, limit=4, today=None):
        """Next open or upcoming filing windows for the selected forms, for the
        compact "Tax Deadlines" column on the dashboard."""
        items = upcoming(366, today=today, selected_forms=self._selected_forms(),
                         adjust=self._adjust)
        return items[:limit]

    def deadline_for(self, form, year, quarter=None):
        """The filing window of one return: `form` for fiscal `year` and
        `quarter` (None for an annual form). None when the calendar has no
        such entry. Independent of the forms selected for reminders."""
        matches = [e for e in deadlines_for_year(year, self._adjust)
                   if e['form'] == form and e['quarter'] == quarter]
        if quarter is None:  # the annual return before any advance
            matches.sort(key=lambda e: e['period'] != 'annual')
        return matches[0] if matches else None

    def year_deadlines(self, year):
        """Every filing window of fiscal `year`, all forms, by window end."""
        return deadlines_for_year(year, self._adjust)

    def selected_forms(self):
        return sorted(self._selected_forms())

    # --- Registry functions ------------------------------------------------ #

    def get_functions(self):
        from contaautonomo.core.functions import Function, Param
        year = Param('year', 'int', 'Fiscal year', required=True, minimum=2000, maximum=2100)
        entry = ('{"form", "period", "period_label", "year", "quarter": 1-4 or None, '
                 '"window_start": date, "window_end": date, "verified": bool}')
        return [
            Function('calendar.upcoming_deadlines', self.upcoming_deadlines,
                     'Next open or upcoming filing windows for the forms the user files.',
                     params=(Param('limit', 'int', 'How many, default 4', minimum=1, maximum=50),
                             Param('today', 'date', 'Reference day, default today')),
                     returns=f'[entry + "state", "days_to_end", "days_to_start"]; entry: {entry}'),
            Function('calendar.deadline_for', self.deadline_for,
                     'The filing window of one return, whether or not the user files the form.',
                     params=(Param('form', 'str', "Form number, e.g. '303'", required=True,
                                   max_length=10),
                             year,
                             Param('quarter', 'int', 'Quarter 1-4; omit for an annual form',
                                   minimum=1, maximum=4)),
                     returns=f'{entry}, or None'),
            Function('calendar.deadlines_for_year', self.year_deadlines,
                     'Every filing window of one fiscal year, all forms, by window end.',
                     params=(year,), returns=f'[{entry}]'),
            Function('calendar.selected_forms', self.selected_forms,
                     'Form numbers the user files (Settings → Fiscal Calendar).',
                     returns='[form], sorted'),
        ]

    def get_dashboard_panels(self):
        # No panel: upcoming windows are messages in the top-bar bell. Opening
        # the dashboard posts any new one at once, besides the daily job.
        self.post_window_messages()
        return []

    # --- Messages (message center) ---------------------------------------- #

    def _post_message(self, text, level, key):
        """messages.post; True when a new message was created. Never raises."""
        functions = self.core.functions
        if not functions.available('messages.post'):
            return False
        try:
            result = functions.call('messages.post', source=self.module_id, text=text,
                                    level=level, key=key)
        except Exception:
            self.logger.warning('Could not post a fiscal calendar message', exc_info=True)
            return False
        return bool(result.get('created'))

    @staticmethod
    def _label(entry):
        label = f"Modelo {entry['form']} · {entry['period_label']}"
        return label if entry.get('verified', True) else label + ' (dates to verify)'

    @staticmethod
    def _key(kind, entry, *extra):
        # Annual forms: their period ('annual', or 'advance1'.. for the yearly VAT).
        quarter = entry['quarter'] if entry['quarter'] is not None else entry['period']
        return ':'.join([kind, entry['form'], str(entry['year']), str(quarter),
                         *map(str, extra)])

    def post_window_messages(self, today=None):
        """One message per filing window of the selected forms that opens
        within LOOKAHEAD_DAYS or is open now, with both dates. Keyed by form
        and period, so it is posted once. Returns how many were new."""
        today = today or date.today()
        created = 0
        try:
            entries = upcoming(LOOKAHEAD_DAYS, today=today,
                               selected_forms=self._selected_forms(), adjust=self._adjust)
        except Exception:
            self.logger.warning('Could not read the fiscal calendar', exc_info=True)
            return 0
        for e in entries:
            start, end = e['window_start'].strftime('%d/%m/%Y'), e['window_end'].strftime('%d/%m/%Y')
            if e['state'] == 'open':
                text = f'📅 {self._label(e)} can be filed now, until {end} — {CALENDAR_LINK}'
            else:
                text = f'📅 {self._label(e)}: file from {start} to {end} — {CALENDAR_LINK}'
            created += self._post_message(text, 'info', self._key('window', e))
        return created

    # --- Routes: year view (F3-X1) ---------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('fiscal_calendar', __name__,
                       template_folder='templates',
                       url_prefix='/fiscal-calendar')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def calendar_index():
            selected = module._selected_forms()
            deadlines = [d for d in all_deadlines(adjust=module._adjust)
                         if d['form'] in selected]
            return render_template('fiscal_calendar_index.html',
                                   deadlines=deadlines,
                                   selected=sorted(selected),
                                   all_forms=available_forms(),
                                   today=date.today(),
                                   version=CALENDAR_VERSION)

        app.register_blueprint(bp)

    # --- Settings panel (F3-D3) ------------------------------------------- #

    def get_settings_html(self, settings):
        return self.render_settings_template(
            'fiscal_calendar_settings_panel.html',
            forms=available_forms(), selected=self._selected_forms())

    def save_settings(self, settings, form):
        if 'fiscal_calendar_submitted' not in form:
            return  # guard: unrelated tab save
        selected = form.getlist('fiscal_forms') if hasattr(form, 'getlist') else \
            form.get('fiscal_forms', [])
        row = self.Config.query.filter_by(key='selected_forms').first()
        if not row:
            row = self.Config(key='selected_forms')
            self._db.session.add(row)
        row.value = json.dumps(list(selected))
        self._db.session.commit()

    # --- Reminder job (F3-D4) --------------------------------------------- #

    def _reminder_state(self):
        try:
            row = self.Config.query.filter_by(key='reminder_state').first()
            return json.loads(row.value) if row and row.value else {}
        except Exception:  # pragma: no cover - defensive
            return {}

    def _save_reminder_state(self, state):
        row = self.Config.query.filter_by(key='reminder_state').first()
        if not row:
            row = self.Config(key='reminder_state')
            self._db.session.add(row)
        row.value = json.dumps(state)
        self._db.session.commit()

    def _run_reminders(self, today=None):
        """Fire idempotent reminders at each threshold. Runs in app context.

        Returns the list of reminder keys fired this run (also used by tests).
        """
        today = today or date.today()
        selected = self._selected_forms()
        state = self._reminder_state()
        fired = []
        for entry in all_deadlines(today=today, adjust=self._adjust):
            if entry['form'] not in selected:
                continue
            days_to_end = (entry['window_end'] - today).days
            for threshold in REMINDER_THRESHOLDS:
                if days_to_end == threshold:
                    key = f"{entry['form']}:{entry['period_label']}:{threshold}"
                    if key in state:
                        continue  # already reminded (idempotent across restarts)
                    self._notify(entry, threshold)
                    state[key] = today.isoformat()
                    fired.append(key)
        if fired:
            self._save_reminder_state(state)
        self.post_window_messages(today=today)
        return fired

    def _notify(self, entry, days_left):
        message = (f"Modelo {entry['form']} ({entry['period_label']}) filing window "
                   f"closes in {days_left} day(s), on "
                   f"{entry['window_end'].strftime('%d/%m/%Y')}.")
        # Always write the user-visible activity log.
        self.log('fiscal_deadline_reminded', message, category='tax')
        # The reminder as a message in the bell.
        level = 'danger' if days_left <= 1 else 'warning' if days_left <= 7 else 'info'
        self._post_message(
            f"⏰ {self._label(entry)}: the filing window closes in {days_left} day(s), "
            f"on {entry['window_end'].strftime('%d/%m/%Y')} — {CALENDAR_LINK}",
            level, self._key('reminder', entry, days_left))
        # Ride any notify capability (F8 email, webhooks...) without hard coupling.
        try:
            channels = self.core.module_manager.find_capabilities('notify')
            for ch in channels:
                try:
                    ch['action'](subject='Fiscal deadline reminder', body=message)
                except Exception as e:  # pragma: no cover - channel errors isolated
                    self.logger.warning('notify channel %s failed: %s',
                                        ch.get('module_id'), e)
        except Exception:  # pragma: no cover - find_capabilities optional
            pass
