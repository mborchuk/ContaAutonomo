#!/usr/bin/env python3
"""
Tax Provision Module (G-2) — "how much tax do I owe so far" on the dashboard.

One total and its four parts, each read through the function registry from
whatever module calculates it; this module adds no tax logic:

  - IVA, open quarter      -> tax.draft_303          box 71
    (filing VAT yearly: vat.yearly_return, the year so far less the advances debited)
  - IRPF, open quarter     -> tax.modelo130_boxes    box 07
  - Renta, year to date    -> tax.modelo100_result   cuota diferencial
  - RETA regularización    -> reta.projection        regularization_delta

The Modelo 100 result already credits the year's Modelo 130 payments
(including the open quarter), so the four parts add up without counting the
130 twice. Each part links to its page (when the bundled module provides it)
and shows its next filing date from calendar.deadline_for. A part nothing
provides (or that fails) is left out of the total and named as missing.

Estimate, not tax advice: the panel says so.
"""

from datetime import date

from flask import Blueprint

from contaautonomo.core.module_manager import BaseModule

from .provision import direction, round2, summarise


def _quarter_of(day):
    return (day.month - 1) // 3 + 1


class TaxProvisionModule(BaseModule):
    """Dashboard panel: the tax owed so far, from the existing engines."""

    @property
    def module_id(self):
        return 'tax_provision'

    @property
    def name(self):
        return 'Tax Provision'

    @property
    def description(self):
        return ('Dashboard panel with the tax owed so far: IVA and Modelo 130 of '
                'the open quarter, the renta result for the year to date and the '
                'RETA regularización, each from its own module. Estimate, not '
                'tax advice.')

    @property
    def version(self):
        return '0.1.0'

    # No dependencies: every part is optional and named as missing when its
    # module is not enabled.

    def register_routes(self, app):
        # No pages of its own; the blueprint makes the panel template findable.
        app.register_blueprint(Blueprint('tax_provision', __name__,
                                         template_folder='templates',
                                         url_prefix='/tax-provision'))

    # --- Parts -------------------------------------------------------------- #

    def _available(self, name):
        functions = getattr(self.core, 'functions', None)
        return functions is not None and functions.available(name)

    def _call(self, name, **arguments):
        return self.core.functions.call(name, **arguments)

    def _calendar_available(self):
        return self._available('calendar.deadline_for')

    def _deadline(self, form, year, quarter=None):
        if not self._calendar_available():
            return None
        try:
            return self._call('calendar.deadline_for', form=form, year=year, quarter=quarter)
        except Exception:  # pragma: no cover - defensive
            self.logger.exception('Fiscal calendar lookup failed')
            return None

    def _link_only_to_the_provider(self, part):
        """The page link belongs to the bundled module; a replacement that
        provides the function may not have that page."""
        provider = (self.core.functions.provider(part['function'])
                    if self._available(part['function']) else None)
        if part['endpoint'] and part['endpoint'].split('.')[0] != provider:
            part['endpoint'] = None

    @staticmethod
    def _missing(part, module_name):
        part.update(amount=None, direction=None,
                    reason=f'{module_name} is not enabled')
        return part

    def _failed(self, part, what):
        self.logger.exception('Tax provision: %s failed', what)
        part.update(amount=None, direction=None,
                    reason='could not be calculated — open its page for details')
        return part

    def _iva_yearly_part(self, year):
        """IVA when the owner files VAT yearly (from 2027): the result of the
        year so far minus the advances already debited; None when the owner
        files quarterly or nothing provides `vat.yearly_return`."""
        if not self._available('vat.yearly_return'):
            return None
        try:
            yearly = self._call('vat.yearly_return', year=year)
        except Exception:
            self.logger.exception('Tax provision: yearly VAT return failed')
            return None
        if not yearly.get('yearly'):
            return None
        amount = round2(yearly['due_so_far'])
        return {'id': 'iva', 'label': f'IVA {year} (yearly return)', 'form': '303',
                'function': 'vat.yearly_return', 'endpoint': 'tax_es_forms.vat_yearly_draft',
                'args': {'year': year}, 'deadline': self._deadline('303', year, None),
                'amount': amount, 'direction': direction(amount, 'refund')}

    def _iva_part(self, year, quarter):
        yearly = self._iva_yearly_part(year)
        if yearly is not None:
            return yearly
        part = {'id': 'iva', 'label': f'IVA {quarter}T {year}', 'form': '303',
                'function': 'tax.draft_303', 'endpoint': 'tax_es_forms.draft_303',
                'args': {'year': year, 'quarter': quarter},
                'deadline': self._deadline('303', year, quarter)}
        if not self._available('tax.draft_303'):
            return self._missing(part, 'Spanish Tax Forms (Modelo 303)')
        try:
            amount = round2(self._call('tax.draft_303', year=year,
                                       quarter=quarter)['boxes']['71'])
        except Exception:
            return self._failed(part, 'Modelo 303')
        # A negative 303 is compensated in a later quarter; only Q4 can be refunded.
        part.update(amount=amount,
                    direction=direction(amount, 'refund' if quarter == 4 else 'compensate'))
        return part

    def _m130_part(self, year, quarter):
        part = {'id': 'm130', 'label': f'IRPF Modelo 130 {quarter}T {year}', 'form': '130',
                'function': 'tax.modelo130_boxes', 'endpoint': 'tax_es_irpf.modelo130',
                'args': {'year': year, 'quarter': quarter},
                'deadline': self._deadline('130', year, quarter)}
        if not self._available('tax.modelo130_boxes'):
            return self._missing(part, 'IRPF Estimator')
        try:
            boxes = self._call('tax.modelo130_boxes', year=year, quarter=quarter)['boxes']
            amount = round2(boxes['07'])
        except Exception:
            return self._failed(part, 'Modelo 130')
        part.update(amount=amount, direction=direction(amount))
        return part

    def _renta_part(self, year):
        part = {'id': 'renta', 'label': f'Renta {year} (Modelo 100, year to date)',
                'form': '100', 'function': 'tax.modelo100_result',
                'endpoint': 'tax_es_irpf.modelo100', 'args': {'year': year},
                'deadline': self._deadline('100', year)}
        if not self._available('tax.modelo100_result'):
            return self._missing(part, 'IRPF Estimator')
        try:
            result = self._call('tax.modelo100_result', year=year)
            amount = round2(result['summary']['modelo100_diferencial'])
        except ValueError as e:  # foral region: not modelled
            part.update(amount=None, direction=None, reason=str(e))
            return part
        except Exception:
            return self._failed(part, 'Modelo 100')
        part.update(amount=amount, direction=direction(amount))
        return part

    def _reta_part(self, today):
        part = {'id': 'reta', 'label': f'RETA regularización {today.year}', 'form': None,
                'function': 'reta.projection', 'endpoint': 'reta_advisor.reta_index',
                'args': {}, 'deadline': None,
                'deadline_note': 'Settled by the Seguridad Social after the year, '
                                 'once the income is known'}
        if not self._available('reta.projection'):
            return self._missing(part, 'RETA Quota Advisor')
        try:
            amount = round2(self._call('reta.projection', today=today)['regularization_delta'])
        except Exception:
            return self._failed(part, 'RETA projection')
        part.update(amount=amount, direction=direction(amount))
        return part

    def provision(self, today=None):
        """The panel data for `today` (default: now)."""
        today = today or date.today()
        year, quarter = today.year, _quarter_of(today)
        parts = [
            self._iva_part(year, quarter),
            self._m130_part(year, quarter),
            self._renta_part(year),
            self._reta_part(today),
        ]
        for part in parts:
            self._link_only_to_the_provider(part)
        summary = summarise(parts)
        summary.update(year=year, quarter=quarter, today=today,
                       calendar_enabled=self._calendar_available())
        return summary

    # --- Dashboard ---------------------------------------------------------- #

    def get_dashboard_panels(self):
        try:
            data = self.provision()
        except Exception:  # a broken panel must not break the dashboard
            self.logger.exception('Tax provision panel failed')
            return []
        return [{
            'id': 'tax_provision',
            'title': 'Tax provision',
            'template': 'tax_provision_panel.html',
            'data': data,
            'order': 5,
        }]
