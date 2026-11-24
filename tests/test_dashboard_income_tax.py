"""The dashboard's income tax comes from a country tax module; the core holds
no country's scale (synthetic data)."""
from datetime import date

import pytest


@pytest.fixture
def no_network(monkeypatch):
    import requests

    def offline(*_a, **_kw):
        raise requests.ConnectionError('tests run offline')
    monkeypatch.setattr(requests, 'get', offline)


@pytest.fixture
def income(app, loaded_modules):
    from contaautonomo.app import Customer, Invoice, db
    customer = Customer(name='Synthetic Client SL', tax_type='standard')
    db.session.add(customer)
    db.session.flush()
    db.session.add(Invoice(invoice_number='D-1', client_name=customer.name,
                           customer_id=customer.id, amount_usd=30000.0, amount_eur=30000.0,
                           exchange_rate=1.0, currency='EUR', status='paid',
                           invoice_date=date(date.today().year, 1, 15)))
    db.session.commit()


def _dashboard(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get('/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_without_an_income_tax_module_the_dashboard_says_so(income, loaded_modules, client,
                                                             monkeypatch, no_network):
    monkeypatch.setattr(loaded_modules, 'calculate_income_tax', lambda context: None)
    html = _dashboard(client)
    assert 'no income tax module is enabled' in html
    assert '€0 - €12,450' not in html and 'IRPF Calculation Details' not in html


def test_the_income_tax_module_answers(income, loaded_modules, client, monkeypatch,
                                       no_network):
    monkeypatch.setattr(loaded_modules, 'calculate_income_tax', lambda context: {
        'income_tax': 1234.5, 'label': 'Synthetic Income Tax',
        'irpf_breakdown': [{'bracket': 'Synthetic band', 'rate': 10, 'amount': 1.0,
                            'tax': 1.0, 'active': True}]})
    html = _dashboard(client)
    assert 'Synthetic Income Tax' in html and '1234.50' in html
    assert 'Income Tax Calculation Details' in html and 'Synthetic band' in html
    assert 'no income tax module is enabled' not in html
