"""The country tax module example in README.md runs as written: it is taken
from the README, loaded like a module and every hook is called."""
import re
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

README = Path(__file__).resolve().parent.parent / 'README.md'


def _example_source():
    text = README.read_text()
    section = text.split('## Writing a Country Tax Module', 1)[1].split('\n## ', 1)[0]
    return re.search(r'```python\n(.*?)```', section, re.S).group(1)


@pytest.fixture
def example(loaded_modules):
    from contaautonomo.core.module_manager import BaseModule
    source = _example_source()
    code = types.ModuleType('readme_tax_example')
    exec(compile(source, 'README.md', 'exec'), code.__dict__)
    cls = next(v for v in vars(code).values()
               if isinstance(v, type) and issubclass(v, BaseModule) and v is not BaseModule)
    return source, cls(loaded_modules.core)


def test_example_uses_the_core_not_app_or_other_modules(example):
    source, module = example
    assert 'from app import' not in source and 'from modules' not in source
    assert module.module_id == 'tax_mycountry' and module.description


def test_example_on_enable_sets_the_vat_rate(example):
    from contaautonomo.app import Settings, db
    _source, module = example
    settings = Settings.query.first() or Settings()
    db.session.add(settings)
    db.session.commit()
    before = settings.default_vat_rate
    try:
        module.on_enable()
        assert Settings.query.first().default_vat_rate == 20.0
    finally:
        settings.default_vat_rate = before
        db.session.commit()


def test_example_hooks_return_what_the_dashboard_reads(example):
    _source, module = example
    income = module.calculate_income_tax({'taxable_income': 10000.0, 'settings': None,
                                          'base_currency': 'EUR', 'currency_symbol': '€'})
    assert income['income_tax'] == 2000.0 and income['label'] and income['irpf_breakdown']

    def inv(amount, tax_type):
        return SimpleNamespace(amount_base=amount, snap_customer=None,
                               customer=SimpleNamespace(tax_type=tax_type))
    vat = module.calculate_vat({'invoices': [inv(1000.0, 'standard'), inv(500.0, 'eu_b2b')],
                                'settings': None, 'base_currency': 'EUR'})
    assert vat == {'vat_collected': 200.0, 'vat_rate': 0.2, 'label': 'VAT'}

    panel = module.get_tax_obligations({'current_year': 2026, 'base_currency': 'EUR',
                                        'exchange_rates': {}, 'settings': None,
                                        'vat_collected': 200.0, 'currency_symbol': '€'})
    assert {'summary_columns', 'breakdown_rows', 'notes', 'deductions', 'tax_total'} <= set(panel)
    assert module.get_field_labels()['vat_number']


def test_dashboard_renders_with_the_example_enabled(example, loaded_modules, client,
                                                    monkeypatch):
    _source, module = example
    monkeypatch.setitem(loaded_modules.modules, 'tax_mycountry', module)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'Social contribution' in resp.get_data(as_text=True)    # Tax Obligations panel


def test_manager_uses_the_example_when_it_is_the_tax_module(example, loaded_modules,
                                                             monkeypatch):
    """With the Spanish modules off, the example answers the dashboard's
    income tax and VAT (the first module that returns a result is used)."""
    _source, module = example
    monkeypatch.setattr(loaded_modules, 'modules', {'tax_mycountry': module})
    tax = loaded_modules.calculate_income_tax({'taxable_income': 5000.0, 'settings': None,
                                               'base_currency': 'EUR', 'currency_symbol': '€'})
    assert tax['label'] == 'Income Tax (20%)' and tax['income_tax'] == 1000.0
    vat = loaded_modules.calculate_vat({'invoices': [], 'settings': None, 'base_currency': 'EUR'})
    assert vat['vat_rate'] == 0.2
