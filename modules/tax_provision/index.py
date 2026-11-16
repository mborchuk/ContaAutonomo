#!/usr/bin/env python3
"""
Tax Provision Module (G-2) — "how much tax do I owe so far" on the dashboard.

One total and its four parts, each read from the module that already
calculates it; this module adds no tax logic:

  - IVA, open quarter      -> tax_es_forms.draft_303()      box 71
  - IRPF, open quarter     -> tax_es_irpf.modelo130_boxes()  box 07
  - Renta, year to date    -> tax_es_irpf.modelo100_result() cuota diferencial
  - RETA regularización    -> reta_advisor.projection()      regularization_delta

The Modelo 100 result already credits the year's Modelo 130 payments
(including the open quarter), so the four parts add up without counting the
130 twice. Each part links to its own page and shows its next filing date
from the Fiscal Calendar. A part whose module is disabled (or that fails) is
left out of the total and named as missing.

Estimate, not tax advice: the panel says so.
"""

from datetime import date

from flask import Blueprint

from module_manager import BaseModule

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

    def _provider(self, module_id):
        mm = getattr(self.core, 'module_manager', None)
        return mm.provider_of(module_id) if mm else None

    def _deadline(self, calendar, form, year, quarter=None):
        if calendar is None:
            return None
        try:
            return calendar.deadline_for(form, year, quarter)
        except Exception:  # pragma: no cover - defensive
            self.logger.exception('Fiscal calendar lookup failed')
            return None

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

    def _iva_part(self, year, quarter, calendar):
        part = {'id': 'iva', 'label': f'IVA {quarter}T {year}', 'form': '303',
                'endpoint': 'tax_es_forms.draft_303',
                'args': {'year': year, 'quarter': quarter},
                'deadline': self._deadline(calendar, '303', year, quarter)}
        forms = self._provider('tax_es_forms')
        if forms is None:
            return self._missing(part, 'Spanish Tax Forms (Modelo 303)')
        try:
            amount = round2(forms.draft_303(year, quarter)['boxes']['71'])
        except Exception:
            return self._failed(part, 'Modelo 303')
        # A negative 303 is compensated in a later quarter; only Q4 can be refunded.
        part.update(amount=amount,
                    direction=direction(amount, 'refund' if quarter == 4 else 'compensate'))
        return part

    def _m130_part(self, year, quarter, calendar):
        part = {'id': 'm130', 'label': f'IRPF Modelo 130 {quarter}T {year}', 'form': '130',
                'endpoint': 'tax_es_irpf.modelo130',
                'args': {'year': year, 'quarter': quarter},
                'deadline': self._deadline(calendar, '130', year, quarter)}
        irpf = self._provider('tax_es_irpf')
        if irpf is None:
            return self._missing(part, 'IRPF Estimator')
        try:
            boxes, _rate = irpf.modelo130_boxes(year, quarter)
            amount = round2(boxes['07'])
        except Exception:
            return self._failed(part, 'Modelo 130')
        part.update(amount=amount, direction=direction(amount))
        return part

    def _renta_part(self, year, calendar):
        part = {'id': 'renta', 'label': f'Renta {year} (Modelo 100, year to date)',
                'form': '100', 'endpoint': 'tax_es_irpf.modelo100', 'args': {'year': year},
                'deadline': self._deadline(calendar, '100', year)}
        irpf = self._provider('tax_es_irpf')
        if irpf is None:
            return self._missing(part, 'IRPF Estimator')
        try:
            result = irpf.modelo100_result(year)
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
                'endpoint': 'reta_advisor.reta_index', 'args': {}, 'deadline': None,
                'deadline_note': 'Settled by the Seguridad Social after the year, '
                                 'once the income is known'}
        reta = self._provider('reta_advisor')
        if reta is None:
            return self._missing(part, 'RETA Quota Advisor')
        try:
            amount = round2(reta.projection(today)['regularization_delta'])
        except Exception:
            return self._failed(part, 'RETA projection')
        part.update(amount=amount, direction=direction(amount))
        return part

    def provision(self, today=None):
        """The panel data for `today` (default: now)."""
        today = today or date.today()
        year, quarter = today.year, _quarter_of(today)
        calendar = self._provider('fiscal_calendar')
        parts = [
            self._iva_part(year, quarter, calendar),
            self._m130_part(year, quarter, calendar),
            self._renta_part(year, calendar),
            self._reta_part(today),
        ]
        summary = summarise(parts)
        summary.update(year=year, quarter=quarter, today=today,
                       calendar_enabled=calendar is not None)
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
