#!/usr/bin/env python3
"""
Fiscal Calendar Module (F3) — Spanish AEAT deadlines + reminders.

CAVEMAN NOTE: local data (calendar_data.py), no external API. Show a dashboard
panel of upcoming filing windows for the forms user actually file, and a daily
scheduler job that shout reminders at T-14/7/1. Reminders ride the `notify`
capability seam (F8 email plug in later) and always write the activity log.
"""

import json
from datetime import date

from flask import Blueprint, render_template

from module_manager import BaseModule

from .calendar_data import (
    CALENDAR_VERSION,
    all_deadlines,
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
        return ('Spanish AEAT filing deadlines (Modelo 303/130/349/111/390/100) '
                'as a dashboard panel with reminders. Local data, no external API.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Fiscal Calendar', 'endpoint': 'fiscal_calendar.calendar_index',
             'icon': '📅', 'group': 'System'}
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
        selected = self._selected_forms()
        checkboxes = []
        for form in available_forms():
            checked = 'checked' if form in selected else ''
            checkboxes.append(
                f'<label style="display:inline-flex;align-items:center;gap:6px;'
                f'margin-right:14px;font-size:14px;">'
                f'<input type="checkbox" name="fiscal_forms" value="{form}" {checked}> '
                f'Modelo {form}</label>')
        return f'''
        <h3>Which forms do you file?</h3>
        <p style="font-size:13px;color:var(--color-text-muted);">
            Only checked forms show on the dashboard and trigger reminders.
            Defaults are inferred from your uploaded tax forms.
        </p>
        <div style="margin:10px 0;">{''.join(checkboxes)}</div>
        <input type="hidden" name="fiscal_calendar_submitted" value="1">
        '''

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
        for entry in all_deadlines():
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
