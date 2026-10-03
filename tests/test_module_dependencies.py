"""Module dependencies are enabled together; capabilities follow their contract."""
import logging

import pytest

from module_manager import BaseModule, valid_capability


def fake(module_id, deps=(), caps=(), provides=(), interface=(), attrs=None):
    class Fake(BaseModule):
        @property
        def module_id(self):
            return module_id

        @property
        def name(self):
            return module_id.upper()

        @property
        def dependencies(self):
            return list(deps)

        @property
        def provides(self):
            return list(provides)

        @property
        def interface(self):
            return list(interface)

        def get_capabilities(self):
            return list(caps)
    for name, value in (attrs or {}).items():
        setattr(Fake, name, value)
    return Fake


@pytest.fixture
def mm(app, loaded_modules, monkeypatch):
    """The shared manager with fake modules added (no routes, no tables)."""
    fakes = {
        'fake_a': fake('fake_a', ['fake_b']),
        'fake_b': fake('fake_b', ['fake_c']),
        'fake_c': fake('fake_c'),
        'fake_d': fake('fake_d', ['no_such_module']),
        'fake_e': fake('fake_e', ['fake_f']),
        'fake_f': fake('fake_f', ['fake_e']),
        # fake_store is a native module; fake_alt can stand in for it.
        'fake_store': fake('fake_store', interface=['read'], attrs={'read': lambda self: 'native'}),
        'fake_alt': fake('fake_alt', provides=['fake_store'], attrs={'read': lambda self: 'alt'}),
        'fake_broken': fake('fake_broken', provides=['fake_store']),
        'fake_user': fake('fake_user', ['fake_store']),
    }
    monkeypatch.setattr(loaded_modules, 'discovered', {**loaded_modules.discovered, **fakes})
    yield loaded_modules
    for mid in fakes:
        loaded_modules.modules.pop(mid, None)


def test_enabling_a_module_enables_what_it_needs_first(mm):
    assert mm.enable_module('fake_a') == ['fake_c', 'fake_b', 'fake_a']
    assert all(mm.is_enabled(m) for m in ('fake_a', 'fake_b', 'fake_c'))


def test_disabling_a_needed_module_is_refused(mm):
    mm.enable_module('fake_a')
    assert mm.disable_module('fake_c') == ['fake_b']
    assert mm.is_enabled('fake_c')
    assert mm.disable_module('fake_a') == []
    assert mm.disable_module('fake_b') == []
    assert mm.disable_module('fake_c') == []
    assert not mm.is_enabled('fake_c')


def test_start_up_enables_missing_dependencies(mm):
    mm._set_enabled('fake_a', True)  # e.g. a dependency declared after install
    assert sorted(mm.ensure_dependencies()) == ['fake_b', 'fake_c']
    assert mm.missing_dependencies('fake_a') == []


def test_dependency_cycle_terminates(mm):
    assert sorted(mm.enable_module('fake_e')) == ['fake_e', 'fake_f']


def test_unknown_dependency_is_logged_not_fatal(mm, caplog):
    with caplog.at_level(logging.WARNING, logger='module_manager'):
        assert mm.enable_module('fake_d') == ['fake_d']
    assert "depends on unknown module 'no_such_module'" in caplog.text


def test_an_alternative_module_satisfies_the_dependency(mm):
    mm.enable_module('fake_alt')
    assert mm.enable_module('fake_user') == ['fake_user']
    assert not mm.is_enabled('fake_store')
    mm.modules['fake_alt'] = mm.discovered['fake_alt'](mm.core)
    assert mm.provider_of('fake_store').read() == 'alt'


def test_native_module_is_enabled_when_no_alternative_is(mm):
    assert mm.enable_module('fake_user') == ['fake_store', 'fake_user']


def test_last_provider_cannot_be_disabled(mm):
    mm.enable_module('fake_alt')
    mm.enable_module('fake_store')
    mm.enable_module('fake_user')
    assert mm.disable_module('fake_store') == []  # fake_alt still provides it
    assert mm.disable_module('fake_alt') == ['fake_user']


def test_provider_without_the_interface_is_not_used(mm, caplog):
    mm.modules['fake_broken'] = mm.discovered['fake_broken'](mm.core)
    with caplog.at_level(logging.WARNING, logger='module_manager'):
        assert mm.provider_of('fake_store') is None
    assert "lacks read" in caplog.text


def test_loaded_modules_have_their_declared_interface(loaded_modules):
    for module_id, instance in loaded_modules.modules.items():
        missing = [n for n in instance.interface if not hasattr(instance, n)]
        assert not missing, f'{module_id} lacks {missing}'


def test_expense_columns_the_tax_modules_read_exist():
    """The expenses contract: tax modules read these core columns directly."""
    from app import Expense
    for column in ('expense_date', 'amount', 'currency', 'net_amount', 'vat_amount',
                   'vat_rate', 'deductible', 'deductible_pct', 'category',
                   'reverse_charge', 'description'):
        assert hasattr(Expense, column), column


def test_toggle_route_refuses_to_disable_a_needed_module(loaded_modules, client):
    for module_id in ('tax_management', 'tax_es_irpf'):
        loaded_modules._set_enabled(module_id, True)
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    resp = client.post('/modules/toggle/tax_management', data={'action': 'disable'},
                       follow_redirects=True)
    assert 'Not disabled: needed by' in resp.get_data(as_text=True)
    assert loaded_modules.is_enabled('tax_management')
    assert 'tax_management' in loaded_modules.modules


def test_declared_dependencies_of_real_modules_exist():
    from module_manager import ModuleManager  # noqa: F401
    import importlib
    for module_id, deps in {'tax_es_forms': ['expenses', 'tax_management', 'tax_es_irpf'],
                            'tax_es_irpf': ['expenses', 'tax_management'],
                            'reta_advisor': ['tax_management']}.items():
        mod = importlib.import_module(f'modules.{module_id}.index')
        cls = next(v for v in vars(mod).values()
                   if isinstance(v, type) and issubclass(v, BaseModule) and v is not BaseModule)
        assert cls(None).dependencies == deps
        for dep in deps:
            importlib.import_module(f'modules.{dep}.index')


# ── Capabilities ─────────────────────────────────────────────────────────

def test_capability_contract():
    assert valid_capability({'type': 'notify', 'action': lambda **kw: True})
    assert not valid_capability({'type': 'notify'})
    assert not valid_capability({'type': '', 'action': print})
    assert not valid_capability({'action': print})
    assert not valid_capability(['notify', print])


def test_invalid_capabilities_are_skipped(mm, caplog):
    good = {'type': 'test_cap', 'name': 'good', 'action': lambda: 'ok'}
    bad = {'type': 'test_cap', 'name': 'bad'}
    mm.modules['fake_c'] = fake('fake_c', caps=[good, bad])(mm.core)
    with caplog.at_level(logging.WARNING, logger='module_manager'):
        found = mm.find_capabilities('test_cap')
    assert [c['name'] for c in found] == ['good']
    assert 'invalid capability' in caplog.text


def test_bundled_modules_declare_valid_capabilities(loaded_modules):
    for module_id, module in loaded_modules.modules.items():
        for cap in module.get_capabilities():
            assert valid_capability(cap), module_id


def test_modules_look_each_other_up_through_provider_of():
    """A direct modules.get('x') would bypass a replacement module."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(p.relative_to(root)) for p in [root / 'app.py', *root.glob('modules/*/*.py')]
                 if re.search(r"\.modules\.get\(\s*['\"]", p.read_text())]
    assert not offenders, offenders
