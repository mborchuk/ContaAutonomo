"""
Function registry: named functions that the core and modules implement and
other code calls by name, so an implementation can be replaced without the
callers changing.

A *contract* (contracts.py) defines a function once: name, version,
parameters, what it returns, and flags (read only, sends data out of the
app, offered to the ledger assistant). The core registers its own
implementations (core_functions.py); a module returns its implementations
from `BaseModule.get_functions()`. Callers use `registry.call(name, ...)`,
which checks the arguments against the contract before the implementation
runs.

Which implementation runs: the core's when it has one, otherwise the first
enabled module in id order. Choosing another one in Settings is a later step.
"""
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

CORE = 'core'
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_KINDS = ('str', 'int', 'date', 'bytes')


class FunctionUnavailable(LookupError):
    """No enabled implementation of the function (or no such contract)."""


class InvalidArguments(ValueError):
    """The arguments do not match the contract. The message names the
    parameter, never its value, so it can be shown to a user."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str                      # one of _KINDS
    description: str = ''
    required: bool = False
    choices: tuple = ()
    minimum: int | None = None     # int only
    maximum: int | None = None     # int only
    max_length: int = 200          # str only

    def __post_init__(self):
        if self.kind not in _KINDS:
            raise ValueError(f'Param {self.name}: unknown kind {self.kind!r}')

    def clean(self, value):
        """(value converted to its kind, None) or (None, problem). The problem
        names the parameter, never the value."""
        if self.kind == 'date':
            if isinstance(value, date):
                return value, None
            if isinstance(value, str) and _DATE.match(value):
                try:
                    return date.fromisoformat(value), None
                except ValueError:
                    pass
            return None, f'{self.name} must be a date (YYYY-MM-DD)'
        if self.kind == 'int':
            if isinstance(value, bool):
                return None, f'{self.name} must be a whole number'
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None, f'{self.name} must be a whole number'
            if ((self.minimum is not None and value < self.minimum)
                    or (self.maximum is not None and value > self.maximum)):
                return None, f'{self.name} must be between {self.minimum} and {self.maximum}'
        elif self.kind == 'bytes':
            if not isinstance(value, (bytes, bytearray)):
                return None, f'{self.name} must be bytes'
            return bytes(value), None
        else:
            if not isinstance(value, str):
                return None, f'{self.name} must be text'
            value = value.strip()
            if len(value) > self.max_length:
                return None, f'{self.name} must be at most {self.max_length} characters'
        if self.choices and value not in self.choices:
            return None, f'{self.name} must be one of: {", ".join(map(str, self.choices))}'
        return value, None


@dataclass(frozen=True)
class Contract:
    name: str                      # 'area.verb_object', e.g. 'ledger.list_invoices'
    version: int
    summary: str                   # for people and for AI tool descriptions
    params: tuple = ()
    returns: str = ''
    read_only: bool = True         # changes nothing in the app
    external: bool = False         # sends data outside the app
    assistant: bool = False        # the ledger assistant may call it
    record_kind: str | None = None  # how the assistant groups the results
    assistant_fields: tuple = field(default=())  # the only fields it may send out

    def __post_init__(self):
        if self.assistant and not self.read_only:
            raise ValueError(f'{self.name}: only a read-only function may be offered '
                             'to the assistant')
        if self.assistant and self.external:
            raise ValueError(f'{self.name}: the assistant may not call a function '
                             'that sends data out')

    @property
    def param_names(self):
        return tuple(p.name for p in self.params)

    def validate(self, arguments):
        """(clean arguments, None) or (None, problem): unknown names and
        missing required ones refused; None or '' means "not given"."""
        known = {p.name: p for p in self.params}
        unknown = sorted(set(arguments) - set(known))
        if unknown:
            return None, f'parameter {unknown[0]!r} is not allowed'
        clean = {}
        for p in self.params:
            value = arguments.get(p.name)
            if value is None or (isinstance(value, str) and not value.strip()):
                if p.required:
                    return None, f'{p.name} is required'
                continue
            value, problem = p.clean(value)
            if problem:
                return None, problem
            clean[p.name] = value
        return clean, None

    def check(self, arguments):
        """The cleaned arguments, or InvalidArguments."""
        clean, problem = self.validate(arguments)
        if problem:
            raise InvalidArguments(problem)
        return clean


def _normalise(entry):
    """{'call': callable, 'route': str|None} from a callable or a dict."""
    if isinstance(entry, dict):
        return {'call': entry.get('call'), 'route': entry.get('route')}
    return {'call': entry, 'route': None}


class FunctionRegistry:
    """Contracts, the core's implementations, and lookup across the loaded
    modules of a ModuleManager. Module implementations are read from
    `get_functions()` on every lookup, so enabling or disabling a module
    takes effect at once."""

    def __init__(self, module_manager=None, contracts=None):
        if contracts is None:
            from contracts import CONTRACTS
            contracts = CONTRACTS
        self.contracts = dict(contracts)
        self._mm = module_manager
        self._core = {}
        self._reported = set()

    # --- Registration ------------------------------------------------------ #

    def problem(self, name, entry):
        """Why `entry` cannot implement `name`, or None when it can."""
        contract = self.contracts.get(name)
        if contract is None:
            return 'implements an unknown function'
        fn = _normalise(entry)['call']
        if not callable(fn):
            return 'is not callable'
        try:
            inspect.signature(fn).bind(**{n: None for n in contract.param_names})
        except TypeError:
            return 'does not take the parameters of the contract'
        except ValueError:
            pass  # no signature available (C function): accept
        return None

    def register_core(self, name, entry):
        """Register the core's implementation of `name`."""
        problem = self.problem(name, entry)
        if problem:
            raise ValueError(f'core {problem}: {name}')
        self._core[name] = _normalise(entry)

    def _report(self, provider, name, problem):
        key = (provider, name, problem)
        if key not in self._reported:
            self._reported.add(key)
            logger.warning("Function '%s' of '%s' ignored: it %s", name, provider, problem)

    def _module_entries(self, module_id, module):
        try:
            raw = module.get_functions() or {}
        except Exception as e:
            self._report(module_id, '*', f'failed to list its functions ({type(e).__name__})')
            return {}
        out = {}
        for name, entry in raw.items():
            problem = self.problem(name, entry)
            if problem:
                self._report(module_id, name, problem)
            else:
                out[name] = _normalise(entry)
        return out

    # --- Lookup ------------------------------------------------------------ #

    def implementations(self, name):
        """[(provider id, {'call', 'route'})] in the order they are used."""
        found = []
        if name in self._core:
            found.append((CORE, self._core[name]))
        modules = dict(self._mm.modules) if self._mm is not None else {}
        for module_id in sorted(modules):
            entry = self._module_entries(module_id, modules[module_id]).get(name)
            if entry is not None:
                found.append((module_id, entry))
        return found

    def core_provides(self, name):
        return name in self._core

    def provider(self, name):
        """The id ('core' or a module id) whose implementation runs, or None."""
        found = self.implementations(name)
        return found[0][0] if found else None

    def available(self, name):
        return self.provider(name) is not None

    def route(self, name):
        """The URL path the running implementation serves for a browser, or
        None (for functions that also have a page or form endpoint)."""
        found = self.implementations(name)
        return found[0][1]['route'] if found else None

    def problem_with(self, name, arguments):
        """What is wrong with `arguments` for `name`, or None. For callers
        that show the problem to a user without catching an exception."""
        contract = self.contracts.get(name)
        if contract is None:
            return f'there is no function {name}'
        return contract.validate(arguments)[1]

    def call(self, name, **arguments):
        """Run the function with arguments checked against its contract."""
        contract = self.contracts.get(name)
        if contract is None:
            raise FunctionUnavailable(f'There is no function {name}')
        found = self.implementations(name)
        if not found:
            raise FunctionUnavailable(f'No enabled module provides {name}')
        return found[0][1]['call'](**contract.check(arguments))

    def find(self, **flags):
        """Contracts with a running implementation whose attributes match
        `flags` (e.g. assistant=True), sorted by name."""
        return [c for name, c in sorted(self.contracts.items())
                if all(getattr(c, k) == v for k, v in flags.items()) and self.available(name)]

    def check_all(self):
        """Log every invalid registration of the loaded modules; returns them
        as [(module id, function, problem)]."""
        problems = []
        modules = dict(self._mm.modules) if self._mm is not None else {}
        for module_id in sorted(modules):
            try:
                raw = modules[module_id].get_functions() or {}
            except Exception as e:
                problems.append((module_id, '*', f'failed to list its functions ({type(e).__name__})'))
                continue
            for name, entry in raw.items():
                problem = self.problem(name, entry)
                if problem:
                    problems.append((module_id, name, problem))
        for module_id, name, problem in problems:
            self._report(module_id, name, problem)
        return problems
