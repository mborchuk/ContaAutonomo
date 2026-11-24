"""ContaAutónomo — the application package.

`contaautonomo.app` is the Flask app; `core/` the module system and the shared
services modules use; `security/` authentication and protection of stored
secrets; `utils/` small shared helpers; `paths.py` where code and data live.
"""
from . import _legacy

_legacy.install()
