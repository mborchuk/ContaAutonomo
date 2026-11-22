#!/usr/bin/env python3
"""
Tax ES Forms Module (F5) — Modelo 303 / 130 draft calculator.

This module does not file anything and gives no tax advice. It adds up the
figures already in the app and lays them out in the boxes of Modelo 303 (IVA)
and Modelo 130 (IRPF pago fraccionado), so the user can copy them into the
AEAT portal. Every page states "estimate, not tax advice".

Pure math live in calculator.py; box numbers live in boxes.py (versioned).
This file only fetch data and render.
"""

from datetime import datetime

from flask import Blueprint, render_template, abort

from module_manager import BaseModule

from .boxes import (
    BOX_TABLE_VERSION,
    MODELO_390_ORDER,
    MODELO_390_OUT_OF_SCOPE,
    MODELO_347_EXCLUSIONS,
    MODELO_347_OUT_OF_SCOPE,
    boxes_for_year,
    modelo_390_boxes_for_year,
    modelo_347_rules_for_year,
)
from .supplier_region import reverse_charge_check, reverse_charge_problem
from .output_vat import invoice_vat_line
from .calculator import (
    compute_modelo_303,
    compute_modelo_130,
    compute_modelo_347,
    compute_modelo_349,
    compute_modelo_390,
    quarter_of,
)


class TaxEsFormsModule(BaseModule):
    """Modelo 303/130 quarterly draft calculator (estimate only)."""

    @property
    def module_id(self):
        return 'tax_es_forms'

    @property
    def name(self):
        return 'Modelo 303/130 Drafts'

    @property
    def description(self):
        return ('Quarterly Modelo 303 (IVA) and 130 (IRPF pago fraccionado) '
                'box-level drafts from your invoices and expenses. Estimate, '
                'not tax advice.')

    @property
    def version(self):
        return '0.1.0'

    @property
    def dependencies(self):
        # Expenses feed 303; the filed returns (Tax Management) carry the VAT
        # credit; Modelo 130 and the equipment rule come from the IRPF Estimator
        # (owner decision 2026-10-03).
        return ['expenses', 'tax.filed_boxes', 'tax.modelo130_boxes', 'tax.equipment_rule']

    @property
    def nav_items(self):
        return [
            {'label': 'Tax Drafts', 'endpoint': 'tax_es_forms.drafts_index',
             'icon': '🧮', 'group': 'Taxes', 'order': 30}
        ]

    def register_models(self, db):
        # No own tables — pure read/aggregate over core Invoice + Expense.
        self._db = db
        return {}

    # --- Rates & currency helpers ---------------------------------------- #

    def _rates(self):
        """Return (vat_rate, irpf_rate) as fractions, from Settings."""
        settings = self.core.get_settings()
        vat = (getattr(settings, 'default_vat_rate', None) or 21.0) / 100.0
        irpf = (getattr(settings, 'default_irpf_rate', None) or 20.0) / 100.0
        return vat, irpf

    def _convert_to_eur(self, amount, from_currency, when=None):
        """Convert an amount to EUR via the shared CurrencyService.

        Fall back to the raw amount (logged) when no rate is available, so a
        draft is never silently blank.
        """
        if not amount or from_currency == 'EUR':
            return amount or 0.0
        date_str = when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else None
        try:
            converted, _rate, _actual = self.core.currency_service.convert(
                amount, from_currency, 'EUR', date_str)
            if converted is not None:
                return converted
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('Currency conversion %s->EUR failed: %s',
                                from_currency, e)
        self.logger.warning('No EUR rate for %s; using unconverted amount',
                            from_currency)
        return amount

    # --- Data fetching ---------------------------------------------------- #

    def _all_invoices(self):
        """Core invoices with the customer relationship intact."""
        return self.core.invoice_service.get_all()

    def _all_expenses(self):
        from app import Expense  # core model, avoids mapper duplication
        return Expense.query.all()

    def _invoices_in(self, year, quarter=None, up_to_quarter=None):
        out = []
        for inv in self._all_invoices():
            d = inv.invoice_date
            if not d or d.year != year:
                continue
            q = quarter_of(d)
            if quarter is not None and q != quarter:
                continue
            if up_to_quarter is not None and q > up_to_quarter:
                continue
            out.append(inv)
        return out

    def _expenses_in(self, year, quarter=None, up_to_quarter=None):
        out = []
        for exp in self._all_expenses():
            d = exp.expense_date
            if not d or d.year != year:
                continue
            q = quarter_of(d)
            if quarter is not None and q != quarter:
                continue
            if up_to_quarter is not None and q > up_to_quarter:
                continue
            out.append(exp)
        return out

    def _ytd_totals(self, year, up_to_quarter):
        """(income_eur, expenses_eur) cumulative through up_to_quarter.

        Expenses use the VAT-excluded net when F4 data is present (matches the
        130 engine), else gross — so the prior-payments estimate is consistent.
        """
        income = sum(inv.amount_eur or 0.0
                     for inv in self._invoices_in(year, up_to_quarter=up_to_quarter)
                     if getattr(inv, 'status', None) != 'cancelled')
        expenses = 0.0
        for exp in self._expenses_in(year, up_to_quarter=up_to_quarter):
            base = exp.net_amount if getattr(exp, 'net_amount', None) is not None else exp.amount
            expenses += self._convert_to_eur(base, exp.currency, exp.expense_date)
        return income, expenses

    # --- Computation entry points (shared by pages + API) ---------------- #

    # Without a tax.equipment_rule provider: category "Equipment", 300 EUR net or more.
    _DEFAULT_EQUIPMENT_RULE = {'threshold': 300.0, 'categories': ['equipment']}

    def _function(self, name):
        """The registry, if something provides `name`; else None."""
        functions = getattr(self.core, 'functions', None)
        return functions if functions is not None and functions.available(name) else None

    def _is_equipment(self):
        """fn(expense, net_eur) -> bool for Modelo 303 boxes 30/31."""
        functions = self._function('tax.equipment_rule')
        rule = (functions.call('tax.equipment_rule') if functions
                else self._DEFAULT_EQUIPMENT_RULE)
        threshold, categories = rule['threshold'], set(rule['categories'])

        def is_equipment(exp, net_eur):
            return net_eur >= threshold and (exp.category or '').strip().lower() in categories
        return is_equipment

    def _carried_forward(self, year, quarter):
        """VAT credit carried into this quarter (box 110), from the filed
        Modelo 303 of the previous quarter: its box 87 plus its negative
        result when compensated (no refund requested in box 73)."""
        prev_year, prev_quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
        functions = self._function('tax.filed_boxes')
        boxes = functions.call('tax.filed_boxes', form_type='303', year=prev_year,
                               quarter=prev_quarter) if functions else {}
        if not boxes:
            return 0.0, None
        carried = boxes.get('87', 0.0)
        if boxes.get('71', 0.0) < 0 and '73' not in boxes:
            carried += -boxes['71']
        return carried, f'filed Modelo 303 {prev_quarter}T {prev_year}'

    def _draft_303(self, year, quarter):
        vat_rate, _ = self._rates()
        invoices = self._invoices_in(year, quarter=quarter)
        expenses = self._expenses_in(year, quarter=quarter)
        carried, carried_from = self._carried_forward(year, quarter)
        result = compute_modelo_303(
            invoices, expenses, vat_rate, convert_expense=self._convert_to_eur,
            is_equipment=self._is_equipment(), carried_forward=carried)
        result['meta']['carried_from'] = carried_from
        result['meta']['checks'] = self._data_checks(invoices, expenses)
        result['labels'] = self._box_labels(year, '303')
        result.update(self._table_info(year))
        result['year'] = year
        result['quarter'] = quarter
        return result

    def draft_303(self, year, quarter):
        """Modelo 303 draft for one quarter, as shown on its page: box 71 is
        the result (> 0 to pay, < 0 to compensate or refund)."""
        return self._draft_303(year, quarter)

    def _draft_349(self, year, quarter):
        result = compute_modelo_349(self._invoices_in(year, quarter=quarter))
        result['year'] = year
        result['quarter'] = quarter
        result.update(self._table_info(year))
        result['box_59'] = self._draft_303(year, quarter)['boxes']['59']
        return result

    # Categories that never carry VAT (exempt or not a purchase).
    _NO_VAT_CATEGORIES = {'insurance', 'social security'}

    def _data_checks(self, invoices, expenses):
        """Things in the data the owner should look at before filing."""
        checks = []
        unpaid = [i for i in invoices if getattr(i, 'status', None) not in ('paid', 'cancelled')]
        if unpaid:
            checks.append(
                f'{len(unpaid)} invoice(s) of this quarter are not marked paid — '
                'they are still counted (invoice date): '
                + ', '.join(i.invoice_number for i in unpaid))
        unmarked = [e for e in expenses
                    if e.vat_amount == 0 and not getattr(e, 'reverse_charge', None)
                    and (e.category or '').strip().lower() not in self._NO_VAT_CATEGORIES]
        if unmarked:
            checks.append(
                f'{len(unmarked)} expense(s) without Spanish VAT are not marked '
                '"Reverse charge" — if the supplier is outside Spain, mark them: '
                + ', '.join((e.description or '?').splitlines()[0][:40] for e in unmarked))
        wrong_region = [e for e in expenses if reverse_charge_problem(e)]
        if wrong_region:
            checks.append(
                f'{len(wrong_region)} expense(s) are marked reverse charge for a region '
                "that does not match the supplier's country or VAT number (boxes 10/11 "
                'are EU suppliers, 12/13 suppliers outside the EU) — check them: '
                + ', '.join(f'{(getattr(e.contractor, "name", None) or "?")[:30]} '
                            f'({e.expense_date:%d/%m/%Y})' for e in wrong_region))
        return checks

    def _draft_130(self, year, quarter):
        # One owner for Modelo 130 (owner decision 2026-10-03): when something
        # provides tax.modelo130_boxes (the IRPF Estimator) its figures are shown.
        functions = self._function('tax.modelo130_boxes')
        if functions is not None:
            m130 = functions.call('tax.modelo130_boxes', year=year, quarter=quarter)
            return {
                'form': '130', 'boxes': m130['boxes'], 'irpf_rate': m130['rate'],
                'breakdown': m130.get('breakdown'),
                'labels': self._box_labels(year, '130'), 'year': year, 'quarter': quarter,
                **self._table_info(year),
                'meta': {'basis': 'cumulative_ytd', 'source': 'tax_es_irpf'},
            }
        _, irpf_rate = self._rates()
        invoices_ytd = self._invoices_in(year, up_to_quarter=quarter)
        expenses_ytd = self._expenses_in(year, up_to_quarter=quarter)
        prior_income, prior_expenses = (
            self._ytd_totals(year, quarter - 1) if quarter > 1 else (0.0, 0.0))
        result = compute_modelo_130(
            invoices_ytd, expenses_ytd, irpf_rate,
            prior_income=prior_income, prior_expenses=prior_expenses,
            convert_expense=self._convert_to_eur)
        result['labels'] = self._box_labels(year, '130')
        result['meta']['source'] = 'tax_es_forms'
        result['year'] = year
        result['quarter'] = quarter
        result.update(self._table_info(year))
        return result

    def _draft_390(self, year):
        """Annual VAT summary: the four quarterly 303 drafts added up."""
        from year_tables import fallback_note
        quarters = [self._draft_303(year, q) for q in (1, 2, 3, 4)]
        result = compute_modelo_390([q['boxes'] for q in quarters])
        table_year, labels, fallback = modelo_390_boxes_for_year(year)
        checks = []
        other_rates = sorted({
            inv.snap_vat_rate for inv in self._invoices_in(year)
            if getattr(inv, 'status', None) != 'cancelled'
            and getattr(inv, 'snap_vat_rate', None) not in (None, 0, 0.0, 21, 21.0)})
        if other_rates:
            checks.append(
                'Invoices at VAT rates other than 21 % ('
                + ', '.join(f'{r:g} %' for r in other_rates)
                + ') are included in casillas 05/06 — move them to the casillas '
                'for their rate (01–04) before filing.')
        for q in quarters:
            for check in q['meta'].get('checks', []):
                checks.append(f"Q{q['quarter']}: {check}")
        result.update({
            'year': year, 'labels': labels, 'order': MODELO_390_ORDER,
            'out_of_scope': MODELO_390_OUT_OF_SCOPE,
            'quarters': [{'quarter': q['quarter'], 'boxes': q['boxes']} for q in quarters],
            'table_year': table_year, 'table_fallback': fallback,
            'table_note': fallback_note(table_year) if fallback else '',
            'meta': {'checks': checks, 'basis': 'sum of quarterly 303 drafts'},
        })
        return result

    def _deadline(self, form, year, quarter=None):
        """The filing window from `calendar.deadline_for`, or None without a provider."""
        functions = self._function('calendar.deadline_for')
        if functions is None:
            return None
        try:
            return functions.call('calendar.deadline_for', form=form, year=year, quarter=quarter)
        except Exception:  # pragma: no cover - defensive
            self.logger.exception('Fiscal calendar lookup failed')
            return None

    def _draft_347(self, year):
        """Annual operations with third parties above the threshold."""
        from year_tables import fallback_note
        vat_rate, _ = self._rates()
        table_year, rules, fallback = modelo_347_rules_for_year(year)
        result = compute_modelo_347(
            self._invoices_in(year), self._expenses_in(year), vat_rate,
            rules['threshold'], convert_expense=self._convert_to_eur)
        deadline = self._deadline('347', year)
        from .aeat_files import residence
        file_warnings = [w for w in (residence(line)[2] for line in result['lines']) if w]
        result.update({
            'year': year, 'source': rules['source'], 'file_warnings': file_warnings,
            'exclusion_labels': MODELO_347_EXCLUSIONS,
            'out_of_scope': MODELO_347_OUT_OF_SCOPE,
            'deadline': deadline,
            'table_year': table_year, 'table_fallback': fallback,
            'table_note': fallback_note(table_year) if fallback else '',
        })
        return result

    # --- AEAT import files (G-5) ------------------------------------------ #

    def aeat_file(self, form, year, quarter=None):
        """(text, filename, info) of the AEAT import file for one draft.
        Raises aeat_files.UnsupportedYear when no official layout covers the
        exercise."""
        from . import aeat_files
        settings = self.core.get_settings()
        if form == '303':
            content, info = aeat_files.modelo_303(self._draft_303(year, quarter), settings)
        elif form == '130':
            content, info = aeat_files.modelo_130(self._draft_130(year, quarter), settings)
        elif form == '349':
            content, info = aeat_files.modelo_349(self._draft_349(year, quarter), settings)
        elif form == '390':
            content, info = aeat_files.modelo_390(
                self._draft_390(year), settings,
                files_347=bool(self._draft_347(year)['lines']))
        elif form == '347':
            content, info = aeat_files.modelo_347(self._draft_347(year), settings)
        else:
            raise ValueError(f'no AEAT file for Modelo {form}')
        period = f'_{quarter}T' if quarter else ''
        return content, f'{form}_{year}{period}.txt', info

    def _aeat_file_response(self, form, year, quarter):
        from flask import Response, redirect, url_for
        from .aeat_files import UnsupportedYear, encode
        try:
            content, filename, _info = self.aeat_file(form, year, quarter)
        except UnsupportedYear as e:
            self.core.flash(str(e), 'warning')
            return redirect(url_for('tax_es_forms.drafts_index'))
        except ValueError:
            # No request values in the log line (log injection); the traceback
            # names the model.
            self.logger.exception('AEAT file generation failed')
            self.core.flash(f'The Modelo {form} file could not be generated. Check the '
                            'draft (a negative amount in a field that cannot be negative).',
                            'danger')
            return redirect(url_for('tax_es_forms.drafts_index'))
        self.core.log_activity('aeat_file_downloaded', 'tax',
                               {'form': form, 'year': year, 'quarter': quarter})
        return Response(encode(content), mimetype='text/plain; charset=iso-8859-1',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @staticmethod
    def _box_labels(year, form):
        _table_year, tables, _fallback = boxes_for_year(year)
        return tables[form]

    @staticmethod
    def _table_info(year):
        """Box table metadata for a draft: version, the table year used, and
        whether it is a fallback from an earlier year (shown as a warning)."""
        from year_tables import fallback_note
        table_year, _tables, fallback = boxes_for_year(year)
        return {'table_version': BOX_TABLE_VERSION, 'table_year': table_year,
                'table_fallback': fallback,
                'table_note': fallback_note(table_year) if fallback else ''}

    def _available_years(self):
        return self.core.data_years()

    # --- Routes ----------------------------------------------------------- #

    def register_routes(self, app):
        bp = Blueprint('tax_es_forms', __name__,
                       template_folder='templates',
                       url_prefix='/tax-forms-draft')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def drafts_index():
            now = datetime.now()
            return render_template(
                'tax_forms_draft_index.html',
                years=module._available_years(),
                current_year=now.year,
                current_quarter=quarter_of(now.date()),
                table_version=BOX_TABLE_VERSION)

        @bp.route('/303/<int:year>/<int:quarter>')
        @login_required
        def draft_303(year, quarter):
            if quarter not in (1, 2, 3, 4):
                abort(404)
            draft = module._draft_303(year, quarter)
            return render_template('tax_forms_draft_303.html', draft=draft)

        @bp.route('/130/<int:year>/<int:quarter>')
        @login_required
        def draft_130(year, quarter):
            if quarter not in (1, 2, 3, 4):
                abort(404)
            draft = module._draft_130(year, quarter)
            return render_template('tax_forms_draft_130.html', draft=draft)

        @bp.route('/390/<int:year>')
        @login_required
        def draft_390(year):
            return render_template('tax_forms_draft_390.html', draft=module._draft_390(year))

        # G-5 — AEAT import files (not validated; see AEAT_FILES.md).
        @bp.route('/<form>/<int:year>/<int:quarter>/aeat-file')
        @login_required
        def aeat_file_quarterly(form, year, quarter):
            if form not in ('303', '130', '349') or quarter not in (1, 2, 3, 4):
                abort(404)
            return module._aeat_file_response(form, year, quarter)

        @bp.route('/<form>/<int:year>/aeat-file')
        @login_required
        def aeat_file_annual(form, year):
            if form not in ('390', '347'):
                abort(404)
            return module._aeat_file_response(form, year, None)

        @bp.route('/347/<int:year>')
        @login_required
        def draft_347(year):
            return render_template('tax_forms_draft_347.html', draft=module._draft_347(year))

        @bp.route('/349/<int:year>/<int:quarter>')
        @login_required
        def draft_349(year, quarter):
            if quarter not in (1, 2, 3, 4):
                abort(404)
            draft = module._draft_349(year, quarter)
            return render_template('tax_forms_draft_349.html', draft=draft)

        app.register_blueprint(bp)

    # --- Registry functions ---------------------------------------------- #

    def get_functions(self):
        from functions import Function, Param
        quarter = (Param('year', 'int', 'Tax year', required=True, minimum=2000, maximum=2100),
                   Param('quarter', 'int', 'Quarter, 1-4', required=True, minimum=1, maximum=4))
        draft = dict(params=quarter, assistant=True, record_kind='draft',
                     assistant_fields=('form', 'year', 'quarter', 'boxes'))
        return [
            Function('vat.reverse_charge_check', self._reverse_charge_check,
                     'Whether a reverse-charge mark ("eu": supplier in another EU country, '
                     '"non_eu": outside the EU) matches where the supplier is, from its '
                     'country or VAT number prefix. "problem" is a sentence for the owner, '
                     'or null when it matches or the region is unknown.',
                     params=(Param('mark', 'str', 'Reverse-charge mark', required=True,
                                   choices=('eu', 'non_eu')),
                             Param('country', 'str', "Supplier's country"),
                             Param('vat_number', 'str', "Supplier's VAT number", max_length=40),
                             Param('supplier', 'str', "Supplier's name")),
                     returns='{"problem": str or null}'),
            Function('tax.draft_303', self.draft_303,
                     'Modelo 303 (IVA) draft boxes for one quarter. Needs year and quarter (1-4).',
                     returns='{"form": "303", "year": int, "quarter": int, '
                             '"boxes": {box: amount}, ...}', **draft),
            Function('tax.draft_130', self.draft_130,
                     'Modelo 130 (IRPF) draft boxes for one quarter. Needs year and quarter (1-4).',
                     returns='{"form": "130", "year": int, "quarter": int, '
                             '"boxes": {box: amount}, ...}', **draft),
            Function('vat.invoice_lines', self.invoice_vat_lines,
                     'Output VAT (IVA repercutido) of each invoice dated in a '
                     'period, cancelled ones included: rate, amount and whether '
                     'the customer accounts for it (intra-EU reverse charge).',
                     params=(Param('date_from', 'date', 'First invoice date', required=True),
                             Param('date_to', 'date', 'Last invoice date', required=True)),
                     returns='{"lines": {"<invoice id>": {"vat_rate_pct": float, '
                             '"vat_eur": float, "reverse_charge": bool}}}'),
        ]

    @staticmethod
    def _reverse_charge_check(mark, country=None, vat_number=None, supplier=None):
        return {'problem': reverse_charge_check(mark, country, vat_number, supplier)}

    def invoice_vat_lines(self, date_from, date_to):
        """Output VAT of each invoice dated date_from..date_to, keyed by the
        invoice id as text. Issued invoices use the snapshot frozen at issue;
        drafts and legacy invoices the current rate (Settings)."""
        vat_rate, _irpf = self._rates()
        lines = {}
        for inv in self._all_invoices():
            d = inv.invoice_date
            if d and date_from <= d <= date_to:
                lines[str(inv.id)] = invoice_vat_line(inv, vat_rate)
        return {'lines': lines}

    def draft_130(self, year, quarter):
        """Modelo 130 draft for one quarter, as shown on its page."""
        return self._draft_130(year, quarter)

    # --- REST API (F5-D4) ------------------------------------------------- #

    def get_api_routes(self):
        return [
            {'path': 'draft/303/<int:year>/<int:quarter>', 'methods': ['GET'],
             'handler': self._api_draft_303,
             'summary': 'Modelo 303 (IVA) draft boxes for a quarter'},
            {'path': 'draft/349/<int:year>/<int:quarter>', 'methods': ['GET'],
             'handler': self._api_draft_349,
             'summary': 'Modelo 349 (intra-EU operations) draft for a quarter'},
            {'path': 'draft/130/<int:year>/<int:quarter>', 'methods': ['GET'],
             'handler': self._api_draft_130,
             'summary': 'Modelo 130 (IRPF) draft boxes for a quarter'},
            {'path': 'draft/347/<int:year>', 'methods': ['GET'],
             'handler': self._api_draft_347,
             'summary': 'Modelo 347 (operations with third parties) check for a year'},
        ]

    def _api_draft_347(self, request, year=None):
        draft = self._draft_347(year)
        draft.pop('exclusion_labels', None)
        draft.pop('file_warnings', None)
        if draft.get('deadline'):
            draft['deadline'] = {k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                                 for k, v in draft['deadline'].items()}
        return draft, 200

    def _api_validate_quarter(self, quarter):
        from api_errors import ApiError
        if quarter not in (1, 2, 3, 4):
            raise ApiError(400, 'bad_request', 'quarter must be 1-4')

    def _api_draft_303(self, request, year=None, quarter=None):
        self._api_validate_quarter(quarter)
        return self._draft_303(year, quarter), 200

    def _api_draft_349(self, request, year=None, quarter=None):
        self._api_validate_quarter(quarter)
        return self._draft_349(year, quarter), 200

    def _api_draft_130(self, request, year=None, quarter=None):
        self._api_validate_quarter(quarter)
        return self._draft_130(year, quarter), 200

    # --- Dashboard Tax Obligations integration (F5-D4) ------------------- #

    def get_tax_obligations(self, context):
        """Show the current-quarter running 303/130 position on the panel.

        Display-only: deductions/tax_total stay 0 so this never disturbs the
        panel's grand total (the expenses/tax modules own that). Single source
        of truth for the figures is the same engine the draft pages use.
        """
        now = datetime.now()
        year = now.year
        quarter = quarter_of(now.date())
        symbol = context.get('currency_symbol', '€')

        d303 = self._draft_303(year, quarter)
        d130 = self._draft_130(year, quarter)
        iva_result = d303['boxes']['71']
        irpf_result = d130['boxes']['07']

        notes = [
            f"Modelo 303 Q{quarter} {year} (estimate): {symbol}{iva_result:.2f} — "
            f"Modelo 130 Q{quarter} {year} (estimate): {symbol}{irpf_result:.2f}. "
            f"Draft only, not tax advice."
        ]
        missing = d303['meta']['missing_expense_vat_count']
        if missing:
            notes.append(
                f"{missing} expense(s) this quarter lack VAT data — deductible "
                f"IVA understated until expense VAT breakdown is captured.")

        return {
            'summary_columns': [
                {'label': f'IVA Q{quarter} (303)', 'value': iva_result},
                {'label': f'IRPF Q{quarter} (130)', 'value': irpf_result},
            ],
            'breakdown_rows': [],
            'notes': notes,
            'deductions': 0,
            'tax_total': 0,
        }
