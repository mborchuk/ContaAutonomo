#!/usr/bin/env python3
"""
External Storage Module
Intercepts core file operations and routes them to configurable storage backends.
Supports: Local filesystem (default), AWS S3, Google Cloud Storage, Google Drive.
"""

from module_manager import BaseModule, FileStorageBackend, LocalStorageBackend
from flask import Blueprint, redirect, url_for, flash
from datetime import datetime
import logging
import os
import io

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AWS S3 Backend
# ---------------------------------------------------------------------------

class S3StorageBackend(FileStorageBackend):
    """AWS S3 storage backend"""

    def __init__(self, bucket, prefix='', region=None,
                 access_key=None, secret_key=None, profile=None):
        self.bucket = bucket
        self.prefix = prefix.strip('/')
        self._client = None
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._profile = profile

    def _get_client(self):
        if self._client is None:
            import boto3
            kwargs = {}
            if self._region:
                kwargs['region_name'] = self._region
            if self._access_key and self._secret_key:
                kwargs['aws_access_key_id'] = self._access_key
                kwargs['aws_secret_access_key'] = self._secret_key
                self._client = boto3.client('s3', **kwargs)
            elif self._profile:
                session = boto3.Session(profile_name=self._profile)
                self._client = session.client('s3', **kwargs)
            else:
                self._client = boto3.client('s3', **kwargs)
        return self._client

    def _s3_key(self, relative_path):
        if self.prefix:
            return self.prefix + '/' + relative_path
        return relative_path

    def save(self, file_data, relative_path):
        client = self._get_client()
        key = self._s3_key(relative_path)
        if hasattr(file_data, 'read'):
            body = file_data.read()
            if hasattr(file_data, 'seek'):
                file_data.seek(0)
        else:
            body = file_data
        client.put_object(Bucket=self.bucket, Key=key, Body=body)
        return relative_path

    def delete(self, storage_key):
        client = self._get_client()
        key = self._s3_key(storage_key)
        try:
            client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.debug('S3 delete failed for %s: %s', key, e)

    def get(self, storage_key):
        client = self._get_client()
        key = self._s3_key(storage_key)
        try:
            response = client.get_object(Bucket=self.bucket, Key=key)
            return response['Body'].read(), os.path.basename(storage_key)
        except Exception:
            return None

    def send(self, storage_key, download_name=None):
        from flask import send_file
        result = self.get(storage_key)
        if not result:
            from flask import abort
            abort(404)
        file_bytes, filename = result
        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=download_name or filename
        )

    def exists(self, storage_key):
        client = self._get_client()
        key = self._s3_key(storage_key)
        try:
            client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Google Cloud Storage Backend
# ---------------------------------------------------------------------------

class GCSStorageBackend(FileStorageBackend):
    """Google Cloud Storage backend.

    Requires: google-cloud-storage
    Auth: Service Account JSON file or Application Default Credentials (ADC).
    """

    def __init__(self, bucket_name, prefix='', credentials_json=None):
        self.bucket_name = bucket_name
        self.prefix = prefix.strip('/')
        self._bucket = None
        self._credentials_json = credentials_json

    def _get_bucket(self):
        if self._bucket is None:
            from google.cloud import storage as gcs
            if self._credentials_json and os.path.isfile(self._credentials_json):
                client = gcs.Client.from_service_account_json(self._credentials_json)
            else:
                client = gcs.Client()
            self._bucket = client.bucket(self.bucket_name)
        return self._bucket

    def _blob_name(self, relative_path):
        if self.prefix:
            return self.prefix + '/' + relative_path
        return relative_path

    def save(self, file_data, relative_path):
        bucket = self._get_bucket()
        blob = bucket.blob(self._blob_name(relative_path))
        if hasattr(file_data, 'read'):
            content = file_data.read()
            if hasattr(file_data, 'seek'):
                file_data.seek(0)
        else:
            content = file_data
        blob.upload_from_string(content)
        return relative_path

    def delete(self, storage_key):
        try:
            bucket = self._get_bucket()
            bucket.blob(self._blob_name(storage_key)).delete()
        except Exception as e:
            logger.debug('GCS delete failed for %s: %s', storage_key, e)

    def get(self, storage_key):
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(self._blob_name(storage_key))
            return blob.download_as_bytes(), os.path.basename(storage_key)
        except Exception:
            return None

    def send(self, storage_key, download_name=None):
        from flask import send_file, abort
        result = self.get(storage_key)
        if not result:
            abort(404)
        file_bytes, filename = result
        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=download_name or filename
        )

    def exists(self, storage_key):
        try:
            bucket = self._get_bucket()
            return bucket.blob(self._blob_name(storage_key)).exists()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Google Drive Backend
# ---------------------------------------------------------------------------

class GoogleDriveStorageBackend(FileStorageBackend):
    """Google Drive storage backend.

    Requires: google-api-python-client, google-auth
    Auth: Service Account JSON with domain-wide delegation, or a regular
          Service Account that has the target folder shared with it.

    Automatically creates subfolders on Drive matching the relative path
    structure (e.g. invoices_pdf/, expenses_files/).

    storage_key for this backend is the Google Drive file ID (opaque string).
    """

    def __init__(self, folder_id, credentials_json=None):
        self.folder_id = folder_id
        self._credentials_json = credentials_json
        self._service = None
        self._folder_cache = {}  # path -> folder_id cache

    def _get_service(self):
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            scope = 'https://www.googleapis.com/auth/drive'
            if self._credentials_json and os.path.isfile(self._credentials_json):
                creds = service_account.Credentials.from_service_account_file(
                    self._credentials_json,
                    scopes=[scope])
                logger.info('[GDrive] auth via service account file: %s, scope=%s',
                            self._credentials_json, scope)
            else:
                import google.auth
                creds, _ = google.auth.default(scopes=[scope])
                logger.info('[GDrive] auth via default credentials, scope=%s', scope)

            self._service = build('drive', 'v3', credentials=creds,
                                  cache_discovery=False)
            logger.info('[GDrive] service built, folder_id=%s', self.folder_id)
        return self._service

    def _get_or_create_folder(self, folder_name, parent_id):
        """Get existing subfolder or create it. Returns folder ID.
        If multiple folders with the same name exist, picks the one with files."""
        cache_key = f'{parent_id}/{folder_name}'
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        service = self._get_service()
        q = (f"name='{folder_name}' and '{parent_id}' in parents "
             f"and mimeType='application/vnd.google-apps.folder' "
             f"and trashed=false")
        resp = service.files().list(
            q=q, fields='files(id)',
            pageSize=10, supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = resp.get('files', [])
        if files:
            if len(files) == 1:
                fid = files[0]['id']
            else:
                # Multiple folders with same name — pick the one that has files
                fid = files[0]['id']
                for f in files:
                    check_q = (f"'{f['id']}' in parents "
                               f"and mimeType!='application/vnd.google-apps.folder' "
                               f"and trashed=false")
                    check_resp = service.files().list(
                        q=check_q, fields='files(id)',
                        pageSize=1, supportsAllDrives=True,
                        includeItemsFromAllDrives=True).execute()
                    if check_resp.get('files'):
                        fid = f['id']
                        break
                logger.info('[GDrive._get_or_create_folder] %d folders named "%s", picked %s',
                            len(files), folder_name, fid)
        else:
            metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id],
            }
            created = service.files().create(
                body=metadata, fields='id').execute()
            fid = created['id']

        self._folder_cache[cache_key] = fid
        return fid

    def _resolve_parent(self, relative_path):
        """Walk the directory parts of relative_path, creating folders as needed.
        Returns (parent_folder_id, filename)."""
        parts = relative_path.replace('\\', '/').split('/')
        filename = parts[-1]
        folder_parts = parts[:-1]

        parent_id = self.folder_id
        for part in folder_parts:
            if part:
                parent_id = self._get_or_create_folder(part, parent_id)
        return parent_id, filename

    def _resolve_existing(self, relative_path):
        """Walk the directory parts of relative_path WITHOUT creating folders.
        Returns list of (parent_folder_id, filename) for ALL matching folder paths."""
        parts = relative_path.replace('\\', '/').split('/')
        filename = parts[-1]
        folder_parts = parts[:-1]

        current_parents = [self.folder_id]
        service = self._get_service()
        for part in folder_parts:
            if not part:
                continue
            next_parents = []
            for pid in current_parents:
                q = (f"name='{part}' and '{pid}' in parents "
                     f"and mimeType='application/vnd.google-apps.folder' "
                     f"and trashed=false")
                resp = service.files().list(
                    q=q, fields='files(id)',
                    pageSize=10, supportsAllDrives=True,
                    includeItemsFromAllDrives=True).execute()
                for f in resp.get('files', []):
                    next_parents.append(f['id'])
            if not next_parents:
                logger.info('[GDrive._resolve_existing] folder "%s" NOT FOUND', part)
                return [], filename
            logger.info('[GDrive._resolve_existing] folder "%s" -> %d match(es): %s',
                        part, len(next_parents), next_parents)
            current_parents = next_parents
        return current_parents, filename

    def _resolve_file_id(self, relative_path):
        """Resolve a relative path to a GDrive file ID.
        Checks ALL duplicate-named folders, then falls back to global search."""
        parts = relative_path.replace('\\', '/').split('/')
        filename = parts[-1]

        # Try 1: check all matching folder paths
        parent_ids, _ = self._resolve_existing(relative_path)
        for parent_id in parent_ids:
            fid = self._find_file(filename, parent_id)
            if fid:
                logger.info('[GDrive._resolve_file_id] found %s in folder %s',
                            filename, parent_id)
                return fid

        # Try 2: search by filename anywhere
        try:
            service = self._get_service()
            q = (f"name='{filename}' "
                 f"and mimeType!='application/vnd.google-apps.folder' "
                 f"and trashed=false")
            resp = service.files().list(
                q=q, fields='files(id,name,parents)',
                pageSize=10, supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute()
            found = resp.get('files', [])
            logger.info('[GDrive._resolve_file_id] fallback found %d files', len(found))
            for f in found:
                return f['id']
        except Exception as e:
            logger.error('[GDrive._resolve_file_id] fallback failed: %s', e)

        return None

    def _to_file_id(self, storage_key):
        """Convert a storage key to a GDrive file ID.

        If storage_key contains '/' it's treated as a relative path and resolved.
        Otherwise it's assumed to be a GDrive file ID already.
        """
        if not storage_key:
            return None
        if '/' in storage_key:
            return self._resolve_file_id(storage_key)
        # Looks like a raw GDrive file ID — verify it exists
        try:
            service = self._get_service()
            service.files().get(fileId=storage_key, fields='id',
                                supportsAllDrives=True).execute()
            return storage_key
        except Exception:
            logger.info('[GDrive._to_file_id] ID %s not accessible', storage_key)
            return None

    def _find_file(self, filename, parent_id):
        """Find a file by name inside a folder. Returns file ID or None."""
        service = self._get_service()
        q = (f"name='{filename}' and '{parent_id}' in parents "
             f"and mimeType!='application/vnd.google-apps.folder' "
             f"and trashed=false")
        logger.info('[GDrive._find_file] query: %s', q)
        resp = service.files().list(
            q=q, fields='files(id,name)',
            pageSize=1, supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = resp.get('files', [])
        logger.info('[GDrive._find_file] found %d files: %s',
                    len(files), files)
        return files[0]['id'] if files else None

    def save(self, file_data, relative_path):
        from googleapiclient.http import MediaInMemoryUpload
        service = self._get_service()

        if hasattr(file_data, 'read'):
            content = file_data.read()
            if hasattr(file_data, 'seek'):
                file_data.seek(0)
        else:
            content = file_data

        media = MediaInMemoryUpload(content, resumable=False)

        # If relative_path looks like a raw GDrive file ID (no '/'), update it directly
        if '/' not in relative_path:
            try:
                service.files().get(fileId=relative_path, fields='id',
                                    supportsAllDrives=True).execute()
                service.files().update(
                    fileId=relative_path, media_body=media).execute()
                logger.info('[GDrive.save] updated by file ID %s', relative_path)
                return relative_path
            except Exception:
                logger.info('[GDrive.save] %s is not a valid file ID, treating as filename',
                            relative_path)

        parent_id, filename = self._resolve_parent(relative_path)
        logger.info('[GDrive.save] parent_id=%s, filename=%s, path=%s',
                    parent_id, filename, relative_path)

        # Check if file already exists — update instead of duplicate
        existing_id = self._find_file(filename, parent_id)
        if existing_id:
            service.files().update(
                fileId=existing_id, media_body=media).execute()
            logger.info('[GDrive.save] updated existing file %s', existing_id)
            return existing_id

        metadata = {
            'name': filename,
            'parents': [parent_id],
        }
        created = service.files().create(
            body=metadata, media_body=media, fields='id').execute()
        file_id = created['id']
        logger.info('[GDrive.save] created new file %s', file_id)
        return file_id

    def delete(self, storage_key):
        try:
            service = self._get_service()
            file_id = self._to_file_id(storage_key)
            if not file_id:
                logger.info('[GDrive.delete] could not resolve key: %s', storage_key)
                return
            service.files().delete(fileId=file_id,
                                   supportsAllDrives=True).execute()
        except Exception as e:
            logger.debug('Google Drive delete failed for %s: %s', storage_key, e)

    def get(self, storage_key):
        try:
            service = self._get_service()
            file_id = self._to_file_id(storage_key)
            if not file_id:
                logger.info('[GDrive.get] could not resolve key: %s', storage_key)
                return None
            meta = service.files().get(fileId=file_id,
                                       fields='name').execute()
            content = service.files().get_media(fileId=file_id).execute()
            logger.info('[GDrive.get] key=%s -> fileId=%s, name=%s, %d bytes',
                        storage_key, file_id, meta.get('name'), len(content))
            return content, meta.get('name', 'file')
        except Exception as e:
            logger.info('[GDrive.get] failed for %s: %s', storage_key, e)
            return None

    def send(self, storage_key, download_name=None):
        from flask import send_file, abort
        result = self.get(storage_key)
        if not result:
            abort(404)
        file_bytes, filename = result
        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=download_name or filename
        )

    def exists(self, storage_key):
        try:
            if not storage_key:
                return False
            if '/' in storage_key:
                fid = self._resolve_file_id(storage_key)
                logger.info('[GDrive.exists] path=%s -> resolved=%s', storage_key, fid)
                return fid is not None
            # Raw GDrive file ID — check directly
            service = self._get_service()
            service.files().get(fileId=storage_key, fields='id',
                                supportsAllDrives=True).execute()
            logger.info('[GDrive.exists] fileId=%s -> True', storage_key)
            return True
        except Exception as e:
            logger.info('[GDrive.exists] key=%s -> False (%s)', storage_key, e)
            return False


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class ExternalStorageModule(BaseModule):

    @property
    def module_id(self):
        return 'external_storage'

    @property
    def name(self):
        return 'External Storage'

    @property
    def description(self):
        return 'Route file storage to external backends (Local, AWS S3, Google Cloud Storage, Google Drive)'

    @property
    def version(self):
        return '0.2.0'

    @property
    def nav_items(self):
        return []

    def register_models(self, db):
        self._db = db

        class StorageConfig(db.Model):
            __tablename__ = 'storage_config'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            storage_type = db.Column(db.String(50), default='local')
            # AWS S3
            s3_bucket = db.Column(db.String(200))
            s3_prefix = db.Column(db.String(200), default='')
            s3_region = db.Column(db.String(50))
            s3_access_key = db.Column(db.String(200))
            s3_secret_key = db.Column(db.String(500))
            s3_profile = db.Column(db.String(100))
            s3_auth_method = db.Column(db.String(20), default='sdk')
            # Google Cloud Storage
            gcs_bucket = db.Column(db.String(200))
            gcs_prefix = db.Column(db.String(200), default='')
            gcs_credentials_json = db.Column(db.String(500))
            gcs_auth_method = db.Column(db.String(20), default='adc')
            # Google Drive
            gdrive_folder_id = db.Column(db.String(200))
            gdrive_credentials_json = db.Column(db.String(500))
            gdrive_auth_method = db.Column(db.String(20), default='adc')
            # Meta
            updated_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.StorageConfig = StorageConfig
        return {'StorageConfig': StorageConfig}

    def register_routes(self, app):
        bp = Blueprint('external_storage', __name__,
                       template_folder='templates',
                       url_prefix='/storage')
        login_required = self.core.login_required
        module = self

        @bp.route('/test', methods=['POST'])
        @login_required
        def storage_test():
            return module._test_connection()

        app.register_blueprint(bp)

    def on_enable(self):
        # Ensure new columns exist (migration for upgrades from v0.1)
        self._migrate_columns()
        self._apply_backend()

    def _migrate_columns(self):
        """Add new columns if upgrading from v0.1 (S3-only schema)."""
        import sqlalchemy
        inspector = sqlalchemy.inspect(self._db.engine)
        existing = {c['name'] for c in inspector.get_columns('storage_config')}
        new_cols = {
            'gcs_bucket': 'VARCHAR(200)',
            'gcs_prefix': 'VARCHAR(200)',
            'gcs_credentials_json': 'VARCHAR(500)',
            'gcs_auth_method': 'VARCHAR(20)',
            'gdrive_folder_id': 'VARCHAR(200)',
            'gdrive_credentials_json': 'VARCHAR(500)',
            'gdrive_auth_method': 'VARCHAR(20)',
        }
        for col, col_type in new_cols.items():
            if col not in existing:
                try:
                    self._db.session.execute(
                        sqlalchemy.text(
                            f'ALTER TABLE storage_config ADD COLUMN {col} {col_type}'))
                    self._db.session.commit()
                except Exception:
                    self._db.session.rollback()

    def _get_config(self):
        config = self.StorageConfig.query.first()
        if not config:
            config = self.StorageConfig(storage_type='local')
            self._db.session.add(config)
            self._db.session.commit()
        return config

    def _apply_backend(self):
        config = self._get_config()
        st = config.storage_type

        if st == 's3' and config.s3_bucket:
            access_key = config.s3_access_key or None
            secret_key = config.s3_secret_key or None
            profile = config.s3_profile or None
            if config.s3_auth_method == 'keys' and access_key and secret_key:
                backend = S3StorageBackend(
                    bucket=config.s3_bucket, prefix=config.s3_prefix or '',
                    region=config.s3_region or None,
                    access_key=access_key, secret_key=secret_key)
            elif config.s3_auth_method == 'profile' and profile:
                backend = S3StorageBackend(
                    bucket=config.s3_bucket, prefix=config.s3_prefix or '',
                    region=config.s3_region or None, profile=profile)
            else:
                backend = S3StorageBackend(
                    bucket=config.s3_bucket, prefix=config.s3_prefix or '',
                    region=config.s3_region or None)
            self.core.set_storage_backend(backend)
            logger.info('Using S3 backend: %s', config.s3_bucket)

        elif st == 'gcs' and config.gcs_bucket:
            creds = config.gcs_credentials_json or None
            backend = GCSStorageBackend(
                bucket_name=config.gcs_bucket,
                prefix=config.gcs_prefix or '',
                credentials_json=creds if config.gcs_auth_method == 'json' else None)
            self.core.set_storage_backend(backend)
            logger.info('Using GCS backend: %s', config.gcs_bucket)

        elif st == 'gdrive' and config.gdrive_folder_id:
            creds = config.gdrive_credentials_json or None
            backend = GoogleDriveStorageBackend(
                folder_id=config.gdrive_folder_id,
                credentials_json=creds if config.gdrive_auth_method == 'json' else None)
            self.core.set_storage_backend(backend)
            logger.info('Using Google Drive backend: folder %s', config.gdrive_folder_id)

        else:
            self.core.set_storage_backend(
                LocalStorageBackend(self.core.app_path))
            logger.info('Using local storage backend')

    def _test_connection(self):
        config = self._get_config()
        st = config.storage_type
        if st == 'local':
            flash('Local storage is always available.', 'success')
            return redirect(url_for('settings') + '#modules')
        try:
            self._apply_backend()
            backend = self.core.storage
            test_key = '_storage_test_probe.txt'
            backend.save(b'test', test_key)
            backend.delete(test_key)
            labels = {'s3': 'S3', 'gcs': 'Google Cloud Storage', 'gdrive': 'Google Drive'}
            flash(f'{labels.get(st, st)} connection successful!', 'success')
        except Exception as e:
            flash(f'Connection failed: {e}', 'danger')
        return redirect(url_for('settings') + '#modules')

    def get_settings_html(self, settings):
        config = self._get_config()
        st = config.storage_type or 'local'

        # S3 values
        s3_bucket = config.s3_bucket or ''
        s3_prefix = config.s3_prefix or ''
        s3_region = config.s3_region or ''
        s3_ak = config.s3_access_key or ''
        s3_sk = config.s3_secret_key or ''
        s3_prof = config.s3_profile or ''
        s3_auth = config.s3_auth_method or 'sdk'

        # GCS values
        gcs_bucket = getattr(config, 'gcs_bucket', '') or ''
        gcs_prefix = getattr(config, 'gcs_prefix', '') or ''
        gcs_creds = getattr(config, 'gcs_credentials_json', '') or ''
        gcs_auth = getattr(config, 'gcs_auth_method', 'adc') or 'adc'

        # Google Drive values
        gd_folder = getattr(config, 'gdrive_folder_id', '') or ''
        gd_creds = getattr(config, 'gdrive_credentials_json', '') or ''
        gd_auth = getattr(config, 'gdrive_auth_method', 'adc') or 'adc'

        # Checked states
        def chk(val, expected):
            return 'checked' if val == expected else ''

        # Shared styles
        rs = 'width: auto !important; padding: 0 !important; margin-right: 8px; vertical-align: middle;'
        ls = 'display: block; margin-bottom: 8px; font-weight: normal; cursor: pointer;'
        panel = 'margin-left: 20px; padding: 15px; background: #f8f9fa; border-radius: 6px; border: 1px solid #e0e0e0; margin-bottom: 15px;'

        # JS helper to show/hide config panels
        js = """
<script>
function esShowPanel(type) {
    ['s3-config','gcs-config','gdrive-config'].forEach(function(id) {
        document.getElementById(id).style.display = 'none';
    });
    if (type !== 'local') {
        var el = document.getElementById(type + '-config');
        if (el) el.style.display = 'block';
    }
}
</script>"""

        return f'''{js}
<h3 style="margin-bottom: 15px; color: #333;">File Storage</h3>
<div style="margin-bottom: 20px;">
    <label style="display: block; margin-bottom: 8px; font-weight: bold; color: #333;">Storage Backend</label>
    <div style="margin-left: 4px;">
        <label style="{ls}">
            <input type="radio" name="es_storage_type" value="local" {chk(st,'local')}
                   onchange="esShowPanel('local')" style="{rs}">
            Local Filesystem (default)
        </label>
        <label style="{ls}">
            <input type="radio" name="es_storage_type" value="s3" {chk(st,'s3')}
                   onchange="esShowPanel('s3')" style="{rs}">
            AWS S3
        </label>
        <label style="{ls}">
            <input type="radio" name="es_storage_type" value="gcs" {chk(st,'gcs')}
                   onchange="esShowPanel('gcs')" style="{rs}">
            Google Cloud Storage
        </label>
        <label style="{ls}">
            <input type="radio" name="es_storage_type" value="gdrive" {chk(st,'gdrive')}
                   onchange="esShowPanel('gdrive')" style="{rs}">
            Google Drive
        </label>
    </div>
</div>

<!-- AWS S3 Config -->
<div id="s3-config" style="display: {'block' if st=='s3' else 'none'}; {panel}">
    <h4 style="margin-top:0; color:#555;">AWS S3 Settings</h4>
    <div class="form-group">
        <label for="es_s3_bucket">S3 Bucket Name *</label>
        <input type="text" id="es_s3_bucket" name="es_s3_bucket" value="{s3_bucket}" placeholder="my-app-files" style="max-width: 300px;">
    </div>
    <div class="form-group">
        <label for="es_s3_prefix">Key Prefix (optional)</label>
        <input type="text" id="es_s3_prefix" name="es_s3_prefix" value="{s3_prefix}" placeholder="autonomos/" style="max-width: 300px;">
        <small style="display: block; margin-top: 4px; color: #666;">Files stored under this prefix in the bucket.</small>
    </div>
    <div class="form-group">
        <label for="es_s3_region">AWS Region</label>
        <input type="text" id="es_s3_region" name="es_s3_region" value="{s3_region}" placeholder="eu-west-1" style="max-width: 200px;">
    </div>
    <div style="margin-bottom: 20px;">
        <label style="display: block; margin-bottom: 8px; font-weight: bold; color: #333;">Authentication Method</label>
        <div style="margin-left: 4px;">
            <label style="{ls}">
                <input type="radio" name="es_s3_auth_method" value="sdk" {chk(s3_auth,'sdk')}
                       onchange="document.getElementById('s3-keys').style.display='none'; document.getElementById('s3-profile').style.display='none'"
                       style="{rs}">
                SDK Default (env vars, instance role)
            </label>
            <label style="{ls}">
                <input type="radio" name="es_s3_auth_method" value="keys" {chk(s3_auth,'keys')}
                       onchange="document.getElementById('s3-keys').style.display='block'; document.getElementById('s3-profile').style.display='none'"
                       style="{rs}">
                Access Keys
            </label>
            <label style="{ls}">
                <input type="radio" name="es_s3_auth_method" value="profile" {chk(s3_auth,'profile')}
                       onchange="document.getElementById('s3-keys').style.display='none'; document.getElementById('s3-profile').style.display='block'"
                       style="{rs}">
                AWS Profile
            </label>
        </div>
    </div>
    <div id="s3-keys" style="display: {'block' if s3_auth=='keys' else 'none'}; margin-left: 20px;">
        <div class="form-group">
            <label for="es_s3_access_key">Access Key ID</label>
            <input type="text" id="es_s3_access_key" name="es_s3_access_key" value="{s3_ak}" style="max-width: 300px;" autocomplete="off">
        </div>
        <div class="form-group">
            <label for="es_s3_secret_key">Secret Access Key</label>
            <input type="password" id="es_s3_secret_key" name="es_s3_secret_key" value="{s3_sk}" style="max-width: 300px;" autocomplete="off">
        </div>
    </div>
    <div id="s3-profile" style="display: {'block' if s3_auth=='profile' else 'none'}; margin-left: 20px;">
        <div class="form-group">
            <label for="es_s3_profile">AWS Profile Name</label>
            <input type="text" id="es_s3_profile" name="es_s3_profile" value="{s3_prof}" placeholder="default" style="max-width: 200px;">
        </div>
    </div>
    <div style="margin-top: 15px;">
        <button type="button" class="btn btn-secondary" style="font-size: 12px; padding: 6px 14px;"
                onclick="var f=document.createElement('form');f.method='POST';f.action='/storage/test';document.body.appendChild(f);f.submit();">
            Test Connection
        </button>
    </div>
</div>

<!-- Google Cloud Storage Config -->
<div id="gcs-config" style="display: {'block' if st=='gcs' else 'none'}; {panel}">
    <h4 style="margin-top:0; color:#555;">Google Cloud Storage Settings</h4>
    <div class="form-group">
        <label for="es_gcs_bucket">GCS Bucket Name *</label>
        <input type="text" id="es_gcs_bucket" name="es_gcs_bucket" value="{gcs_bucket}" placeholder="my-gcs-bucket" style="max-width: 300px;">
    </div>
    <div class="form-group">
        <label for="es_gcs_prefix">Object Prefix (optional)</label>
        <input type="text" id="es_gcs_prefix" name="es_gcs_prefix" value="{gcs_prefix}" placeholder="autonomos/" style="max-width: 300px;">
    </div>
    <div style="margin-bottom: 20px;">
        <label style="display: block; margin-bottom: 8px; font-weight: bold; color: #333;">Authentication Method</label>
        <div style="margin-left: 4px;">
            <label style="{ls}">
                <input type="radio" name="es_gcs_auth_method" value="adc" {chk(gcs_auth,'adc')}
                       onchange="document.getElementById('gcs-json').style.display='none'"
                       style="{rs}">
                Application Default Credentials (gcloud auth / env GOOGLE_APPLICATION_CREDENTIALS)
            </label>
            <label style="{ls}">
                <input type="radio" name="es_gcs_auth_method" value="json" {chk(gcs_auth,'json')}
                       onchange="document.getElementById('gcs-json').style.display='block'"
                       style="{rs}">
                Service Account JSON Key File
            </label>
        </div>
    </div>
    <div id="gcs-json" style="display: {'block' if gcs_auth=='json' else 'none'}; margin-left: 20px;">
        <div class="form-group">
            <label for="es_gcs_credentials_json">Path to JSON Key File</label>
            <input type="text" id="es_gcs_credentials_json" name="es_gcs_credentials_json" value="{gcs_creds}" placeholder="/path/to/service-account.json" style="max-width: 400px;">
            <small style="display: block; margin-top: 4px; color: #666;">Absolute path on the server to the Service Account JSON key file.</small>
        </div>
    </div>
    <div style="margin-top: 15px;">
        <button type="button" class="btn btn-secondary" style="font-size: 12px; padding: 6px 14px;"
                onclick="var f=document.createElement('form');f.method='POST';f.action='/storage/test';document.body.appendChild(f);f.submit();">
            Test Connection
        </button>
    </div>
</div>

<!-- Google Drive Config -->
<div id="gdrive-config" style="display: {'block' if st=='gdrive' else 'none'}; {panel}">
    <h4 style="margin-top:0; color:#555;">Google Drive Settings</h4>
    <div class="form-group">
        <label for="es_gdrive_folder_id">Drive Folder ID *</label>
        <input type="text" id="es_gdrive_folder_id" name="es_gdrive_folder_id" value="{gd_folder}" placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs" style="max-width: 400px;">
        <small style="display: block; margin-top: 4px; color: #666;">The ID from the folder URL: drive.google.com/drive/folders/<b>THIS_PART</b></small>
    </div>
    <div style="margin-bottom: 20px;">
        <label style="display: block; margin-bottom: 8px; font-weight: bold; color: #333;">Authentication Method</label>
        <div style="margin-left: 4px;">
            <label style="{ls}">
                <input type="radio" name="es_gdrive_auth_method" value="adc" {chk(gd_auth,'adc')}
                       onchange="document.getElementById('gdrive-json').style.display='none'"
                       style="{rs}">
                Application Default Credentials
            </label>
            <label style="{ls}">
                <input type="radio" name="es_gdrive_auth_method" value="json" {chk(gd_auth,'json')}
                       onchange="document.getElementById('gdrive-json').style.display='block'"
                       style="{rs}">
                Service Account JSON Key File
            </label>
        </div>
    </div>
    <div id="gdrive-json" style="display: {'block' if gd_auth=='json' else 'none'}; margin-left: 20px;">
        <div class="form-group">
            <label for="es_gdrive_credentials_json">Path to JSON Key File</label>
            <input type="text" id="es_gdrive_credentials_json" name="es_gdrive_credentials_json" value="{gd_creds}" placeholder="/path/to/service-account.json" style="max-width: 400px;">
            <small style="display: block; margin-top: 4px; color: #666;">The Service Account must have access to the target folder (share the folder with the SA email).</small>
        </div>
    </div>
    <div style="margin-top: 15px;">
        <button type="button" class="btn btn-secondary" style="font-size: 12px; padding: 6px 14px;"
                onclick="var f=document.createElement('form');f.method='POST';f.action='/storage/test';document.body.appendChild(f);f.submit();">
            Test Connection
        </button>
    </div>
</div>'''

    def save_settings(self, settings, form):
        if 'es_storage_type' not in form:
            return
        config = self._get_config()
        config.storage_type = form.get('es_storage_type', 'local')
        # S3
        config.s3_bucket = form.get('es_s3_bucket', '') or None
        config.s3_prefix = form.get('es_s3_prefix', '') or ''
        config.s3_region = form.get('es_s3_region', '') or None
        config.s3_auth_method = form.get('es_s3_auth_method', 'sdk')
        config.s3_access_key = form.get('es_s3_access_key', '') or None
        config.s3_secret_key = form.get('es_s3_secret_key', '') or None
        config.s3_profile = form.get('es_s3_profile', '') or None
        # GCS
        config.gcs_bucket = form.get('es_gcs_bucket', '') or None
        config.gcs_prefix = form.get('es_gcs_prefix', '') or ''
        config.gcs_auth_method = form.get('es_gcs_auth_method', 'adc')
        config.gcs_credentials_json = form.get('es_gcs_credentials_json', '') or None
        # Google Drive
        config.gdrive_folder_id = form.get('es_gdrive_folder_id', '') or None
        config.gdrive_auth_method = form.get('es_gdrive_auth_method', 'adc')
        config.gdrive_credentials_json = form.get('es_gdrive_credentials_json', '') or None
        config.updated_at = datetime.utcnow()
        self._db.session.commit()
        self._apply_backend()
