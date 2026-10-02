"""Report PDF pagination: a section heading or table header row must never be
left alone at the bottom of a page — it moves to the page holding its rows."""
import importlib.util
import io
import os
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

TEMPLATE = os.path.join(os.path.dirname(__file__), '..', 'modules', 'reports',
                        'report_templates', 'official_template.py')


def _load_template():
    spec = importlib.util.spec_from_file_location('official_template', TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SETTINGS = SimpleNamespace(business_name='Test SL', owner_name='Owner',
                           vat_number='B1', nie_number='X1', address='Calle 1',
                           postal_code='12006', city='Castellón',
                           email='owner@example.com', phone='')


def _render(income_rows):
    report_data = {
        'report_type': 'Financial Report', 'period_text': 'Full Year 2026',
        'currency_mode': 'base', 'base_currency': 'EUR',
        'show_income': True, 'show_expenses': True, 'show_summary': True,
        'income_data': [{
            'invoice_number': f'INC{i:03d}', 'invoice_date': '01/02/2026',
            'client_name': 'Client', 'amount': 100.0, 'currency': 'EUR',
            'amount_eur': 100.0, 'status': 'paid',
        } for i in range(income_rows)],
        'expenses_data': [{
            'expense_date': '01/03/2026', 'invoice_number': f'EXPROW{i}',
            'contractor_name': 'Contractor', 'category': 'Services',
            'description': 'Service', 'amount_eur': 10.0,
        } for i in range(3)],
        'ss_data': [], 'extra_sections': [],
    }
    buffer = io.BytesIO()
    _load_template().generate_report(buffer, report_data, SETTINGS)
    return [page.extract_text() or '' for page in PdfReader(buffer).pages]


def _page_with(pages, text):
    return next(i for i, page in enumerate(pages) if text in page)


@pytest.mark.parametrize('income_rows', range(20, 52))
def test_heading_never_orphaned_from_rows(income_rows):
    """Sweep the income table length so the following headings land at every
    position near a page bottom, including the exact break points."""
    pages = _render(income_rows)

    # Expenses heading + header row share a page with the first expense row.
    assert _page_with(pages, 'EXPENSES SUMMARY') == _page_with(pages, 'EXPROW0')

    # The small financial summary stays whole with its heading.
    assert _page_with(pages, 'FINANCIAL SUMMARY') == _page_with(pages, 'Net Profit/Loss')


def test_long_table_repeats_header_on_continuation_page():
    pages = _render(80)
    continuation = [p for p in pages if 'INC07' in p and 'INCOME SUMMARY' not in p]
    assert continuation, 'income table expected to span two pages'
    assert 'Invoice #' in continuation[0]
