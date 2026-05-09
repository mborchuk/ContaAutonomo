#!/usr/bin/env python3
"""
Backup Module
Full backup/restore: DB (JSON) + all uploaded files in a single ZIP archive.
Supports optional AES encryption, custom backup path, and external storage integration.
"""

from module_manager import BaseModule, LocalStorageBackend
from flask import (Blueprint, request, redirect, url_for,
                   flash, send_file, session, render_template)
from datetime import datetime, date, timezone
from dataclasses import dataclass
from pathlib import Path
from io import BytesIO
import os, re, json, shutil, zipfile, base64
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


@dataclass
class ColumnMeta:
    """Metadata for a single database column."""
    name: str
    type: str


class BackupSerializer:
    """Handles type-safe serialization of DB rows to JSON and back."""

    # Column type strings that map to date handling
    _DATE_TYPES = {'DATE'}
    _DATETIME_TYPES = {'DATETIME', 'TIMESTAMP'}
    _BINARY_TYPES = {'BLOB', 'BINARY', 'VARBINARY', 'LONGBLOB', 'MEDIUMBLOB',
                     'TINYBLOB', 'BYTEA', 'LARGEBINARY'}
    _BOOL_TYPES = {'BOOLEAN', 'BOOL'}
    _INT_TYPES = {'INTEGER', 'INT', 'SMALLINT', 'BIGINT', 'TINYINT', 'MEDIUMINT'}
    _FLOAT_TYPES = {'FLOAT', 'DOUBLE', 'REAL', 'NUMERIC', 'DECIMAL'}

    def serialize_value(self, value, column_type: str):
        """
        Convert a Python value to its JSON-safe representation.

        Type mapping:
        - date → ISO 8601 string (YYYY-MM-DD)
        - datetime → ISO 8601 with timezone
        - bytes/BLOB → Base64 with __base64__ prefix
        - bool → JSON boolean
        - int/float → JSON number
        - None → JSON null
        - Everything else → JSON string
        """
        if value is None:
            return None

        # Normalize column type for comparison
        col_upper = self._normalize_type(column_type)

        # Handle by Python type first (more reliable than column type string)
        if isinstance(value, datetime):
            # Ensure timezone info is present
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        elif isinstance(value, date):
            return value.isoformat()
        elif isinstance(value, (bytes, bytearray)):
            return '__base64__' + base64.b64encode(value).decode('ascii')
        elif isinstance(value, bool):
            return value
        elif isinstance(value, int):
            return value
        elif isinstance(value, float):
            return value

        # Fall back to column type hints for string values that may need special handling
        if col_upper in self._BINARY_TYPES and isinstance(value, str):
            # Already a string representation — pass through
            if value.startswith('__base64__'):
                return value
            # Try to encode as bytes
            return '__base64__' + base64.b64encode(value.encode('utf-8')).decode('ascii')

        # Everything else → string
        return str(value)

    def deserialize_value(self, value, column_type: str):
        """
        Convert a JSON value back to its Python/DB representation.

        Inverse of serialize_value.
        """
        if value is None:
            return None

        col_upper = self._normalize_type(column_type)

        # Binary: decode __base64__ prefix
        if col_upper in self._BINARY_TYPES:
            if isinstance(value, str) and value.startswith('__base64__'):
                return base64.b64decode(value[len('__base64__'):])
            return value

        # Boolean
        if col_upper in self._BOOL_TYPES:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes')
            return bool(value)

        # Integer
        if col_upper in self._INT_TYPES:
            if isinstance(value, (int, float)):
                return int(value)
            return int(value) if value is not None else None

        # Float
        if col_upper in self._FLOAT_TYPES:
            if isinstance(value, (int, float)):
                return float(value)
            return float(value) if value is not None else None

        # Datetime (must check before date since datetime strings also match date)
        if col_upper in self._DATETIME_TYPES:
            if isinstance(value, str):
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            return value

        # Date
        if col_upper in self._DATE_TYPES:
            if isinstance(value, str):
                # ISO 8601 date string: YYYY-MM-DD
                return date.fromisoformat(value)
            return value

        # Everything else: return as-is (string)
        return value

    def serialize_table(self, table_name: str, rows: list, columns: list) -> dict:
        """
        Serialize an entire table with schema metadata in v3.0 format.

        Args:
            table_name: Name of the database table.
            rows: List of dicts, each representing a row.
            columns: List of ColumnMeta objects with name and type.

        Returns:
            Dict with 'columns' metadata array and 'rows' array.
        """
        col_type_map = {col.name: col.type for col in columns}

        serialized_rows = []
        for row in rows:
            serialized_row = {}
            for col in columns:
                value = row.get(col.name)
                serialized_row[col.name] = self.serialize_value(value, col.type)
            serialized_rows.append(serialized_row)

        return {
            'columns': [{'name': col.name, 'type': col.type} for col in columns],
            'rows': serialized_rows,
        }

    def deserialize_table(self, table_data: dict) -> tuple:
        """
        Deserialize table data back to column metadata and rows.

        Args:
            table_data: Dict with 'columns' and 'rows' keys (v3.0 format).

        Returns:
            Tuple of (list[ColumnMeta], list[dict]) — column metadata and deserialized rows.
        """
        columns = [
            ColumnMeta(name=col['name'], type=col['type'])
            for col in table_data['columns']
        ]
        col_type_map = {col.name: col.type for col in columns}

        deserialized_rows = []
        for row in table_data['rows']:
            deserialized_row = {}
            for col in columns:
                value = row.get(col.name)
                deserialized_row[col.name] = self.deserialize_value(value, col.type)
            deserialized_rows.append(deserialized_row)

        return columns, deserialized_rows

    @staticmethod
    def _normalize_type(column_type: str) -> str:
        """
        Normalize a SQLAlchemy column type string for comparison.

        Strips parenthesized parameters (e.g., VARCHAR(200) → VARCHAR)
        and converts to uppercase.
        """
        if not column_type:
            return ''
        # Remove anything in parentheses: VARCHAR(200) → VARCHAR
        normalized = re.sub(r'\(.*\)', '', column_type).strip().upper()
        return normalized


FILE_FOLDERS = ['expenses_files', 'documents_files', 'tax_forms', 'invoices_pdf']

SUPPORTED_VERSIONS = {'2.1', '3.0'}


class VersionDetector:
    """Detects backup version and selects restore strategy."""

    def detect_version(self, db_snapshot: dict) -> str:
        """Returns version string ('2.1' or '3.0') from the snapshot's version field.

        Raises ValueError for unrecognized versions.
        """
        version = db_snapshot.get('version')
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unrecognized backup version: {version!r}. "
                f"Supported versions: {sorted(SUPPORTED_VERSIONS)}"
            )
        return version

    def has_file_manifest(self, archive: zipfile.ZipFile) -> bool:
        """Check if archive contains a file_manifest.json."""
        return 'file_manifest.json' in archive.namelist()


class TopologicalSorter:
    """Sorts tables respecting FK dependencies for correct insertion order."""

    def sort(self, inspector, table_names: list[str]) -> list[str]:
        """Return tables in insertion order (parents before children).

        Uses DFS-based topological sort on the foreign key dependency graph.
        Self-referencing tables (self-edges) are skipped to avoid cycles.

        Args:
            inspector: SQLAlchemy inspector providing get_foreign_keys(table_name).
            table_names: List of table names to sort.

        Returns:
            List of table names ordered so that referenced (parent) tables
            appear before referencing (child) tables.
        """
        # Build adjacency graph: for each table, collect its dependencies
        # (tables it references via foreign keys)
        table_set = set(table_names)
        dependencies: dict[str, set[str]] = {t: set() for t in table_names}

        for table_name in table_names:
            try:
                fks = inspector.get_foreign_keys(table_name)
            except Exception:
                fks = []
            for fk in fks:
                referred = fk.get('referred_table')
                if referred and referred != table_name and referred in table_set:
                    dependencies[table_name].add(referred)

        # DFS-based topological sort (post-order gives reverse topo order)
        ordered: list[str] = []
        visited: set[str] = set()
        in_stack: set[str] = set()  # for cycle detection

        def _dfs(node: str) -> None:
            if node in visited:
                return
            if node in in_stack:
                # Cycle detected — skip to avoid infinite recursion
                return
            in_stack.add(node)
            for dep in dependencies.get(node, set()):
                _dfs(dep)
            in_stack.discard(node)
            visited.add(node)
            ordered.append(node)

        for table_name in table_names:
            _dfs(table_name)

        return ordered


@dataclass
class FileEntry:
    """Represents a single file in the backup manifest."""
    key: str           # storage key (relative path or backend-specific ID)
    sha256: str        # hex digest
    size: int          # bytes
    backend: str       # 'local' | 's3' | 'gcs' | 'gdrive'


class FileManifestBuilder:
    """Discovers and catalogs all files for backup."""

    # Tables and their columns that reference files
    _FILE_REFERENCE_COLUMNS = {
        'expense': 'file_path',
        'document': 'file_path',
        'document_file': 'file_path',
        'tax_form': 'file_path',
        'invoice': 'pdf_storage_key',
    }

    def build_manifest(self, db_session, storage_backend, app_root: Path) -> list:
        """
        Scan local dirs + DB references to build complete file list.

        Args:
            db_session: SQLAlchemy session for querying file references.
            storage_backend: Storage backend instance (has .get(key) method).
            app_root: Path to the application root directory.

        Returns:
            List of FileEntry objects representing all files to include in backup.
        """
        import hashlib
        from sqlalchemy import text, inspect as sa_inspect

        seen_keys: dict[str, FileEntry] = {}  # key → FileEntry (for deduplication)

        # Step 1: Scan local directories
        for folder in FILE_FOLDERS:
            folder_path = app_root / folder
            if not folder_path.exists() or not folder_path.is_dir():
                continue
            for file_path in folder_path.rglob('*'):
                if not file_path.is_file() or file_path.name.startswith('.'):
                    continue
                relative_key = str(file_path.relative_to(app_root))
                try:
                    file_bytes = file_path.read_bytes()
                    entry = FileEntry(
                        key=relative_key,
                        sha256=self.compute_hash(file_bytes),
                        size=len(file_bytes),
                        backend='local',
                    )
                    seen_keys[relative_key] = entry
                except (OSError, IOError) as e:
                    logger.warning(
                        'Could not read local file %s: %s',
                        _sanitize_for_log(relative_key), e
                    )

        # Step 2: Query DB for file references and fetch from storage backend
        is_local = isinstance(storage_backend, LocalStorageBackend)
        if not is_local:
            backend_type = self._detect_backend_type(storage_backend)
            try:
                inspector = sa_inspect(db_session.get_bind())
                existing_tables = set(inspector.get_table_names())
            except Exception:
                existing_tables = set()

            for table_name, column_name in self._FILE_REFERENCE_COLUMNS.items():
                if table_name not in existing_tables:
                    continue
                try:
                    # Verify column exists in table
                    columns = [c['name'] for c in inspector.get_columns(table_name)]
                    if column_name not in columns:
                        continue

                    result = db_session.execute(
                        text(f'SELECT "{column_name}" FROM "{table_name}" '
                             f'WHERE "{column_name}" IS NOT NULL AND "{column_name}" != \'\'')
                    )
                    for row in result:
                        storage_key = row[0]
                        if not storage_key or storage_key in seen_keys:
                            # Already included (deduplication) or empty
                            continue
                        try:
                            file_result = storage_backend.get(storage_key)
                            if file_result is None:
                                logger.warning(
                                    'File not found on storage backend: %s',
                                    _sanitize_for_log(storage_key)
                                )
                                continue
                            # storage.get() returns (bytes, filename) tuple
                            file_bytes = file_result[0] if isinstance(file_result, tuple) else file_result
                            entry = FileEntry(
                                key=storage_key,
                                sha256=self.compute_hash(file_bytes),
                                size=len(file_bytes),
                                backend=backend_type,
                            )
                            seen_keys[storage_key] = entry
                        except Exception as e:
                            logger.warning(
                                'Could not fetch file from storage backend %s: %s',
                                _sanitize_for_log(storage_key), e
                            )
                except Exception as e:
                    logger.warning(
                        'Error querying table %s for file references: %s',
                        _sanitize_for_log(table_name), e
                    )

        return list(seen_keys.values())

    @staticmethod
    def compute_hash(file_bytes: bytes) -> str:
        """SHA-256 hex digest of file content.

        Args:
            file_bytes: Raw bytes of the file.

        Returns:
            Lowercase 64-character hex string.
        """
        import hashlib
        return hashlib.sha256(file_bytes).hexdigest()

    @staticmethod
    def _detect_backend_type(storage_backend) -> str:
        """Determine the backend type string from the storage backend instance.

        Returns one of: 'local', 's3', 'gcs', 'gdrive'.
        """
        class_name = type(storage_backend).__name__.lower()
        if 's3' in class_name:
            return 's3'
        elif 'gcs' in class_name or 'googlecloud' in class_name:
            return 'gcs'
        elif 'drive' in class_name or 'gdrive' in class_name:
            return 'gdrive'
        return 'local'


class BackupCreator:
    """Creates v3.0 backup archives.

    Orchestrates the full backup flow: serializes all database tables,
    builds a file manifest, creates a ZIP archive, and optionally encrypts
    and uploads to external storage.
    """

    # Known module config table names
    _KNOWN_CONFIG_TABLES = {'backup_config', 'storage_config'}
    # Suffix that identifies module config tables
    _CONFIG_SUFFIX = '_config'

    def __init__(self, db, storage_backend, app_root: Path, backup_dir: Path,
                 encrypt_func=None, is_external_storage_enabled_func=None):
        """
        Initialize BackupCreator with required dependencies.

        Args:
            db: SQLAlchemy database instance (has .session, .engine).
            storage_backend: Storage backend instance (has .get(), .save()).
            app_root: Path to the application root directory.
            backup_dir: Path to the backup output directory.
            encrypt_func: Optional callable(data: bytes, password: str) -> bytes.
            is_external_storage_enabled_func: Optional callable() -> bool.
        """
        self._db = db
        self._storage_backend = storage_backend
        self._app_root = app_root
        self._backup_dir = backup_dir
        self._encrypt_func = encrypt_func
        self._is_external_storage_enabled = is_external_storage_enabled_func or (lambda: False)
        self._serializer = BackupSerializer()
        self._manifest_builder = FileManifestBuilder()

    def create_backup(self, password: str | None = None, prefix: str = '') -> tuple[bool, str]:
        """
        Create a full v3.0 backup.

        Returns (success, filename_or_error).

        The backup includes:
        - db_backup.json: v3.0 DB snapshot with all tables and schema metadata
        - file_manifest.json: manifest of all backed-up files
        - All files from local directories and external storage
        """
        try:
            from sqlalchemy import text, inspect as sa_inspect

            # Generate filename
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            ext = '.zip.enc' if password else '.zip'
            filename = f'{prefix}backup_{ts}{ext}'

            # Step 1: Serialize all database tables
            db_snapshot = self._serialize_database()
            if db_snapshot is None:
                return False, 'Failed to read settings table — backup aborted'

            # Step 2: Build file manifest
            manifest_entries = self._manifest_builder.build_manifest(
                self._db.session, self._storage_backend, self._app_root
            )

            # Step 3: Build file manifest JSON
            file_manifest = {
                'version': '1.0',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'files': [
                    {
                        'key': entry.key,
                        'sha256': entry.sha256,
                        'size': entry.size,
                        'backend': entry.backend,
                    }
                    for entry in manifest_entries
                ],
            }

            # Step 4: Create ZIP archive
            buf = BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Write DB snapshot
                zf.writestr('db_backup.json',
                            json.dumps(db_snapshot, indent=2, ensure_ascii=False))
                # Write file manifest
                zf.writestr('file_manifest.json',
                            json.dumps(file_manifest, indent=2, ensure_ascii=False))
                # Write all files
                self._add_files_to_zip(zf, manifest_entries)

            data = buf.getvalue()

            # Step 5: Encrypt if password provided
            if password:
                if self._encrypt_func is None:
                    return False, 'Encryption requested but no encryption function available'
                data = self._encrypt_func(data, password)

            # Step 6: Save locally
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            (self._backup_dir / filename).write_bytes(data)
            logger.info('Backup saved locally: %s', self._backup_dir / filename)

            # Step 7: Upload to external storage if configured
            if self._is_external_storage_enabled():
                try:
                    self._storage_backend.save(data, f'backups/{filename}')
                    logger.info('Backup sent to external storage: backups/%s', filename)
                except Exception as e:
                    logger.warning('Could not save to external storage: %s', e)

            return True, filename

        except Exception as e:
            logger.error('Error creating backup: %s', e)
            return False, str(e)

    def _serialize_database(self) -> dict | None:
        """
        Serialize all database tables into v3.0 format.

        Returns the DB snapshot dict, or None if settings table cannot be read.
        """
        from sqlalchemy import text, inspect as sa_inspect

        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

        try:
            inspector = sa_inspect(self._db.engine)
            all_tables = inspector.get_table_names()
        except Exception as e:
            logger.error('Failed to inspect database: %s', e)
            return None

        # Requirement 3.1: If settings table can't be read, fail immediately
        if 'settings' in all_tables:
            try:
                self._db.session.execute(text('SELECT 1 FROM "settings" LIMIT 1'))
            except Exception as e:
                logger.error('Cannot read settings table: %s', e)
                return None

        # Identify module config tables
        module_configs = self._identify_module_configs(all_tables)

        # Build the snapshot
        tables_data = {}
        for table_name in all_tables:
            if not _SAFE_NAME.match(table_name):
                logger.warning('Skipping table with unsafe name: %s',
                               _sanitize_for_log(table_name))
                continue
            try:
                # Get column metadata
                columns_info = inspector.get_columns(table_name)
                columns = [
                    ColumnMeta(name=col['name'], type=str(col['type']))
                    for col in columns_info
                ]

                # Get all rows
                result = self._db.session.execute(
                    text(f'SELECT * FROM "{table_name}"')
                )
                col_names = [col.name for col in columns]
                rows = []
                for row in result:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        row_dict[col.name] = row[i]
                    rows.append(row_dict)

                # Serialize using BackupSerializer
                tables_data[table_name] = self._serializer.serialize_table(
                    table_name, rows, columns
                )
            except Exception as e:
                logger.warning('Error serializing table %s: %s',
                               _sanitize_for_log(table_name), e)
                # For settings table, this is fatal (Req 3.1)
                if table_name == 'settings':
                    return None
                continue

        db_snapshot = {
            'version': '3.0',
            'created_at': datetime.now(timezone.utc).isoformat(),
            '_module_configs': sorted(module_configs),
            'tables': tables_data,
        }

        return db_snapshot

    def _identify_module_configs(self, all_tables: list[str]) -> list[str]:
        """
        Identify module-specific configuration tables.

        A table is considered a module config if:
        - It ends with '_config' suffix, OR
        - It's in the known config tables set
        """
        configs = []
        for table_name in all_tables:
            if (table_name.endswith(self._CONFIG_SUFFIX)
                    or table_name in self._KNOWN_CONFIG_TABLES):
                configs.append(table_name)
        return configs

    def _add_files_to_zip(self, zf: zipfile.ZipFile,
                          manifest_entries: list) -> None:
        """
        Add all manifest files to the ZIP archive.

        Reads files from local directories or storage backend.
        Logs warnings for individual file fetch failures and continues.
        """
        for entry in manifest_entries:
            try:
                file_bytes = self._read_file(entry)
                if file_bytes is not None:
                    zf.writestr(entry.key, file_bytes)
                else:
                    logger.warning(
                        'File not found during backup: %s',
                        _sanitize_for_log(entry.key)
                    )
            except Exception as e:
                logger.warning(
                    'Could not include file in backup %s: %s',
                    _sanitize_for_log(entry.key), e
                )

    def _read_file(self, entry: FileEntry) -> bytes | None:
        """
        Read file bytes for a given FileEntry.

        Tries local filesystem first, then falls back to storage backend.
        """
        # Try local filesystem
        local_path = self._app_root / entry.key
        if local_path.exists() and local_path.is_file():
            return local_path.read_bytes()

        # Try storage backend
        try:
            result = self._storage_backend.get(entry.key)
            if result is None:
                return None
            # storage.get() returns (bytes, filename) tuple or just bytes
            if isinstance(result, tuple):
                return result[0]
            return result
        except Exception as e:
            logger.warning(
                'Storage backend error for %s: %s',
                _sanitize_for_log(entry.key), e
            )
            return None


@dataclass
class RestoreReport:
    """Detailed status report returned after a restore operation."""
    success: bool
    version_detected: str           # '2.1' or '3.0'
    tables_restored: list           # table names successfully restored
    tables_failed: list             # table names that failed
    files_restored: list            # file keys written
    files_skipped: list             # file keys where hash matched
    files_failed: list              # file keys that errored
    warnings: list                  # non-fatal issues
    rollback_performed: bool        # whether DB was rolled back


@dataclass
class FileRestoreResult:
    """Result of file restore step (Step 5)."""
    files_restored: list
    files_skipped: list
    files_failed: list


class RestorePipeline:
    """Ordered restore with rollback capability.

    Executes a 5-step restore pipeline:
      1. Restore settings table
      2. Restore module_enabled and activate modules
      3. Restore module config tables
      4. Restore remaining tables (topological order, FK disabled)
      5. Restore files (hash-diff only)

    On DB failure in steps 1-4, rolls back to pre-restore snapshot.
    """

    def __init__(self, db, storage_backend, module_manager, app_root: Path,
                 decrypt_func=None):
        """
        Initialize RestorePipeline with required dependencies.

        Args:
            db: SQLAlchemy database instance (has .session, .engine).
            storage_backend: Storage backend instance (has .get(), .save()).
            module_manager: Module manager for activating modules during restore.
            app_root: Path to the application root directory.
            decrypt_func: Optional callable(data: bytes, password: str) -> bytes.
        """
        self._db = db
        self._storage_backend = storage_backend
        self._module_manager = module_manager
        self._app_root = app_root
        self._decrypt_func = decrypt_func
        self._serializer = BackupSerializer()
        self._sorter = TopologicalSorter()
        self._version_detector = VersionDetector()
        # Tracking lists for RestoreReport (populated by steps 1-4)
        self._tables_restored: list[str] = []
        self._warnings: list[str] = []

    def restore(self, archive_bytes: bytes, password: str | None = None) -> RestoreReport:
        """Execute the full restore pipeline.

        Uses VersionDetector to select restore strategy:
        - v3.0: full 5-step pipeline
        - v2.1: legacy restore logic

        Args:
            archive_bytes: Raw bytes of the backup archive (ZIP, possibly encrypted).
            password: Optional decryption password.

        Returns:
            RestoreReport with detailed status of the restore operation.
        """
        # Reset tracking state for this restore operation
        self._tables_restored = []
        self._warnings = []

        # Step 1: Decrypt if needed
        raw_bytes = archive_bytes
        if password:
            if not self._decrypt_func:
                return RestoreReport(
                    success=False,
                    version_detected='unknown',
                    tables_restored=[],
                    tables_failed=[],
                    files_restored=[],
                    files_skipped=[],
                    files_failed=[],
                    warnings=['Password provided but no decrypt function configured'],
                    rollback_performed=False,
                )
            try:
                raw_bytes = self._decrypt_func(archive_bytes, password)
            except Exception as e:
                return RestoreReport(
                    success=False,
                    version_detected='unknown',
                    tables_restored=[],
                    tables_failed=[],
                    files_restored=[],
                    files_skipped=[],
                    files_failed=[],
                    warnings=[f'Decryption failed: {e}'],
                    rollback_performed=False,
                )

        # Step 2: Open ZIP
        try:
            archive = zipfile.ZipFile(BytesIO(raw_bytes), 'r')
        except (zipfile.BadZipFile, Exception) as e:
            return RestoreReport(
                success=False,
                version_detected='unknown',
                tables_restored=[],
                tables_failed=[],
                files_restored=[],
                files_skipped=[],
                files_failed=[],
                warnings=[f'Invalid archive: {e}'],
                rollback_performed=False,
            )

        try:
            # Step 3: Read db_backup.json
            if 'db_backup.json' not in archive.namelist():
                return RestoreReport(
                    success=False,
                    version_detected='unknown',
                    tables_restored=[],
                    tables_failed=[],
                    files_restored=[],
                    files_skipped=[],
                    files_failed=[],
                    warnings=['Archive does not contain db_backup.json'],
                    rollback_performed=False,
                )

            db_snapshot = json.loads(
                archive.read('db_backup.json').decode('utf-8')
            )

            # Step 4: Detect version
            try:
                version = self._version_detector.detect_version(db_snapshot)
            except ValueError as e:
                return RestoreReport(
                    success=False,
                    version_detected='unknown',
                    tables_restored=[],
                    tables_failed=[],
                    files_restored=[],
                    files_skipped=[],
                    files_failed=[],
                    warnings=[str(e)],
                    rollback_performed=False,
                )

            # Step 5: Branch by version
            if version == '3.0':
                return self._restore_v3(db_snapshot, archive)
            elif version == '2.1':
                return self._restore_v2_legacy(db_snapshot, archive)
            else:
                return RestoreReport(
                    success=False,
                    version_detected=version,
                    tables_restored=[],
                    tables_failed=[],
                    files_restored=[],
                    files_skipped=[],
                    files_failed=[],
                    warnings=[f'Unsupported backup version: {version}'],
                    rollback_performed=False,
                )
        finally:
            archive.close()

    def _restore_v3(self, db_snapshot: dict, archive: zipfile.ZipFile) -> RestoreReport:
        """Execute the full v3.0 5-step restore pipeline.

        Creates a pre-restore snapshot, executes steps 1-4 (DB restore),
        rolls back on any DB failure, then executes step 5 (file restore).

        Args:
            db_snapshot: Parsed DB snapshot dict from db_backup.json.
            archive: Open ZipFile containing the backup.

        Returns:
            RestoreReport with detailed status.
        """
        # Create pre-restore snapshot for rollback
        try:
            snapshot = self._create_pre_restore_snapshot()
        except Exception as e:
            return RestoreReport(
                success=False,
                version_detected='3.0',
                tables_restored=[],
                tables_failed=[],
                files_restored=[],
                files_skipped=[],
                files_failed=[],
                warnings=[f'Failed to create pre-restore snapshot: {e}'],
                rollback_performed=False,
            )

        # Execute steps 1-4 (DB restore) with rollback on failure
        rollback_performed = False
        try:
            self._step1_restore_settings(db_snapshot)
            self._step2_restore_modules(db_snapshot)
            self._step3_restore_module_configs(db_snapshot)
            self._step4_restore_tables(db_snapshot)
        except Exception as e:
            logger.error('DB restore failed at steps 1-4: %s', e)
            self._warnings.append(f'DB restore failed: {e}')
            try:
                self._rollback(snapshot)
                rollback_performed = True
                logger.info('Rollback completed successfully')
            except Exception as rb_err:
                logger.error('Rollback also failed: %s', rb_err)
                self._warnings.append(f'Rollback failed: {rb_err}')
                rollback_performed = True

            return RestoreReport(
                success=False,
                version_detected='3.0',
                tables_restored=list(self._tables_restored),
                tables_failed=[],
                files_restored=[],
                files_skipped=[],
                files_failed=[],
                warnings=list(self._warnings),
                rollback_performed=rollback_performed,
            )

        # Step 5: File restore (failures don't trigger rollback)
        files_restored: list[str] = []
        files_skipped: list[str] = []
        files_failed: list[str] = []

        if self._version_detector.has_file_manifest(archive):
            # Use manifest-based file restore
            try:
                manifest_data = json.loads(
                    archive.read('file_manifest.json').decode('utf-8')
                )
                file_entries = manifest_data.get('files', [])
                file_result = self._step5_restore_files(file_entries, archive)
                files_restored = file_result.files_restored
                files_skipped = file_result.files_skipped
                files_failed = file_result.files_failed
            except Exception as e:
                logger.warning('File manifest restore failed: %s', e)
                self._warnings.append(f'File manifest restore failed: {e}')
        else:
            # No manifest — extract files directly to local dirs (legacy behavior)
            files_restored, files_failed = self._extract_files_to_local(archive)

        return RestoreReport(
            success=True,
            version_detected='3.0',
            tables_restored=list(self._tables_restored),
            tables_failed=[],
            files_restored=files_restored,
            files_skipped=files_skipped,
            files_failed=files_failed,
            warnings=list(self._warnings),
            rollback_performed=False,
        )

    def _restore_v2_legacy(self, db_snapshot: dict, archive: zipfile.ZipFile) -> RestoreReport:
        """Restore a v2.1 backup using legacy logic.

        Skips backup_config and module_enabled tables, restores remaining
        tables in FK order. For files, extracts directly to local dirs
        (no manifest).

        Args:
            db_snapshot: Parsed DB snapshot dict from db_backup.json.
            archive: Open ZipFile containing the backup.

        Returns:
            RestoreReport with detailed status.
        """
        ok, msg = self._legacy_restore(db_snapshot, archive)

        # Extract files directly to local dirs (no manifest in v2.1)
        files_restored, files_failed = self._extract_files_to_local(archive)

        if not ok:
            self._warnings.append(msg)

        return RestoreReport(
            success=ok,
            version_detected='2.1',
            tables_restored=list(self._tables_restored),
            tables_failed=[],
            files_restored=files_restored,
            files_skipped=[],
            files_failed=files_failed,
            warnings=list(self._warnings),
            rollback_performed=False,
        )

    def _legacy_restore(self, db_snapshot: dict, archive: zipfile.ZipFile) -> tuple[bool, str]:
        """Execute legacy v2.1 restore logic.

        Skips backup_config and module_enabled tables, restores remaining
        tables in FK dependency order. This mirrors the behavior of the
        original BackupModule._restore_db_from_json().

        Args:
            db_snapshot: Parsed DB snapshot dict.
            archive: Open ZipFile (used for context, not directly here).

        Returns:
            Tuple of (success: bool, message: str).
        """
        from sqlalchemy import text, inspect as sa_inspect

        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

        try:
            tables = db_snapshot.get('tables', db_snapshot)
            inspector = sa_inspect(self._db.engine)
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
                        if not _SAFE_NAME.match(tname):
                            continue
                        self._db.session.execute(text(f'DELETE FROM "{tname}"'))
                    except Exception as e:
                        logger.debug('Could not clear table %s: %s',
                                     _sanitize_for_log(tname), e)

            # Insert in forward order (parents first)
            date_fields = {'invoice_date', 'due_date', 'expense_date'}
            dt_fields = {'created_at', 'updated_at'}
            for tname in ordered:
                if tname not in tables:
                    continue
                if not _SAFE_NAME.match(tname):
                    continue
                cols = [c['name'] for c in inspector.get_columns(tname)]
                table_rows = tables[tname]
                # Handle both v2.1 format (list of dicts) and v3.0 format (dict with columns/rows)
                if isinstance(table_rows, dict) and 'rows' in table_rows:
                    _, table_rows = self._serializer.deserialize_table(table_rows)
                    table_rows = [{c.name if hasattr(c, 'name') else c: v
                                   for c, v in zip(cols, row.values())}
                                  if isinstance(row, dict) else row
                                  for row in table_rows]
                    # Re-extract as list of dicts from deserialized
                    columns, deserialized_rows = self._serializer.deserialize_table(tables[tname])
                    table_rows = deserialized_rows

                for rd in table_rows:
                    # Normalize date and datetime fields if present
                    for k, v in list(rd.items()):
                        if v and k in date_fields:
                            try:
                                rd[k] = datetime.fromisoformat(v).date() if isinstance(v, str) else v
                            except (ValueError, TypeError):
                                pass
                        elif v and k in dt_fields:
                            try:
                                rd[k] = datetime.fromisoformat(v) if isinstance(v, str) else v
                            except (ValueError, TypeError):
                                pass
                    # Only insert columns that exist in current schema
                    row_cols = [c for c in rd if c in cols and _SAFE_NAME.match(c)]
                    if not row_cols:
                        continue
                    placeholders = ', '.join(f':{c}' for c in row_cols)
                    col_names = ', '.join(f'"{c}"' for c in row_cols)
                    vals = {c: rd[c] for c in row_cols}
                    self._db.session.execute(
                        text(f'INSERT INTO "{tname}" ({col_names}) '
                             f'VALUES ({placeholders})'), vals)
                self._tables_restored.append(tname)

            self._db.session.commit()
            return True, 'OK'
        except Exception as e:
            self._db.session.rollback()
            return False, f'DB restore error: {e}'

    def _extract_files_to_local(self, archive: zipfile.ZipFile) -> tuple[list[str], list[str]]:
        """Extract files from archive directly to local directories.

        Used for archives without a file manifest (legacy behavior).
        Only extracts files whose top-level directory is in FILE_FOLDERS.

        Args:
            archive: Open ZipFile containing the backup.

        Returns:
            Tuple of (files_restored, files_failed) lists.
        """
        files_restored: list[str] = []
        files_failed: list[str] = []

        for name in archive.namelist():
            if name == 'db_backup.json' or name == 'file_manifest.json':
                continue
            parts = name.split('/')
            if parts[0] in FILE_FOLDERS:
                try:
                    target = self._app_root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
                    files_restored.append(name)
                except Exception as e:
                    logger.warning(
                        'Failed to extract file %s: %s',
                        _sanitize_for_log(name), e
                    )
                    files_failed.append(name)

        return files_restored, files_failed

    def _create_pre_restore_snapshot(self) -> bytes:
        """Snapshot current DB state for rollback.

        Uses SQLAlchemy inspector to discover all tables, then serializes
        each table using BackupSerializer in v3.0 format. Returns the
        snapshot as JSON bytes that can be used by _rollback().

        Returns:
            JSON bytes representing the current database state.
        """
        from sqlalchemy import text, inspect as sa_inspect

        inspector = sa_inspect(self._db.engine)
        all_tables = inspector.get_table_names()

        tables_data = {}
        for table_name in all_tables:
            try:
                # Get column metadata
                columns_info = inspector.get_columns(table_name)
                columns = [
                    ColumnMeta(name=col['name'], type=str(col['type']))
                    for col in columns_info
                ]

                # Get all rows
                result = self._db.session.execute(
                    text(f'SELECT * FROM "{table_name}"')
                )
                rows = []
                for row in result:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        row_dict[col.name] = row[i]
                    rows.append(row_dict)

                # Serialize using BackupSerializer
                tables_data[table_name] = self._serializer.serialize_table(
                    table_name, rows, columns
                )
            except Exception as e:
                logger.warning(
                    'Could not snapshot table %s: %s',
                    _sanitize_for_log(table_name), e
                )

        snapshot = {
            'version': '3.0',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'tables': tables_data,
        }

        return json.dumps(snapshot, ensure_ascii=False).encode('utf-8')

    def _rollback(self, snapshot: bytes) -> None:
        """Restore DB to pre-restore state from snapshot.

        Parses the JSON snapshot bytes, then for each table:
        1. DELETE all existing rows
        2. INSERT rows from the snapshot

        Foreign key constraints are disabled during rollback to avoid
        ordering issues (PRAGMA foreign_keys = OFF for SQLite).

        Args:
            snapshot: JSON bytes produced by _create_pre_restore_snapshot().
        """
        from sqlalchemy import text, inspect as sa_inspect

        snapshot_data = json.loads(snapshot.decode('utf-8'))
        tables = snapshot_data.get('tables', {})

        inspector = sa_inspect(self._db.engine)
        existing_tables = set(inspector.get_table_names())

        # Get topological order for safe insertion
        table_names = [t for t in tables.keys() if t in existing_tables]
        ordered_tables = self._sorter.sort(inspector, table_names)

        # Pre-fetch all column info to avoid inspector calls during transaction
        table_columns: dict[str, set[str]] = {}
        for table_name in ordered_tables:
            table_columns[table_name] = {
                c['name'] for c in inspector.get_columns(table_name)
            }

        try:
            # Disable FK constraints for SQLite
            self._db.session.execute(text('PRAGMA foreign_keys = OFF'))
            self._db.session.commit()

            # Delete all rows in reverse topological order (children first)
            for table_name in reversed(ordered_tables):
                try:
                    self._db.session.execute(text(f'DELETE FROM "{table_name}"'))
                except Exception as e:
                    logger.warning(
                        'Rollback: could not clear table %s: %s',
                        _sanitize_for_log(table_name), e
                    )

            # Insert rows in topological order (parents first)
            for table_name in ordered_tables:
                if table_name not in tables:
                    continue
                table_data = tables[table_name]
                columns, rows = self._serializer.deserialize_table(table_data)
                col_names_in_db = table_columns[table_name]

                for row in rows:
                    # Only insert columns that exist in current schema
                    row_cols = [c.name for c in columns if c.name in col_names_in_db and c.name in row]
                    if not row_cols:
                        continue
                    col_list = ', '.join(f'"{c}"' for c in row_cols)
                    placeholders = ', '.join(f':{c}' for c in row_cols)
                    vals = {c: row[c] for c in row_cols}
                    self._db.session.execute(
                        text(f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'),
                        vals
                    )

            self._db.session.commit()
        except Exception as e:
            self._db.session.rollback()
            logger.error('Rollback failed: %s', e)
            raise
        finally:
            # Re-enable FK constraints
            try:
                self._db.session.execute(text('PRAGMA foreign_keys = ON'))
                self._db.session.commit()
            except Exception:
                pass

    def _step1_restore_settings(self, data: dict) -> None:
        """Restore settings table first.

        Clears the settings table and inserts rows from the snapshot.
        If the settings table is not present in the snapshot, logs a warning and skips.

        Args:
            data: The DB snapshot dict containing 'tables' key.

        Raises:
            Exception: On any DB failure (caller should trigger rollback).
        """
        from sqlalchemy import text

        tables = data.get('tables', {})
        if 'settings' not in tables:
            msg = 'Settings table not in backup, skipping step 1'
            logger.warning(msg)
            self._warnings.append(msg)
            return

        table_data = tables['settings']
        self._db.session.execute(text('DELETE FROM "settings"'))

        columns, rows = self._serializer.deserialize_table(table_data)
        for row in rows:
            row_cols = [c.name for c in columns if c.name in row]
            if not row_cols:
                continue
            col_list = ', '.join(f'"{c}"' for c in row_cols)
            placeholders = ', '.join(f':{c}' for c in row_cols)
            vals = {c: row[c] for c in row_cols}
            self._db.session.execute(
                text(f'INSERT INTO "settings" ({col_list}) VALUES ({placeholders})'),
                vals
            )
        self._db.session.commit()
        self._tables_restored.append('settings')
        logger.info('Step 1: settings table restored')

    def _step2_restore_modules(self, data: dict) -> None:
        """Restore module_enabled table and activate modules.

        Clears the module_enabled table, inserts rows from the snapshot,
        then activates each enabled module via the module manager.
        If module_enabled is not in the snapshot, logs a warning and skips.
        Module activation failures are logged as warnings but do not abort.

        Args:
            data: The DB snapshot dict containing 'tables' key.

        Raises:
            Exception: On DB failure (caller should trigger rollback).
        """
        from sqlalchemy import text

        tables = data.get('tables', {})
        if 'module_enabled' not in tables:
            msg = 'module_enabled table not in backup, skipping step 2'
            logger.warning(msg)
            self._warnings.append(msg)
            return

        table_data = tables['module_enabled']
        self._db.session.execute(text('DELETE FROM "module_enabled"'))

        columns, rows = self._serializer.deserialize_table(table_data)
        for row in rows:
            row_cols = [c.name for c in columns if c.name in row]
            if not row_cols:
                continue
            col_list = ', '.join(f'"{c}"' for c in row_cols)
            placeholders = ', '.join(f':{c}' for c in row_cols)
            vals = {c: row[c] for c in row_cols}
            self._db.session.execute(
                text(f'INSERT INTO "module_enabled" ({col_list}) VALUES ({placeholders})'),
                vals
            )
        self._db.session.commit()
        self._tables_restored.append('module_enabled')

        # Activate each enabled module
        for row in rows:
            module_id = row.get('module_id')
            enabled = row.get('enabled')
            if module_id and enabled:
                try:
                    self._module_manager.activate_module(module_id)
                except Exception as e:
                    msg = f'Could not activate module {_sanitize_for_log(module_id)}: {e}'
                    logger.warning(msg)
                    self._warnings.append(msg)

        logger.info('Step 2: module_enabled table restored and modules activated')

    def _step3_restore_module_configs(self, data: dict) -> None:
        """Restore module-specific configuration tables.

        Gets the list of config tables from data['_module_configs'], then
        for each one that exists in data['tables'], clears and inserts rows.

        Args:
            data: The DB snapshot dict containing 'tables' and '_module_configs' keys.

        Raises:
            Exception: On DB failure (caller should trigger rollback).
        """
        from sqlalchemy import text

        module_configs = data.get('_module_configs', [])
        tables = data.get('tables', {})

        if not module_configs:
            logger.info('No module config tables listed, skipping step 3')
            return

        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

        for table_name in module_configs:
            if table_name not in tables:
                continue
            if not _SAFE_NAME.match(table_name):
                msg = f'Skipping module config table with unsafe name: {_sanitize_for_log(table_name)}'
                logger.warning(msg)
                self._warnings.append(msg)
                continue

            table_data = tables[table_name]
            self._db.session.execute(text(f'DELETE FROM "{table_name}"'))

            columns, rows = self._serializer.deserialize_table(table_data)
            for row in rows:
                row_cols = [c.name for c in columns if c.name in row]
                if not row_cols:
                    continue
                col_list = ', '.join(f'"{c}"' for c in row_cols)
                placeholders = ', '.join(f':{c}' for c in row_cols)
                vals = {c: row[c] for c in row_cols}
                self._db.session.execute(
                    text(f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'),
                    vals
                )
            self._db.session.commit()
            self._tables_restored.append(table_name)

        logger.info('Step 3: module config tables restored')

    def _step4_restore_tables(self, data: dict) -> None:
        """Restore remaining tables in topological order with FK constraints disabled.

        Determines remaining tables (not settings, not module_enabled, not in
        _module_configs), sorts them topologically, then clears and inserts rows
        with foreign key constraints disabled.

        Args:
            data: The DB snapshot dict containing 'tables' key.

        Raises:
            Exception: On DB failure (caller should trigger rollback).
        """
        from sqlalchemy import text, inspect as sa_inspect

        tables = data.get('tables', {})
        module_configs = set(data.get('_module_configs', []))
        skip_tables = {'settings', 'module_enabled'} | module_configs

        # Get remaining tables that are in the snapshot
        remaining = [t for t in tables.keys() if t not in skip_tables]

        if not remaining:
            logger.info('No remaining tables to restore in step 4')
            return

        _SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

        # Filter out unsafe table names
        remaining = [t for t in remaining if _SAFE_NAME.match(t)]

        # Sort in topological order using the inspector
        inspector = sa_inspect(self._db.engine)
        ordered_tables = self._sorter.sort(inspector, remaining)

        try:
            # Disable FK constraints
            self._db.session.execute(text('PRAGMA foreign_keys = OFF'))
            self._db.session.commit()

            for table_name in ordered_tables:
                if table_name not in tables:
                    continue

                table_data = tables[table_name]
                self._db.session.execute(text(f'DELETE FROM "{table_name}"'))

                columns, rows = self._serializer.deserialize_table(table_data)
                for row in rows:
                    row_cols = [c.name for c in columns if c.name in row]
                    if not row_cols:
                        continue
                    col_list = ', '.join(f'"{c}"' for c in row_cols)
                    placeholders = ', '.join(f':{c}' for c in row_cols)
                    vals = {c: row[c] for c in row_cols}
                    self._db.session.execute(
                        text(f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders})'),
                        vals
                    )
                self._db.session.commit()
                self._tables_restored.append(table_name)

            logger.info('Step 4: remaining tables restored in topological order')
        finally:
            # Re-enable FK constraints
            try:
                self._db.session.execute(text('PRAGMA foreign_keys = ON'))
                self._db.session.commit()
            except Exception:
                pass

    def _step5_restore_files(self, manifest: list, archive: zipfile.ZipFile) -> FileRestoreResult:
        """Restore files to target storage using hash-diff strategy.

        For each file entry in the manifest:
        1. Check if file already exists in target storage
        2. If it exists, compute SHA-256 and compare with manifest hash
        3. If hash matches → skip; if differs or file missing → write from archive
        4. On any error for a single file → log warning, continue with remaining

        Args:
            manifest: List of file manifest entries from file_manifest.json.
                Each entry is a dict with keys: key, sha256, size, backend.
            archive: Open ZipFile containing the backup files.

        Returns:
            FileRestoreResult with lists of restored, skipped, and failed files.
        """
        files_restored: list[str] = []
        files_skipped: list[str] = []
        files_failed: list[str] = []

        is_local = isinstance(self._storage_backend, LocalStorageBackend)

        for entry in manifest:
            file_key = entry.get('key', '')
            expected_hash = entry.get('sha256', '')

            try:
                # Step 1: Check if file already exists and get its content
                existing_bytes: bytes | None = None

                if is_local:
                    file_path = self._app_root / file_key
                    if file_path.exists():
                        existing_bytes = file_path.read_bytes()
                else:
                    result = self._storage_backend.get(file_key)
                    if result is not None:
                        existing_bytes = result[0]  # (file_bytes, filename)

                # Step 2: If file exists, compute hash and compare
                if existing_bytes is not None:
                    current_hash = FileManifestBuilder.compute_hash(existing_bytes)
                    if current_hash == expected_hash:
                        # Hash matches — skip this file
                        files_skipped.append(file_key)
                        continue

                # Step 3: File doesn't exist or hash differs — write from archive
                file_bytes = archive.read(file_key)

                if is_local:
                    target_path = self._app_root / file_key
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(file_bytes)
                else:
                    self._storage_backend.save(file_bytes, file_key)

                files_restored.append(file_key)

            except Exception as e:
                logger.warning(
                    'Failed to restore file %s: %s',
                    _sanitize_for_log(file_key), e
                )
                files_failed.append(file_key)
                continue

        return FileRestoreResult(
            files_restored=files_restored,
            files_skipped=files_skipped,
            files_failed=files_failed,
        )


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
                    password = session.get('_enc_token') or session.get('_password')
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
                password = session.get('_enc_token') or session.get('_password')
            ok, msg, report = module._restore_full_backup(filename, password)
            if ok:
                module.core.log_activity('backup_restored', 'backup', filename)
            if report:
                module._flash_restore_report(report)
            else:
                # No report available (early failure before pipeline ran)
                flash(msg, 'success' if ok else 'error')
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

            raw_bytes = f.read()

            # Try to handle as ZIP archive first (v3.0 full backup)
            if f.filename.endswith('.zip') or f.filename.endswith('.zip.enc'):
                password = None
                if f.filename.endswith('.enc'):
                    cfg = module._get_config()
                    if cfg.encrypt_method == 'custom' and cfg.custom_password:
                        password = cfg.custom_password
                    else:
                        password = session.get('_enc_token') or session.get('_password')
                pipeline = module._get_restore_pipeline()
                report = pipeline.restore(raw_bytes, password=password)
                if report.success:
                    module.core.log_activity('backup_restored', 'backup',
                                             f'ZIP upload: {f.filename}')
                module._flash_restore_report(report)
                return redirect(url_for('settings') + '#security')

            # Fall back to JSON restore (legacy v2.1 or raw JSON)
            try:
                jd = json.loads(raw_bytes.decode('utf-8'))
            except Exception:
                flash('Invalid file. Expected a JSON or ZIP backup file.', 'danger')
                return redirect(url_for('backup.upload_restore'))

            # Check if JSON is v3.0 format — use RestorePipeline logic
            version = jd.get('version')
            if version == '3.0':
                ok, msg = module._restore_v3_from_json(jd)
            else:
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

            # Check if demo data is v3.0 format — use RestorePipeline logic
            version = jd.get('version')
            if version == '3.0':
                ok, msg = module._restore_v3_from_json(jd)
            else:
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
                "var c=document.createElement('input');c.type='hidden';c.name='csrf_token';"
                "var m=document.querySelector('meta[name=csrf-token]');"
                "if(m)c.value=m.content;f.appendChild(c);"
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
            "f.appendChild(e);"
            "var c=document.createElement('input');c.type='hidden';c.name='csrf_token';"
            "var m=document.querySelector('meta[name=csrf-token]');"
            "if(m)c.value=m.content;f.appendChild(c);"
            "document.body.appendChild(f);f.submit();"
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
                "var c=document.createElement('input');c.type='hidden';c.name='csrf_token';"
                "var m=document.querySelector('meta[name=csrf-token]');"
                "if(m)c.value=m.content;f.appendChild(c);"
                "document.body.appendChild(f);f.submit();}"
            )
            a(f'<button type="button" class="btn btn-success"'
              f' style="{BS}" onclick="{js_r}">Restore</button>')
            js_d = (
                "if(confirm('Delete this backup?')){"
                "var f=document.createElement('form');"
                "f.method='POST';"
                "f.action='/backup/delete/" + fn + "';"
                "var c=document.createElement('input');c.type='hidden';c.name='csrf_token';"
                "var m=document.querySelector('meta[name=csrf-token]');"
                "if(m)c.value=m.content;f.appendChild(c);"
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

    # ── component initialization ───────────────────────────────────

    def _get_backup_creator(self):
        """Lazily initialize and return the BackupCreator instance."""
        return BackupCreator(
            db=self._db,
            storage_backend=self.core.storage,
            app_root=Path(self.core.app_path),
            backup_dir=self._backup_dir(),
            encrypt_func=self._encrypt_bytes,
            is_external_storage_enabled_func=self._is_external_storage_enabled,
        )

    def _get_restore_pipeline(self):
        """Lazily initialize and return the RestorePipeline instance."""
        return RestorePipeline(
            db=self._db,
            storage_backend=self.core.storage,
            module_manager=self.core.module_manager,
            app_root=Path(self.core.app_path),
            decrypt_func=self._decrypt_bytes,
        )

    def _restore_v3_from_json(self, json_data: dict) -> tuple:
        """Restore a v3.0 format JSON snapshot using the RestorePipeline.

        This handles the case where a user uploads a raw db_backup.json file
        (not wrapped in a ZIP archive). Creates a temporary in-memory ZIP
        containing the JSON and delegates to RestorePipeline.

        Args:
            json_data: Parsed v3.0 DB snapshot dict.

        Returns:
            Tuple of (success: bool, message: str).
        """
        try:
            # Wrap the JSON in a minimal ZIP archive for RestorePipeline
            buf = BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('db_backup.json',
                            json.dumps(json_data, indent=2, ensure_ascii=False))
            archive_bytes = buf.getvalue()

            pipeline = self._get_restore_pipeline()
            report = pipeline.restore(archive_bytes, password=None)

            if report.success:
                parts = ['Data restored successfully!']
                if report.tables_restored:
                    parts.append(f'{len(report.tables_restored)} tables restored.')
                if report.warnings:
                    parts.append(f'Warnings: {"; ".join(report.warnings[:3])}')
                parts.append('Please restart the application.')
                return True, ' '.join(parts)
            else:
                msg = 'Restore failed.'
                if report.rollback_performed:
                    msg += ' Database rolled back to previous state.'
                if report.warnings:
                    msg += f' {"; ".join(report.warnings[:3])}'
                return False, msg
        except Exception as e:
            return False, f'Restore error: {e}'

    @staticmethod
    def _format_restore_success(report: 'RestoreReport') -> str:
        """Format a successful RestoreReport into a user-friendly message."""
        parts = ['Backup restored!']
        if report.tables_restored:
            parts.append(f'{len(report.tables_restored)} tables restored.')
        if report.files_restored:
            parts.append(f'{len(report.files_restored)} files restored.')
        if report.files_skipped:
            parts.append(f'{len(report.files_skipped)} files unchanged (hash match).')
        if report.files_failed:
            parts.append(f'{len(report.files_failed)} files failed.')
        if report.warnings:
            parts.append(f'Warnings: {"; ".join(report.warnings[:3])}')
        parts.append('Please restart the application.')
        return ' '.join(parts)

    @staticmethod
    def _format_restore_failure(report: 'RestoreReport') -> str:
        """Format a failed RestoreReport into a user-friendly error message."""
        msg = 'Restore failed.'
        if report.rollback_performed:
            msg += ' Database rolled back to previous state.'
        if report.warnings:
            msg += f' {"; ".join(report.warnings[:3])}'
        return msg

    # ── core backup logic ───────────────────────────────────────────

    def _create_full_backup(self, password=None, prefix=''):
        """Create a full backup using the BackupCreator component."""
        creator = self._get_backup_creator()
        return creator.create_backup(password=password, prefix=prefix)

    def _flash_restore_report(self, report):
        """Flash detailed restore report messages to the UI.

        Uses multiple flash() calls with appropriate categories so the user
        sees a clear breakdown of what happened during restore.
        """
        if report.success:
            # Main success summary
            parts = ['Restore complete.']
            if report.tables_restored:
                parts.append(
                    f'{len(report.tables_restored)} tables restored,')
            if report.files_restored:
                parts.append(
                    f'{len(report.files_restored)} files restored,')
            if report.files_skipped:
                parts.append(
                    f'{len(report.files_skipped)} files skipped.')
            # Clean trailing comma if present
            summary = ' '.join(parts).rstrip(',') + '.'
            # Avoid double period
            summary = summary.replace('..', '.')
            flash(summary, 'success')

            # Warn about failed files
            if report.files_failed:
                flash(
                    f'{len(report.files_failed)} file(s) failed to restore.',
                    'warning')

            # Flash each warning individually
            if report.warnings:
                for warning in report.warnings:
                    flash(warning, 'warning')
        else:
            # Main error message
            flash('Restore failed.', 'error')

            # Inform about rollback
            if report.rollback_performed:
                flash(
                    'Database rolled back to previous state.', 'info')

            # Flash warnings
            if report.warnings:
                for warning in report.warnings:
                    flash(warning, 'warning')

    def _restore_full_backup(self, filename, password=None):
        """Restore a full backup using the RestorePipeline component."""
        try:
            file_path = self._get_backup_file_path(filename)
            if not file_path:
                return False, 'Backup file not found'
            raw = file_path.read_bytes()
            if filename.endswith('.enc'):
                if not password:
                    return False, 'Password required to decrypt this backup'

            # Create a pre-restore copy of the DB file (safety net)
            db_path = Path('instance/invoices.db')
            if db_path.exists():
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy2(db_path, f'instance/invoices.db.backup_{ts}')

            # Delegate to RestorePipeline
            pipeline = self._get_restore_pipeline()
            report = pipeline.restore(raw, password=password)

            if report.success:
                # Build success message with details
                parts = ['Backup restored!']
                if report.tables_restored:
                    parts.append(f'{len(report.tables_restored)} tables restored.')
                if report.files_restored:
                    parts.append(f'{len(report.files_restored)} files restored.')
                if report.files_skipped:
                    parts.append(f'{len(report.files_skipped)} files unchanged (hash match).')
                if report.files_failed:
                    parts.append(f'{len(report.files_failed)} files failed.')
                if report.warnings:
                    parts.append(f'Warnings: {"; ".join(report.warnings[:3])}')
                parts.append('Please restart the application.')
                return True, ' '.join(parts)
            else:
                # Build failure message
                msg = 'Restore failed.'
                if report.rollback_performed:
                    msg += ' Database rolled back to previous state.'
                if report.warnings:
                    msg += f' {"; ".join(report.warnings[:3])}'
                return False, msg
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
                    password = session.get('_enc_token') or session.get('_password')
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
