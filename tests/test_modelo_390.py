"""D-2 — Modelo 390 draft: every casilla equals the sum of the quarterly 303s."""
from datetime import date

import pytest

from modules.tax_es_forms.boxes import (
    MODELO_390_BOXES,
    MODELO_390_ORDER,
    MODELO_390_TABLES,
    modelo_390_boxes_for_year,
)
from modules.tax_es_forms.calculator import compute_modelo_390

# 390 casilla -> 303 box it adds up over the four quarters.
DIRECT = {'05': '01', '06': '03', '551': '10', '552': '11', '27': '12', '28': '13',
          '48': '28', '49': '29', '50': '30', '51': '31', '597': '36', '598': '37',
          '58': '38', '59': '39', '85': '78', '99': '01', '103': '59', '110': '120'}


def _q(**boxes):
    base = {k: 0.0 for k in ('01', '03', '10', '11', '12', '13', '27', '28', '29', '30',
                             '31', '36', '37', '38', '39', '45', '46', '59', '120', '78',
                             '71', '87', '110')}
    base.update(boxes)
    return base


QUARTERS = [
    _q(**{'01': 1000, '03': 210, '28': 200, '29': 42, '59': 500, '71': 168, '46': 168,
          '45': 42, '27': 210}),
    _q(**{'01': 2000, '03': 420, '10': 100, '11': 21, '36': 100, '37': 21, '30': 800,
          '31': 168, '120': 300, '27': 441, '45': 189, '46': 252, '71': 252}),
    _q(**{'01': 500, '03': 105, '28': 1000, '29': 210, '27': 105, '45': 210, '46': -105,
          '71': -105}),
    _q(**{'01': 1500, '03': 315, '12': 50, '13': 10.5, '38': 50, '39': 10.5, '78': 105,
          '27': 325.5, '45': 10.5, '46': 315, '71': 210}),
]


def test_each_casilla_is_the_sum_of_the_quarters():
    boxes = compute_modelo_390(QUARTERS)['boxes']
    for c390, b303 in DIRECT.items():
        assert boxes[c390] == pytest.approx(sum(q[b303] for q in QUARTERS)), c390


def test_totals_agree_with_the_303_results():
    boxes = compute_modelo_390(QUARTERS)['boxes']
    assert boxes['47'] == pytest.approx(sum(q['27'] for q in QUARTERS))   # devengado
    assert boxes['64'] == pytest.approx(sum(q['45'] for q in QUARTERS))   # deducible
    assert boxes['65'] == pytest.approx(sum(q['46'] for q in QUARTERS))
    assert boxes['86'] == pytest.approx(sum(q['71'] for q in QUARTERS))
    assert boxes['95'] == pytest.approx(168 + 252 + 210)                  # positive results
    assert boxes['97'] == 0.0                                             # Q4 was payable
    assert boxes['108'] == pytest.approx(boxes['99'] + boxes['103'] + boxes['110'])


def test_last_quarter_to_compensate():
    quarters = QUARTERS[:3] + [_q(**{'71': -80.0})]
    assert compute_modelo_390(quarters)['boxes']['97'] == 80.0


def test_every_casilla_has_a_label_and_position():
    boxes = compute_modelo_390(QUARTERS)['boxes']
    assert set(boxes) == set(MODELO_390_BOXES) == set(MODELO_390_ORDER)


def test_table_year_fallback():
    own = min(MODELO_390_TABLES)
    assert modelo_390_boxes_for_year(own)[2] is False
    assert modelo_390_boxes_for_year(own + 1)[2] is True


# --- module integration -----------------------------------------------------------

def test_draft_390_matches_quarterly_drafts(loaded_modules):
    from app import Customer, Invoice, _issue_invoice, db
    forms = loaded_modules.modules['tax_es_forms']
    year = 2031   # synthetic year with no other data
    cust = Customer(name='Annual SL', tax_type='standard')
    db.session.add(cust)
    db.session.flush()
    for i, (month, amount) in enumerate([(2, 1000.0), (5, 500.0), (11, 250.0)]):
        inv = Invoice(invoice_number=f'D2-{i}', client_name=cust.name, customer_id=cust.id,
                      amount_usd=0.0, amount_eur=amount, exchange_rate=1.0,
                      invoice_date=date(year, month, 10), status='draft', currency='EUR')
        db.session.add(inv)
        db.session.commit()
        _issue_invoice(inv)
    draft = forms._draft_390(year)
    quarters = [forms._draft_303(year, q)['boxes'] for q in (1, 2, 3, 4)]
    assert draft['boxes']['05'] == pytest.approx(sum(q['01'] for q in quarters)) == 1750.0
    assert draft['boxes']['06'] == pytest.approx(sum(q['03'] for q in quarters))
    assert draft['table_fallback'] is True and str(max(MODELO_390_TABLES)) in draft['table_note']


def test_draft_390_page(loaded_modules, client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/tax-forms-draft/390/2031').get_data(as_text=True)
    assert 'Modelo 390' in html and 'Not covered by this draft' in html
    assert 'Resultado de la liquidación' in html
    index = client.get('/tax-forms-draft/').get_data(as_text=True)
    assert '/tax-forms-draft/390/' in index
