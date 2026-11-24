"""The core's routes, one file per area. Importing this package registers
them on the app object (`contaautonomo.web.app`); `contaautonomo.app` imports
it. The route files import from `contaautonomo.web`, never from the app
module, so there is no import cycle."""
from . import (contractors, customers, dashboard, invoices, messages,  # noqa: F401
               settings, system)
