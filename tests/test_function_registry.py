"""The function registry: contracts, core and module implementations,
argument checks, and dependencies on function names."""
import logging
from datetime import date
from types import SimpleNamespace

import pytest

from contracts import CONTRACTS
from functions import (CORE, Contract, FunctionRegistry, FunctionUnavailable,
                       InvalidArguments, Param)
from module_manager import BaseModule

ECHO = Contract('test.echo', 1, 'Echo the text.',
                params=(Param('text', 'str', required=True, max_length=10),
                        Param('times', 'int', minimum=1, maximum=3)))
DAY = Contract('test.day', 1, 'A date.', params=(Param('day', 'date'),))


def _manager(**modules):
    return SimpleNamespace(modules=modules)


def _module(functions=None, raises=False):
    def get_functions():
        if raises:
            raise RuntimeError('broken')
        return functions or {}
    return SimpleNamespace(get_functions=get_functions)


# --- contracts and parameters ------------------------------------------------ #

@pytest.mark.parametrize('arguments,expected', [
    ({'text': ' hi '}, {'text': 'hi'}),
    ({'text': 'hi', 'times': '2'}, {'text': 'hi', 'times': 2}),
    ({'text': 'hi', 'times': None}, {'text': 'hi'}),
    ({'text': 'hi', 'times': ''}, {'text': 'hi'}),
])
def test_contract_cleans_arguments(arguments, expected):
    assert ECHO.check(arguments) == expected


@pytest.mark.parametrize('arguments,problem', [
    ({}, 'text is required'),
    ({'text': '   '}, 'text is required'),
    ({'text': 'x' * 11}, 'text must be at most 10 characters'),
    ({'text': 'hi', 'times': 4}, 'times must be between 1 and 3'),
    ({'text': 'hi', 'times': 'two'}, 'times must be a whole number'),
    ({'text': 'hi', 'times': True}, 'times must be a whole number'),
    ({'text': 5}, 'text must be text'),
    ({'text': 'hi', 'id': 1}, "parameter 'id' is not allowed"),
])
def test_contract_refuses_bad_arguments(arguments, problem):
    assert ECHO.validate(arguments) == (None, problem)
    with pytest.raises(InvalidArguments, match=problem.replace('(', r'\(')):
        ECHO.check(arguments)


def test_problem_names_the_parameter_not_the_value():
    _, problem = DAY.validate({'day': '<script>'})
    assert problem == 'day must be a date (YYYY-MM-DD)'
    assert DAY.check({'day': '2035-02-03'}) == {'day': date(2035, 2, 3)}
    assert DAY.validate({'day': '2035-02-30'})[1] == 'day must be a date (YYYY-MM-DD)'


def test_choices_and_bytes():
    c = Contract('test.file', 1, 'x', params=(Param('data', 'bytes', required=True),
                                             Param('kind', 'str', choices=('a', 'b'))))
    assert c.check({'data': bytearray(b'1'), 'kind': 'a'}) == {'data': b'1', 'kind': 'a'}
    assert c.validate({'data': 'text'})[1] == 'data must be bytes'
    assert c.validate({'data': b'1', 'kind': 'c'})[1] == 'kind must be one of: a, b'


def test_assistant_functions_must_be_read_only_and_internal():
    with pytest.raises(ValueError):
        Contract('test.write', 1, 'x', read_only=False, assistant=True)
    with pytest.raises(ValueError):
        Contract('test.out', 1, 'x', external=True, assistant=True)
    with pytest.raises(ValueError):
        Param('x', 'float')


def test_shipped_contracts_are_complete():
    for name, contract in CONTRACTS.items():
        assert name == contract.name and '.' in name and contract.version >= 1
        assert contract.summary and contract.returns
        if contract.assistant:
            assert contract.read_only and not contract.external
            assert contract.record_kind and contract.assistant_fields


# --- the registry ------------------------------------------------------------ #

def test_module_implementation_is_found_and_called():
    reg = FunctionRegistry(_manager(echo_mod=_module({'test.echo': lambda text, times=1: text * times})),
                           contracts={'test.echo': ECHO})
    assert reg.available('test.echo') and reg.provider('test.echo') == 'echo_mod'
    assert reg.call('test.echo', text='ab', times='2') == 'abab'
    with pytest.raises(InvalidArguments):
        reg.call('test.echo', text='ab', times=9)


def test_core_implementation_comes_first():
    reg = FunctionRegistry(_manager(alt=_module({'test.echo': lambda text, times=1: 'alt'})),
                           contracts={'test.echo': ECHO})
    reg.register_core('test.echo', lambda text, times=1: 'core')
    assert [p for p, _ in reg.implementations('test.echo')] == [CORE, 'alt']
    assert reg.call('test.echo', text='x') == 'core'
    assert reg.core_provides('test.echo')


def test_modules_are_tried_in_id_order():
    reg = FunctionRegistry(_manager(zeta=_module({'test.echo': lambda text, times=1: 'z'}),
                                    alpha=_module({'test.echo': lambda text, times=1: 'a'})),
                           contracts={'test.echo': ECHO})
    assert reg.call('test.echo', text='x') == 'a'


def test_missing_function_and_unknown_contract():
    reg = FunctionRegistry(_manager(), contracts={'test.echo': ECHO})
    with pytest.raises(FunctionUnavailable, match='No enabled module provides test.echo'):
        reg.call('test.echo', text='x')
    with pytest.raises(FunctionUnavailable, match='There is no function test.nope'):
        reg.call('test.nope')
    assert reg.problem_with('test.nope', {}) == 'there is no function test.nope'
    assert reg.problem_with('test.echo', {}) == 'text is required'
    assert reg.problem_with('test.echo', {'text': 'ok'}) is None


def test_invalid_implementations_are_ignored_and_logged_once(caplog):
    mods = _manager(
        bad=_module({'test.echo': lambda only_this: None,   # wrong parameters
                     'test.unknown': lambda: None,           # no such contract
                     'test.day': 'not callable'}),
        broken=_module(raises=True),
        good=_module({'test.echo': lambda text, **rest: 'good'}))
    reg = FunctionRegistry(mods, contracts={'test.echo': ECHO, 'test.day': DAY})
    with caplog.at_level(logging.WARNING, logger='functions'):
        assert reg.call('test.echo', text='x') == 'good'
        reg.call('test.echo', text='x')
        problems = reg.check_all()
    assert sorted((m, n) for m, n, _ in problems) == [
        ('bad', 'test.day'), ('bad', 'test.echo'), ('bad', 'test.unknown'), ('broken', '*')]
    assert caplog.text.count("Function 'test.echo' of 'bad' ignored") == 1
    assert 'does not take the parameters of the contract' in caplog.text
    assert not reg.available('test.day')


def test_core_registration_is_checked():
    reg = FunctionRegistry(_manager(), contracts={'test.echo': ECHO})
    with pytest.raises(ValueError):
        reg.register_core('test.echo', lambda: None)
    with pytest.raises(ValueError):
        reg.register_core('test.unknown', lambda: None)


def test_routes_and_find():
    reg = FunctionRegistry(
        _manager(web=_module({'test.echo': {'call': lambda text, times=1: text,
                                            'route': '/web/echo'}})),
        contracts={'test.echo': ECHO, 'test.day': DAY,
                   'test.ask': Contract('test.ask', 1, 'x', assistant=True,
                                        record_kind='r', assistant_fields=('id',))})
    reg.register_core('test.ask', lambda: {})
    assert reg.route('test.echo') == '/web/echo' and reg.route('test.day') is None
    assert [c.name for c in reg.find()] == ['test.ask', 'test.echo']
    assert [c.name for c in reg.find(assistant=True)] == ['test.ask']


def test_disabling_a_module_removes_its_functions_at_once():
    modules = {'echo_mod': _module({'test.echo': lambda text, times=1: text})}
    reg = FunctionRegistry(SimpleNamespace(modules=modules), contracts={'test.echo': ECHO})
    assert reg.available('test.echo')
    modules.pop('echo_mod')
    assert not reg.available('test.echo')


# --- the module manager -------------------------------------------------------- #

def _fake(module_id, deps=(), functions=None):
    class Fake(BaseModule):
        @property
        def module_id(self):
            return module_id

        @property
        def name(self):
            return module_id

        @property
        def dependencies(self):
            return list(deps)

        def get_functions(self):
            return dict(functions or {})
    return Fake


@pytest.fixture
def mm(app, loaded_modules, monkeypatch):
    monkeypatch.setitem(loaded_modules.functions.contracts, 'test.echo', ECHO)
    fakes = {
        'fn_impl': _fake('fn_impl', functions={'test.echo': lambda text, times=1: text}),
        'fn_user': _fake('fn_user', deps=['test.echo']),
        'fn_core_user': _fake('fn_core_user', deps=['ledger.list_invoices']),
        'fn_lost': _fake('fn_lost', deps=['test.nothing']),
    }
    monkeypatch.setattr(loaded_modules, 'discovered', {**loaded_modules.discovered, **fakes})
    yield loaded_modules
    for module_id in fakes:
        loaded_modules.modules.pop(module_id, None)


def test_a_function_dependency_enables_its_provider(mm):
    assert mm.providers('test.echo') == ['fn_impl']
    assert mm.enable_module('fn_user') == ['fn_impl', 'fn_user']
    assert mm.missing_dependencies('fn_user') == []
    assert mm.required_by('fn_impl') == ['fn_user']


def test_a_core_function_satisfies_a_dependency(mm):
    assert mm.enable_module('fn_core_user') == ['fn_core_user']
    assert mm.missing_dependencies('fn_core_user') == []


def test_unknown_function_dependency_is_logged(mm, caplog):
    with caplog.at_level(logging.WARNING, logger='module_manager'):
        assert mm.enable_module('fn_lost') == ['fn_lost']
    assert "depends on unknown function 'test.nothing'" in caplog.text
    assert mm.missing_dependencies('fn_lost') == ['test.nothing']


def test_module_states_list_functions(mm):
    states = {s['module_id']: s for s in mm.get_all_module_states()}
    assert states['fn_impl']['functions'] == ['test.echo']
    assert states['fn_user']['functions'] == []


def test_core_services_expose_the_registry(loaded_modules):
    assert loaded_modules.core.functions is loaded_modules.functions


def test_bundled_modules_register_valid_functions(loaded_modules):
    assert loaded_modules.functions.check_all() == []


# --- the core's functions -------------------------------------------------- #

@pytest.fixture
def invoices(app, loaded_modules):
    from app import Customer, Invoice, db
    acme = Customer(name='ACME Synthetic SL', email='a@example.invalid', tax_type='standard')
    db.session.add(acme)
    db.session.flush()
    for number, d, status in (('RF-1', date(2035, 1, 5), 'issued'),
                              ('RF-2', date(2035, 2, 5), 'paid'),
                              ('RF-3', date(2035, 3, 5), 'issued')):
        db.session.add(Invoice(invoice_number=number, client_name=acme.name, customer_id=acme.id,
                               amount_usd=0.0, amount_eur=100.0, exchange_rate=1.0,
                               invoice_date=d, status=status, currency='EUR'))
    db.session.commit()
    return loaded_modules.functions


def test_core_lists_invoices_with_filters_and_paging(invoices):
    out = invoices.call('ledger.list_invoices', date_from='2035-02-01', status='issued')
    assert [r['invoice_number'] for r in out['data']] == ['RF-3'] and out['total'] == 1
    page = invoices.call('ledger.list_invoices', client='acme', per_page=2, page=2)
    assert [r['invoice_number'] for r in page['data']] == ['RF-1']
    assert page['total'] == 3 and page['page'] == 2
    assert invoices.provider('ledger.list_invoices') == CORE


def test_core_lists_customers(invoices):
    names = [c['name'] for c in invoices.call('ledger.list_customers')['data']]
    assert 'ACME Synthetic SL' in names


def test_api_endpoints_return_the_core_records(invoices, loaded_modules, app):
    api = loaded_modules.modules['api']
    with app.test_request_context('/api/v1/invoices?client=acme&per_page=500'):
        body, status = api._list_invoices()
    assert status == 200 and body == invoices.call('ledger.list_invoices', client='acme',
                                                   per_page=100)
    with app.test_request_context('/api/v1/invoices?status=' + 'x' * 30):
        with pytest.raises(Exception) as err:
            api._list_invoices()
    assert err.value.status == 400 and 'status must be at most 20' in err.value.message
    with app.test_request_context('/api/v1/customers'):
        body, _ = api._list_customers()
    assert body == invoices.call('ledger.list_customers')


def test_modules_raise_the_core_api_error():
    """ApiError lives in the core; modules must not import it from the api module."""
    import pathlib
    import re
    from api_errors import ApiError
    from modules.api.index import ApiError as Reexported
    assert Reexported is ApiError
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(p.relative_to(root)) for p in root.glob('modules/*/*.py')
                 if re.search(r'from modules\.api\.index import ApiError', p.read_text())]
    assert not offenders, offenders
