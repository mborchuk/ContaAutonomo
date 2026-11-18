#!/usr/bin/env python3
"""
Fiscal Calendar Module (F3) — Spanish AEAT deadlines + reminders.

Uses local data (calendar_data.py), no external API. Shows a dashboard panel
of upcoming filing windows for the forms the user actually files, and runs a
daily scheduler job that sends reminders 14, 7 and 1 days before a deadline.
Reminders go through the `notify` capability (email plugs in through F8) and
are always written to the activity log.
"""

import json
from datetime import date

from flask import Blueprint, render_template

from module_manager import BaseModule

from .calendar_data import (
    CALENDAR_VERSION,
    all_deadlines,
    deadlines_for_year,
    upcoming,
    available_forms,
)

# Reminder thresholds (days before the filing window closes).
REMINDER_THRESHOLDS = (14, 7, 1)
# How far ahead the dashboard panel looks.
PANEL_LOOKAHEAD_DAYS = 60


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
                'as a dashboard panel with reminders. Local data, no external API.')

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
            self.logger.error('Failed to register reminder job: %s', e)

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
            from app import TaxForm
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

    # --- Dashboard panel (F3-D2) ------------------------------------------ #

    def upcoming_deadlines(self, limit=4, today=None):
        """Next open or upcoming filing windows for the selected forms, for the
        compact "Tax Deadlines" column on the dashboard."""
        items = upcoming(366, today=today, selected_forms=self._selected_forms())
        return items[:limit]

    def deadline_for(self, form, year, quarter=None):
        """The filing window of one return: `form` for fiscal `year` and
        `quarter` (None for an annual form). None when the calendar has no
        such entry. Independent of the forms selected for reminders."""
        for entry in deadlines_for_year(year):
            if entry['form'] == form and entry['quarter'] == quarter:
                return entry
        return None

    def year_deadlines(self, year):
        """Every filing window of fiscal `year`, all forms, by window end."""
        return deadlines_for_year(year)

    def selected_forms(self):
        return sorted(self._selected_forms())

    # --- Registry functions ------------------------------------------------ #

    def get_functions(self):
        from functions import Function, Param
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
        items = upcoming(PANEL_LOOKAHEAD_DAYS, selected_forms=self._selected_forms())
        if not items:
            return []
        return [{
            'id': 'fiscal_calendar',
            'title': '📅 Fiscal Deadlines',
            'template': 'fiscal_calendar_panel.html',
            'data': {'deadlines': items, 'today': date.today()},
            'order': 12,
        }]

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
            deadlines = [d for d in all_deadlines() if d['form'] in selected]
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
        for entry in all_deadlines(today=today):
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
        return fired

    def _notify(self, entry, days_left):
        message = (f"Modelo {entry['form']} ({entry['period_label']}) filing window "
                   f"closes in {days_left} day(s), on "
                   f"{entry['window_end'].strftime('%d/%m/%Y')}.")
        # Always write the user-visible activity log.
        self.core.log_activity('fiscal_deadline_reminder', 'system', message)
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
