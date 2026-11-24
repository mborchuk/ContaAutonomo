"""The function registry: declarations in the core and in modules, the first
registration owning a name, warnings for later claims, argument checks, and
dependencies on function names."""
import logging
from datetime import date
from types import SimpleNamespace

import pytest

from contaautonomo.core.functions import (CORE, Function, FunctionRegistry, FunctionUnavailable,
                       InvalidArguments, Param)
from contaautonomo.core.module_manager import BaseModule

TEXT = Param('text', 'str', required=True, max_length=10)
TIMES = Param('times', 'int', minimum=1, maximum=3)


def echo(call=None, name='test.echo', **extra):
    return Function(name, call or (lambda text, times=1: text * times), 'Echo the text.',
                    params=(TEXT, TIMES), **extra)


def _manager(**modules):
    return SimpleNamespace(modules=modules)


def _module(*functions, raises=False):
    def get_functions():
        if raises:
            raise RuntimeError('broken')
        return list(functions)
    return SimpleNamespace(get_functions=get_functions)


# --- declarations ------------------------------------------------------------ #

@pytest.mark.parametrize('arguments,expected', [
    ({'text': ' hi '}, {'text': 'hi'}),
    ({'text': 'hi', 'times': '2'}, {'text': 'hi', 'times': 2}),
    ({'text': 'hi', 'times': None}, {'text': 'hi'}),
    ({'text': 'hi', 'times': ''}, {'text': 'hi'}),
])
def test_declaration_cleans_arguments(arguments, expected):
    assert echo().check(arguments) == expected


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
def test_declaration_refuses_bad_arguments(arguments, problem):
    assert echo().validate(arguments) == (None, problem)
    with pytest.raises(InvalidArguments):
        echo().check(arguments)


def test_problem_names_the_parameter_not_the_value():
    day = Function('test.day', lambda day=None: day, 'A date.', params=(Param('day', 'date'),))
    assert day.validate({'day': '<script>'})[1] == 'day must be a date (YYYY-MM-DD)'
    assert day.check({'day': '2035-02-03'}) == {'day': date(2035, 2, 3)}
    assert day.validate({'day': '2035-02-30'})[1] == 'day must be a date (YYYY-MM-DD)'


def test_choices_bytes_and_signature():
    fn = Function('test.file', lambda data, kind=None: None, 'x',
                  params=(Param('data', 'bytes', required=True),
                          Param('kind', 'str', choices=('a', 'b'))))
    assert fn.check({'data': bytearray(b'1'), 'kind': 'a'}) == {'data': b'1', 'kind': 'a'}
    assert fn.validate({'data': 'text'})[1] == 'data must be bytes'
    assert fn.validate({'data': b'1', 'kind': 'c'})[1] == 'kind must be one of: a, b'
    assert fn.signature == 'test.file(data, kind?)'


@pytest.mark.parametrize('function,problem', [
    (Function('nodot', lambda: None, 'x'), 'has no valid name'),
    (Function('test.x', 'not callable', 'x'), 'has no callable'),
    (Function('test.x', lambda: None, ''), 'has no summary'),
    (echo(call=lambda only_this: None), 'does not take its declared parameters'),
    (Function('test.x', lambda: None, 'x', read_only=False, assistant=True,
              record_kind='r', assistant_fields=('id',)), 'is not read-only or sends data out'),
    (Function('test.x', lambda: None, 'x', external=True, assistant=True,
              record_kind='r', assistant_fields=('id',)), 'is not read-only or sends data out'),
    (Function('test.x', lambda: None, 'x', assistant=True), 'without record_kind'),
])
def test_bad_declarations_are_explained(function, problem):
    assert problem in function.problem()


def test_unknown_param_kind():
    with pytest.raises(ValueError):
        Param('x', 'decimal')


@pytest.mark.parametrize('value,result', [
    (1.5, (1.5, None)), (2, (2.0, None)), ('-3.25', (-3.25, None)),
    (True, (None, 'eur must be a number')), ('abc', (None, 'eur must be a number')),
    ('nan', (None, 'eur must be a number')), (float('inf'), (None, 'eur must be a number')),
    (1e9, (None, 'eur must be between -1000 and 1000')),
])
def test_float_param(value, result):
    assert Param('eur', 'float', minimum=-1000, maximum=1000).clean(value) == result


# --- the registry ------------------------------------------------------------ #

def test_module_function_is_found_and_called():
    reg = FunctionRegistry(_manager(echo_mod=_module(echo())))
    assert reg.available('test.echo') and reg.provider('test.echo') == 'echo_mod'
    assert reg.call('test.echo', text='ab', times='2') == 'abab'
    with pytest.raises(InvalidArguments):
        reg.call('test.echo', text='ab', times=9)


def test_first_registration_owns_the_name_and_later_claims_warn(caplog):
    first = _module(echo(call=lambda text, times=1: 'first'))
    second = _module(echo(call=lambda text, times=1: 'second'),
                     Function('test.extra', lambda: 'extra', 'An extra function.'))
    reg = FunctionRegistry(_manager(first_mod=first, second_mod=second))
    assert reg.call('test.echo', text='x') == 'first'
    assert reg.call('test.extra') == 'extra'           # its own new names still work
    assert reg.status('second_mod') == {
        'provides': ['test.extra()'],
        'replaces_core': [],
        'conflicts': [{'function': 'test.echo', 'owner': 'first_mod'}],
        'problems': []}
    assert reg.status('first_mod')['provides'] == ['test.echo(text, times?)']
    with caplog.at_level(logging.WARNING, logger='functions'):
        conflicts, _ = reg.check_all()
        reg.check_all()
    assert conflicts == [('second_mod', 'test.echo', 'first_mod')]
    assert caplog.text.count("Function 'test.echo' of 'second_mod' is not used: "
                             "'first_mod' already provides it") == 1


def test_the_next_declaration_takes_over_when_the_owner_goes():
    modules = {'first_mod': _module(echo(call=lambda text, times=1: 'first')),
               'second_mod': _module(echo(call=lambda text, times=1: 'second'))}
    reg = FunctionRegistry(SimpleNamespace(modules=modules))
    assert reg.call('test.echo', text='x') == 'first'
    modules.pop('first_mod')
    assert reg.call('test.echo', text='x') == 'second'
    assert reg.status('second_mod')['conflicts'] == []
    modules.pop('second_mod')
    assert not reg.available('test.echo')


def test_the_core_registers_first():
    reg = FunctionRegistry(_manager(alt=_module(echo(call=lambda text, times=1: 'alt'))))
    reg.register_core(echo(call=lambda text, times=1: 'core'))
    assert reg.call('test.echo', text='x') == 'core' and reg.provider('test.echo') == CORE
    assert reg.status('alt')['conflicts'] == [{'function': 'test.echo', 'owner': CORE}]
    assert reg.core_provides('test.echo')
    with pytest.raises(ValueError):
        reg.register_core(echo())                       # twice
    with pytest.raises(ValueError):
        reg.register_core(echo(call=lambda: None, name='test.other'))   # bad declaration


def test_a_module_replaces_a_core_default_without_a_conflict():
    modules = {'alt': _module(echo(call=lambda text, times=1: 'alt'))}
    reg = FunctionRegistry(SimpleNamespace(modules=modules))
    reg.register_core(echo(call=lambda text, times=1: 'core'), default=True)
    assert reg.is_core_default('test.echo') and reg.core_provides('test.echo')
    assert reg.call('test.echo', text='x') == 'alt' and reg.provider('test.echo') == 'alt'
    assert reg.status('alt')['replaces_core'] == ['test.echo']
    assert reg.status('alt')['conflicts'] == []
    assert reg.check_all() == ([], [])
    modules.pop('alt')                                  # module disabled: default is back
    assert reg.call('test.echo', text='x') == 'core' and reg.provider('test.echo') == CORE
    assert [fn.name for fn in reg.find()] == ['test.echo']


def test_two_modules_on_a_core_default_conflict_with_each_other():
    reg = FunctionRegistry(_manager(first_mod=_module(echo(call=lambda text, times=1: '1')),
                                    second_mod=_module(echo(call=lambda text, times=1: '2'))))
    reg.register_core(echo(call=lambda text, times=1: 'core'), default=True)
    assert reg.call('test.echo', text='x') == '1'
    assert reg.status('second_mod')['conflicts'] == [{'function': 'test.echo',
                                                      'owner': 'first_mod'}]
    assert reg.status('second_mod')['replaces_core'] == []


def test_bad_declarations_are_ignored_and_reported():
    mods = _manager(
        bad=_module(echo(call=lambda only_this: None), 'not a function', echo(), echo()),
        broken=_module(raises=True))
    reg = FunctionRegistry(mods)
    assert reg.provider('test.echo') == 'bad'           # the valid declaration counts
    assert reg.status('bad')['problems'] == [
        {'function': 'test.echo', 'problem': 'has code that does not take its declared parameters'},
        {'function': '?', 'problem': 'returned something that is not a Function'},
        {'function': 'test.echo', 'problem': 'declares this name twice'}]
    assert reg.status('broken')['problems'] == [
        {'function': '*', 'problem': 'failed to list its functions (RuntimeError)'}]


def test_missing_function():
    reg = FunctionRegistry(_manager())
    with pytest.raises(FunctionUnavailable, match='No enabled module provides test.echo'):
        reg.call('test.echo', text='x')
    assert reg.problem_with('test.echo', {}) == 'no enabled module provides test.echo'


def test_routes_find_and_problem_with():
    reg = FunctionRegistry(_manager(web=_module(
        echo(route='/web/echo'),
        Function('test.ask', lambda: {}, 'x', assistant=True, record_kind='r',
                 assistant_fields=('id',)))))
    assert reg.route('test.echo') == '/web/echo' and reg.route('test.ask') is None
    assert [f.name for f in reg.find()] == ['test.ask', 'test.echo']
    assert [f.name for f in reg.find(assistant=True)] == ['test.ask']
    assert reg.problem_with('test.echo', {}) == 'text is required'
    assert reg.problem_with('test.echo', {'text': 'ok'}) is None


# --- the module manager -------------------------------------------------------- #

def _fake(module_id, deps=(), functions=()):
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
            return list(functions)
    return Fake


@pytest.fixture
def mm(app, loaded_modules, monkeypatch):
    fakes = {
        'fn_impl': _fake('fn_impl', functions=[echo()]),
        'fn_user': _fake('fn_user', deps=['test.echo']),
        'fn_core_user': _fake('fn_core_user', deps=['ledger.list_invoices']),
        'fn_lost': _fake('fn_lost', deps=['test.nothing']),
        'fn_rival': _fake('fn_rival', functions=[
            Function('ledger.list_customers', lambda: {'data': []}, 'Rival customer list.')]),
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


def test_module_cards_show_functions_and_claims(mm, app, client):
    mm.modules['fn_impl'] = mm.discovered['fn_impl'](mm.core)
    mm.modules['fn_rival'] = mm.discovered['fn_rival'](mm.core)
    states = {s['module_id']: s for s in mm.get_all_module_states()}
    assert states['fn_impl']['functions'] == ['test.echo(text, times?)']
    assert states['fn_rival']['function_conflicts'] == [
        {'function': 'ledger.list_customers', 'owner': CORE}]
    assert states['fn_user']['functions'] == []
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/settings').get_data(as_text=True)
    assert '<code>ledger.list_customers</code> is already provided by <strong>core</strong>' in html


def test_core_services_expose_the_registry(loaded_modules):
    assert loaded_modules.core.functions is loaded_modules.functions


def test_bundled_modules_declare_valid_functions_without_conflicts(loaded_modules):
    assert loaded_modules.functions.check_all() == ([], [])


# --- the core's functions -------------------------------------------------- #

@pytest.fixture
def invoices(app, loaded_modules):
    from contaautonomo.app import Customer, Invoice, db
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
    from contaautonomo.utils.api_errors import ApiError
    from modules.api.index import ApiError as Reexported
    assert Reexported is ApiError
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(p.relative_to(root)) for p in root.glob('modules/*/*.py')
                 if re.search(r'from modules\.api\.index import ApiError', p.read_text())]
    assert not offenders, offenders
