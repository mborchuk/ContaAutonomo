"""D-1 — IVA columns, totals and the reverse-charge note in the report PDF."""
import importlib.util
import io
import os
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from modules.tax_es_forms.calculator import invoice_vat_line

TEMPLATE = os.path.join(os.path.dirname(__file__), '..', 'modules', 'reports',
                        'report_templates', 'official_template.py')
SETTINGS = SimpleNamespace(business_name='Test SL', owner_name='Owner',
                           vat_number='B1', nie_number='X1', address='Calle 1',
                           postal_code='12006', city='Castellón',
                           email='owner@example.com', phone='')


def _template():
    spec = importlib.util.spec_from_file_location('official_template_iva', TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _income(number, amount, rate, vat, reverse=False, currency='EUR'):
    return {'invoice_number': number, 'invoice_date': '01/02/2026',
            'client_name': 'Client', 'amount': amount, 'currency': currency,
            'amount_eur': amount, 'status': 'issued', 'vat_rate_pct': rate,
            'vat_eur': vat, 'reverse_charge': reverse}


def _expense(number, amount, rate, vat, reverse=False):
    return {'expense_date': '01/03/2026', 'invoice_number': number,
            'contractor_name': 'Contractor', 'category': 'Services',
            'description': 'Service', 'amount_eur': amount, 'vat_rate_pct': rate,
            'vat_eur': vat, 'reverse_charge': reverse}


def _render(income, expenses, currency_mode='base'):
    data = {
        'report_type': 'Financial Report', 'period_text': 'Q1 2026',
        'currency_mode': currency_mode, 'base_currency': 'EUR',
        'show_income': True, 'show_expenses': True, 'show_summary': True,
        'income_data': income, 'expenses_data': expenses,
        'ss_data': [], 'extra_sections': [],
    }
    buf = io.BytesIO()
    _template().generate_report(buf, data, SETTINGS)
    return '\n'.join(p.extract_text() or '' for p in PdfReader(buf).pages)


@pytest.mark.parametrize('mode', ['base', 'original'])
def test_iva_columns_totals_and_note(mode):
    text = _render(
        [_income('INC-A', 100.0, 21.0, 21.0),
         _income('INC-B', 200.0, 0.0, 0.0, reverse=True)],
        [_expense('EXP-A', 121.0, 21.0, 21.0),
         _expense('EXP-B', 50.0, None, 0.0, reverse=True)],
        currency_mode=mode)
    assert text.count('IVA %') == 2
    assert '21%' in text and 'ISP' in text
    assert '€ 21.00' in text          # income IVA total and expense IVA total
    assert 'Artículo 194 Directiva 2006/112/EC' in text


def test_no_note_without_reverse_charge_rows():
    text = _render([_income('INC-C', 100.0, 21.0, 21.0)],
                   [_expense('EXP-C', 121.0, 21.0, 21.0)])
    assert 'IVA %' in text
    assert 'Artículo 194' not in text


def test_iva_totals_sum_rows():
    text = _render([_income('INC-D', 100.0, 21.0, 21.0),
                    _income('INC-E', 50.0, 10.0, 5.0)],
                   [_expense('EXP-D', 10.0, None, None)])
    assert '€ 26.00' in text          # 21 + 5
    assert '–' in text                # expense without VAT data


# --- invoice_vat_line --------------------------------------------------------

def _inv(**kw):
    base = dict(status='issued', amount_eur=100.0, snap_vat_rate=None,
                snap_vat_amount=None, snap_taxable_base=None, snap_customer=None,
                customer=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_vat_line_uses_snapshot():
    line = invoice_vat_line(_inv(snap_vat_rate=10.0, snap_vat_amount=10.0,
                                 snap_customer='{"tax_type": "standard"}'), 0.21)
    assert line == {'vat_rate_pct': 10.0, 'vat_eur': 10.0, 'reverse_charge': False}


def test_vat_line_legacy_standard_derives_rate():
    line = invoice_vat_line(_inv(customer=SimpleNamespace(tax_type='standard')), 0.21)
    assert line['vat_rate_pct'] == pytest.approx(21.0)
    assert line['vat_eur'] == pytest.approx(21.0)


def test_vat_line_eu_b2b_is_reverse_charge():
    line = invoice_vat_line(_inv(snap_vat_rate=0.0, snap_vat_amount=0.0,
                                 snap_customer='{"tax_type": "eu_b2b"}'), 0.21)
    assert line == {'vat_rate_pct': 0.0, 'vat_eur': 0.0, 'reverse_charge': True}
