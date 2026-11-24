"""Where ContaAutónomo finds its code and keeps its data.

The code lives in this package (`contaautonomo/`) and the plug-in modules in
`modules/` next to it. Runtime data — the database and keys under
`instance/`, `logs/`, `backups/` and the uploaded files (`invoices_pdf/`,
`expenses_files/`, `documents_files/`, …) — lives in the data folder: the
repository root (`/app` in the Docker image), as before the code moved into
the package, so existing installations and volumes keep their paths. Set
`CONTAAUTONOMO_DATA_DIR` to keep the data elsewhere; the test suite points it
at a temporary folder.
"""
import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
MODULES_DIR = os.path.join(PROJECT_ROOT, 'modules')


def data_dir():
    """The folder that holds the runtime data (read each time, so a test can
    set the environment variable before the app starts)."""
    return os.path.abspath(os.environ.get('CONTAAUTONOMO_DATA_DIR') or PROJECT_ROOT)


def instance_dir():
    return os.path.join(data_dir(), 'instance')
