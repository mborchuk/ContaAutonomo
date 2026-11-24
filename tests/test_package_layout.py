"""The core is the `contaautonomo` package; the repository root holds no
Python code. Old top-level import names (2.x) still work, as the same module
objects, with a DeprecationWarning."""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from contaautonomo import _legacy, paths

ROOT = Path(__file__).resolve().parent.parent


def test_no_python_file_at_the_repository_root():
    tracked = subprocess.run(['git', 'ls-files', '*.py'], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    assert [f for f in tracked if '/' not in f] == []


@pytest.mark.parametrize('old, new', sorted(_legacy.OLD_NAMES.items()))
def test_old_import_names_are_the_same_modules(old, new):
    sys.modules.pop(old, None)
    with pytest.warns(DeprecationWarning, match=new):
        legacy = importlib.import_module(old)
    assert legacy is importlib.import_module(new)


def test_old_names_share_the_one_database():
    sys.modules.pop('app', None)
    with pytest.warns(DeprecationWarning):
        from app import db  # noqa: F401 — the 2.x name
    from contaautonomo.app import db as current
    assert db is current


def test_data_folder_defaults_to_the_repository_root(monkeypatch):
    monkeypatch.delenv('CONTAAUTONOMO_DATA_DIR', raising=False)
    assert paths.data_dir() == str(ROOT)
    assert paths.instance_dir() == str(ROOT / 'instance')
    assert paths.MODULES_DIR == str(ROOT / 'modules')


def test_data_folder_can_be_moved(monkeypatch, tmp_path):
    monkeypatch.setenv('CONTAAUTONOMO_DATA_DIR', str(tmp_path))
    assert paths.data_dir() == str(tmp_path)
    assert paths.instance_dir() == str(tmp_path / 'instance')


def test_the_suite_keeps_its_data_out_of_the_checkout(loaded_modules):
    assert not paths.data_dir().startswith(str(ROOT))
    assert loaded_modules.core.app_path == paths.data_dir()
