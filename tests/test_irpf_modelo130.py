"""Modelo 130 owned by the IRPF Estimator (tax_es_irpf).

Synthetic figures shaped like a real year: EU B2B income with one invoice
still unpaid, VAT-bearing and VAT-free expenses, two equipment purchases,
monthly RETA plus a regularisation. The rules are the owner's of 2026-10-03:
income by invoice date (unpaid included, cancelled excluded), expenses without
VAT, RETA from Social Security payments, equipment depreciated at 25% a year
from the quarter of purchase, items under 300 EUR deducted in full, 5%
difícil justificación left to Modelo 100.
"""
from datetime import date

import pytest

from modules.tax_es_irpf.depreciation import depreciation_in_year


# ── Pure depreciation ────────────────────────────────────────────────────

def test_full_quarter_from_purchase_quarter():
    bought = date(2026, 7, 3)
    assert depreciation_in_year(1000.0, bought, 2026, 2, 25.0) == 0.0
    assert depreciation_in_year(1000.0, bought, 2026, 3, 25.0) == 62.5
    assert depreciation_in_year(1000.0, bought, 2026, 4, 25.0) == 125.0
    assert depreciation_in_year(1000.0, bought, 2027, 4, 25.0) == 250.0


def test_depreciation_stops_at_cost():
    bought = date(2026, 7, 3)  # 16 quarters: 2026 Q3 → 2030 Q2
    assert depreciation_in_year(1000.0, bought, 2030, 2, 25.0) == 125.0
    assert depreciation_in_year(1000.0, bought, 2030, 4, 25.0) == 125.0
    assert depreciation_in_year(1000.0, bought, 2031, 4, 25.0) == 0.0


def test_no_depreciation_without_cost_or_rate():
    assert depreciation_in_year(0.0, date(2026, 1, 1), 2026, 4, 25.0) == 0.0
    assert depreciation_in_year(500.0, date(2026, 1, 1), 2026, 4, 0.0) == 0.0


# ── Module, on synthetic data ────────────────────────────────────────────

@pytest.fixture
def year_2026(app, loaded_modules):
    from app import Customer, Expense, Invoice, Settings, db

    irpf = loaded_modules.modules['tax_es_irpf']
    ss_model = loaded_modules.modules['tax_management'].SSPayment
    if not Settings.query.first():
        db.session.add(Settings())
    customer = Customer(name='ACME Synthetic s.r.o.', tax_type='eu_b2b')
    db.session.add(customer)
    db.session.flush()

    def invoice(number, day, amount, status):
        db.session.add(Invoice(
            invoice_number=number, client_name=customer.name, customer_id=customer.id,
            amount_usd=amount, amount_eur=amount, exchange_rate=1.0, currency='EUR',
            invoice_date=day, status=status))

    invoice('T-1', date(2026, 2, 27), 10000.0, 'paid')
    invoice('T-2', date(2026, 5, 29), 10000.0, 'paid')
    invoice('T-3', date(2026, 8, 31), 5000.0, 'paid')
    invoice('T-4', date(2026, 9, 30), 5000.0, 'pending')   # unpaid: still counts
    invoice('T-5', date(2026, 9, 15), 999.0, 'cancelled')  # never counts

    def expense(day, category, net, vat, deductible=True):
        db.session.add(Expense(
            expense_date=day, category=category, description='synthetic',
            amount=net + vat, net_amount=net, vat_amount=vat,
            vat_rate=21.0 if vat else 0.0, currency='EUR',
            deductible=deductible, deductible_pct=100.0))

    expense(date(2026, 1, 6), 'Services', 100.0, 21.0)
    expense(date(2026, 3, 11), 'Equipment', 40.0, 8.4)      # under 300: in full
    expense(date(2026, 6, 18), 'Equipment', 800.0, 168.0)   # depreciated from Q2
    expense(date(2026, 7, 3), 'Equipment', 1000.0, 210.0)   # depreciated from Q3
    expense(date(2026, 7, 9), 'Services', 18.0, 0.0)        # VAT-free service
    expense(date(2026, 8, 1), 'Services', 50.0, 10.5, deductible=False)

    for month in range(1, 10):
        db.session.add(ss_model(payment_date=date(2026, month, 28), amount=300.0,
                                description='synthetic monthly quota'))
    db.session.add(ss_model(payment_date=date(2026, 2, 17), amount=900.0,
                            description='synthetic regularisation'))
    db.session.commit()
    return irpf


def test_q1_boxes(year_2026):
    boxes, rate = year_2026.modelo130_boxes(2026, 1)
    # 100 + 40 expenses, RETA 3 × 300 + 900 regularisation
    assert rate == 0.20
    assert boxes == {'01': 10000.0, '02': 1940.0, '03': 8060.0, '04': 1612.0,
                     '05': 0.0, '06': 0.0, '07': 1612.0}


def test_q3_boxes(year_2026):
    boxes, _ = year_2026.modelo130_boxes(2026, 3)
    # income 30,000 (unpaid T-4 in, cancelled T-5 out)
    # expenses 100 + 40 + 18 in full; depreciation 800 × 6.25% × 2 + 1000 × 6.25%;
    # RETA 9 × 300 + 900; non-deductible 50 out; VAT never counted
    assert boxes['01'] == 30000.0
    assert boxes['02'] == pytest.approx(158.0 + 100.0 + 62.5 + 3600.0)
    assert boxes['03'] == pytest.approx(26079.5)
    assert boxes['04'] == pytest.approx(5215.9)
    q2, _ = year_2026.modelo130_boxes(2026, 2)
    assert boxes['05'] == pytest.approx(q2['05'] + q2['07'])
    assert boxes['07'] == pytest.approx(boxes['04'] - boxes['05'])


def test_expense_in_full_when_configured(year_2026):
    profile = year_2026._get_profile()
    profile.equipment_method = 'expense'
    boxes, _ = year_2026.modelo130_boxes(2026, 3)
    assert boxes['02'] == pytest.approx(158.0 + 800.0 + 1000.0 + 3600.0)


def test_dificil_justificacion_off_by_default(year_2026):
    assert year_2026._get_profile().apply_dificil_justif_m130 is False


def test_tax_drafts_shows_the_irpf_figures(year_2026, loaded_modules):
    drafts = loaded_modules.modules['tax_es_forms']
    draft = drafts._draft_130(2026, 3)
    assert draft['meta']['source'] == 'tax_es_irpf'
    assert draft['boxes'] == year_2026.modelo130_boxes(2026, 3)[0]
