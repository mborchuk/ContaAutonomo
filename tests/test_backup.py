"""Backups run unattended with the default settings, and restore round-trips.

All data is synthetic; files are written under pytest's tmp_path only.
"""
import os
import stat
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def bk(loaded_modules, tmp_path, monkeypatch):
    """The backup module, isolated to tmp_path, with default configuration."""
    from app import Settings, db
    from module_manager import CoreServices

    module = loaded_modules.modules['backup']
    root = tmp_path / 'app_root'
    root.mkdir()
    monkeypatch.setattr(CoreServices, 'app_path', property(lambda self: str(root)))
    monkeypatch.setitem(module.core.app.config, 'BACKUP_KEY_FILE',
                        str(tmp_path / 'instance' / 'backup.key'))
    monkeypatch.delenv('BACKUP_KEY', raising=False)

    module.BackupConfig.query.delete()
    db.session.commit()
    cfg = module._get_config()  # fresh row with the defaults
    cfg.backup_path = str(tmp_path / 'backups')
    if not Settings.query.first():
        db.session.add(Settings())
    db.session.commit()
    return module


def _backups(module):
    return sorted(p.name for p in module._backup_dir().iterdir())


def test_backup_module_enabled_by_default(loaded_modules):
    mm = loaded_modules
    model = mm._get_module_enabled_model()
    assert model.query.filter_by(module_id='backup').first() is None
    assert mm.is_enabled('backup')
    assert 'backup' in mm.modules
    assert not mm.is_enabled('tax_poland')


def test_disabling_a_default_enabled_module_persists(loaded_modules):
    from app import db

    mm = loaded_modules
    model = mm._get_module_enabled_model()
    module = mm.modules['backup']
    try:
        mm.disable_module('backup')
        assert mm.is_enabled('backup') is False
    finally:
        model.query.filter_by(module_id='backup').delete()
        db.session.commit()
        mm.modules['backup'] = module


def test_defaults_encrypt_with_backup_key_and_auto_backup_on(bk):
    from app import Settings

    assert bk._get_config().encrypt_method == 'key'
    assert Settings().auto_backup_enabled is not False  # column default
    assert Settings.query.first().auto_backup_enabled is True


def test_scheduled_backup_runs_with_defaults(bk, tmp_path):
    bk._scheduled_backup()

    files = _backups(bk)
    assert len(files) == 1 and files[0].endswith('.zip.enc')
    key_file = tmp_path / 'instance' / 'backup.key'
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    raw = (bk._backup_dir() / files[0]).read_bytes()
    assert bk._decrypt_bytes(raw, key_file.read_text().strip())[:2] == b'PK'


def test_backup_key_from_environment(bk, tmp_path, monkeypatch):
    monkeypatch.setenv('BACKUP_KEY', 'synthetic-env-key')
    assert bk._backup_key() == 'synthetic-env-key'
    assert not (tmp_path / 'instance' / 'backup.key').exists()


def test_legacy_app_password_config_switches_to_key(bk):
    from app import db

    cfg = bk._get_config()
    cfg.encrypt_method = 'app_password'
    db.session.commit()
    bk.on_enable()
    assert bk._get_config().encrypt_method == 'key'


def test_restore_round_trip_database_and_files(bk):
    from app import Customer, Invoice, db
    from modules.backup.index import FILE_FOLDERS

    root = bk.core.app_path
    db.session.add(Customer(name='ACME Synthetic SL'))
    db.session.add(Invoice(invoice_number='2026/0001', client_name='ACME Synthetic SL',
                           amount_usd=100, amount_eur=100, exchange_rate=1,
                           invoice_date=date(2026, 1, 15), status='issued',
                           series='2026', sequence_number=1, snap_vat_rate=21.0))
    db.session.commit()
    for folder in FILE_FOLDERS:
        os.makedirs(os.path.join(root, folder))
        with open(os.path.join(root, folder, 'synthetic.txt'), 'w') as f:
            f.write(folder)

    ok, filename = bk._create_full_backup(password=bk._backup_key())
    assert ok, filename

    Invoice.query.delete()
    Customer.query.delete()
    db.session.commit()
    for folder in FILE_FOLDERS:
        os.remove(os.path.join(root, folder, 'synthetic.txt'))

    ok, msg = bk._restore_full_backup(filename, bk._decryption_candidates())
    assert ok, msg
    db.session.expire_all()
    inv = Invoice.query.one()
    assert (inv.invoice_number, inv.series, inv.snap_vat_rate) == ('2026/0001', '2026', 21.0)
    assert Customer.query.one().name == 'ACME Synthetic SL'
    for folder in FILE_FOLDERS:
        with open(os.path.join(root, folder, 'synthetic.txt')) as f:
            assert f.read() == folder


def test_restore_legacy_app_password_backup_with_login_password(bk, tmp_path, monkeypatch):
    """Backups made with the old 'app_password' method restore when the owner
    types the login password; no key is kept in the session any more."""
    from auth import auth_manager

    monkeypatch.setattr(auth_manager, 'config_file', str(tmp_path / 'auth_config.json'))
    auth_manager.setup_password('synthetic-login-pass')
    legacy_key = auth_manager.get_encryption_key('synthetic-login-pass').decode()
    ok, filename = bk._create_full_backup(password=legacy_key)
    assert ok, filename

    ok, msg = bk._restore_full_backup(filename, bk._decryption_candidates())
    assert not ok
    ok, msg = bk._restore_full_backup(
        filename, bk._decryption_candidates('synthetic-login-pass'))
    assert ok, msg


def test_wrong_key_does_not_restore(bk):
    ok, filename = bk._create_full_backup(password='synthetic-other-key')
    assert ok
    ok, msg = bk._restore_full_backup(filename, bk._decryption_candidates())
    assert not ok and 'Decryption failed' in msg


def test_backups_in_the_same_second_do_not_overwrite(bk):
    now = datetime.now()
    for t in (now, now + timedelta(seconds=1)):
        (bk._backup_dir() / f"backup_{t.strftime('%Y%m%d_%H%M%S')}.zip").write_bytes(b'x')

    ok, filename = bk._create_full_backup()
    assert ok
    assert filename.endswith('_2.zip')
    assert len(_backups(bk)) == 3
