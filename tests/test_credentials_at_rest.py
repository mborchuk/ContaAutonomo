"""C-2 — stored service credentials are encrypted at rest."""
import json

import pytest
from sqlalchemy import text

import secret_box

SECRET = 'plain-credential-value-123'


def _raw(db, sql):
    return db.session.execute(text(sql)).fetchone()[0]


def test_encrypt_round_trip_and_passthrough():
    token = secret_box.encrypt(SECRET)
    assert token.startswith(secret_box.PREFIX) and SECRET not in token
    assert secret_box.decrypt(token) == SECRET
    assert secret_box.encrypt(token) == token          # idempotent
    assert secret_box.encrypt('') == '' and secret_box.encrypt(None) is None
    assert secret_box.decrypt('legacy-plaintext') == 'legacy-plaintext'


def test_wrong_key_reads_empty(monkeypatch):
    token = secret_box.encrypt(SECRET)
    monkeypatch.setenv('CREDENTIALS_KEY', 'another-key')
    assert secret_box.decrypt(token) == ''


def test_key_file_created_private(tmp_path, monkeypatch):
    import os
    import stat
    key_file = tmp_path / 'instance' / 'credentials.key'
    monkeypatch.delenv('CREDENTIALS_KEY', raising=False)
    monkeypatch.setenv('CREDENTIALS_KEY_FILE', str(key_file))
    token = secret_box.encrypt(SECRET)
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert secret_box.decrypt(token) == SECRET


def test_core_currency_key_stored_encrypted_and_migrated(app):
    from app import Settings, db, init_database
    s = Settings.query.first() or Settings()
    s.currency_provider_api_key = SECRET
    db.session.add(s)
    db.session.commit()
    raw = _raw(db, 'SELECT currency_provider_api_key FROM settings ORDER BY id LIMIT 1')
    assert SECRET not in raw
    db.session.expire_all()
    assert Settings.query.first().currency_provider_api_key == SECRET

    # Legacy plaintext row -> encrypted by the start-up migration.
    db.session.execute(text('UPDATE settings SET currency_provider_api_key = :v'), {'v': SECRET})
    db.session.commit()
    init_database()
    raw = _raw(db, 'SELECT currency_provider_api_key FROM settings ORDER BY id LIMIT 1')
    assert raw.startswith(secret_box.PREFIX)
    db.session.expire_all()
    assert Settings.query.first().currency_provider_api_key == SECRET


def test_backup_password_encrypted_and_migrated(loaded_modules):
    from app import db
    mod = loaded_modules.modules['backup']
    cfg = mod._get_config()
    cfg.custom_password = SECRET
    db.session.commit()
    assert SECRET not in _raw(db, 'SELECT custom_password FROM backup_config LIMIT 1')
    db.session.execute(text('UPDATE backup_config SET custom_password = :v'), {'v': SECRET})
    db.session.commit()
    mod.on_enable()
    assert _raw(db, 'SELECT custom_password FROM backup_config LIMIT 1').startswith(
        secret_box.PREFIX)
    db.session.expire_all()
    assert mod._get_config().custom_password == SECRET
    mod._get_config().custom_password = ''
    db.session.commit()


def test_smtp_password_encrypted_in_json_and_migrated(loaded_modules):
    from app import db
    mod = loaded_modules.modules['invoice_email']
    cfg = mod._smtp_config()
    cfg['password'] = SECRET
    mod._save_smtp_config(cfg)
    raw = _raw(db, "SELECT value FROM invoice_email_config WHERE key = 'smtp'")
    assert SECRET not in raw
    assert mod._smtp_config()['password'] == SECRET

    legacy = json.loads(raw)
    legacy['password'] = SECRET
    db.session.execute(text("UPDATE invoice_email_config SET value = :v WHERE key = 'smtp'"),
                       {'v': json.dumps(legacy)})
    db.session.commit()
    assert mod._smtp_config()['password'] == SECRET   # legacy still readable
    mod._encrypt_stored_password()
    assert SECRET not in _raw(db, "SELECT value FROM invoice_email_config WHERE key = 'smtp'")


@pytest.mark.parametrize('module_id,table,attr', [
    ('external_storage', 'storage_config', 's3_secret_key'),
    ('ai_parser', 'ai_parser_config', 'openai_api_key'),
    ('pdf_signature', 'pdf_signature_config', 'certificate_password'),
])
def test_module_credential_columns_encrypted(loaded_modules, module_id, table, attr):
    from app import db
    inst = loaded_modules.discovered[module_id](loaded_modules.core)
    inst.register_models(db)
    db.create_all()
    cfg = inst._get_config()
    setattr(cfg, attr, SECRET)
    db.session.commit()
    raw = _raw(db, f'SELECT {attr} FROM {table} LIMIT 1')
    assert raw.startswith(secret_box.PREFIX) and SECRET not in raw
    db.session.expire_all()
    assert getattr(inst._get_config(), attr) == SECRET


def test_database_dump_reveals_no_credential(loaded_modules):
    """The backup JSON (raw SQL dump) contains ciphertext only."""
    from app import Settings, db
    s = Settings.query.first() or Settings()
    s.currency_provider_api_key = SECRET
    db.session.add(s)
    mod = loaded_modules.modules['invoice_email']
    cfg = mod._smtp_config()
    cfg['password'] = SECRET
    mod._save_smtp_config(cfg)
    db.session.commit()
    dump = loaded_modules.modules['backup']._dump_db_json()
    assert SECRET not in dump
