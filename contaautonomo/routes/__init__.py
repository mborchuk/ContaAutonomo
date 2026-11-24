"""The core's routes, one file per area. Importing this package registers
them on `contaautonomo.app.app`; app.py does so once its helpers exist."""
from . import (contractors, customers, dashboard, invoices, messages,  # noqa: F401
               settings, system)
