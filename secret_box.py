"""Encryption at rest for stored service credentials (task C-2).

SMTP, cloud storage, AI provider and currency provider credentials, the custom
backup password, the PDF certificate password and the API token are stored
encrypted, so a copy of the database (or of a backup) alone does not reveal
them.

Key: the ``CREDENTIALS_KEY`` environment variable, else the key file
``instance/credentials.key`` (override with ``CREDENTIALS_KEY_FILE``), created
with mode 0600 on first use. Keep a copy in a password manager. If the key is
lost, stored credentials cannot be decrypted: they read as empty and have to
be entered again in Settings — no other data is affected.

Stored format: ``enc:v1:<Fernet token>``. Values without the prefix are legacy
plaintext; they are still read as-is and are encrypted by the one-time
migration (:func:`encrypt_existing`) when the app or module starts.
"""
import base64
import hashlib
import logging
import os
import secrets

from sqlalchemy.types import String, TypeDecorator

logger = logging.getLogger(__name__)

PREFIX = 'enc:v1:'
ENV_VAR = 'CREDENTIALS_KEY'
FILE_ENV_VAR = 'CREDENTIALS_KEY_FILE'

_fernet_cache = {}


def _key_file_path():
    path = os.environ.get(FILE_ENV_VAR, '').strip()
    if path:
        return path
    try:
        from flask import current_app
        configured = current_app.config.get(FILE_ENV_VAR)
        if configured:
            return configured
        return os.path.join(current_app.instance_path, 'credentials.key')
    except RuntimeError:  # no app context (scripts)
        return os.path.join('instance', 'credentials.key')


def _load_key():
    key = os.environ.get(ENV_VAR, '').strip()
    if key:
        return key
    path = _key_file_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32) + '\n')
        logger.warning('Created credentials key file %s — keep a copy in a password '
                       'manager: stored credentials cannot be decrypted without it', path)
    with open(path) as f:
        return f.read().strip()


def _fernet():
    from cryptography.fernet import Fernet
    key = _load_key()
    if key not in _fernet_cache:
        derived = hashlib.sha256(f'contaautonomo-credentials:{key}'.encode()).digest()
        _fernet_cache.clear()
        _fernet_cache[key] = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet_cache[key]


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    """Encrypt a credential. Empty values and already-encrypted values pass through."""
    if value is None or value == '' or is_encrypted(value):
        return value
    token = _fernet().encrypt(str(value).encode('utf-8')).decode('ascii')
    return PREFIX + token


def decrypt(value):
    """Decrypt a stored credential. Legacy plaintext is returned unchanged.

    Returns '' (and logs) when the value was encrypted with another key.
    """
    if not is_encrypted(value):
        return value
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')
    except InvalidToken:
        logger.error('A stored credential cannot be decrypted with the current '
                     'credentials key; enter it again in Settings')
        return ''


class EncryptedString(TypeDecorator):
    """String column whose value is encrypted with :func:`encrypt` on write and
    decrypted on read. Raw SQL (backups) sees the ciphertext."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)


def encrypt_existing(db, table, columns):
    """One-time, idempotent migration: encrypt plaintext values in place.

    `db` is the Flask-SQLAlchemy object. Returns the number of values
    encrypted. Table and column names come from code, never from user input.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.session.connection())
    if table not in inspector.get_table_names():
        return 0
    present = {c['name'] for c in inspector.get_columns(table)}
    cols = [c for c in columns if c in present]
    if not cols:
        return 0
    changed = 0
    rows = db.session.execute(text(
        f'SELECT id, {", ".join(cols)} FROM "{table}"')).fetchall()
    for row in rows:
        for i, col in enumerate(cols, start=1):
            value = row[i]
            if value and not is_encrypted(value):
                db.session.execute(text(f'UPDATE "{table}" SET {col} = :v WHERE id = :id'),
                                   {'v': encrypt(value), 'id': row[0]})
                changed += 1
    db.session.commit()
    if changed:
        logger.info('Encrypted %d stored credential(s) in %s', changed, table)
    return changed
