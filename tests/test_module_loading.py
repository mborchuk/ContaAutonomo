"""F13-D3 — every module in modules/ discovers, instantiates and loads.

Catches broken index.py files, junk directories, and modules that raise during
register_models/register_routes — failures that otherwise get logged and
silently skipped at startup.
"""
import pytest


def _discovered_ids():
    import os
    base = os.path.join(os.path.dirname(__file__), '..', 'modules')
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, 'index.py')):
            out.append(name)
    return out


def test_all_module_dirs_have_index(loaded_modules):
    """Junk dirs (leftover __pycache__ etc.) must not shadow real modules."""
    import os
    base = os.path.join(os.path.dirname(__file__), '..', 'modules')
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if not os.path.isdir(path) or name.startswith('__'):
            continue
        assert os.path.exists(os.path.join(path, 'index.py')), \
            f'modules/{name} has no index.py — dead directory, delete it'


@pytest.mark.parametrize('module_id', _discovered_ids())
def test_module_discovers_and_instantiates(loaded_modules, module_id):
    mm = loaded_modules
    assert module_id in mm.discovered, f'{module_id} not discovered'
    instance = mm.discovered[module_id](mm.core)
    assert instance.module_id == module_id, \
        f'{module_id}: module_id property does not match directory name'
    assert instance.name
