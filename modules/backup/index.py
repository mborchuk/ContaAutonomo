#!/usr/bin/env python3
"""
Backup Module
Full backup/restore: DB (JSON) + all uploaded files in a single ZIP archive.
Supports optional AES encryption, custom backup path, and external storage integration.
"""

from module_manager import BaseModule
from settings_forms import secret_from_form
from flask import (Blueprint, request, redirect, url_for,
                   flash, send_file, render_template)
from datetime import datetime, date
from pathlib import Path
from io import BytesIO
import os
import re
import json
import shutil
import zipfile
import logging
from secret_box import EncryptedString, encrypt_existing

logger = logging.getLogger(__name__)


def _sanitize_for_log(value, max_length=200):
    """
    Sanitize potentially user-controlled values before logging.

    - Coerces the value to a string.
    - Removes ASCII control characters (including line breaks) to prevent
      log injection via forged line breaks or terminal control sequences.
    - Truncates overly long values to avoid log flooding.
    """
    # Ensure we are working with a string representation
    if not isinstance(value, str):
        value = str(value)
    # Strip all ASCII control characters (U+0000–U+001F and U+007F), including CR/LF
    control_chars = ''.join(chr(i) for i in range(32)) + chr(127)
    translation_table = str.maketrans('', '', control_chars)
    cleaned = value.translate(translation_table)
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + '…'
    return cleaned


FILE_FOLDERS = ['expenses_files', 'documents_files', 'tax_forms', 'invoices_pdf',
                'invoice_logos', 'pdf_signature_files']

# Encryption methods: 'key' (default) — the deployment's backup key, from the
# BACKUP_KEY environment variable or the key file app.config['BACKUP_KEY_FILE'];
# 'custom' — a password stored in backup_config; 'none'. The legacy
# 'app_password' (key derived from the login password, only available inside
# a logged-in request) is migrated to 'key' because unattended backups could
# never use it.
DEFAULT_ENCRYPT_METHOD = 'key'


class BackupModule(BaseModule):

    enabled_by_default = True

    @property
    def module_id(self):
        return 'backup'

    @property
    def name(self):
        return 'Backup & Restore'

    @property
    def description(self):
        return 'Full encrypted backups (DB + files) with scheduling'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return []

    @property
    def settings_tab(self):
        return {'id': 'backup', 'label': 'Backup'}

    # ── models ──────────────────────────────────────────────────────

    def register_models(self, db):
        self._db = db

        class BackupConfig(db.Model):
            __tablename__ = 'backup_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            backup_path = db.Column(db.String(500), default='')
            encrypt_method = db.Column(db.String(20), default=DEFAULT_ENCRYPT_METHOD)
            custom_password = db.Column(EncryptedString(500), default='')
            use_external_storage = db.Column(db.Boolean, default=True)
            updated_at = db.Column(db.DateTime, default=datetime.utcnow)

        # We only need Settings reference for auto_backup_enabled check
        class Settings(db.Model):
            __tablename__ = 'settings'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)

        self.BackupConfig = BackupConfig
        self.Settings = Settings
        return {'BackupConfig': BackupConfig}

    # ── routes ──────────────────────────────────────────────────────

    def register_routes(self, app):
        bp = Blueprint('backup', __name__,
                       template_folder='templates',
                       url_prefix='/backup')
        login_required = self.core.login_required
        module = self
        self._app = app

        # Rate-limit the resource-intensive backup/restore endpoints.
        from app import limiter

        def _rl(rule):
            def deco(f):
                return limiter.limit(rule)(f) if limiter else f
            return deco

        @bp.route('/', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def create_backup():
            encrypt = request.form.get('encrypt', 'yes') == 'yes'
            password = None
            if encrypt:
                cfg = module._get_config()
                password = module._encryption_password(cfg)
                if cfg.encrypt_method != 'none' and not password:
                    flash('No password available for encryption.', 'danger')
                    return redirect(url_for('settings') + '#security')
            prefix = 'manual_'
            ok, result = module._create_full_backup(password=password, prefix=prefix)
            if ok:
                module.core.log_activity('backup_created', 'backup', result)
            flash(f'Backup created: {result}' if ok else f'Backup failed: {result}',
                  'success' if ok else 'danger')
            return redirect(url_for('settings') + '#security')

        @bp.route('/download/<filename>')
        @login_required
        def download_backup(filename):
            path = module._get_backup_file_path(filename)
            if path:
                return send_file(str(path), as_attachment=True)
            flash('Backup file not found', 'danger')
            return redirect(url_for('settings') + '#security')

        @bp.route('/restore/<filename>', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def restore_backup(filename):
            ok, msg = module._restore_full_backup(
                filename, module._decryption_candidates(
                    request.form.get('password', '').strip() or None))
            if ok:
                module.core.log_activity('backup_restored', 'backup', filename)
            flash(msg, 'success' if ok else 'danger')
            return redirect(url_for('settings') + '#security')

        @bp.route('/delete/<filename>', methods=['POST'])
        @login_required
        def delete_backup(filename):
            ok, msg = module._delete_backup_file(filename)
            flash(msg, 'success' if ok else 'danger')
            return redirect(url_for('settings') + '#security')

        @bp.route('/upload-restore', methods=['GET', 'POST'])
        @_rl('10/minute')
        @login_required
        def upload_restore():
            if request.method == 'GET':
                return render_template('restore.html')
            f = request.files.get('backup_file')
            if not f or not f.filename:
                flash('No file selected.', 'danger')
                return redirect(url_for('backup.upload_restore'))
            try:
                jd = json.loads(f.read().decode('utf-8'))
            except Exception:
                flash('Invalid JSON file.', 'danger')
                return redirect(url_for('backup.upload_restore'))
            ok, msg = module._restore_db_from_json(jd)
            if ok:
                module.core.log_activity('backup_restored', 'backup',
                                         f'JSON upload: {f.filename}')
                flash('Data restored successfully! Please restart the application.',
                      'success')
            else:
                flash(f'Restore failed: {msg}', 'danger')
            return redirect(url_for('settings') + '#security')

        @bp.route('/load-demo', methods=['POST'])
        @_rl('10/minute')
        @login_required
        def load_demo():
            demo_path = Path(module.core.app_path) / 'demo_data.json'
            if not demo_path.exists():
                flash('demo_data.json not found.', 'danger')
                return redirect(url_for('settings') + '#security')
            try:
                jd = json.loads(demo_path.read_text('utf-8'))
            except Exception:
                flash('Failed to parse demo_data.json.', 'danger')
                return redirect(url_for('settings') + '#security')
            ok, msg = module._restore_db_from_json(jd)
            if ok:
                module.core.log_activity('demo_loaded', 'backup', 'demo_data.json')
                flash('Demo data loaded! Please restart the application.', 'success')
            else:
                flash(f'Demo load failed: {msg}', 'danger')
            return redirect(url_for('settings') + '#security')

        app.register_blueprint(bp)

    def on_enable(self):
        encrypt_existing(self._db, 'backup_config', ['custom_password'])
        # Migrate: add use_external_storage column if missing
        try:
            from sqlalchemy import inspect as sa_inspect, text
            inspector = sa_inspect(self._db.engine)
            cols = [c['name'] for c in inspector.get_columns('backup_config')]
            if 'use_external_storage' not in cols:
                with self._db.engine.connect() as conn:
                    conn.execute(text(
                        'ALTER TABLE backup_config '
                        'ADD COLUMN use_external_storage BOOLEAN DEFAULT 1'))
                    conn.commit()
        except Exception as e:
            logger.debug('backup_config migration: %s', e)  # table may not exist yet

        # Legacy 'app_password' cannot encrypt unattended backups: use the key.
        cfg = self._get_config()
        if cfg.encrypt_method == 'app_password':
            cfg.encrypt_method = DEFAULT_ENCRYPT_METHOD
            self._db.session.commit()
            logger.info('Backup encryption switched from app_password to the '
                        'backup key; earlier backups still restore with the '
                        'login password')

        # Defer the startup backup ~60s instead of running it inline: a large
        # DB + many files could otherwise delay the app becoming available.
        import threading

        def _deferred_startup_backup():
            # Every worker loads this module; only the scheduler leader backs up.
            if not self.core.scheduler.is_leader:
                return
            with self.core.app.app_context():
                try:
                    self._perform_startup_backup()
                except Exception as e:
                    logger.error('Deferred startup backup failed: %s', e)

        t = threading.Timer(60.0, _deferred_startup_backup)
        t.daemon = True
        t.start()

        # Register daily backup job with the scheduler
        self.core.scheduler.add_job(
            job_id='backup.daily',
            func=self._scheduled_backup,
            job_type='daily',
            time_str='03:00',
            description='Daily automatic backup & cleanup',
        )

    # ── config helpers ──────────────────────────────────────────────

    def _get_config(self):
        cfg = self.BackupConfig.query.first()
        if not cfg:
            cfg = self.BackupConfig(
                backup_path='', encrypt_method=DEFAULT_ENCRYPT_METHOD,
                use_external_storage=True)
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

    def _backup_key(self):
        """The deployment's backup key: BACKUP_KEY, else the key file
        (created on first use, readable by the owner only)."""
        return self.core.deployment_key('BACKUP_KEY', 'BACKUP_KEY_FILE', 'backup')

    def _encryption_password(self, cfg):
        """Password used to encrypt a new backup (None = unencrypted)."""
        if cfg.encrypt_method == 'none':
            return None
        if cfg.encrypt_method == 'custom':
            return cfg.custom_password or None
        return self._backup_key()

    def _decryption_candidates(self, typed_password=None):
        """Passwords to try on an encrypted backup, most likely first.

        *typed_password* is what the owner entered on restore: a custom
        backup password, or the login password of a backup made with the
        legacy 'app_password' method (its key is derived the same way).
        """
        cfg = self._get_config()
        candidates = [self._encryption_password(cfg), self._backup_key()]
        if typed_password:
            candidates.append(typed_password)
            try:
                from auth import auth_manager
                key = auth_manager.get_encryption_key(typed_password)
                candidates.append(key.decode('utf-8') if isinstance(key, bytes) else key)
            except Exception as e:  # no auth config (external login only)
                logger.debug('legacy backup key derivation: %s', e)
        return [c for i, c in enumerate(candidates) if c and c not in candidates[:i]]

    def _backup_dir(self):
        cfg = self._get_config()
        if cfg.backup_path and cfg.backup_path.strip():
            p = Path(cfg.backup_path.strip())
        else:
            p = Path(self.core.app_path) / 'backups'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _is_external_storage_enabled(self):
        try:
            mm = self.core.module_manager
            if mm and mm.is_enabled('external_storage'):
                return 'external_storage' in mm.modules
        except Exception as e:
            logger.debug('external_storage check: %s', e)
        return False

    # ── settings UI ─────────────────────────────────────────────────

    def get_settings_html(self, settings):
        cfg = self._get_config()
        method = cfg.encrypt_method if cfg.encrypt_method in ('none', 'custom') else 'key'
        return self.render_settings_template(
            'backup_settings_panel.html',
            cfg=cfg, method=method,
            auto_backup=bool(settings and settings.auto_backup_enabled),
            ext_ok=self._is_external_storage_enabled(),
            key_source=self._key_source(),
            demo_available=(Path(self.core.app_path) / 'demo_data.json').exists(),
            backups=self._get_backup_list())

    def _key_source(self):
        """Where the backup key comes from, for the settings page (never the key)."""
        if os.environ.get('BACKUP_KEY', '').strip():
            return 'the BACKUP_KEY environment variable'
        return f"the key file {self.core.app.config['BACKUP_KEY_FILE']}"

    def save_settings(self, settings, form):
        # Only update auto_backup if the field was actually in the form
        # (i.e. submitted from the Security tab, not General Settings)
        if 'bk_encrypt_method' in form:
            settings.auto_backup_enabled = form.get('auto_backup_enabled') == 'on'
            try:
                settings.daily_backup_retention_count = int(
                    form.get('daily_backup_retention_count', '4'))
            except ValueError:
                settings.daily_backup_retention_count = 4
            cfg = self._get_config()
            cfg.backup_path = form.get('bk_backup_path', '').strip()
            method = form.get('bk_encrypt_method', DEFAULT_ENCRYPT_METHOD)
            cfg.encrypt_method = (method if method in ('key', 'custom', 'none')
                                  else DEFAULT_ENCRYPT_METHOD)
            cfg.custom_password = secret_from_form(form, 'bk_custom_password',
                                                   cfg.custom_password or '')
            cfg.use_external_storage = form.get('bk_use_external_storage') == 'on'
            cfg.updated_at = datetime.utcnow()
            self._db.session.commit()

    # ── core backup logic ───────────────────────────────────────────

    def _create_full_backup(self, password=None, prefix=''):
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            ext = '.zip.enc' if password else '.zip'
            filename = f'{prefix}backup_{ts}{ext}'
            backup_dir = self._backup_dir()
            app_root = Path(self.core.app_path)

            buf = BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('db_backup.json', self._dump_db_json())
                for folder in FILE_FOLDERS:
                    fp = app_root / folder
                    if fp.exists() and fp.is_dir():
                        for f in fp.rglob('*'):
                            if f.is_file() and not f.name.startswith('.'):
                                zf.write(f, str(f.relative_to(app_root)))

            data = buf.getvalue()
            if password:
                data = self._encrypt_bytes(data, password)

            # Exclusive create: never overwrite a backup made the same second.
            stem, n = filename, 1
            while True:
                try:
                    with open(backup_dir / filename, 'xb') as f:
                        f.write(data)
                    break
                except FileExistsError:
                    n += 1
                    filename = stem.replace(ts, f'{ts}_{n}', 1)
            logger.info('Backup saved locally: %s', backup_dir / filename)

            cfg = self._get_config()
            if cfg.use_external_storage and self._is_external_storage_enabled():
                try:
                    self.core.storage.save(data, f'backups/{filename}')
                    logger.info('Backup sent to external storage: backups/%s', filename)
                except Exception as e:
                    logger.warning('Could not save to external storage: %s', e)

            return True, filename
        except Exception as e:
            logger.error('Error creating backup: %s', e)
            return False, str(e)

    def _dump_db_json(self):
        """Dump ALL database tables to JSON dynamically (no hardcoded models)."""
        from sqlalchemy import text, inspect as sa_inspect
        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
        data = {'version': '2.1', 'created_at': datetime.now().isoformat(),
                'tables': {}}
        inspector = sa_inspect(self._db.engine)
        skip = {'backup_config'}  # don't backup our own config
        for table_name in inspector.get_table_names():
            if table_name in skip:
                continue
            if not _SAFE_NAME.match(table_name):
                continue
            columns = [c['name'] for c in inspector.get_columns(table_name)]
            rows = []
            result = self._db.session.execute(
                text(f'SELECT * FROM "{table_name}"'))
            for row in result:
                d = {}
                for i, col in enumerate(columns):
                    v = row[i]
                    if isinstance(v, (datetime, date)):
                        v = v.isoformat()
                    d[col] = v
                rows.append(d)
            data['tables'][table_name] = rows
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _restore_full_backup(self, filename, passwords=()):
        """Restore a backup; *passwords* are tried in order on an encrypted one."""
        if isinstance(passwords, str):
            passwords = [passwords]
        try:
            file_path = self._get_backup_file_path(filename)
            if not file_path:
                return False, 'Backup file not found'
            raw = file_path.read_bytes()
            if filename.endswith('.enc'):
                if not passwords:
                    return False, 'Password required to decrypt this backup'
                for password in passwords:
                    try:
                        raw = self._decrypt_bytes(raw, password)
                        break
                    except Exception:
                        continue
                else:
                    return False, 'Decryption failed. Wrong password?'
            # Safety copy of the current SQLite file before it is overwritten.
            db_file = self._db.engine.url.database
            if db_file and db_file != ':memory:' and os.path.exists(db_file):
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy2(db_file, f'{db_file}.backup_{ts}')
            app_root = Path(self.core.app_path)
            with zipfile.ZipFile(BytesIO(raw), 'r') as zf:
                if 'db_backup.json' in zf.namelist():
                    jd = json.loads(zf.read('db_backup.json').decode('utf-8'))
                    ok, msg = self._restore_db_from_json(jd)
                    if not ok:
                        return False, msg
                for name in zf.namelist():
                    if name == 'db_backup.json':
                        continue
                    parts = name.split('/')
                    if parts[0] in FILE_FOLDERS:
                        target = app_root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(name))
            return True, 'Backup restored! Please restart the application.'
        except zipfile.BadZipFile:
            return False, 'Invalid backup file (not a valid ZIP)'
        except Exception as e:
            return False, f'Error restoring backup: {e}'

    def _restore_db_from_json(self, json_data):
        """Restore database tables from JSON dynamically.
        Handles foreign key order automatically."""
        from sqlalchemy import text, inspect as sa_inspect
        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

        def _check_name(name):
            """Validate identifier against schema and allowed characters."""
            if not _SAFE_NAME.match(name):
                raise ValueError(f'Invalid identifier: {name!r}')
            return name

        try:
            tables = json_data.get('tables', json_data)
            db = self._db
            # Inspect through the session's own connection: an engine-level
            # inspector checks out (and on return rolls back) a connection,
            # which on a shared connection undoes the deletes below.
            inspector = sa_inspect(db.session.connection())
            existing = set(inspector.get_table_names())
            skip = {'backup_config', 'module_enabled'}

            # Build dependency order: tables with FKs come after referenced tables
            fk_deps = {}
            for tname in existing:
                if tname in skip:
                    continue
                refs = set()
                for fk in inspector.get_foreign_keys(tname):
                    ref = fk.get('referred_table')
                    if ref and ref != tname:
                        refs.add(ref)
                fk_deps[tname] = refs

            # Topological sort for delete (reverse) and insert (forward)
            ordered = []
            visited = set()

            def visit(t):
                if t in visited or t not in fk_deps:
                    return
                visited.add(t)
                for dep in fk_deps.get(t, set()):
                    visit(dep)
                ordered.append(t)

            for t in fk_deps:
                visit(t)

            # Delete in reverse order (children first)
            for tname in reversed(ordered):
                if tname in tables or tname in existing:
                    try:
                        safe_t = _check_name(tname)
                        db.session.execute(text(f'DELETE FROM "{safe_t}"'))
                    except Exception as e:
                        logger.debug('Could not clear table %s: %s', tname, e)

            # Insert in forward order (parents first)
            date_fields = {'invoice_date', 'due_date', 'expense_date'}
            dt_fields = {'created_at', 'updated_at'}
            for tname in ordered:
                if tname not in tables:
                    continue
                safe_t = _check_name(tname)
                cols = [c['name'] for c in inspector.get_columns(tname)]
                for rd in tables[tname]:
                    # Normalize date and datetime fields if present
                    for k, v in list(rd.items()):
                        if v and k in date_fields:
                            try:
                                rd[k] = datetime.fromisoformat(v).date()
                            except (ValueError, TypeError) as exc:
                                safe_k = _sanitize_for_log(k)
                                safe_v = _sanitize_for_log(repr(v))
                                logger.debug(
                                    "Skipping invalid date value for key '%s': %r (%s)",
                                    safe_k, safe_v, exc
                                )
                        elif v and k in dt_fields:
                            try:
                                rd[k] = datetime.fromisoformat(v)
                            except (ValueError, TypeError) as exc:
                                safe_k = _sanitize_for_log(k)
                                safe_v = _sanitize_for_log(repr(v))
                                logger.debug(
                                    "Skipping invalid datetime value for user key [%s]: %r (%s)",
                                    safe_k, safe_v, exc
                                )
                    # Only insert columns that exist in current schema
                    row_cols = [_check_name(c) for c in rd if c in cols]
                    if not row_cols:
                        continue
                    placeholders = ', '.join(f':{c}' for c in row_cols)
                    col_names = ', '.join(f'"{c}"' for c in row_cols)
                    vals = {c: rd[c] for c in row_cols}
                    db.session.execute(
                        text(f'INSERT INTO "{safe_t}" ({col_names}) '
                             f'VALUES ({placeholders})'), vals)

            db.session.commit()
            return True, 'OK'
        except Exception as e:
            db.session.rollback()
            return False, f'DB restore error: {e}'

    # ── encryption ──────────────────────────────────────────────────

    def _encrypt_bytes(self, data, password):
        import hashlib
        import hmac
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher, algorithms, modes)
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            return self._encrypt_fallback(data, password)
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        iv = os.urandom(16)
        pad_len = 16 - (len(data) % 16)
        padded = data + bytes([pad_len] * pad_len)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv),
                        backend=default_backend())
        enc = cipher.encryptor()
        ct = enc.update(padded) + enc.finalize()
        mac = hmac.new(key, salt + iv + ct, hashlib.sha256).digest()
        return salt + iv + mac + ct

    def _decrypt_bytes(self, data, password):
        import hashlib
        import hmac as hmac_mod
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher, algorithms, modes)
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            return self._decrypt_fallback(data, password)
        salt, iv = data[:16], data[16:32]
        mac_stored, ct = data[32:64], data[64:]
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        mac_calc = hmac_mod.new(key, salt + iv + ct, hashlib.sha256).digest()
        if not hmac_mod.compare_digest(mac_stored, mac_calc):
            raise ValueError('HMAC verification failed')
        dec = Cipher(algorithms.AES(key), modes.CBC(iv),
                     backend=default_backend()).decryptor()
        padded = dec.update(ct) + dec.finalize()
        return padded[:-padded[-1]]

    def _encrypt_fallback(self, data, password):
        import tempfile
        from auth import auth_manager
        with tempfile.NamedTemporaryFile(delete=False, suffix='.dat') as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            enc_path = auth_manager.encrypt_file(tmp_path, password)
            result = Path(enc_path).read_bytes()
            Path(enc_path).unlink(missing_ok=True)
            return result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _decrypt_fallback(self, data, password):
        import tempfile
        from auth import auth_manager
        with tempfile.NamedTemporaryFile(delete=False, suffix='.enc') as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            dec_path = tmp_path.replace('.enc', '.dec')
            auth_manager.decrypt_file(tmp_path, password, dec_path)
            result = Path(dec_path).read_bytes()
            Path(dec_path).unlink(missing_ok=True)
            return result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ── file listing & utilities ────────────────────────────────────

    def _backup_folders(self):
        """Folders whose files count as backups: the configured backup
        directory, then the legacy `backups/` in the working directory.

        Never `instance/`: it holds the live database and the auth config,
        which must not be listed, downloaded, restored from or deleted here.
        """
        folders = [self._backup_dir()]
        legacy = Path('backups')
        if legacy.is_dir() and legacy.resolve() != folders[0].resolve():
            folders.append(legacy)
        return folders

    def _get_backup_list(self):
        backups = []
        valid_ext = {'.zip', '.enc', '.encrypted', '.db'}
        for folder in self._backup_folders():
            if not folder.exists():
                continue
            for f in folder.iterdir():
                if f.is_file() and (f.suffix in valid_ext
                                     or f.name.endswith('.zip.enc')):
                    if not any(b['filename'] == f.name for b in backups):
                        backups.append(self._backup_info(f))
        backups.sort(key=lambda x: x['sort_key'], reverse=True)
        return backups

    @staticmethod
    def _backup_info(path):
        size = path.stat().st_size
        if size < 1024:
            sz = f"{size} B"
        elif size < 1024 * 1024:
            sz = f"{size / 1024:.1f} KB"
        else:
            sz = f"{size / (1024 * 1024):.1f} MB"
        m = re.search(r'(\d{8}_\d{6})', path.name)
        if m:
            d = m.group(1)
            fmt = f"{d[6:8]}/{d[4:6]}/{d[0:4]} {d[9:11]}:{d[11:13]}"
            sk = d
        else:
            fmt, sk = "Unknown", "0"
        btype = "Manual" if path.name.startswith('manual_') else "Daily"
        return {'filename': path.name, 'date': fmt, 'size': sz,
                'type': btype, 'sort_key': sk}

    def _get_backup_file_path(self, filename):
        if '..' in filename or '/' in filename or '\\' in filename:
            return None
        for folder in self._backup_folders():
            p = folder / filename
            if p.is_file():
                return p
        return None

    def _delete_backup_file(self, filename):
        if '..' in filename or '/' in filename:
            return False, 'Invalid filename'
        path = self._get_backup_file_path(filename)
        if not path:
            return False, 'Backup file not found'
        try:
            path.unlink()
            cfg = self._get_config()
            if cfg.use_external_storage and self._is_external_storage_enabled():
                try:
                    self.core.storage.delete(f'backups/{filename}')
                except Exception as e:
                    logger.debug('Could not delete remote backup %s: %s',
                                 _sanitize_for_log(filename), e)
            return True, f'Backup {filename} deleted'
        except Exception as e:
            return False, f'Error: {e}'

    def _cleanup_old_backups(self, retention_count=4):
        try:
            backup_dir = self._backup_dir()
            if not backup_dir.exists():
                return
            daily = [f for f in backup_dir.iterdir()
                     if f.is_file() and not f.name.startswith('manual_')]
            daily.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            for old in daily[retention_count:]:
                try:
                    old.unlink()
                    logger.info('Deleted old daily backup: %s', old.name)
                except Exception as e:
                    logger.warning('Error deleting %s: %s', old.name, e)
        except Exception as e:
            logger.error('Error cleaning up backups: %s', e)

    def _should_create_backup(self):
        try:
            backup_dir = self._backup_dir()
            if not backup_dir.exists():
                return True
            today = date.today().strftime('%Y%m%d')
            today_bk = [f for f in backup_dir.glob(f'backup_{today}_*')
                        if not f.name.startswith('manual_')]
            return len(today_bk) == 0
        except Exception:
            return True

    def _perform_startup_backup(self):
        try:
            settings = self.Settings.query.first()
            if not settings or not settings.auto_backup_enabled:
                return
            if not self._should_create_backup():
                logger.info('Daily backup already exists, skipping...')
                return
            cfg = self._get_config()
            password = self._encryption_password(cfg)
            if not password and cfg.encrypt_method != 'none':
                logger.warning('Automatic backup skipped: no password available')
                return
            logger.info('Automatic Full Backup (Daily)')
            ok, result = self._create_full_backup(password=password)
            if ok:
                ret = settings.daily_backup_retention_count or 4
                self._cleanup_old_backups(ret)
                logger.info('Keeping %d most recent daily backups', ret)
            else:
                logger.error('Backup failed: %s', result)
        except Exception as e:
            logger.error('Error during startup backup: %s', e)

    def _scheduled_backup(self):
        """Called by the core scheduler for daily automatic backups."""
        try:
            settings = self.Settings.query.first()
            if not settings or not settings.auto_backup_enabled:
                return
            if not self._should_create_backup():
                return
            cfg = self._get_config()
            password = self._encryption_password(cfg)
            if not password and cfg.encrypt_method != 'none':
                logger.warning('Scheduled backup skipped: no password available')
                return
            logger.info('Running scheduled daily backup...')
            ok, result = self._create_full_backup(password=password)
            if ok:
                ret = settings.daily_backup_retention_count or 4
                self._cleanup_old_backups(ret)
            else:
                logger.error('Scheduled backup failed: %s', result)
        except Exception as e:
            logger.error('Scheduled backup error: %s', e)
            raise
