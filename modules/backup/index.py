#!/usr/bin/env python3
"""
Backup Module
Full backup/restore: DB (JSON) + all uploaded files in a single ZIP archive.
Supports optional AES encryption, custom backup path, and external storage integration.
"""

from module_manager import BaseModule
from flask import (Blueprint, request, redirect, url_for,
                   flash, send_file, session, render_template)
from datetime import datetime, date
from pathlib import Path
from io import BytesIO
import os, re, json, shutil, zipfile
import logging

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


FILE_FOLDERS = ['expenses_files', 'documents_files', 'tax_forms', 'invoices_pdf']


class BackupModule(BaseModule):

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
            encrypt_method = db.Column(db.String(20), default='app_password')
            custom_password = db.Column(db.String(500), default='')
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

        @bp.route('/', methods=['POST'])
        @login_required
        def create_backup():
            encrypt = request.form.get('encrypt', 'yes') == 'yes'
            password = None
            if encrypt:
                cfg = module._get_config()
                if cfg.encrypt_method == 'none':
                    password = None  # no encryption even if requested
                elif cfg.encrypt_method == 'custom' and cfg.custom_password:
                    password = cfg.custom_password
                else:
                    password = session.get('_password')
                if encrypt and cfg.encrypt_method != 'none' and not password:
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
        @login_required
        def restore_backup(filename):
            cfg = module._get_config()
            if cfg.encrypt_method == 'none':
                password = None
            elif cfg.encrypt_method == 'custom' and cfg.custom_password:
                password = cfg.custom_password
            else:
                password = session.get('_password')
            ok, msg = module._restore_full_backup(filename, password)
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

        # Run startup backup immediately (first launch of the day)
        self._perform_startup_backup()

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
                backup_path='', encrypt_method='app_password',
                use_external_storage=True)
            self._db.session.add(cfg)
            self._db.session.commit()
        return cfg

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
        auto_ck = 'checked' if settings and settings.auto_backup_enabled else ''
        bp = cfg.backup_path or ''
        enc_none = 'checked' if cfg.encrypt_method == 'none' else ''
        enc_app = 'checked' if cfg.encrypt_method == 'app_password' else ''
        enc_cust = 'checked' if cfg.encrypt_method == 'custom' else ''
        # Default to app_password if not set to any known value
        if not enc_none and not enc_cust:
            enc_app = 'checked'
        cp = cfg.custom_password or ''
        cp_show = 'block' if cfg.encrypt_method == 'custom' else 'none'
        ext_ok = self._is_external_storage_enabled()
        use_ext = 'checked' if cfg.use_external_storage else ''
        backups = self._get_backup_list()
        RS = ('width:auto!important;padding:0!important;'
              'margin-right:8px;vertical-align:middle;')
        return self._render_settings(
            auto_ck, bp, enc_none, enc_app, enc_cust, cp, cp_show,
            ext_ok, use_ext, backups, RS)

    def _render_settings(self, auto_ck, bp, enc_none, enc_app, enc_cust,
                         cp, cp_show, ext_ok, use_ext, backups, RS):
        h = []
        a = h.append
        a('<h3 style="margin-bottom:15px;color:#333;">Backup &amp; Restore</h3>')
        a('<p style="color:#666;margin-bottom:20px;">'
          'Full backups include database (JSON) and all uploaded files '
          'in a ZIP archive.</p>')

        # auto backup checkbox
        a('<div style="margin-bottom:15px;">')
        a('<label style="display:block;font-weight:normal;cursor:pointer;">')
        a(f'<input type="checkbox" name="auto_backup_enabled" {auto_ck}'
          f' style="{RS}"> Enable automatic daily backup</label>')
        a('<small style="display:block;margin-top:4px;margin-left:24px;'
          'color:#666;">Runs once per day on startup.</small></div>')

        # custom backup path
        a('<div class="form-group">')
        a('<label for="bk_backup_path">Backup Directory (optional)</label>')
        a(f'<input type="text" id="bk_backup_path" name="bk_backup_path"'
          f' value="{bp}" placeholder="Leave empty for default (backups/)"'
          f' style="max-width:400px;">')
        a('<small style="display:block;margin-top:4px;color:#666;">'
          'Absolute path or relative to app root.</small></div>')

        # external storage option
        if ext_ok:
            a('<div style="margin-bottom:15px;">')
            a('<label style="display:block;font-weight:normal;cursor:pointer;">')
            a(f'<input type="checkbox" name="bk_use_external_storage" {use_ext}'
              f' style="{RS}"> Also send backups to External Storage</label>')
            a('<small style="display:block;margin-top:4px;margin-left:24px;'
              'color:#666;">Copies archive to the storage configured in '
              'External Storage module.</small></div>')
        else:
            a('<div style="margin-bottom:15px;padding:10px;background:#f8f9fa;'
              'border-radius:4px;color:#666;font-size:13px;">'
              '\u2139\ufe0f Enable the <strong>External Storage</strong> '
              'module to send backups to S3 or other remote storage.</div>')

        # encryption
        LS = 'display:block;margin-bottom:8px;font-weight:normal;cursor:pointer;'
        a('<div style="margin-bottom:20px;">')
        a('<label style="display:block;margin-bottom:8px;font-weight:bold;'
          'color:#333;">Encryption</label><div style="margin-left:4px;">')
        a(f'<label style="{LS}"><input type="radio" name="bk_encrypt_method"'
          f' value="none" {enc_none}'
          f' onchange="document.getElementById(\'bk-custom-pw\').style.display=\'none\'"'
          f' style="{RS}"> No encryption</label>')
        a(f'<label style="{LS}"><input type="radio" name="bk_encrypt_method"'
          f' value="app_password" {enc_app}'
          f' onchange="document.getElementById(\'bk-custom-pw\').style.display=\'none\'"'
          f' style="{RS}"> Use application password</label>')
        a(f'<label style="{LS}"><input type="radio" name="bk_encrypt_method"'
          f' value="custom" {enc_cust}'
          f' onchange="document.getElementById(\'bk-custom-pw\').style.display=\'block\'"'
          f' style="{RS}"> Use separate backup password</label>')
        a('</div>')

        a(f'<div id="bk-custom-pw" style="display:{cp_show};margin-left:24px;">')
        a('<div class="form-group">')
        a('<label for="bk_custom_password">Backup Password</label>')
        a(f'<input type="password" id="bk_custom_password"'
          f' name="bk_custom_password" value="{cp}"'
          f' style="max-width:300px;" autocomplete="off">')
        a('</div></div></div>')

        a('<hr style="margin:20px 0;border:none;border-top:1px solid #e0e0e0;">')

        # create backup buttons
        a('<h4 style="margin-bottom:10px;">Create New Backup</h4>')
        a('<div style="margin-bottom:20px;">')
        a(self._btn_backup(encrypt=True))
        a(self._btn_backup(encrypt=False))
        a('</div>')

        # restore / demo data
        a('<h4 style="margin-bottom:10px;">Restore Data</h4>')
        a('<div style="margin-bottom:20px;">')
        # Upload JSON backup
        js_upload = "window.location.href='/backup/upload-restore';"
        a(f'<button type="button" class="btn btn-info"'
          f' style="margin-right:10px;" onclick="{js_upload}">'
          f'\U0001f4e4 Upload JSON Backup</button>')
        # Load demo data
        demo_path = Path(self.core.app_path) / 'demo_data.json'
        if demo_path.exists():
            js_demo = (
                "if(confirm('Load demo data? This will REPLACE all current data.')){"
                "var f=document.createElement('form');"
                "f.method='POST';f.action='/backup/load-demo';"
                "document.body.appendChild(f);f.submit();}"
            )
            a(f'<button type="button" class="btn btn-warning"'
              f' style="margin-right:10px;" onclick="{js_demo}">'
              f'\U0001f9ea Load Demo Data</button>')
        a('</div>')

        # backup list
        a('<h4>Saved Backups</h4>')
        if backups:
            a(self._render_backup_table(backups))
        else:
            a('<div style="text-align:center;padding:30px;color:#999;">'
              '<p>No backups found.</p></div>')

        return '\n'.join(h)

    @staticmethod
    def _btn_backup(encrypt):
        val = 'yes' if encrypt else 'no'
        label = '\U0001f512 Full Backup (Encrypted)' if encrypt else '\U0001f4e6 Full Backup (No encryption)'
        cls = 'btn-success' if encrypt else 'btn-primary'
        js = (
            "var f=document.createElement('form');"
            "f.method='POST';f.action='/backup/';"
            "var i=document.createElement('input');"
            "i.type='hidden';i.name='backup_type';i.value='full';"
            "f.appendChild(i);"
            "var e=document.createElement('input');"
            "e.type='hidden';e.name='encrypt';e.value='" + val + "';"
            "f.appendChild(e);document.body.appendChild(f);f.submit();"
        )
        return (f'<button type="button" class="btn {cls}" '
                f'style="margin-right:10px;" onclick="{js}">'
                f'{label}</button>')

    @staticmethod
    def _render_backup_table(backups):
        h = []
        a = h.append
        a('<table style="width:100%;border-collapse:collapse;margin-top:10px;">')
        a('<thead><tr style="background:#f8f9fa;">')
        a('<th style="padding:10px;text-align:left;">File</th>')
        a('<th style="padding:10px;text-align:left;">Type</th>')
        a('<th style="padding:10px;text-align:left;">Date</th>')
        a('<th style="padding:10px;text-align:left;">Size</th>')
        a('<th style="padding:10px;text-align:center;">Actions</th>')
        a('</tr></thead><tbody>')
        for b in backups:
            fn = b['filename']
            bg = '#007bff' if b['type'] == 'Manual' else '#28a745'
            a(f'<tr style="border-bottom:1px solid #e0e0e0;">')
            a(f'<td style="padding:10px;font-size:13px;">{fn}</td>')
            a(f'<td style="padding:10px;">'
              f'<span style="padding:3px 8px;border-radius:3px;'
              f'font-size:12px;background:{bg};color:white;">'
              f'{b["type"]}</span></td>')
            a(f'<td style="padding:10px;">{b["date"]}</td>')
            a(f'<td style="padding:10px;">{b["size"]}</td>')
            a('<td style="padding:10px;text-align:center;">')
            BS = 'padding:4px 8px;font-size:12px;margin-right:3px;'
            a(f'<a href="/backup/download/{fn}" class="btn btn-primary"'
              f' style="{BS}">Download</a>')
            js_r = (
                "if(confirm('Restore from this backup?')){"
                "var f=document.createElement('form');"
                "f.method='POST';"
                "f.action='/backup/restore/" + fn + "';"
                "document.body.appendChild(f);f.submit();}"
            )
            a(f'<button type="button" class="btn btn-success"'
              f' style="{BS}" onclick="{js_r}">Restore</button>')
            js_d = (
                "if(confirm('Delete this backup?')){"
                "var f=document.createElement('form');"
                "f.method='POST';"
                "f.action='/backup/delete/" + fn + "';"
                "document.body.appendChild(f);f.submit();}"
            )
            a(f'<button type="button" class="btn btn-danger"'
              f' style="{BS}" onclick="{js_d}">Delete</button>')
            a('</td></tr>')
        a('</tbody></table>')
        return '\n'.join(h)

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
            cfg.encrypt_method = form.get('bk_encrypt_method', 'app_password')
            cfg.custom_password = form.get('bk_custom_password', '').strip()
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

            (backup_dir / filename).write_bytes(data)
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

    def _restore_full_backup(self, filename, password=None):
        try:
            file_path = self._get_backup_file_path(filename)
            if not file_path:
                return False, 'Backup file not found'
            raw = file_path.read_bytes()
            if filename.endswith('.enc'):
                if not password:
                    return False, 'Password required to decrypt this backup'
                try:
                    raw = self._decrypt_bytes(raw, password)
                except Exception:
                    return False, 'Decryption failed. Wrong password?'
            db_path = Path('instance/invoices.db')
            if db_path.exists():
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy2(db_path, f'instance/invoices.db.backup_{ts}')
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
            inspector = sa_inspect(db.engine)
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
        import hashlib, hmac
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
        import hashlib, hmac as hmac_mod
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

    def _get_backup_list(self):
        backups = []
        backup_dir = self._backup_dir()
        valid_ext = {'.zip', '.enc', '.encrypted', '.db'}
        if backup_dir.exists():
            for f in backup_dir.iterdir():
                if f.is_file() and (f.suffix in valid_ext
                                     or f.name.endswith('.zip.enc')):
                    backups.append(self._backup_info(f))
        for legacy in [Path('backups'), Path('instance')]:
            if legacy.exists() and legacy != backup_dir:
                for f in legacy.iterdir():
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
        if '..' in filename or '/' in filename:
            return None
        for folder in [self._backup_dir(), Path('backups'), Path('instance')]:
            p = folder / filename
            if p.exists():
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
            if cfg.encrypt_method == 'none':
                password = None
            elif cfg.encrypt_method == 'custom' and cfg.custom_password:
                password = cfg.custom_password
            else:
                try:
                    password = session.get('_password')
                except RuntimeError:
                    password = None
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
            if cfg.encrypt_method == 'none':
                password = None
            elif cfg.encrypt_method == 'custom' and cfg.custom_password:
                password = cfg.custom_password
            else:
                password = None
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
