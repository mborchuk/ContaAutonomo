"""
Function registry: named functions that the core and modules declare,
implement and call by name, so an implementation can be replaced without its
callers changing — also by a module from outside this repository.

A module declares each function it provides in its own folder, description
and code together, as a `Function` returned from `BaseModule.get_functions()`:
name, parameters, what it returns, flags (read only, sends data out of the
app, offered to the ledger assistant) and the callable. The core declares its
own the same way (core_functions.py).

The first provider to register a name owns it: the core first, then the
modules in the order they load. A later module declaring the same name is
not used for it; the registry records a warning that the Modules tab shows on
that module's card, with the owner's name. When the owner is disabled, the
next declaration in load order takes over.

Callers use `registry.call(name, ...)`, which checks the arguments against the
owner's declaration before its code runs.
"""
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

CORE = 'core'
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_NAME = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
_KINDS = ('str', 'int', 'date', 'bytes')


class FunctionUnavailable(LookupError):
    """No enabled provider of the function, or it cannot run now."""


class InvalidArguments(ValueError):
    """The arguments do not match the declaration. The message names the
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
class Function:
    """One function a provider declares: its description and its code."""
    name: str                      # 'area.verb_object', e.g. 'ledger.list_invoices'
    call: object                   # the callable; takes every param by name
    summary: str                   # for people and for AI tool descriptions
    params: tuple = ()
    returns: str = ''
    version: int = 1
    read_only: bool = True         # changes nothing in the app
    external: bool = False         # sends data outside the app
    assistant: bool = False        # the ledger assistant may call it
    record_kind: str | None = None  # how the assistant groups the results
    assistant_fields: tuple = field(default=())  # the only fields it may send out
    route: str | None = None       # URL a browser form posts to, if any

    @property
    def param_names(self):
        return tuple(p.name for p in self.params)

    @property
    def signature(self):
        """'ledger.list_invoices(date_from?, date_to?, …)' — '?' = optional."""
        names = [p.name + ('' if p.required else '?') for p in self.params]
        return f'{self.name}({", ".join(names)})'

    def problem(self):
        """Why this declaration cannot be used, or None."""
        if not isinstance(self.name, str) or not _NAME.match(self.name):
            return "has no valid name ('area.verb_object')"
        if not callable(self.call):
            return 'has no callable'
        if not self.summary:
            return 'has no summary'
        if not all(isinstance(p, Param) for p in self.params):
            return 'declares a parameter that is not a Param'
        if self.assistant and (not self.read_only or self.external):
            return 'is offered to the assistant but is not read-only or sends data out'
        if self.assistant and not (self.record_kind and self.assistant_fields):
            return 'is offered to the assistant without record_kind and assistant_fields'
        try:
            inspect.signature(self.call).bind(**{n: None for n in self.param_names})
        except TypeError:
            return 'has code that does not take its declared parameters'
        except ValueError:
            pass  # no signature available (C function): accept
        return None

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


class FunctionRegistry:
    """The core's functions plus those of the loaded modules of a
    ModuleManager, in registration order. Module declarations are read from
    `get_functions()` on every lookup, so enabling or disabling a module takes
    effect at once."""

    def __init__(self, module_manager=None):
        self._mm = module_manager
        self._core = {}
        self._reported = set()

    # --- Registration ------------------------------------------------------ #

    def register_core(self, function):
        """Register one of the core's functions; refuses a bad declaration."""
        problem = function.problem() if isinstance(function, Function) else 'is not a Function'
        if problem:
            raise ValueError(f'core function {getattr(function, "name", function)!r} {problem}')
        if function.name in self._core:
            raise ValueError(f'core function {function.name!r} is registered twice')
        self._core[function.name] = function

    @staticmethod
    def declarations(module):
        """(functions, problems) a module declares: [Function], [(name, problem)]."""
        try:
            raw = list(module.get_functions() or [])
        except Exception as e:
            return [], [('*', f'failed to list its functions ({type(e).__name__})')]
        functions, problems = [], []
        for entry in raw:
            if not isinstance(entry, Function):
                problems.append(('?', 'returned something that is not a Function'))
                continue
            problem = entry.problem()
            if problem:
                problems.append((str(entry.name), problem))
            else:
                functions.append(entry)
        return functions, problems

    def _providers(self):
        modules = list(self._mm.modules.items()) if self._mm is not None else []
        return [(CORE, None)] + modules

    def _resolve(self):
        """(owners {name: (provider, Function)}, conflicts, problems): the first
        provider in registration order owns a name; later ones conflict."""
        owners, conflicts, problems = {}, [], []
        for provider, module in self._providers():
            if module is None:
                declared = list(self._core.values())
            else:
                declared, bad = self.declarations(module)
                problems += [(provider, name, p) for name, p in bad]
            seen = set()
            for fn in declared:
                if fn.name in seen:
                    problems.append((provider, fn.name, 'declares this name twice'))
                    continue
                seen.add(fn.name)
                if fn.name in owners:
                    conflicts.append((provider, fn.name, owners[fn.name][0]))
                else:
                    owners[fn.name] = (provider, fn)
        return owners, conflicts, problems

    # --- Lookup ------------------------------------------------------------ #

    def get(self, name):
        """The owner's declaration of `name`, or None."""
        owner = self._resolve()[0].get(name)
        return owner[1] if owner else None

    def provider(self, name):
        """The id ('core' or a module id) that owns `name`, or None."""
        owner = self._resolve()[0].get(name)
        return owner[0] if owner else None

    def available(self, name):
        return self.get(name) is not None

    def core_provides(self, name):
        return name in self._core

    def route(self, name):
        fn = self.get(name)
        return fn.route if fn else None

    def problem_with(self, name, arguments):
        """What is wrong with `arguments` for `name`, or None. For callers that
        show the problem to a user without catching an exception."""
        fn = self.get(name)
        if fn is None:
            return f'no enabled module provides {name}'
        return fn.validate(arguments)[1]

    def call(self, name, **arguments):
        """Run the owner's code with arguments checked against its declaration."""
        fn = self.get(name)
        if fn is None:
            raise FunctionUnavailable(f'No enabled module provides {name}')
        return fn.call(**fn.check(arguments))

    def find(self, **flags):
        """Owned functions whose attributes match `flags` (e.g. assistant=True),
        sorted by name."""
        owners = self._resolve()[0]
        return [fn for name, (_, fn) in sorted(owners.items())
                if all(getattr(fn, k) == v for k, v in flags.items())]

    def status(self, module_id):
        """For a module's card: what it provides, what it declares but another
        provider owns, and declarations that cannot be used."""
        owners, conflicts, problems = self._resolve()
        return {
            'provides': sorted(fn.signature for p, fn in owners.values() if p == module_id),
            'conflicts': [{'function': name, 'owner': owner}
                          for p, name, owner in conflicts if p == module_id],
            'problems': [{'function': name, 'problem': problem}
                         for p, name, problem in problems if p == module_id],
        }

    def check_all(self):
        """Log every conflict and bad declaration once; returns
        (conflicts, problems) as lists of tuples."""
        _, conflicts, problems = self._resolve()
        for provider, name, owner in conflicts:
            self._log(('conflict', provider, name, owner),
                      "Function '%s' of '%s' is not used: '%s' already provides it",
                      name, provider, owner)
        for provider, name, problem in problems:
            self._log(('problem', provider, name, problem),
                      "Function '%s' of '%s' is ignored: it %s", name, provider, problem)
        return conflicts, problems

    def _log(self, key, message, *args):
        if key not in self._reported:
            self._reported.add(key)
            logger.warning(message, *args)
