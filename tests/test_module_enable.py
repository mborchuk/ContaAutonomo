"""Enabling a module while the app is serving: saved, loaded on the next start."""


def test_enable_while_serving_defers_load_without_error(loaded_modules, client):
    from app import db

    mm = loaded_modules
    module_id = 'invoice_comments'  # has a blueprint; not enabled by the fixture
    model = mm._get_module_enabled_model()
    assert module_id in mm.discovered and module_id not in mm.modules

    client.get('/health')  # the app has now served a request
    try:
        mm.enable_module(module_id)

        assert mm.is_enabled(module_id)              # choice saved
        assert module_id not in mm.failed_modules    # no "failed to load"
        assert module_id not in mm.modules           # loads after a restart
        state = next(m for m in mm.get_all_module_states()
                     if m['module_id'] == module_id)
        assert state['restart_required'] and not state['load_error']
    finally:
        model.query.filter_by(module_id=module_id).delete()
        db.session.commit()
        mm.failed_modules.pop(module_id, None)


def test_loaded_modules_are_not_marked_restart_required(loaded_modules):
    from app import db

    mm = loaded_modules
    model = mm._get_module_enabled_model()
    if not model.query.filter_by(module_id='expenses').first():
        db.session.add(model(module_id='expenses', enabled=True))
        db.session.commit()
    states = {m['module_id']: m for m in mm.get_all_module_states()}
    assert 'expenses' in mm.modules and states['expenses']['enabled']
    assert not states['expenses']['restart_required']
