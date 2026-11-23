#!/usr/bin/env python3
"""
Tax Data module — the owner checks the yearly tax tables in the app.

Tax figures that change by year (box numbers, thresholds, RETA brackets) ship
with the modules that use them, keyed by year. When a year has no table of
its own, those modules use the latest earlier one and warn. This module lists
every such table (other modules declare them as `year_table` capabilities),
shows how to check each one against the official source, and lets the owner
confirm it unchanged for the year or enter the new values. The modules ask
for that through `taxdata.year_values`; their warning comes from
`taxdata.year_notice`.

A `year_table` capability:
    {'type': 'year_table', 'id': 'es.threshold_347', 'name': 'Modelo 347 threshold',
     'holds': str, 'steps': (str, ...), 'links': ((label, url), ...),
     'after_check': str,
     'action': year -> {'table_year': int, 'built_in': bool,
                        'fields': [{'key', 'label', 'kind': 'money'|'percent',
                                    'value', 'row'?, 'col'?}],
                        'auto': (ok, message) or None},
     'validate': (year, {key: number}) -> problem or None   # optional}
"""

import json
import logging
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from module_manager import BaseModule
from timeutil import utcnow

logger = logging.getLogger(__name__)

YEAR_MIN, YEAR_MAX = 2000, 2100


class TaxDataModule(BaseModule):
    """Check, confirm and change the yearly tax tables of the other modules."""

    enabled_by_default = True

    @property
    def module_id(self):
        return 'tax_data'

    @property
    def name(self):
        return 'Tax Data'

    @property
    def description(self):
        return ('Yearly tax tables (box numbers, thresholds, RETA brackets): how to check '
                'each one, and confirm it or enter the new values for a year.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [{'label': 'Tax Data', 'endpoint': 'tax_data.tax_data_index',
                 'icon': '🗂️', 'group': 'Taxes', 'order': 70}]

    # --- Model ------------------------------------------------------------- #

    def register_models(self, db):
        self._db = db

        class TaxDataEntry(db.Model):
            """The owner confirmed `table_id` for `year`; `values_json` holds
            the values that differ from the table the app has ({} = unchanged)."""
            __tablename__ = 'tax_data_entry'
            __table_args__ = (db.UniqueConstraint('table_id', 'year'),
                              {'extend_existing': True})
            id = db.Column(db.Integer, primary_key=True)
            table_id = db.Column(db.String(80), nullable=False)
            year = db.Column(db.Integer, nullable=False)
            values_json = db.Column(db.Text, nullable=False, default='{}')
            confirmed_on = db.Column(db.Date, nullable=False)
            updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

        self.Entry = TaxDataEntry
        return {'TaxDataEntry': TaxDataEntry}

    def _entry(self, table_id, year):
        return self.Entry.query.filter_by(table_id=table_id, year=year).first()

    @staticmethod
    def _values(entry):
        try:
            values = json.loads(entry.values_json or '{}')
        except ValueError:
            return {}
        return {k: float(v) for k, v in values.items() if isinstance(v, (int, float))}

    # --- Tables the other modules declare ---------------------------------- #

    def _tables(self):
        mgr = self.core.module_manager
        caps = mgr.find_capabilities('year_table') if mgr else []
        return sorted((c for c in caps if isinstance(c.get('id'), str)),
                      key=lambda c: (c.get('module_name', ''), c.get('name', '')))

    def _table(self, table_id):
        return next((c for c in self._tables() if c['id'] == table_id), None)

    def _info(self, cap, year):
        """What the declaring module has for `year`, or None if it failed."""
        try:
            info = cap['action'](year)
        except Exception:
            logger.exception('year_table %s failed for %s', _for_log(cap.get('id')), _for_log(year))
            return None
        auto = info.get('auto')
        info['auto'] = {'ok': auto[0], 'message': auto[1]} if auto and auto[1] else None
        return info

    # --- Registry functions ------------------------------------------------ #

    def get_functions(self):
        from functions import Function, Param
        table = Param('table', 'str', 'Table id, e.g. es.threshold_347', required=True,
                      max_length=80)
        year = Param('year', 'int', 'Fiscal year', required=True,
                     minimum=YEAR_MIN, maximum=YEAR_MAX)
        return [
            Function('taxdata.year_values', self.year_values,
                     'Whether the owner confirmed a yearly tax table for a year, and the '
                     'values entered for it that differ from the table the app has.',
                     params=(table, year),
                     returns='{"confirmed": bool, "confirmed_on": "YYYY-MM-DD" or null, '
                             '"values": {key: number}}'),
            Function('taxdata.year_notice', self.year_notice,
                     'The warning (HTML) for a page that shows a year with an earlier '
                     "year's table: how to check it, the automatic check, and a link to "
                     'confirm it in Tax Data.',
                     params=(table, year,
                             Param('table_year', 'int', 'Year of the table used', required=True,
                                   minimum=YEAR_MIN, maximum=YEAR_MAX)),
                     returns='{"html": str}'),
        ]

    def year_values(self, table, year):
        entry = self._entry(table, year)
        if entry is None:
            return {'confirmed': False, 'confirmed_on': None, 'values': {}}
        return {'confirmed': True, 'confirmed_on': entry.confirmed_on.isoformat(),
                'values': self._values(entry)}

    def year_notice(self, table, year, table_year):
        cap = self._table(table)
        info = self._info(cap, year) if cap else None
        html = render_template('tax_data_notice.html', cap=cap, info=info, year=year,
                               table_year=table_year,
                               confirm_url=url_for('tax_data.tax_data_index', year=year)
                               + '#' + _anchor(table))
        return {'html': str(html)}

    # --- Page --------------------------------------------------------------- #

    def _rows(self, year):
        """Each table with its state for `year`, as the page shows it."""
        rows = []
        for cap in self._tables():
            info = self._info(cap, year)
            entry = self._entry(cap['id'], year)
            values = self._values(entry) if entry else {}
            fields = [dict(f, value=values.get(f['key'], f['value']),
                           changed=f['key'] in values, built_in_value=f['value'])
                      for f in (info or {}).get('fields', [])]
            rows.append({'cap': cap, 'info': info, 'entry': entry, 'anchor': _anchor(cap['id']),
                         'changed': len(values), 'plain': [f for f in fields if not f.get('row')],
                         'grid': _grid([f for f in fields if f.get('row')])})
        return rows

    def register_routes(self, app):
        bp = Blueprint('tax_data', __name__, template_folder='templates',
                       url_prefix='/tax-data')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def tax_data_index():
            year = request.args.get('year', type=int) or date.today().year
            if not YEAR_MIN <= year <= YEAR_MAX:
                abort(404)
            return render_template('tax_data_index.html', year=year,
                                   rows=module._rows(year),
                                   years=(year - 1, year, year + 1),
                                   this_year=date.today().year)

        @bp.route('/<table_id>/<int:year>', methods=['POST'])
        @login_required
        def tax_data_confirm(table_id, year):
            cap = module._table(table_id)
            info = module._info(cap, year) if cap else None
            if info is None or not YEAR_MIN <= year <= YEAR_MAX:
                abort(404)
            values, problem = _read_values(info['fields'], request.form)
            if problem is None and values and cap.get('validate'):
                try:
                    problem = cap['validate'](year, values)
                except Exception:
                    logger.exception('year_table %s validate failed', _for_log(table_id))
                    problem = 'These values could not be checked.'
            if problem:
                flash(f"{cap['name']}: {problem} Nothing was saved.", 'danger')
            else:
                module._save(table_id, year, values)
                what = (f'saved with {len(values)} changed value(s)' if values
                        else 'confirmed unchanged')
                flash(f"{cap['name']} for {year}: {what}.", 'success')
                module.core.log_activity('tax_data_confirmed', 'tax',
                                         f'{table_id} {year}: {len(values)} changed value(s)')
            return redirect(url_for('tax_data.tax_data_index', year=year)
                            + '#' + _anchor(table_id))

        @bp.route('/<table_id>/<int:year>/undo', methods=['POST'])
        @login_required
        def tax_data_undo(table_id, year):
            entry = module._entry(table_id, year)
            if entry is not None:
                module._db.session.delete(entry)
                module._db.session.commit()
                flash(f'The confirmation for {year} was removed.', 'success')
                module.core.log_activity('tax_data_unconfirmed', 'tax', f'{table_id} {year}')
            return redirect(url_for('tax_data.tax_data_index', year=year)
                            + '#' + _anchor(table_id))

        app.register_blueprint(bp)

    def _save(self, table_id, year, values):
        entry = self._entry(table_id, year)
        if entry is None:
            entry = self.Entry(table_id=table_id, year=year)
            self._db.session.add(entry)
        entry.values_json = json.dumps(values, sort_keys=True)
        entry.confirmed_on = date.today()
        self._db.session.commit()


# --- Helpers ---------------------------------------------------------------- #

def _for_log(value):
    """A value for a log line: line breaks removed (no forged entries)."""
    return str(value).replace('\r', ' ').replace('\n', ' ')[:100]


def _anchor(table_id):
    return 'table-' + table_id.replace('.', '-')


def _grid(fields):
    """Fields with a 'row' and 'col' as {'cols': [...], 'rows': [(label, {col: field})]}."""
    cols, rows = [], {}
    for f in fields:
        if f['col'] not in cols:
            cols.append(f['col'])
        rows.setdefault(f['row'], {})[f['col']] = f
    return {'cols': cols, 'rows': list(rows.items())} if rows else None


def _number(text):
    """'3005,06', '3.005,06', '3,005.06' or '3005.06' → 3005.06; None if not
    a number. The last of ',' and '.' is the decimal separator."""
    text = (text or '').strip().replace(' ', '')
    if ',' in text and text.rfind(',') > text.rfind('.'):
        text = text.replace('.', '').replace(',', '.')
    else:
        text = text.replace(',', '')
    try:
        return float(text)
    except ValueError:
        return None


def _read_values(fields, form):
    """({key: number} that differ from the table the app has, problem or None)."""
    values = {}
    for f in fields:
        raw = form.get('v_' + f['key'])
        if raw is None:
            continue
        number = _number(raw)
        if number is None or number < 0:
            return {}, f"{f['label']}: enter a number."
        if f.get('kind') == 'percent' and number > 100:
            return {}, f"{f['label']}: a percentage is at most 100."
        if round(number, 2) != round(float(f['value']), 2):
            values[f['key']] = round(number, 2)
    return values, None
