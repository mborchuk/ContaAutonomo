"""The import names of the core before it moved into this package.

Modules written for 2.x import the core by top-level names
(`from module_manager import BaseModule`, `from app import db`). Those names
keep working: each one is the same module object as its new name, so `db`,
the models and the module manager are never loaded twice. Importing an old
name emits a DeprecationWarning that gives the new one; the old names go in
the next major version.
"""
import importlib
import importlib.abc
import importlib.util
import sys
import warnings

OLD_NAMES = {
    'app': 'contaautonomo.app',
    **{name: f'contaautonomo.core.{name}' for name in (
        'module_manager', 'functions', 'core_functions', 'message_center',
        'invoice_pdf', 'invoice_amounts', 'pdf_text', 'repositories',
        'schema_migrations')},
    **{name: f'contaautonomo.security.{name}' for name in (
        'auth', 'auth_routes', 'secret_box', 'rate_limit', 'file_validation')},
    **{name: f'contaautonomo.utils.{name}' for name in (
        'constants', 'timeutil', 'db_helpers', 'currency_converter', 'api_errors',
        'settings_forms')},
}


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in OLD_NAMES:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        new = OLD_NAMES[spec.name]
        warnings.warn(f'import {spec.name!r} is deprecated; use {new!r}',
                      DeprecationWarning, stacklevel=3)
        return importlib.import_module(new)

    def exec_module(self, module):
        pass  # the real module is already loaded


def install():
    if not any(isinstance(f, _Alias) for f in sys.meta_path):
        sys.meta_path.append(_Alias())
