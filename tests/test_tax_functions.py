"""Tax data through the function registry: the data owner declares it, the
other tax modules call it by name and follow a replacement."""
import json
from datetime import date

import pytest

from functions import Function, Param
from module_manager import BaseModule

YEAR = 2034  # synthetic year, no other test writes it


def _replacement(mm, mid, *functions):
    class Replacement(BaseModule):
        module_id = mid
        name = 'Replacement'

        def get_functions(self):
            return list(functions)
    return Replacement(mm.core)


@pytest.fixture
def registry(loaded_modules):
    return loaded_modules.functions


def test_filed_boxes_and_ss_payments(loaded_modules, registry):
    from app import db
    tm = loaded_modules.modules['tax_management']
    db.session.add(tm.TaxForm(form_type='130', year=YEAR, quarter=1, file_path='',
                              status='filed', filed_boxes=json.dumps({'07': 210.5})))
    db.session.add_all([tm.SSPayment(payment_date=date(YEAR, 1, 31), amount=300.0),
                        tm.SSPayment(payment_date=date(YEAR, 2, 28), amount=310.0),
                        tm.SSPayment(payment_date=date(YEAR + 1, 1, 31), amount=999.0)])
    db.session.commit()

    assert registry.call('tax.filed_boxes', form_type='130', year=YEAR, quarter=1) == \
        {'07': 210.5}
    assert registry.call('tax.filed_boxes', form_type='130', year=YEAR, quarter=2) == {}
    assert registry.call('ss.payments_total', date_from=f'{YEAR}-01-01',
                         date_to=f'{YEAR}-12-31') == {'total': 610.0, 'count': 2}
    assert loaded_modules.modules['reta_advisor']._paid_ytd(YEAR) == 610.0
    assert loaded_modules.modules['reta_advisor']._paid_ytd(YEAR + 5) is None


def test_irpf_reads_social_security_through_the_registry(loaded_modules, monkeypatch):
    irpf = loaded_modules.modules['tax_es_irpf']
    paid = Function('ss.payments_total', lambda date_from, date_to: {'total': 123.0, 'count': 1},
                    'Paid.', params=(Param('date_from', 'date', required=True),
                                     Param('date_to', 'date', required=True)))
    monkeypatch.delitem(loaded_modules.modules, 'tax_management')
    monkeypatch.setitem(loaded_modules.modules, 'zz_ss', _replacement(loaded_modules, 'zz_ss', paid))
    assert irpf._ss_paid(date(YEAR, 1, 1), date(YEAR, 3, 31)) == 123.0
    monkeypatch.delitem(loaded_modules.modules, 'zz_ss')
    assert irpf._ss_paid(date(YEAR, 1, 1), date(YEAR, 3, 31)) == 0.0
    assert irpf._filed_boxes('130', YEAR, 1) == {}


def test_irpf_functions(registry):
    m130 = registry.call('tax.modelo130_boxes', year=YEAR, quarter=1)
    assert set(m130['boxes']) == {'01', '02', '03', '04', '05', '06', '07'}
    assert 0 < m130['rate'] < 1
    rule = registry.call('tax.equipment_rule')
    assert isinstance(rule['threshold'], float)
    assert all(c == c.lower() for c in rule['categories'])
    assert 'summary' in registry.call('tax.modelo100_result', year=YEAR)


def test_reta_projection_takes_numbers(registry):
    base = registry.call('reta.projection', today=date(YEAR, 6, 30))
    more = registry.call('reta.projection', today=f'{YEAR}-06-30', income_delta='12000',
                         expense_delta=0, monthly_quota=300.5)
    assert more['current_monthly_quota'] == 300.5
    assert more['recommended_monthly_quota'] >= base['recommended_monthly_quota']


def test_modelo_303_equipment_follows_the_rule_provider(loaded_modules, monkeypatch):
    forms = loaded_modules.modules['tax_es_forms']
    laptop = type('E', (), {'category': 'Laptops'})()
    rule = Function('tax.equipment_rule',
                    lambda: {'threshold': 100.0, 'categories': ['laptops']}, 'Rule.')
    monkeypatch.delitem(loaded_modules.modules, 'tax_es_irpf')
    assert not forms._is_equipment()(laptop, 500.0)       # default: "Equipment" only
    monkeypatch.setitem(loaded_modules.modules, 'zz_rule', _replacement(loaded_modules, 'zz_rule', rule))
    assert forms._is_equipment()(laptop, 150.0)
    assert not forms._is_equipment()(laptop, 99.0)


def test_tax_provision_follows_a_replacement_without_its_page(loaded_modules, monkeypatch):
    projection = Function('reta.projection',
                          lambda today=None: {'regularization_delta': 12.345}, 'RETA.',
                          params=(Param('today', 'date'),))
    monkeypatch.delitem(loaded_modules.modules, 'reta_advisor')
    monkeypatch.setitem(loaded_modules.modules, 'zz_reta', _replacement(loaded_modules, 'zz_reta', projection))
    provision = loaded_modules.modules['tax_provision'].provision(date(YEAR, 5, 15))
    reta = next(p for p in provision['parts'] if p['id'] == 'reta')
    assert reta['amount'] == 12.35 and reta['endpoint'] is None
    iva = next(p for p in provision['parts'] if p['id'] == 'iva')
    assert iva['endpoint'] == 'tax_es_forms.draft_303'
