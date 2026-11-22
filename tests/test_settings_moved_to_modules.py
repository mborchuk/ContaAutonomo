"""Settings that only one module uses live in that module (2.0.0): the report
template in Reports, the automatic-backup switch and retention in Backup. An
upgraded database keeps the old core columns; the modules copy their values
once (synthetic values)."""
import pytest
from sqlalchemy import inspect, text

LEGACY = (('report_template', 'VARCHAR(100)'), ('auto_backup_enabled', 'BOOLEAN'),
          ('daily_backup_retention_count', 'INTEGER'))


@pytest.fixture
def legacy_settings(app, loaded_modules):
    """The core settings table as a 1.x database has it, with values."""
    from app import Settings, db
    if not Settings.query.first():
        db.session.add(Settings())
        db.session.commit()
    columns = {c['name'] for c in inspect(db.session.connection()).get_columns('settings')}
    for name, ddl in LEGACY:
        if name not in columns:
            db.session.execute(text(f'ALTER TABLE settings ADD COLUMN {name} {ddl}'))

    def set_values(**values):
        for name, value in values.items():
            db.session.execute(text(f'UPDATE settings SET {name} = :v'), {'v': value})
        db.session.commit()
    yield set_values
    loaded_modules.modules['backup'].BackupConfig.query.delete()
    loaded_modules.modules['reports'].ReportsConfig.query.delete()
    db.session.commit()


def test_the_core_settings_model_has_no_module_columns():
    from app import Settings
    for name, _ddl in LEGACY:
        assert not hasattr(Settings, name)


def test_backup_takes_the_old_values_when_its_config_is_created(legacy_settings,
                                                                 loaded_modules):
    from app import db
    backup = loaded_modules.modules['backup']
    legacy_settings(auto_backup_enabled=0, daily_backup_retention_count=7)
    backup.BackupConfig.query.delete()
    db.session.commit()
    cfg = backup._get_config()
    assert cfg.auto_backup_enabled is False and cfg.daily_backup_retention_count == 7


def test_backup_copies_the_old_values_when_it_adds_the_columns(legacy_settings,
                                                                loaded_modules):
    """An existing 1.x backup_config row without the new columns."""
    from app import db
    backup = loaded_modules.modules['backup']
    backup._get_config()
    legacy_settings(auto_backup_enabled=0, daily_backup_retention_count=9)
    db.session.execute(text('ALTER TABLE backup_config DROP COLUMN auto_backup_enabled'))
    db.session.execute(text('ALTER TABLE backup_config DROP COLUMN daily_backup_retention_count'))
    db.session.commit()
    db.session.expunge_all()
    backup._migrate()
    db.session.expunge_all()
    cfg = backup._get_config()
    assert cfg.auto_backup_enabled is False and cfg.daily_backup_retention_count == 9


def test_reports_takes_the_old_template_and_ignores_a_missing_one(legacy_settings,
                                                                    loaded_modules):
    from app import db
    reports = loaded_modules.modules['reports']
    legacy_settings(report_template='official_template')
    reports.ReportsConfig.query.delete()
    db.session.commit()
    assert reports._get_config().report_template == 'official_template'

    reports.ReportsConfig.query.delete()
    legacy_settings(report_template='../not_a_template')
    assert reports._get_config().report_template == '../not_a_template'
    assert reports._report_template() == 'official_template'    # never loaded


def test_reports_settings_tab_saves_only_a_listed_template(legacy_settings, loaded_modules,
                                                           client):
    reports = loaded_modules.modules['reports']
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    html = client.get('/settings').get_data(as_text=True)
    assert 'id="mod_reports"' in html and 'name="report_template"' in html
    client.post('/settings', data={'_settings_tab': 'reports',
                                   'report_template': '../../app'})
    assert reports._get_config().report_template != '../../app'
    client.post('/settings', data={'_settings_tab': 'reports',
                                   'report_template': 'official_template'})
    assert reports._get_config().report_template == 'official_template'
