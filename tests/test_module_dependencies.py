"""Module dependencies are enabled together; capabilities follow their contract."""
import logging

import pytest

from module_manager import BaseModule, valid_capability


def fake(module_id, deps=(), caps=()):
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

        def get_capabilities(self):
            return list(caps)
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
