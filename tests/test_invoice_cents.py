"""Tax figures add each invoice in cents, as the books do: a converted invoice
stores its EUR amount with many decimals, and adding the raw values gave a
Modelo 130 box 01 one cent above the filed return."""
from datetime import date
from types import SimpleNamespace

import pytest

from contaautonomo.core.invoice_amounts import invoice_base_eur, to_cents
from modules.tax_es_forms.output_vat import invoice_output_vat
from modules.tax_es_forms.calculator import compute_modelo_303


@pytest.mark.parametrize('raw, cents', [
    (45.1234567, 45.12), (2.675, 2.68), (2.665, 2.67), (100.004, 100.0),
    (-1.005, -1.01), (None, 0.0), (0, 0.0),
])
def test_to_cents_rounds_half_up(raw, cents):
    assert to_cents(raw) == cents


def _inv(amount_eur, tax_type='standard', **snap):
    return SimpleNamespace(amount_eur=amount_eur, customer=SimpleNamespace(tax_type=tax_type),
                           status='pending', invoice_date=date(2026, 8, 1),
                           snap_vat_rate=snap.get('rate'), snap_vat_amount=snap.get('vat'),
                           snap_taxable_base=snap.get('base'))


def test_invoice_base_and_output_vat_are_cents():
    assert invoice_base_eur(_inv(100.004)) == 100.0
    assert invoice_output_vat(_inv(100.005), 0.21) == (100.01, 21.0)
    assert invoice_output_vat(_inv(0.0, rate=21.0, base=1000.004, vat=210.0009), 0.21) == \
        (1000.0, 210.0)


def test_303_bases_add_the_cents_of_each_invoice():
    boxes = compute_modelo_303([_inv(0.005, 'eu_b2b')] * 3, [], vat_rate=0.21)['boxes']
    assert boxes['59'] == 0.03           # 3 × 0.01, not round(0.015) = 0.02


@pytest.fixture
def irpf_2039(loaded_modules):
    from contaautonomo.app import Customer, Invoice, Settings, db
    if not Settings.query.first():
        db.session.add(Settings())
    customer = Customer(name='Cents Synthetic Ltd', tax_type='non_eu')
    db.session.add(customer)
    db.session.flush()
    rows = [Invoice(invoice_number=f'CENT-{n}', client_name=customer.name,
                    customer_id=customer.id, amount_usd=1100.0, amount_eur=1000.004,
                    exchange_rate=0.9091, currency='USD', invoice_date=date(2039, 2, n),
                    status='issued') for n in (1, 2, 3)]
    db.session.add_all(rows)
    db.session.commit()
    yield loaded_modules.modules['tax_es_irpf']
    for row in rows:
        db.session.delete(row)
    db.session.delete(customer)
    db.session.commit()


def test_modelo_130_income_is_the_sum_of_invoice_cents(irpf_2039):
    boxes, _ = irpf_2039.modelo130_boxes(2039, 1)
    assert boxes['01'] == 3000.0         # 3 × 1000.00, not round(3000.012) = 3000.01
