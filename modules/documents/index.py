#!/usr/bin/env python3
"""
Documents Module
Store and manage documents with categories, tags, file preview, and download.
Categories are auto-collected from documents + managed via a dedicated page.
"""

from module_manager import BaseModule
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash)
from datetime import datetime, date, timedelta
import json
import os
from db_helpers import db_get, db_get_or_404
from timeutil import utcnow

from . import ai_suggest, dublin_core, integrity, retention, text_index

# Background reading of PDF text. The scheduler ticks every minute; the job
# runs when the interval set in Settings → Documents has passed (stored in the
# database, so every worker process sees the same setting).
TEXT_JOB_TICK = 60             # seconds
TEXT_JOB_DEFAULTS = {'enabled': True, 'interval': 5, 'batch': 10}
TEXT_JOB_INTERVAL_RANGE = (1, 1440)   # minutes
TEXT_JOB_BATCH_RANGE = (1, 100)       # files per run
TEXT_JOB_BATCH = TEXT_JOB_DEFAULTS['batch']
TEXT_JOB_MAX_ATTEMPTS = 3      # a file that keeps failing is left alone
TEXT_JOB_BUDGET = 600          # seconds a scheduled run may read (job timeout 900)
READ_NOW_BUDGET = 60           # seconds for "Read now" (gunicorn timeout is 120)
# AI suggestions: off until the owner turns them on in Settings → Documents.
AI_MAX_CHARS_DEFAULT = 20_000
AI_MAX_CHARS_RANGE = (1_000, 100_000)
AI_UNANSWERED = 'The AI provider did not answer. Check the server log.'
AI_STALE_MINUTES = 15          # a request running longer was cut off
AI_POLL_SECONDS = 3            # how often the document page checks for the answer
TEXT_STATUS_LABELS = {
    'ok': 'Searchable',
    'no_text_layer': 'No text layer (a scan: needs OCR)',
    'encrypted': 'Encrypted: text not readable',
    'unreadable': 'Not a readable PDF',
    'too_large': 'Too large to read',
    'error': 'Could not be read',
}
# Document-level badge of how far its PDFs' text has been read (CSS class of
# the shared status badges; the text says it too, never the colour alone).
TEXT_BADGES = {
    'searchable': {'label': 'Text read', 'css': 'status-paid',
                   'title': 'The text of every PDF is read and searchable'},
    'partly': {'label': 'Partly read', 'css': 'status-warning',
               'title': 'Some PDFs are searchable; others are scans or cannot be read'},
    'waiting': {'label': 'Text pending', 'css': 'status-closed',
                'title': 'Waiting for the background job (Settings → Documents)'},
    'scan': {'label': 'Scan: needs OCR', 'css': 'status-warning',
             'title': 'No text layer: an OCR module is needed to read it'},
    'failed': {'label': 'Text not readable', 'css': 'status-failed',
               'title': 'Encrypted, broken, too large, or could not be fetched'},
}


def _create_text_index(target, connection, **kw):
    text_index.create(connection)


def _drop_text_index(target, connection, **kw):
    text_index.drop(connection)


class DocumentsModule(BaseModule):

    @property
    def module_id(self):
        return 'documents'

    @property
    def name(self):
        return 'Documents'

    @property
    def description(self):
        return 'Store and manage documents with categories, tags, preview, and download'

    @property
    def version(self):
        return '0.10.0'

    @property
    def nav_items(self):
        return [
            {'label': 'All Documents', 'endpoint': 'documents.documents_index', 'icon': '📁',
             'group': 'Documents'},
            {'label': 'Categories', 'endpoint': 'documents.categories_index', 'icon': '🏷️',
             'group': 'Documents'},
            {'label': 'Retention', 'endpoint': 'documents.retention_index', 'icon': '🗄️',
             'group': 'Documents'},
        ]

    def register_models(self, db):
        self._db = db

        class Document(db.Model):
            __tablename__ = 'document'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(300), nullable=False)
            source = db.Column(db.String(100))       # kept for backward compat, hidden from UI
            category = db.Column(db.String(100))
            document_date = db.Column(db.Date)
            expiry_date = db.Column(db.Date)
            amount = db.Column(db.Float)
            tags = db.Column(db.String(500))
            description = db.Column(db.Text)
            reference_number = db.Column(db.String(100))
            counterparty = db.Column(db.String(200))
            status = db.Column(db.String(20), default='active')  # active, archived, pending, expired
            file_path = db.Column(db.String(500), nullable=False)
            original_filename = db.Column(db.String(300))
            file_size = db.Column(db.Integer)
            file_format = db.Column(db.String(10))
            # Dublin Core (ISO 15836): see dublin_core.py for the mapping.
            identifier = db.Column(db.String(30), unique=True)   # DOC-2026-0001
            creator = db.Column(db.String(200))                  # who issued or wrote it
            language = db.Column(db.String(10))                  # ISO 639-1
            rights = db.Column(db.String(100))
            # Records (retention.py): declared as a record, kept until.
            record_declared_at = db.Column(db.DateTime)
            retain_until = db.Column(db.Date)
            created_at = db.Column(db.DateTime, default=utcnow)
            updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

        class DocumentCategory(db.Model):
            __tablename__ = 'document_category'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(100), unique=True, nullable=False)
            color = db.Column(db.String(7), default='#e2e3e5')
            retention_years = db.Column(db.Integer)   # None: no period set

        class DocumentFile(db.Model):
            __tablename__ = 'document_file'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
            file_path = db.Column(db.String(500), nullable=False)
            original_filename = db.Column(db.String(300))
            file_size = db.Column(db.Integer)
            file_format = db.Column(db.String(10))
            # Integrity (integrity.py): fingerprint of the current content,
            # its version number, and the PDF/A level the PDF declares.
            sha256 = db.Column(db.String(64))
            version = db.Column(db.Integer, default=1)
            pdfa = db.Column(db.String(12))
            created_at = db.Column(db.DateTime, default=utcnow)

        class DocumentFileVersion(db.Model):
            """An earlier content of a file, kept when it was replaced or signed."""
            __tablename__ = 'document_file_version'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            file_id = db.Column(db.Integer, db.ForeignKey('document_file.id'), nullable=False)
            version = db.Column(db.Integer, nullable=False)
            file_path = db.Column(db.String(500), nullable=False)
            original_filename = db.Column(db.String(300))
            file_size = db.Column(db.Integer)
            sha256 = db.Column(db.String(64))
            reason = db.Column(db.String(30))        # replaced, signed
            created_at = db.Column(db.DateTime, default=utcnow)

        class DocumentDisposal(db.Model):
            """A deleted or disposed document, kept after it is gone (ISO 15489)."""
            __tablename__ = 'document_disposal'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            identifier = db.Column(db.String(30))
            name = db.Column(db.String(300))
            category = db.Column(db.String(100))
            document_date = db.Column(db.Date)
            was_record = db.Column(db.Boolean, default=False)
            retain_until = db.Column(db.Date)
            action = db.Column(db.String(20), nullable=False)    # deleted, disposed
            reason = db.Column(db.String(300))
            files = db.Column(db.Text)       # "name (SHA-256)" per file, one per line
            created_at = db.Column(db.DateTime, default=utcnow)

        class DocumentConfig(db.Model):
            __tablename__ = 'document_config'
            __table_args__ = {'extend_existing': True}
            key = db.Column(db.String(50), primary_key=True)
            value = db.Column(db.String(200))

        class DocumentHistory(db.Model):
            __tablename__ = 'document_history'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
            action = db.Column(db.String(50), nullable=False)  # created, updated, file_added, file_removed, status_changed
            details = db.Column(db.Text)
            created_at = db.Column(db.DateTime, default=utcnow)

        class DocumentNote(db.Model):
            __tablename__ = 'document_note'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
            title = db.Column(db.String(200), nullable=False)
            text = db.Column(db.Text, nullable=False)
            created_at = db.Column(db.DateTime, default=utcnow)
            updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

        class DocumentAISuggestion(db.Model):
            """What an AI provider proposed for a document; the owner applies it."""
            __tablename__ = 'document_ai_suggestion'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
            provider = db.Column(db.String(100))      # name shown to the owner
            model = db.Column(db.String(100))
            file_ids = db.Column(db.String(500))      # files whose text was sent
            chars_sent = db.Column(db.Integer)
            fields = db.Column(db.Text)               # JSON {field: value}, checked
            # running (asked, no answer yet), new, applied, discarded, failed, empty
            status = db.Column(db.String(12), default='new')
            error = db.Column(db.String(300))         # why failed / empty
            created_at = db.Column(db.DateTime, default=utcnow)
            decided_at = db.Column(db.DateTime)

        class DocumentText(db.Model):
            """Text read from one stored PDF (pdf.extract_text), for search."""
            __tablename__ = 'document_text'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            storage_key = db.Column(db.String(500), unique=True, nullable=False)
            sha256 = db.Column(db.String(64))
            status = db.Column(db.String(20), nullable=False)  # pdf_text statuses, or error
            method = db.Column(db.String(30))
            page_count = db.Column(db.Integer)
            empty_pages = db.Column(db.Integer)
            text = db.Column(db.Text)
            attempts = db.Column(db.Integer, default=1)
            extracted_at = db.Column(db.DateTime, default=utcnow)

        # The FTS5 index follows the table in create_all / drop_all.
        from sqlalchemy import event
        table = DocumentText.__table__
        if not event.contains(table, 'after_create', _create_text_index):
            event.listen(table, 'after_create', _create_text_index)
            event.listen(table, 'before_drop', _drop_text_index)

        self.Document = Document
        self.DocumentText = DocumentText
        self.DocumentAISuggestion = DocumentAISuggestion
        self.DocumentCategory = DocumentCategory
        self.DocumentFile = DocumentFile
        self.DocumentFileVersion = DocumentFileVersion
        self.DocumentConfig = DocumentConfig
        self.DocumentDisposal = DocumentDisposal
        self.DocumentHistory = DocumentHistory
        self.DocumentNote = DocumentNote
        return {'DocumentCategory': DocumentCategory, 'DocumentFile': DocumentFile,
                'DocumentText': DocumentText, 'DocumentAISuggestion': DocumentAISuggestion,
                'DocumentFileVersion': DocumentFileVersion, 'DocumentDisposal': DocumentDisposal,
                'DocumentConfig': DocumentConfig, 'DocumentHistory': DocumentHistory,
                'DocumentNote': DocumentNote}

    def on_enable(self):
        """Migrate: add new columns if missing + seed default categories + migrate files."""
        try:
            from sqlalchemy import inspect as sa_inspect, text
            inspector = sa_inspect(self._db.engine)

            if 'document' in inspector.get_table_names():
                cols = [c['name'] for c in inspector.get_columns('document')]
                migrations = [
                    ('category', "VARCHAR(100)"),
                    ('expiry_date', "DATE"),
                    ('amount', "FLOAT"),
                    ('tags', "VARCHAR(500)"),
                    ('file_size', "INTEGER"),
                    ('file_format', "VARCHAR(10)"),
                    ('reference_number', "VARCHAR(100)"),
                    ('counterparty', "VARCHAR(200)"),
                    ('status', "VARCHAR(20) DEFAULT 'active'"),
                    ('updated_at', "DATETIME"),
                    ('identifier', "VARCHAR(30)"),
                    ('creator', "VARCHAR(200)"),
                    ('language', "VARCHAR(10)"),
                    ('rights', "VARCHAR(100)"),
                    ('record_declared_at', "DATETIME"),
                    ('retain_until', "DATE"),
                ]
                for col, typedef in migrations:
                    if col not in cols:
                        with self._db.engine.connect() as conn:
                            conn.execute(text(
                                f'ALTER TABLE document ADD COLUMN {col} {typedef}'))
                            conn.commit()
                with self._db.engine.connect() as conn:
                    conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS '
                                      'ix_document_identifier ON document (identifier)'))
                    conn.commit()
                self._backfill_identifiers()

            # Create document_file table if not exists
            if 'document_file' not in inspector.get_table_names():
                self.DocumentFile.__table__.create(self._db.engine)
            else:
                file_cols = [c['name'] for c in inspector.get_columns('document_file')]
                for col, typedef in (('sha256', 'VARCHAR(64)'), ('version', 'INTEGER DEFAULT 1'),
                                     ('pdfa', 'VARCHAR(12)')):
                    if col not in file_cols:
                        with self._db.engine.connect() as conn:
                            conn.execute(text(
                                f'ALTER TABLE document_file ADD COLUMN {col} {typedef}'))
                            conn.commit()
            if 'document_file_version' not in inspector.get_table_names():
                self.DocumentFileVersion.__table__.create(self._db.engine)
            if 'document_disposal' not in inspector.get_table_names():
                self.DocumentDisposal.__table__.create(self._db.engine)
            if 'document_ai_suggestion' not in inspector.get_table_names():
                self.DocumentAISuggestion.__table__.create(self._db.engine)
            if 'document_text' not in inspector.get_table_names():
                self.DocumentText.__table__.create(self._db.engine)
            self._ensure_text_index()
            if 'document_category' in inspector.get_table_names():
                cat_cols = [c['name'] for c in inspector.get_columns('document_category')]
                if 'retention_years' not in cat_cols:
                    with self._db.engine.connect() as conn:
                        conn.execute(text('ALTER TABLE document_category '
                                          'ADD COLUMN retention_years INTEGER'))
                        conn.commit()

            # Create document_history table if not exists
            if 'document_history' not in inspector.get_table_names():
                self.DocumentHistory.__table__.create(self._db.engine)

            # Migrate existing file_path from Document to DocumentFile
            self._migrate_files_to_multi()

            if self.DocumentCategory.query.count() == 0:
                defaults = [
                    ('Tax', '#fff3cd'),
                    ('Insurance', '#d4edda'),
                    ('Bank', '#d1ecf1'),
                    ('Contract', '#e2d5f1'),
                    ('Certificate', '#fde2e4'),
                    ('Utilities', '#d6e9f8'),
                    ('Social Security', '#d4edda'),
                    ('Other', '#e2e3e5'),
                ]
                for name, color in defaults:
                    self._db.session.add(self.DocumentCategory(name=name, color=color))
                self._db.session.commit()
        except Exception as e:
            self.logger.debug('document migration: %s', e)
        try:
            self.core.scheduler.add_job(
                job_id='documents.expiry_messages', func=self._daily_messages,
                job_type='daily', time_str='07:30',
                description='Messages for documents about to expire or just expired, '
                            'and records past their retention date',
                timeout=120)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register the expiry-messages job: %s', e)
        try:
            self.core.scheduler.add_job(
                job_id='documents.read_text', func=self.scheduled_read_text,
                job_type='interval', interval=TEXT_JOB_TICK,
                description='Read the text of new PDF files for search '
                            '(how often and how many: Settings → Documents)',
                timeout=900)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register the read-text job: %s', e)

    def _backfill_identifiers(self):
        """Give every document without one an identifier, oldest first, in
        the year it was added (once; new documents get one when created)."""
        docs = self.Document.query.filter(self.Document.identifier.is_(None)).order_by(
            self.Document.created_at, self.Document.id).all()
        for doc in docs:
            doc.identifier = self._new_identifier(doc.created_at)
            self._db.session.flush()
        if docs:
            self._db.session.commit()

    def _new_identifier(self, when=None):
        """The next free identifier of the year of `when` (default: now)."""
        year = (when or datetime.now()).year
        prefix = f'{dublin_core.IDENTIFIER_PREFIX}-{year}-'
        existing = [i for (i,) in self._db.session.query(self.Document.identifier).filter(
            self.Document.identifier.like(prefix + '%')).all()]
        return dublin_core.next_identifier(year, existing)

    def _migrate_files_to_multi(self):
        """Move file_path from Document rows into DocumentFile table (one-time migration)."""
        docs = self.Document.query.filter(
            self.Document.file_path.isnot(None),
            self.Document.file_path != ''
        ).all()
        for doc in docs:
            existing = self.DocumentFile.query.filter_by(
                document_id=doc.id).first()
            if existing:
                continue
            df = self.DocumentFile(
                document_id=doc.id,
                file_path=doc.file_path,
                original_filename=doc.original_filename,
                file_size=doc.file_size,
                file_format=doc.file_format,
            )
            self._db.session.add(df)
        self._db.session.commit()

    def register_routes(self, app):
        bp = Blueprint('documents', __name__,
                       template_folder='templates', url_prefix='/documents')
        login_required = self.core.login_required
        module = self

        @bp.route('/')
        @login_required
        def documents_index():
            return module._list_documents()

        @bp.route('/create', methods=['GET', 'POST'])
        @login_required
        def documents_create():
            return module._create_document()

        @bp.route('/edit/<int:id>', methods=['GET', 'POST'])
        @login_required
        def documents_edit(id):
            return module._edit_document(id)

        @bp.route('/view/<int:id>')
        @login_required
        def documents_view(id):
            return module._view_document(id)

        @bp.route('/view/<int:id>/dublin-core.xml')
        @login_required
        def documents_dublin_core(id):
            return module._dublin_core_xml(id)

        @bp.route('/delete/<int:id>', methods=['POST'])
        @login_required
        def documents_delete(id):
            return module._delete_document(id)

        @bp.route('/download/<int:id>')
        @login_required
        def documents_download(id):
            return module._download_document(id)

        @bp.route('/preview/<int:id>')
        @login_required
        def documents_preview(id):
            return module._preview_document(id)

        @bp.route('/file/<int:file_id>/download')
        @login_required
        def file_download(file_id):
            return module._download_file(file_id)

        @bp.route('/file/<int:file_id>/preview')
        @login_required
        def file_preview(file_id):
            return module._preview_file(file_id)

        @bp.route('/file/<int:file_id>/replace', methods=['POST'])
        @login_required
        def file_replace(file_id):
            return module._replace_file(file_id)

        @bp.route('/file-version/<int:version_id>/download')
        @login_required
        def file_version_download(version_id):
            return module._download_version(version_id)

        @bp.route('/view/<int:id>/declare', methods=['POST'])
        @login_required
        def documents_declare(id):
            return module._declare_record(id)

        @bp.route('/view/<int:id>/undeclare', methods=['POST'])
        @login_required
        def documents_undeclare(id):
            return module._undeclare_record(id)

        @bp.route('/retention')
        @login_required
        def retention_index():
            return module._retention_index()

        @bp.route('/view/<int:id>/ai-suggest', methods=['POST'])
        @login_required
        def documents_ai_suggest(id):
            return module._ai_suggest(id)

        @bp.route('/view/<int:id>/ai-apply', methods=['POST'])
        @login_required
        def documents_ai_apply(id):
            return module._ai_apply(id)

        @bp.route('/view/<int:id>/ai-status')
        @login_required
        def documents_ai_status(id):
            return module._ai_status(id)

        @bp.route('/view/<int:id>/ai-discard', methods=['POST'])
        @login_required
        def documents_ai_discard(id):
            return module._ai_discard(id)

        @bp.route('/text/read-now', methods=['POST'])
        @login_required
        def text_read_now():
            return module._read_text_now()

        @bp.route('/retention/<int:id>/keep', methods=['POST'])
        @login_required
        def retention_keep(id):
            return module._retention_keep(id)

        @bp.route('/retention/<int:id>/dispose', methods=['POST'])
        @login_required
        def retention_dispose(id):
            return module._retention_dispose(id)

        @bp.route('/categories/<int:id>/retention', methods=['POST'])
        @login_required
        def categories_update_retention(id):
            return module._update_category_retention(id)

        @bp.route('/view/<int:id>/verify', methods=['POST'])
        @login_required
        def documents_verify(id):
            return module._verify_document(id)

        @bp.route('/file/<int:file_id>/delete', methods=['POST'])
        @login_required
        def file_delete(file_id):
            return module._delete_file(file_id)

        @bp.route('/file/<int:file_id>/sign/<cap_index>', methods=['POST'])
        @login_required
        def file_sign(file_id, cap_index):
            return module._sign_file(file_id, int(cap_index))

        @bp.route('/categories')
        @login_required
        def categories_index():
            return module._categories_index()

        @bp.route('/categories/add', methods=['POST'])
        @login_required
        def categories_add():
            return module._add_category()

        @bp.route('/categories/<int:id>/delete', methods=['POST'])
        @login_required
        def categories_delete(id):
            return module._delete_category(id)

        @bp.route('/categories/<int:id>/color', methods=['POST'])
        @login_required
        def categories_update_color(id):
            return module._update_category_color(id)

        @bp.route('/categories/toggle-row-color', methods=['POST'])
        @login_required
        def categories_toggle_row_color():
            return module._toggle_row_color()

        @bp.route('/duplicate/<int:id>', methods=['POST'])
        @login_required
        def documents_duplicate(id):
            return module._duplicate_document(id)

        @bp.route('/bulk', methods=['POST'])
        @login_required
        def documents_bulk():
            return module._bulk_action()

        @bp.route('/note/add/<int:doc_id>', methods=['POST'])
        @login_required
        def note_add(doc_id):
            return module._add_note(doc_id)

        @bp.route('/note/edit/<int:note_id>', methods=['POST'])
        @login_required
        def note_edit(note_id):
            return module._edit_note(note_id)

        @bp.route('/note/delete/<int:note_id>', methods=['POST'])
        @login_required
        def note_delete(note_id):
            return module._delete_note(note_id)

        app.register_blueprint(bp)

    # --- REST API (served under /api/v1/m/documents/... ) ---

    def get_api_routes(self):
        """Expose document metadata (read-only)."""
        return [
            {'path': 'documents', 'methods': ['GET'],
             'handler': self._api_documents,
             'summary': 'List documents (filters: category, status, page, per_page)'},
            {'path': 'documents/<int:doc_id>', 'methods': ['GET'],
             'handler': self._api_get_document, 'summary': 'Get one document'},
            {'path': 'documents/<int:doc_id>/dublin-core', 'methods': ['GET'],
             'handler': self._api_dublin_core,
             'summary': 'Dublin Core (ISO 15836) metadata of one document'},
        ]

    def _api_serialize_document(self, d):
        return {
            'id': d.id,
            'identifier': d.identifier,
            'name': d.name,
            'category': d.category,
            'status': d.status,
            'tags': d.tags,
            'document_date': d.document_date.isoformat() if d.document_date else None,
            'expiry_date': d.expiry_date.isoformat() if d.expiry_date else None,
            'amount': d.amount,
            'reference_number': d.reference_number,
            'counterparty': d.counterparty,
            'creator': d.creator,
            'language': d.language,
            'rights': d.rights,
            'original_filename': d.original_filename,
        }

    def _api_documents(self, request):
        from api_errors import ApiError
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = min(100, max(1, int(request.args.get('per_page', 20))))
        except ValueError:
            raise ApiError(400, 'bad_request', 'page/per_page must be integers')
        query = self.Document.query
        category = request.args.get('category')
        if category:
            query = query.filter(self.Document.category == category)
        status = request.args.get('status')
        if status:
            query = query.filter(self.Document.status == status)
        query = query.order_by(self.Document.document_date.desc(),
                               self.Document.id.desc())
        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()
        return {
            'data': [self._api_serialize_document(d) for d in rows],
            'page': page, 'per_page': per_page, 'total': total,
        }, 200

    def _api_get_document(self, request, doc_id=None):
        from api_errors import ApiError
        doc = db_get(self.Document, doc_id)
        if not doc:
            raise ApiError(404, 'not_found', f'Document #{doc_id} not found')
        return self._api_serialize_document(doc), 200

    def _api_dublin_core(self, request, doc_id=None):
        from api_errors import ApiError
        doc = db_get(self.Document, doc_id)
        if not doc:
            raise ApiError(404, 'not_found', f'Document #{doc_id} not found')
        return dublin_core.as_dict(self._dc_record(doc)), 200

    # ---- Dublin Core (ISO 15836) ----

    def _dc_record(self, doc):
        formats = [f.file_format for f in self.DocumentFile.query.filter_by(
            document_id=doc.id).order_by(self.DocumentFile.created_at).all()]
        return dublin_core.record(doc, formats)

    def _dublin_core_xml(self, id):
        from flask import Response
        doc = db_get_or_404(self.Document, id)
        xml = dublin_core.to_xml(self._dc_record(doc))
        name = (doc.identifier or f'document-{doc.id}') + '-dc.xml'
        return Response(xml, mimetype='application/xml',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})

    def _metadata_from_form(self, form):
        """creator, language, rights from the form (language: ISO 639-1 or none)."""
        rights = form.get('rights', '').strip()
        return {'creator': form.get('creator', '').strip()[:200] or None,
                'language': dublin_core.clean_language(form.get('language')),
                'rights': rights[:100] or None}

    # ---- helpers ----

    def _get_config(self, key, default=''):
        row = db_get(self.DocumentConfig, key)
        return row.value if row else default

    def _set_config(self, key, value):
        row = db_get(self.DocumentConfig, key)
        if row:
            row.value = value
        else:
            self._db.session.add(self.DocumentConfig(key=key, value=value))
        self._db.session.commit()

    def _get_all_categories(self):
        """Merge managed categories + distinct categories from documents."""
        managed = {c.name for c in self.DocumentCategory.query.all()}
        doc_cats = self._db.session.query(self.Document.category).distinct().all()
        for (cat,) in doc_cats:
            if cat:
                managed.add(cat)
        return sorted(managed)

    def _get_category_colors(self):
        """Return dict name->color from DocumentCategory table."""
        return {c.name: c.color for c in self.DocumentCategory.query.all()}

    def _get_all_tags(self):
        """Collect all unique tags from documents, stripped of # prefix."""
        tags = set()
        for (raw,) in self._db.session.query(self.Document.tags).filter(
                self.Document.tags.isnot(None)).all():
            for t in raw.split(','):
                t = t.strip().lstrip('#')
                if t:
                    tags.add(t)
        return sorted(tags)

    @staticmethod
    def _clean_tags(raw):
        """Strip # prefix and whitespace from comma-separated tags."""
        if not raw:
            return None
        cleaned = ','.join(t.strip().lstrip('#') for t in raw.split(',') if t.strip())
        return cleaned or None

    def _get_file_meta(self, file):
        """Extract file size and format from uploaded file."""
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        ext = ''
        if file.filename:
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        return size, ext

    def _auto_add_category(self, name):
        """Auto-add category to DocumentCategory if it doesn't exist."""
        if not name:
            return
        existing = self.DocumentCategory.query.filter_by(name=name).first()
        if not existing:
            self._db.session.add(self.DocumentCategory(name=name))
            self._db.session.commit()

    def _log_history(self, doc_id, action, details=''):
        """Record a history entry for a document."""
        self._db.session.add(self.DocumentHistory(
            document_id=doc_id, action=action, details=details))

    def _track_changes(self, doc, form):
        """Compare form data with current doc and log changes."""
        changes = []
        field_map = {
            'name': 'Name', 'category': 'Category', 'tags': 'Tags',
            'description': 'Description', 'reference_number': 'Reference',
            'counterparty': 'Counterparty', 'status': 'Status',
            'creator': 'Creator', 'language': 'Language', 'rights': 'Rights',
        }
        for field, label in field_map.items():
            old = getattr(doc, field, '') or ''
            new = form.get(field, '').strip()
            if old != new:
                changes.append(f'{label}: "{old}" → "{new}"')
        # Date fields
        for field, label in [('document_date', 'Date'), ('expiry_date', 'Expiry')]:
            old = getattr(doc, field)
            old_str = old.strftime('%Y-%m-%d') if old else ''
            new_str = form.get(field, '').strip()
            if old_str != new_str:
                changes.append(f'{label}: {old_str or "—"} → {new_str or "—"}')
        # Amount
        old_amt = f'{doc.amount:.2f}' if doc.amount else ''
        new_amt = form.get('amount', '').strip()
        if old_amt != new_amt:
            changes.append(f'Amount: {old_amt or "—"} → {new_amt or "—"}')
        return changes

    # ---- document CRUD ----

    def _view_document(self, id):
        doc = db_get_or_404(self.Document, id)
        doc_files = self.DocumentFile.query.filter_by(
            document_id=doc.id).order_by(self.DocumentFile.created_at).all()
        versions = {}
        if doc_files:
            for v in self.DocumentFileVersion.query.filter(
                    self.DocumentFileVersion.file_id.in_([f.id for f in doc_files])).order_by(
                    self.DocumentFileVersion.version.desc()).all():
                versions.setdefault(v.file_id, []).append(v)
        history = self.DocumentHistory.query.filter_by(
            document_id=doc.id).order_by(self.DocumentHistory.created_at.desc()).all()
        notes = self.DocumentNote.query.filter_by(
            document_id=doc.id).order_by(self.DocumentNote.created_at.desc()).all()
        category_colors = self._get_category_colors()
        file_states = self._file_text_states(doc_files)
        return render_template('document_view.html', doc=doc, doc_files=doc_files,
                               versions=versions, short=integrity.short,
                               text_states=file_states,
                               ai=self._ai_panel(doc, doc_files),
                               text_badge=self._document_text_state(list(file_states.values())),
                               is_record=self._is_record(doc),
                               retention_due=retention.is_due(doc),
                               category_retention=self._category_retention(doc.category),
                               history=history, notes=notes,
                               category_colors=category_colors,
                               today=date.today())

    def _list_documents(self):
        query = self.Document.query

        q = request.args.get('q', '').strip()
        text_hits = {}
        if q:
            like = f'%{q}%'
            match = (self.Document.name.ilike(like) |
                     self.Document.description.ilike(like) |
                     self.Document.tags.ilike(like) |
                     self.Document.identifier.ilike(like) |
                     self.Document.reference_number.ilike(like))
            text_hits = self._text_matches(q)    # words in the files' text
            if text_hits:
                match = match | self.Document.id.in_(list(text_hits))
            query = query.filter(match)

        cat = request.args.get('category', '').strip()
        if cat:
            query = query.filter(self.Document.category == cat)

        year = request.args.get('year', type=int)
        if year:
            query = query.filter(
                self._db.func.strftime('%Y', self.Document.document_date) == str(year))

        tag = request.args.get('tag', '').strip()
        if tag:
            query = query.filter(self.Document.tags.ilike(f'%{tag}%'))

        # Sorting
        sort_by = request.args.get('sort', 'document_date')
        sort_dir = request.args.get('dir', 'desc')
        sort_col = getattr(self.Document, sort_by, self.Document.document_date)
        query = query.order_by(sort_col.asc() if sort_dir == 'asc' else sort_col.desc())

        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = 50
        total = query.count()
        documents = query.offset((page - 1) * per_page).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        # Load attached files for each document
        doc_ids = [d.id for d in documents]
        files_by_doc = {}
        if doc_ids:
            all_files = self.DocumentFile.query.filter(
                self.DocumentFile.document_id.in_(doc_ids)
            ).order_by(self.DocumentFile.created_at).all()
            for f in all_files:
                files_by_doc.setdefault(f.document_id, []).append(f)

        categories = self._get_all_categories()
        category_colors = self._get_category_colors()

        all_tags = self._get_all_tags()
        years = set()
        for doc in self.Document.query.all():
            if doc.document_date:
                years.add(doc.document_date.year)

        return render_template('documents.html',
                               documents=documents,
                               files_by_doc=files_by_doc,
                               categories=categories,
                               category_colors=category_colors,
                               fill_row_color=(self._get_config('fill_row_color', '0') == '1'),
                               all_tags=all_tags,
                               years=sorted(years, reverse=True),
                               sort_by=sort_by, sort_dir=sort_dir,
                               page=page, total_pages=total_pages, total=total,
                               text_hits=text_hits,
                               text_badges=self._documents_text_states(files_by_doc),
                               today=date.today())

    def _create_document(self):
        if request.method == 'POST':
            try:
                files = request.files.getlist('files')
                has_files = any(f and f.filename for f in files)
                if not has_files:
                    flash('At least one file is required.', 'danger')
                    return redirect(url_for('documents.documents_create'))

                doc_date = None
                if request.form.get('document_date'):
                    doc_date = datetime.strptime(
                        request.form['document_date'], '%Y-%m-%d').date()

                expiry = None
                if request.form.get('expiry_date'):
                    expiry = datetime.strptime(
                        request.form['expiry_date'], '%Y-%m-%d').date()

                amount = None
                if request.form.get('amount'):
                    amount = float(request.form['amount'])

                category = request.form.get('category', '').strip() or None
                self._auto_add_category(category)

                # Use first file info for backward-compat columns on Document
                first = next(f for f in files if f and f.filename)
                first_size, first_fmt = self._get_file_meta(first)

                doc = self.Document(
                    name=request.form['name'],
                    category=category,
                    document_date=doc_date,
                    expiry_date=expiry,
                    amount=amount,
                    tags=self._clean_tags(request.form.get('tags', '')),
                    description=request.form.get('description', '').strip() or None,
                    reference_number=request.form.get('reference_number', '').strip() or None,
                    counterparty=request.form.get('counterparty', '').strip() or None,
                    status=request.form.get('status', 'active').strip(),
                    identifier=self._new_identifier(),
                    **self._metadata_from_form(request.form),
                    file_path='_multi_',
                    original_filename=first.filename,
                    file_size=first_size,
                    file_format=first_fmt,
                )
                self._db.session.add(doc)
                self._db.session.flush()  # get doc.id

                for f in files:
                    if not f or not f.filename:
                        continue
                    self._save_document_file(doc.id, f)

                self._log_history(doc.id, 'created',
                                  f'Document "{doc.name}" created as {doc.identifier}')
                self._db.session.commit()

                # Auto-sign PDF files if requested
                self._auto_sign_files(doc.id, request.form)

                # Save initial note if provided
                note_title = request.form.get('initial_note_title', '').strip()
                note_text = request.form.get('initial_note_text', '').strip()
                if note_title and note_text:
                    self._db.session.add(self.DocumentNote(
                        document_id=doc.id, title=note_title, text=note_text))
                    self._log_history(doc.id, 'updated', f'Note added: {note_title}')
                    self._db.session.commit()

                flash('Document added.', 'success')
                return redirect(url_for('documents.documents_index'))
            except Exception as e:
                self._db.session.rollback()
                self.logger.error('Error creating document: %s', e)
                flash('Error creating record. Please try again.', 'danger')

        categories = self._get_all_categories()
        return render_template('document_form.html', document=None,
                               categories=categories, doc_files=[],
                               all_tags=self._get_all_tags(),
                               languages=dublin_core.LANGUAGES, rights=dublin_core.RIGHTS)

    def _edit_document(self, id):
        doc = db_get_or_404(self.Document, id)
        refused = self._refuse_if_record(doc)
        if refused:
            return refused
        if request.method == 'POST':
            try:
                # Track changes before applying
                changes = self._track_changes(doc, request.form)

                doc.name = request.form['name']
                doc.category = request.form.get('category', '').strip() or None
                self._auto_add_category(doc.category)

                if request.form.get('document_date'):
                    doc.document_date = datetime.strptime(
                        request.form['document_date'], '%Y-%m-%d').date()
                else:
                    doc.document_date = None

                if request.form.get('expiry_date'):
                    doc.expiry_date = datetime.strptime(
                        request.form['expiry_date'], '%Y-%m-%d').date()
                else:
                    doc.expiry_date = None

                doc.amount = float(request.form['amount']) if request.form.get('amount') else None
                doc.tags = self._clean_tags(request.form.get('tags', ''))
                doc.description = request.form.get('description', '').strip() or None
                doc.reference_number = request.form.get('reference_number', '').strip() or None
                doc.counterparty = request.form.get('counterparty', '').strip() or None
                doc.status = request.form.get('status', 'active').strip()
                for field, value in self._metadata_from_form(request.form).items():
                    setattr(doc, field, value)
                doc.updated_at = utcnow()

                # Add new files
                files = request.files.getlist('files')
                added_files = []
                for f in files:
                    if f and f.filename:
                        self._save_document_file(doc.id, f)
                        added_files.append(f.filename)

                if changes:
                    self._log_history(doc.id, 'updated', '; '.join(changes))
                if added_files:
                    self._log_history(doc.id, 'file_added', ', '.join(added_files))

                self._db.session.commit()

                # Auto-sign new PDF files if requested
                if added_files:
                    self._auto_sign_files(doc.id, request.form)

                # Save note if provided during edit
                note_title = request.form.get('initial_note_title', '').strip()
                note_text = request.form.get('initial_note_text', '').strip()
                if note_title and note_text:
                    self._db.session.add(self.DocumentNote(
                        document_id=doc.id, title=note_title, text=note_text))
                    self._log_history(doc.id, 'updated', f'Note added: {note_title}')
                    self._db.session.commit()

                flash('Document updated.', 'success')
                return redirect(url_for('documents.documents_view', id=doc.id))
            except Exception as e:
                self._db.session.rollback()
                self.logger.error('Error updating document: %s', e)
                flash('Error processing form data. Please check your input.', 'danger')

        categories = self._get_all_categories()
        doc_files = self.DocumentFile.query.filter_by(
            document_id=doc.id).order_by(self.DocumentFile.created_at).all()
        return render_template('document_form.html', document=doc,
                               categories=categories, doc_files=doc_files,
                               all_tags=self._get_all_tags(),
                               languages=dublin_core.LANGUAGES, rights=dublin_core.RIGHTS)

    def _storage_delete_quiet(self, storage_key):
        """Delete a stored file, tolerating missing/unresolvable keys.

        A file already gone from the backend (e.g. removed in Google Drive)
        must never block deleting the database record.
        """
        self._forget_text(storage_key)
        try:
            self.core.storage.delete(storage_key)
        except Exception as e:
            self.logger.warning('Storage delete failed for %s: %s',
                                storage_key, e)

    def _delete_document_children(self, doc):
        """Remove everything referencing the document, storage files included.

        document_file, document_history and document_note all carry a NOT NULL
        FK to document.id (and SQLite runs with foreign_keys=ON), so every
        child row must go before the document itself can be deleted.
        """
        for df in self.DocumentFile.query.filter_by(document_id=doc.id).all():
            self._delete_file_and_versions(df)
        if doc.file_path and doc.file_path != '_multi_':
            self._storage_delete_quiet(doc.file_path)
        self.DocumentHistory.query.filter_by(document_id=doc.id).delete(
            synchronize_session=False)
        self.DocumentNote.query.filter_by(document_id=doc.id).delete(
            synchronize_session=False)
        self.DocumentAISuggestion.query.filter_by(document_id=doc.id).delete(
            synchronize_session=False)

    def _delete_document(self, id):
        doc = db_get_or_404(self.Document, id)
        refused = self._refuse_if_record(doc)
        if refused:
            return refused
        try:
            self._log_disposal(doc, 'deleted')
            self._delete_document_children(doc)
            self._db.session.delete(doc)
            self._db.session.commit()
            flash('Document deleted.', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error deleting document: %s', e)
            flash('Error deleting record. Please try again.', 'danger')
        return redirect(url_for('documents.documents_index'))

    def _download_document(self, id):
        doc = db_get_or_404(self.Document, id)
        # Try first attached file
        df = self.DocumentFile.query.filter_by(document_id=doc.id).first()
        if df:
            return self._download_file(df.id)
        # Fallback to legacy file_path
        if not doc.file_path or not self.core.storage.exists(doc.file_path):
            flash(f'File not found: {doc.original_filename or doc.file_path}.', 'danger')
            return redirect(url_for('documents.documents_index'))
        return self.core.storage.send(doc.file_path,
                                      download_name=doc.original_filename)

    def _preview_document(self, id):
        doc = db_get_or_404(self.Document, id)
        # Try first attached file
        df = self.DocumentFile.query.filter_by(document_id=doc.id).first()
        if df:
            return self._preview_file(df.id)
        # Fallback to legacy file_path
        resp = self.core.preview_file(doc.file_path, doc.original_filename)
        if not resp:
            flash(f'File not found: {doc.original_filename or doc.file_path}. '
                  'It may have been deleted or not yet uploaded.', 'danger')
            return redirect(url_for('documents.documents_index'))
        return resp

    # ---- individual file operations ----

    def _save_document_file(self, document_id, file):
        """Save an uploaded file and create a DocumentFile record, with its
        SHA-256 fingerprint and the PDF/A level it declares."""
        file_size, file_format = self._get_file_meta(file)
        data = file.read()
        file.seek(0)
        storage_key = self.core.storage.save(file, self._storage_key(file.filename))
        df = self.DocumentFile(
            document_id=document_id,
            file_path=storage_key,
            original_filename=file.filename,
            file_size=file_size,
            file_format=file_format,
            sha256=integrity.sha256(data),
            version=1,
            pdfa=integrity.pdfa_claim(data) if file_format == 'pdf' else None,
        )
        self._db.session.add(df)
        return df

    @staticmethod
    def _storage_key(filename, version=None):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        safe_name = (filename or 'file').replace(' ', '_').replace('/', '_').replace('\\', '_')
        label = f'v{version}_' if version else ''
        return os.path.join('documents_files', f'{timestamp}_{label}{safe_name}')

    def _new_version(self, df, data, reason, filename=None):
        """Make `data` the current content of `df`; the earlier content is
        kept as a DocumentFileVersion (its stored file is not touched)."""
        self._db.session.add(self.DocumentFileVersion(
            file_id=df.id, version=df.version or 1, file_path=df.file_path,
            original_filename=df.original_filename, file_size=df.file_size,
            sha256=df.sha256, reason=reason))
        new_version = (df.version or 1) + 1
        if filename:
            df.original_filename = filename
            df.file_format = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        df.file_path = self.core.storage.save(
            data, self._storage_key(df.original_filename, new_version))
        df.file_size = len(data)
        df.sha256 = integrity.sha256(data)
        df.pdfa = integrity.pdfa_claim(data) if (df.file_format or '').lower() == 'pdf' else None
        df.version = new_version
        return new_version

    def _key_in_use(self, key):
        """Another file or version still points at this stored file (a
        duplicated document shares its files)."""
        return (self.DocumentFile.query.filter_by(file_path=key).count()
                + self.DocumentFileVersion.query.filter_by(file_path=key).count()) > 0

    # ---- text of stored PDFs, for search ----

    def _ensure_text_index(self):
        """Create the FTS5 index when it is missing (database from before it,
        or SQLite gained FTS5) and index the stored texts."""
        session = self._db.session
        if text_index.available(session):
            return
        with self._db.engine.begin() as conn:
            created = text_index.create(conn)
        if created:
            text_index.rebuild(session)
            session.commit()

    def _pending_query(self):
        """Stored PDFs whose text has not been read, or failed and may be
        tried again."""
        DF, DT = self.DocumentFile, self.DocumentText
        return (self._db.session.query(DF.file_path)
                .outerjoin(DT, DT.storage_key == DF.file_path)
                .filter((self._db.func.lower(DF.file_format) == 'pdf')
                        | DF.original_filename.ilike('%.pdf'))
                .filter((DT.id.is_(None))
                        | ((DT.status == 'error') & (DT.attempts < TEXT_JOB_MAX_ATTEMPTS))))

    def _pending_text_files(self, limit):
        """Storage keys of the next `limit` PDFs to read, oldest files first."""
        DF = self.DocumentFile
        query = self._pending_query().order_by(DF.created_at, DF.id)
        return list(dict.fromkeys(key for (key,) in query.limit(limit * 3).all()))[:limit]

    def read_text(self, storage_key):
        """Read one stored PDF through pdf.extract_text and keep the result.
        Returns the status, or None when nothing provides the function."""
        functions = getattr(self.core, 'functions', None)
        if functions is None or not functions.available('pdf.extract_text'):
            return None
        session = self._db.session
        row = self.DocumentText.query.filter_by(storage_key=storage_key).first()
        try:
            stored = self.core.storage.get(storage_key)
            if not stored:
                raise FileNotFoundError('stored file not found')
            data = stored[0]
            result = functions.call('pdf.extract_text', pdf_bytes=data)
        except Exception as e:
            # Storage (Google Drive) may be unreachable for a while: try later.
            self.logger.warning('Reading text of a document file failed: %s',
                                type(e).__name__)
            if row is None:
                row = self.DocumentText(storage_key=storage_key, attempts=0)
                session.add(row)
            row.status, row.attempts = 'error', (row.attempts or 0) + 1
            row.extracted_at = utcnow()
            session.commit()
            return 'error'
        if row is not None:
            if self._indexed():
                text_index.remove(session, row.id, row.text)
        else:
            row = self.DocumentText(storage_key=storage_key, attempts=0)
            session.add(row)
        row.status = result.get('status') or 'error'
        row.method = result.get('method')
        row.page_count = result.get('page_count')
        row.empty_pages = len(result.get('empty_pages') or [])
        row.text = result.get('text') or ''
        row.sha256 = integrity.sha256(data)
        row.attempts = (row.attempts or 0) + 1
        row.extracted_at = utcnow()
        session.flush()
        if self._indexed():
            text_index.add(session, row.id, row.text)
        session.commit()
        return row.status

    def read_pending_text(self, limit=TEXT_JOB_BATCH, budget=None):
        """Read the text of up to `limit` PDFs not read yet, stopping after
        `budget` seconds (None: no limit). Returns {storage key: status} for
        the files it read."""
        import time
        started = time.monotonic()
        done = {}
        for key in self._pending_text_files(limit):
            if budget is not None and time.monotonic() - started > budget:
                break
            status = self.read_text(key)
            if status is None:      # nothing provides pdf.extract_text
                break
            done[key] = status
        return done

    def text_job_config(self):
        """{enabled, interval (minutes), batch (files)} from Settings → Documents."""
        def number(key, default, bounds):
            try:
                value = int(self._get_config(key, default))
            except (TypeError, ValueError):
                return default
            return min(max(value, bounds[0]), bounds[1])
        d = TEXT_JOB_DEFAULTS
        return {'enabled': self._get_config('text_job_enabled', '1') == '1',
                'interval': number('text_job_interval', d['interval'], TEXT_JOB_INTERVAL_RANGE),
                'batch': number('text_job_batch', d['batch'], TEXT_JOB_BATCH_RANGE)}

    def _text_job_last_run(self):
        try:
            return datetime.fromisoformat(self._get_config('text_job_last_run', ''))
        except ValueError:
            return None

    def scheduled_read_text(self, now=None):
        """Scheduler tick: read a batch when the job is on and its interval
        has passed since the last run. Returns what read_pending_text did, or
        {} when it was not time."""
        cfg = self.text_job_config()
        if not cfg['enabled']:
            return {}
        now = now or datetime.now()
        last = self._text_job_last_run()
        if last and now - last < timedelta(minutes=cfg['interval']):
            return {}
        # Recorded first, so a run that fails is not repeated every minute.
        self._set_config('text_job_last_run', now.isoformat(timespec='seconds'))
        return self.read_pending_text(cfg['batch'], budget=TEXT_JOB_BUDGET)

    def _text_counts(self):
        """How far the stored PDFs have been read, for the settings panel."""
        DT = self.DocumentText
        by_status = dict(self._db.session.query(DT.status, self._db.func.count(DT.id))
                         .group_by(DT.status).all())
        return {'waiting': self._pending_query().distinct().count(),
                'searchable': by_status.get('ok', 0),
                'no_text_layer': by_status.get('no_text_layer', 0),
                'failed': sum(n for s, n in by_status.items()
                              if s not in ('ok', 'no_text_layer'))}

    def _read_text_now(self):
        """Read one batch now (Settings → Documents → Read now), within the
        time a web request may take."""
        done = self.read_pending_text(self.text_job_config()['batch'], budget=READ_NOW_BUDGET)
        if not done and not self._pending_text_files(1):
            flash('No PDF is waiting to be read.', 'info')
        elif not done:
            flash('Nothing provides pdf.extract_text: no PDF could be read.', 'warning')
        else:
            ok = sum(1 for s in done.values() if s == 'ok')
            flash(f'{len(done)} PDF(s) read, {ok} searchable.', 'success')
        self._set_config('text_job_last_run', datetime.now().isoformat(timespec='seconds'))
        return redirect(url_for('settings') + '#mod_documents')

    # ---- Settings → Documents ----

    @property
    def settings_tab(self):
        return {'id': 'documents', 'label': 'Documents'}

    def get_settings_html(self, settings):
        cfg = self.text_job_config()
        return self.render_settings_template(
            'documents_settings_panel.html', cfg=cfg, counts=self._text_counts(),
            last_run=self._text_job_last_run(),
            interval_range=TEXT_JOB_INTERVAL_RANGE, batch_range=TEXT_JOB_BATCH_RANGE,
            ai=self.ai_config(), ai_providers=self._ai_providers(),
            ai_available=self._ai_functions() is not None,
            ai_max_range=AI_MAX_CHARS_RANGE,
            categories=self._get_all_categories())

    def save_settings(self, settings, form):
        if 'documents_submitted' not in form:
            return  # guard: another tab's form
        values = {}
        for key, bounds, what in (('text_job_interval', TEXT_JOB_INTERVAL_RANGE, 'Text reading'),
                                  ('text_job_batch', TEXT_JOB_BATCH_RANGE, 'Text reading'),
                                  ('ai_max_chars', AI_MAX_CHARS_RANGE, 'AI suggestions')):
            if key == 'ai_max_chars' and key not in form:
                continue
            try:
                value = int(form.get(key, ''))
            except (TypeError, ValueError):
                self.core.flash(f'{what}: enter whole numbers; nothing was changed.',
                                'warning')
                return
            if not bounds[0] <= value <= bounds[1]:
                self.core.flash(f'{what}: values must be between {bounds[0]} and '
                                f'{bounds[1]}; nothing was changed.', 'warning')
                return
            values[key] = str(value)
        values['text_job_enabled'] = '1' if form.get('text_job_enabled') else '0'
        if 'ai_submitted' in form:
            values['ai_enabled'] = '1' if form.get('ai_enabled') else '0'
            provider = (form.get('ai_provider') or '').strip()
            known = {p['id'] for p in (self._ai_providers() or [])}
            values['ai_provider'] = provider if provider in known else ''
            names = set(self._get_all_categories())
            excluded = [c for c in (form.getlist('ai_excluded') if hasattr(form, 'getlist')
                                    else []) if c in names]
            values['ai_excluded'] = json.dumps(sorted(excluded))[:200]
        for key, value in values.items():
            self._set_config(key, value)

    # ---- AI suggestions for fields and tags ----

    def ai_config(self):
        """{enabled, provider ('' = the AI module's own choice), excluded
        categories, max_chars} from Settings → Documents."""
        try:
            excluded = json.loads(self._get_config('ai_excluded', '[]'))
        except ValueError:
            excluded = []
        try:
            max_chars = int(self._get_config('ai_max_chars', AI_MAX_CHARS_DEFAULT))
        except (TypeError, ValueError):
            max_chars = AI_MAX_CHARS_DEFAULT
        lo, hi = AI_MAX_CHARS_RANGE
        return {'enabled': self._get_config('ai_enabled', '0') == '1',
                'provider': self._get_config('ai_provider', ''),
                'excluded': excluded if isinstance(excluded, list) else [],
                'max_chars': min(max(max_chars, lo), hi)}

    def _ai_functions(self):
        functions = getattr(self.core, 'functions', None)
        if functions is None or not functions.available('ai.complete_text'):
            return None
        return functions

    def _ai_providers(self):
        """The providers the AI module offers (ai.list_providers), or None."""
        functions = self._ai_functions()
        if functions is None or not functions.available('ai.list_providers'):
            return None
        try:
            return functions.call('ai.list_providers')['data']
        except Exception as e:  # pragma: no cover - defensive
            self.logger.warning('ai.list_providers failed: %s', type(e).__name__)
            return None

    def _ai_destination(self):
        """Where a suggestion request goes: {available, name, model, kind,
        reason, provider (passed to ai.complete_text, or None)}. Never falls
        back to another provider than the one the owner chose."""
        cfg = self.ai_config()
        if self._ai_functions() is None:
            return {'available': False, 'name': None, 'model': None, 'kind': None,
                    'provider': None,
                    'reason': 'No AI module is enabled: enable the AI Agent (Settings → Modules).'}
        providers = self._ai_providers()
        if cfg['provider']:
            match = next((p for p in providers or [] if p['id'] == cfg['provider']), None)
            if match is None:
                return {'available': False, 'name': cfg['provider'], 'model': None,
                        'kind': None, 'provider': cfg['provider'],
                        'reason': 'The provider chosen in Settings → Documents is not offered '
                                  'by the AI module now.'}
            return {**{k: match[k] for k in ('name', 'model', 'kind', 'available', 'reason')},
                    'provider': match['id']}
        if providers:
            match = next((p for p in providers if p['selected']), providers[0])
            return {**{k: match[k] for k in ('name', 'model', 'kind', 'available', 'reason')},
                    'provider': None}
        status = self._ai_functions().call('ai.text_status') \
            if self._ai_functions().available('ai.text_status') else {'available': True}
        return {'available': bool(status.get('available')), 'name': status.get('provider'),
                'model': None, 'kind': None, 'reason': status.get('reason'), 'provider': None}

    def _readable_files(self, doc_files):
        """[(file, text)] of the document's PDFs whose text has been read."""
        rows = {r.storage_key: r for r in self.DocumentText.query.filter(
            self.DocumentText.storage_key.in_([f.file_path for f in doc_files]),
            self.DocumentText.status == 'ok').all()} if doc_files else {}
        return [(f, rows[f.file_path]) for f in doc_files if f.file_path in rows]

    def _suggestions(self, doc_id):
        """This document's suggestions, read fresh: the background request
        writes them in another database session."""
        return (self.DocumentAISuggestion.query.execution_options(populate_existing=True)
                .filter_by(document_id=doc_id))

    def _ai_panel(self, doc, doc_files):
        """What the document page shows for AI suggestions."""
        cfg = self.ai_config()
        panel = {'state': 'off', 'files': [], 'destination': None, 'suggestion': None,
                 'max_chars': cfg['max_chars'], 'running': None, 'problem': None,
                 'poll_seconds': AI_POLL_SECONDS}
        running = self._ai_running(doc.id)
        if running:
            panel['running'] = {'provider': running.provider, 'model': running.model,
                                'since': running.created_at}
        last = (self._suggestions(doc.id)
                .filter(self.DocumentAISuggestion.status != 'running')
                .order_by(self.DocumentAISuggestion.created_at.desc()).first())
        if last and last.status in ('failed', 'empty'):
            panel['problem'] = {'provider': last.provider, 'error': last.error,
                                'at': last.decided_at or last.created_at}
        latest = (self._suggestions(doc.id).filter_by(status='new')
                  .order_by(self.DocumentAISuggestion.created_at.desc()).first())
        if latest:
            fields = ai_suggest.from_json(latest.fields)
            panel['suggestion'] = {
                'id': latest.id, 'provider': latest.provider, 'model': latest.model,
                'created_at': latest.created_at,
                'rows': [{'field': f, 'label': ai_suggest.FIELDS[f][0],
                          'current': getattr(doc, f, None), 'value': fields[f],
                          'checked': not getattr(doc, f, None) or f == 'tags'}
                         for f in ai_suggest.FIELDS if f in fields]}
        if not cfg['enabled']:
            return panel
        if self._is_record(doc):
            panel['state'] = 'record'
        elif doc.category and doc.category in cfg['excluded']:
            panel['state'] = 'excluded'
        else:
            readable = self._readable_files(doc_files)
            panel['files'] = [{'id': f.id, 'name': f.original_filename or 'file',
                               'pages': row.page_count, 'chars': len(row.text or '')}
                              for f, row in readable]
            panel['state'] = 'ready' if readable else 'no_text'
            panel['destination'] = self._ai_destination()
        return panel

    def _ai_suggest(self, id):
        """Send the text of the ticked files to the chosen provider and keep
        the checked reply as a suggestion."""
        doc = db_get_or_404(self.Document, id)
        back = redirect(url_for('documents.documents_view', id=doc.id) + '#ai-suggestions')
        refused = self._refuse_if_record(doc)
        if refused:
            return refused
        cfg = self.ai_config()
        if not cfg['enabled']:
            flash('AI suggestions are off. Turn them on in Settings → Documents.', 'warning')
            return back
        if doc.category and doc.category in cfg['excluded']:
            flash(f'Documents of the category "{doc.category}" are never sent to AI '
                  '(Settings → Documents).', 'warning')
            return back
        doc_files = self.DocumentFile.query.filter_by(document_id=doc.id).order_by(
            self.DocumentFile.created_at).all()
        ticked = set()
        for raw in request.form.getlist('ai_file'):
            try:
                ticked.add(int(raw))
            except ValueError:
                continue
        chosen = [(f, row) for f, row in self._readable_files(doc_files) if f.id in ticked]
        if not chosen:
            flash('Tick at least one file whose text may be sent.', 'warning')
            return back
        dest = self._ai_destination()
        if not dest['available']:
            flash(dest['reason'] or 'The AI provider is not available.', 'warning')
            return back
        texts = [row.text for _f, row in chosen]
        categories = self._get_all_categories()
        system, user = ai_suggest.build_prompt(texts, categories, self._get_all_tags(),
                                               cfg['max_chars'])
        arguments = {'system': system, 'user': user, 'max_tokens': ai_suggest.REPLY_TOKENS}
        if dest['provider']:
            arguments['provider'] = dest['provider']
        if self._ai_running(doc.id):
            flash('A request for this document is already running; its answer appears here.',
                  'info')
            return back
        sent = ai_suggest.characters_sent(texts, cfg['max_chars'])
        row = self.DocumentAISuggestion(
            document_id=doc.id, provider=dest['name'], model=dest['model'],
            file_ids=','.join(str(f.id) for f, _ in chosen), chars_sent=sent,
            status='running')
        self._db.session.add(row)
        self._db.session.commit()
        self.core.log_activity('document_sent_to_ai', 'documents',
                               f'{doc.identifier or doc.id}: text of {len(chosen)} file(s), '
                               f'{sent} characters, to {dest["name"]}')
        from flask import current_app
        app = current_app._get_current_object()
        row_id = row.id     # read now: the row object belongs to this request's session
        self._start_background(lambda: self._run_ai_request(app, row_id, arguments, categories))
        flash(f'Asked {dest["name"]}. The suggestions appear on this page when the answer '
              'arrives; this can take a minute or more.', 'info')
        return back

    def _start_background(self, work):
        """Run `work` outside the web request (tests run it at once)."""
        import threading
        threading.Thread(target=work, name='documents-ai-suggest', daemon=True).start()

    def _run_ai_request(self, app, suggestion_id, arguments, categories):
        """Ask the AI and store the checked answer on the running suggestion."""
        with app.app_context():
            row = self._db.session.get(self.DocumentAISuggestion, suggestion_id)
            if row is None:
                return
            status, error, fields = 'failed', AI_UNANSWERED, {}
            try:
                reply = self._ai_functions().call('ai.complete_text', **arguments)
                fields = ai_suggest.clean(ai_suggest.parse_reply(reply), categories)
                status, error = ('new', None) if fields else (
                    'empty', 'The AI found nothing to suggest for this document.')
            except ValueError:
                error = 'The AI reply could not be read as suggestions. Try again.'
            except Exception as e:
                self.logger.warning('AI suggestion for a document failed: %s',
                                    type(e).__name__)
                if type(e).__name__ == 'FunctionUnavailable':
                    error = str(e)[:300]
            if status == 'new':
                self.DocumentAISuggestion.query.filter(
                    self.DocumentAISuggestion.document_id == row.document_id,
                    self.DocumentAISuggestion.status == 'new',
                    self.DocumentAISuggestion.id != row.id).update(
                    {'status': 'discarded', 'decided_at': utcnow()}, synchronize_session=False)
                row.fields = ai_suggest.to_json(fields)
                self._log_history(row.document_id, 'ai_suggested',
                                  f'{len(fields)} field(s) suggested by {row.provider}')
            row.status, row.error, row.decided_at = status, error, utcnow()
            self._db.session.commit()
            self._db.session.remove()

    def _ai_running(self, doc_id):
        """The request for this document still waiting for its answer, or None.
        One older than AI_STALE_MINUTES was cut off (the app restarted) and is
        closed as failed."""
        row = (self._suggestions(doc_id).filter_by(status='running')
               .order_by(self.DocumentAISuggestion.created_at.desc()).first())
        if row is None:
            return None
        if row.created_at and utcnow() - row.created_at > timedelta(minutes=AI_STALE_MINUTES):
            row.status, row.decided_at = 'failed', utcnow()
            row.error = 'No answer arrived (the request was interrupted). Ask again.'
            self._db.session.commit()
            return None
        return row

    def _ai_status(self, id):
        """JSON for the document page while a request runs: {running, status}."""
        from flask import jsonify
        doc = db_get_or_404(self.Document, id)
        running = self._ai_running(doc.id)
        latest = (self._suggestions(doc.id)
                  .order_by(self.DocumentAISuggestion.created_at.desc()).first())
        return jsonify(running=running is not None,
                       status=latest.status if latest else None)

    def _ai_apply(self, id):
        """Apply the ticked suggested fields; tags are added to the existing ones."""
        doc = db_get_or_404(self.Document, id)
        back = redirect(url_for('documents.documents_view', id=doc.id))
        refused = self._refuse_if_record(doc)
        if refused:
            return refused
        suggestion = self.DocumentAISuggestion.query.filter_by(
            id=request.form.get('suggestion_id', type=int), document_id=doc.id,
            status='new').first()
        if suggestion is None:
            flash('That suggestion is no longer open.', 'warning')
            return back
        fields = ai_suggest.from_json(suggestion.fields)
        applied = []
        for field, value in fields.items():
            if not request.form.get(f'apply_{field}'):
                continue
            if field == 'tags':
                current = [t for t in (doc.tags or '').split(',') if t.strip()]
                value = self._clean_tags(','.join(current + [t for t in value
                                                             if t not in current]))
            if field == 'category':
                self._auto_add_category(value)
            setattr(doc, field, value)
            applied.append(ai_suggest.FIELDS[field][0])
        suggestion.status, suggestion.decided_at = 'applied', utcnow()
        if applied:
            self._log_history(doc.id, 'ai_suggestion_applied',
                              f'{", ".join(applied)} (suggested by {suggestion.provider})')
        self._db.session.commit()
        flash(f'Applied: {", ".join(applied)}.' if applied else 'Nothing was applied.',
              'success' if applied else 'info')
        return back

    def _ai_discard(self, id):
        doc = db_get_or_404(self.Document, id)
        self.DocumentAISuggestion.query.filter_by(document_id=doc.id, status='new').update(
            {'status': 'discarded', 'decided_at': utcnow()}, synchronize_session=False)
        self._db.session.commit()
        flash('Suggestion discarded.', 'info')
        return redirect(url_for('documents.documents_view', id=doc.id))

    def _indexed(self):
        return text_index.available(self._db.session)

    def _forget_text(self, storage_key):
        """Drop the text of a stored file that is being deleted."""
        row = self.DocumentText.query.filter_by(storage_key=storage_key).first()
        if row is None:
            return
        if self._indexed():
            text_index.remove(self._db.session, row.id, row.text)
        self._db.session.delete(row)

    def _text_matches(self, query):
        """{document id: snippet} of documents whose file text matches."""
        snippets = text_index.search(self._db.session, query)
        if not snippets:
            return {}
        found = {}
        for doc_id, key in self._db.session.query(
                self.DocumentFile.document_id, self.DocumentFile.file_path).filter(
                self.DocumentFile.file_path.in_(list(snippets))).all():
            found.setdefault(doc_id, snippets[key])
        return found

    @staticmethod
    def _is_pdf(f):
        return ((f.file_format or '').lower() == 'pdf'
                or (f.original_filename or '').lower().endswith('.pdf'))

    def _file_text_states(self, files):
        """{file id: {state, label}} for the PDFs among `files`, one query.
        state: waiting, searchable, scan or failed (TEXT_BADGES)."""
        pdfs = [f for f in files if self._is_pdf(f)]
        if not pdfs:
            return {}
        rows = {r.storage_key: r for r in self.DocumentText.query.filter(
            self.DocumentText.storage_key.in_([f.file_path for f in pdfs])).all()}
        out = {}
        for f in pdfs:
            row = rows.get(f.file_path)
            if row is None:
                state, label = 'waiting', 'Waiting to be read'
            elif row.status == 'error' and (row.attempts or 0) < TEXT_JOB_MAX_ATTEMPTS:
                state, label = 'waiting', 'Could not be fetched, will try again'
            elif row.status == 'ok':
                state = 'searchable'
                label = f'Searchable, {row.page_count} page(s)' + (
                    f', {row.empty_pages} without text' if row.empty_pages else '')
            elif row.status == 'no_text_layer':
                state, label = 'scan', TEXT_STATUS_LABELS['no_text_layer']
            else:
                state = 'failed'
                label = TEXT_STATUS_LABELS.get(row.status, row.status)
            out[f.id] = {'state': state, 'label': label}
        return out

    @staticmethod
    def _document_text_state(states):
        """One badge for a document from its PDFs' states, or None without PDFs:
        waiting while any PDF is; searchable when all are; partly when some are."""
        values = [s['state'] for s in states]
        if not values:
            return None
        if 'waiting' in values:
            key = 'waiting'
        elif all(v == 'searchable' for v in values):
            key = 'searchable'
        elif 'searchable' in values:
            key = 'partly'
        elif all(v == 'scan' for v in values):
            key = 'scan'
        else:
            key = 'failed'
        return {'state': key, **TEXT_BADGES[key]}

    def _documents_text_states(self, files_by_doc):
        """{document id: badge} for the list page."""
        files = [f for fs in files_by_doc.values() for f in fs]
        per_file = self._file_text_states(files)
        out = {}
        for doc_id, fs in files_by_doc.items():
            badge = self._document_text_state([per_file[f.id] for f in fs if f.id in per_file])
            if badge:
                out[doc_id] = badge
        return out

    def _delete_file_and_versions(self, df):
        """Delete a file with its earlier versions; a stored file another
        document still uses is kept."""
        keys = [df.file_path] + [key for (key,) in self._db.session.query(
            self.DocumentFileVersion.file_path).filter_by(file_id=df.id).all()]
        # Versions first: they reference the file (foreign key).
        self.DocumentFileVersion.query.filter_by(file_id=df.id).delete(
            synchronize_session=False)
        self._db.session.delete(df)
        self._db.session.flush()
        for key in dict.fromkeys(keys):
            if key and not self._key_in_use(key):
                self._storage_delete_quiet(key)

    def _replace_file(self, file_id):
        """Upload a new content for a file; the earlier one stays as a version."""
        df = db_get_or_404(self.DocumentFile, file_id)
        refused = self._refuse_if_record(db_get_or_404(self.Document, df.document_id))
        if refused:
            return refused
        upload = request.files.get('file')
        if not upload or not upload.filename:
            flash('Choose the new file.', 'danger')
            return redirect(url_for('documents.documents_view', id=df.document_id))
        try:
            data = upload.read()
            old = df.original_filename or 'file'
            version = self._new_version(df, data, 'replaced', filename=upload.filename)
            self._log_history(df.document_id, 'file_replaced',
                              f'{old} → {df.original_filename}: version {version}, '
                              f'SHA-256 {integrity.short(df.sha256)}')
            self._db.session.commit()
            flash(f'File replaced; version {version - 1} is kept.', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error replacing document file: %s', e)
            flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('documents.documents_view', id=df.document_id))

    def _download_version(self, version_id):
        v = db_get_or_404(self.DocumentFileVersion, version_id)
        df = db_get_or_404(self.DocumentFile, v.file_id)
        if not self.core.storage.exists(v.file_path):
            flash('File not found.', 'danger')
            return redirect(url_for('documents.documents_view', id=df.document_id))
        name = v.original_filename or df.original_filename or 'file'
        stem, dot, ext = name.rpartition('.')
        return self.core.storage.send(
            v.file_path, download_name=f'{stem}_v{v.version}{dot}{ext}' if dot else f'{name}_v{v.version}')

    def _verify_document(self, id):
        """Read every file and earlier version back and compare it with its
        SHA-256 fingerprint. A file stored before fingerprints existed gets
        one now (from then on it is checked)."""
        doc = db_get_or_404(self.Document, id)
        changed, missing, recorded, matched = [], [], [], 0
        items = []
        for df in self.DocumentFile.query.filter_by(document_id=doc.id).all():
            items.append((df, df.original_filename or 'file'))
            for v in self.DocumentFileVersion.query.filter_by(file_id=df.id).all():
                items.append((v, f'{v.original_filename or df.original_filename or "file"} '
                                 f'(version {v.version})'))
        for item, label in items:
            try:
                result = self.core.storage.get(item.file_path)
            except Exception:
                result = None
            if not result:
                missing.append(label)
                continue
            fingerprint = integrity.sha256(result[0])
            if not item.sha256:
                item.sha256 = fingerprint
                recorded.append(label)
            elif item.sha256 != fingerprint:
                changed.append(label)
            else:
                matched += 1
        summary = f'{matched} of {len(items)} file(s) unchanged'
        if changed:
            summary += '; changed since stored: ' + ', '.join(changed)
        if missing:
            summary += '; missing: ' + ', '.join(missing)
        if recorded:
            summary += '; fingerprint recorded now: ' + ', '.join(recorded)
        self._log_history(doc.id, 'integrity_checked', summary)
        self._db.session.commit()
        level = 'danger' if changed or missing else 'success'
        flash(f'Integrity check: {summary}.', level)
        return redirect(url_for('documents.documents_view', id=doc.id))

    def _download_file(self, file_id):
        """Download a single attached file."""
        df = db_get_or_404(self.DocumentFile, file_id)
        if not self.core.storage.exists(df.file_path):
            flash('File not found.', 'danger')
            return redirect(url_for('documents.documents_edit', id=df.document_id))
        return self.core.storage.send(df.file_path,
                                      download_name=df.original_filename)

    def _preview_file(self, file_id):
        """Preview a single attached file inline."""
        df = db_get_or_404(self.DocumentFile, file_id)
        resp = self.core.preview_file(df.file_path, df.original_filename)
        if not resp:
            flash('File not found.', 'danger')
            return redirect(url_for('documents.documents_edit', id=df.document_id))
        return resp

    def _delete_file(self, file_id):
        """Delete a single attached file."""
        df = db_get_or_404(self.DocumentFile, file_id)
        doc_id = df.document_id
        fname = df.original_filename or 'file'
        refused = self._refuse_if_record(db_get_or_404(self.Document, doc_id))
        if refused:
            return refused
        try:
            self._delete_file_and_versions(df)
            self._log_history(doc_id, 'file_removed', fname)
            self._db.session.commit()
            flash('File removed.', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error removing document file: %s', e)
            flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('documents.documents_edit', id=doc_id))

    def _auto_sign_files(self, doc_id, form):
        """Auto-sign PDF files after upload if sign checkboxes were checked."""
        signers = self.core.module_manager.find_capabilities('pdf_sign')
        if not signers:
            return
        selected = [i for i in range(len(signers)) if form.get(f'sign_cap_{i}')]
        if not selected:
            return
        doc_files = self.DocumentFile.query.filter_by(document_id=doc_id).all()
        for df in doc_files:
            if (df.file_format or '').lower() != 'pdf':
                continue
            try:
                result = self.core.storage.get(df.file_path)
                if not result:
                    continue
                pdf_bytes, _ = result
                for idx in selected:
                    cap = signers[idx]
                    pdf_bytes = cap['action'](pdf_bytes)
                self._new_version(df, pdf_bytes, 'signed')
                names = [signers[i].get('name', '') for i in selected]
                self._log_history(doc_id, 'updated',
                                  f'Auto-signed: {df.original_filename} ({", ".join(names)})')
            except Exception as e:
                self.logger.error('Auto-sign failed for %s: %s', df.original_filename, e)
        self._db.session.commit()

    def _sign_file(self, file_id, cap_index):
        """Sign a PDF file using a discovered capability."""
        df = db_get_or_404(self.DocumentFile, file_id)
        doc_id = df.document_id
        refused = self._refuse_if_record(db_get_or_404(self.Document, doc_id))
        if refused:
            return refused

        fmt = (df.file_format or '').lower()
        if fmt != 'pdf':
            flash('Only PDF files can be signed.', 'danger')
            return redirect(url_for('documents.documents_view', id=doc_id))

        # Find signing capabilities
        signers = self.core.module_manager.find_capabilities('pdf_sign')
        if cap_index < 0 or cap_index >= len(signers):
            flash('Signing method not available.', 'danger')
            return redirect(url_for('documents.documents_view', id=doc_id))

        cap = signers[cap_index]
        try:
            result = self.core.storage.get(df.file_path)
            if not result:
                flash('File not found in storage.', 'danger')
                return redirect(url_for('documents.documents_view', id=doc_id))
            pdf_bytes, _ = result

            signed = cap['action'](pdf_bytes)
            self._new_version(df, signed, 'signed')
            self._log_history(doc_id, 'updated',
                              f'File signed ({cap.get("name", "unknown")}): {df.original_filename}')
            self._db.session.commit()
            flash(f'File signed with {cap.get("name", "signer")}.', 'success')
        except Exception as e:
            self.logger.error('Sign file error: %s', e)
            flash('Signing failed. Check server logs.', 'danger')

        return redirect(url_for('documents.documents_view', id=doc_id))

    # ---- category management ----

    def _categories_index(self):
        managed = self.DocumentCategory.query.order_by(
            self.DocumentCategory.name).all()
        # count documents per category
        counts = {}
        for (cat, cnt) in (self._db.session.query(
                self.Document.category, self._db.func.count(self.Document.id))
                .group_by(self.Document.category).all()):
            if cat:
                counts[cat] = cnt
        return render_template('categories.html',
                               categories=managed, counts=counts,
                               common_periods=retention.COMMON_PERIODS,
                               fill_row_color=(self._get_config('fill_row_color', '0') == '1'))

    def _add_category(self):
        name = request.form.get('name', '').strip()
        color = request.form.get('color', '#e2e3e5').strip()
        if not name:
            flash('Category name is required.', 'danger')
            return redirect(url_for('documents.categories_index'))
        existing = self.DocumentCategory.query.filter_by(name=name).first()
        if existing:
            existing.color = color
            self._db.session.commit()
            flash(f'Category "{name}" color updated.', 'success')
            return redirect(url_for('documents.categories_index'))
        self._db.session.add(self.DocumentCategory(name=name, color=color))
        self._db.session.commit()
        flash(f'Category "{name}" added.', 'success')
        return redirect(url_for('documents.categories_index'))

    def _update_category_color(self, id):
        cat = db_get_or_404(self.DocumentCategory, id)
        color = request.form.get('color', '').strip()
        if color:
            cat.color = color
            self._db.session.commit()
        return redirect(url_for('documents.categories_index'))

    def _delete_category(self, id):
        cat = db_get_or_404(self.DocumentCategory, id)
        name = cat.name
        self._db.session.delete(cat)
        self._db.session.commit()
        flash(f'Category "{name}" removed from list. Documents with this category are not affected.', 'info')
        return redirect(url_for('documents.categories_index'))

    def _toggle_row_color(self):
        current = self._get_config('fill_row_color', '0')
        self._set_config('fill_row_color', '0' if current == '1' else '1')
        return redirect(url_for('documents.categories_index'))

    def _duplicate_document(self, id):
        doc = db_get_or_404(self.Document, id)
        new_doc = self.Document(
            name=f'{doc.name} (copy)',
            category=doc.category,
            document_date=date.today(),
            expiry_date=doc.expiry_date,
            amount=doc.amount,
            tags=doc.tags,
            description=doc.description,
            reference_number=doc.reference_number,
            counterparty=doc.counterparty,
            status='active',
            identifier=self._new_identifier(),
            creator=doc.creator,
            language=doc.language,
            rights=doc.rights,
            file_path='_multi_',
            original_filename=doc.original_filename,
            file_size=doc.file_size,
            file_format=doc.file_format,
        )
        self._db.session.add(new_doc)
        self._db.session.flush()
        # Copy attached files references (same storage keys, no file duplication)
        for df in self.DocumentFile.query.filter_by(document_id=doc.id).all():
            new_df = self.DocumentFile(
                document_id=new_doc.id,
                file_path=df.file_path,
                original_filename=df.original_filename,
                file_size=df.file_size,
                file_format=df.file_format,
                sha256=df.sha256,
                version=df.version,
                pdfa=df.pdfa,
            )
            self._db.session.add(new_df)
        self._db.session.commit()
        flash(f'Document duplicated as "{new_doc.name}".', 'success')
        return redirect(url_for('documents.documents_edit', id=new_doc.id))

    def _bulk_action(self):
        action = request.form.get('bulk_action', '')
        ids = request.form.getlist('doc_ids')
        if not ids:
            flash('No documents selected.', 'danger')
            return redirect(url_for('documents.documents_index'))
        ids = [int(i) for i in ids]
        docs = self.Document.query.filter(self.Document.id.in_(ids)).all()
        records = [d for d in docs if self._is_record(d)]
        if records:
            docs = [d for d in docs if not self._is_record(d)]
            flash(f'{len(records)} record(s) left unchanged: records cannot be changed or '
                  'deleted.', 'warning')

        if action == 'delete':
            for doc in docs:
                self._log_disposal(doc, 'deleted')
                self._delete_document_children(doc)
                self._db.session.delete(doc)
            self._db.session.commit()
            flash(f'{len(docs)} document(s) deleted.', 'success')

        elif action == 'set_category':
            cat = request.form.get('bulk_category', '').strip()
            if cat:
                self._auto_add_category(cat)
                for doc in docs:
                    doc.category = cat
                self._db.session.commit()
                flash(f'Category set to "{cat}" for {len(docs)} document(s).', 'success')

        elif action == 'add_tag':
            tag = request.form.get('bulk_tag', '').strip()
            if tag:
                for doc in docs:
                    existing = set(t.strip() for t in (doc.tags or '').split(',') if t.strip())
                    existing.add(tag)
                    doc.tags = ','.join(sorted(existing))
                self._db.session.commit()
                flash(f'Tag "{tag}" added to {len(docs)} document(s).', 'success')

        return redirect(url_for('documents.documents_index'))

    # ---- records and retention (ISO 15489, retention.py) ----

    @staticmethod
    def _is_record(doc):
        return bool(doc.record_declared_at)

    def _refuse_if_record(self, doc):
        """A redirect with the reason when `doc` is a record, else None."""
        if not self._is_record(doc):
            return None
        flash('This document is a record: its details and files cannot be changed or '
              'deleted. Notes can still be added.', 'warning')
        return redirect(url_for('documents.documents_view', id=doc.id))

    def _category_retention(self, name):
        if not name:
            return None
        cat = self.DocumentCategory.query.filter_by(name=name).first()
        return cat.retention_years if cat else None

    def _declare_record(self, id):
        doc = db_get_or_404(self.Document, id)
        if self._is_record(doc):
            return redirect(url_for('documents.documents_view', id=doc.id))
        years = (retention.clean_years(request.form.get('retention_years'))
                 or self._category_retention(doc.category))
        if not years:
            flash('Set how many years to keep it (here or for its category).', 'danger')
            return redirect(url_for('documents.documents_view', id=doc.id))
        doc.record_declared_at = utcnow()
        doc.retain_until = retention.retain_until(years, doc.document_date, date.today())
        recorded = self._fingerprint_missing(doc)
        self._log_history(doc.id, 'declared_record',
                          f'Declared a record, kept until {doc.retain_until:%d/%m/%Y} '
                          f'({years} years)' + (f'; fingerprints taken: {recorded}' if recorded else ''))
        self._db.session.commit()
        flash(f'Declared a record; kept until {doc.retain_until:%d/%m/%Y}.', 'success')
        return redirect(url_for('documents.documents_view', id=doc.id))

    def _fingerprint_missing(self, doc):
        """Take the SHA-256 of files stored before fingerprints existed, so a
        record's files can be verified. Returns how many were taken."""
        taken = 0
        for df in self.DocumentFile.query.filter_by(document_id=doc.id,
                                                    sha256=None).all():
            try:
                result = self.core.storage.get(df.file_path)
            except Exception:
                result = None
            if result:
                df.sha256 = integrity.sha256(result[0])
                taken += 1
        return taken

    def _undeclare_record(self, id):
        doc = db_get_or_404(self.Document, id)
        reason = request.form.get('reason', '').strip()[:300]
        if not reason:
            flash('Say why the record status is removed.', 'danger')
            return redirect(url_for('documents.documents_view', id=doc.id))
        if self._is_record(doc):
            self._log_history(doc.id, 'record_released',
                              f'Record status removed (was kept until '
                              f'{doc.retain_until:%d/%m/%Y}): {reason}' if doc.retain_until
                              else f'Record status removed: {reason}')
            doc.record_declared_at = None
            doc.retain_until = None
            self._db.session.commit()
            flash('The document is no longer a record.', 'success')
        return redirect(url_for('documents.documents_view', id=doc.id))

    def _retention_index(self):
        today = date.today()
        records = self.Document.query.filter(
            self.Document.record_declared_at.isnot(None)).order_by(
            self.Document.retain_until).all()
        due = [d for d in records if retention.is_due(d, today)]
        upcoming = [d for d in records if not retention.is_due(d, today)
                    and d.retain_until and d.retain_until <= today + timedelta(days=365)]
        without_period = [c.name for c in self.DocumentCategory.query.order_by(
            self.DocumentCategory.name).all() if not c.retention_years]
        log = self.DocumentDisposal.query.order_by(
            self.DocumentDisposal.created_at.desc()).limit(200).all()
        return render_template('retention.html', due=due, upcoming=upcoming,
                               record_count=len(records), without_period=without_period,
                               log=log, today=today)

    def _retention_keep(self, id):
        doc = db_get_or_404(self.Document, id)
        years = retention.clean_years(request.form.get('years', '1')) or 1
        if self._is_record(doc) and doc.retain_until:
            old = doc.retain_until
            doc.retain_until = date(max(old.year, date.today().year) + years, 12, 31)
            self._log_history(doc.id, 'retention_extended',
                              f'Kept until {doc.retain_until:%d/%m/%Y} (was {old:%d/%m/%Y})')
            self._db.session.commit()
            flash(f'"{doc.name}" is kept until {doc.retain_until:%d/%m/%Y}.', 'success')
        return redirect(url_for('documents.retention_index'))

    def _retention_dispose(self, id):
        """Dispose of a record whose retention date has passed: the document
        and its files are deleted; the disposal log keeps what it was."""
        doc = db_get_or_404(self.Document, id)
        if not retention.is_due(doc):
            flash('Only a record past its retention date can be disposed of.', 'danger')
            return redirect(url_for('documents.retention_index'))
        try:
            name = doc.name
            self._log_disposal(doc, 'disposed', request.form.get('reason', '').strip()[:300]
                               or 'Retention period ended')
            self._delete_document_children(doc)
            self._db.session.delete(doc)
            self._db.session.commit()
            flash(f'"{name}" was disposed of; the disposal log keeps its record.', 'success')
        except Exception as e:
            self._db.session.rollback()
            self.logger.error('Error disposing of a document: %s', e)
            flash('Error disposing of the document. Please try again.', 'danger')
        return redirect(url_for('documents.retention_index'))

    def _log_disposal(self, doc, action, reason=None):
        """Write what is about to be deleted to the disposal log."""
        files = self.DocumentFile.query.filter_by(document_id=doc.id).all()
        self._db.session.add(self.DocumentDisposal(
            identifier=doc.identifier, name=doc.name, category=doc.category,
            document_date=doc.document_date, was_record=self._is_record(doc),
            retain_until=doc.retain_until, action=action, reason=reason,
            files='\n'.join(f'{f.original_filename or "file"} ({f.sha256 or "no fingerprint"})'
                            for f in files) or None))

    def _update_category_retention(self, id):
        cat = db_get_or_404(self.DocumentCategory, id)
        raw = request.form.get('retention_years', '').strip()
        years = retention.clean_years(raw)
        if raw and years is None:
            flash(f'Enter whole years from 1 to {retention.MAX_YEARS}, or leave it empty.',
                  'danger')
        else:
            cat.retention_years = years
            self._db.session.commit()
        return redirect(url_for('documents.categories_index'))

    def post_retention_messages(self, today=None):
        """A message for each record that reached its retention date
        (`messages.post`, key per record and date, so once). Never raises."""
        functions = self.core.functions
        if not functions.available('messages.post'):
            return 0
        today = today or date.today()
        created = 0
        try:
            for doc in self.Document.query.filter(
                    self.Document.record_declared_at.isnot(None),
                    self.Document.retain_until < today).all():
                name = (doc.name or 'Untitled').replace('[', '(').replace(']', ')')
                text = (f'🗄 Record "{name}" was kept until {doc.retain_until:%d/%m/%Y} — '
                        '[review it](/documents/retention)')
                result = functions.call('messages.post', source=self.module_id, text=text,
                                        level='info',
                                        key=f'retention-due:{doc.id}:{doc.retain_until.isoformat()}')
                created += 1 if result.get('created') else 0
        except Exception:
            self._db.session.rollback()
            self.logger.warning('Could not post the retention messages', exc_info=True)
        return created

    def _daily_messages(self):
        self.post_expiry_messages()
        self.post_retention_messages()

    # ---- notes ----

    def _add_note(self, doc_id):
        title = request.form.get('note_title', '').strip()
        text = request.form.get('note_text', '').strip()
        if title and text:
            note = self.DocumentNote(document_id=doc_id, title=title, text=text)
            self._db.session.add(note)
            self._log_history(doc_id, 'updated', f'Note added: {title}')
            self._db.session.commit()
        return redirect(url_for('documents.documents_view', id=doc_id))

    def _edit_note(self, note_id):
        note = db_get_or_404(self.DocumentNote, note_id)
        title = request.form.get('note_title', '').strip()
        text = request.form.get('note_text', '').strip()
        if title and text:
            note.title = title
            note.text = text
            self._db.session.commit()
        return redirect(url_for('documents.documents_view', id=note.document_id))

    def _delete_note(self, note_id):
        note = db_get_or_404(self.DocumentNote, note_id)
        doc_id = note.document_id
        self._log_history(doc_id, 'updated', f'Note removed: {note.title}')
        self._db.session.delete(note)
        self._db.session.commit()
        return redirect(url_for('documents.documents_view', id=doc_id))

    # ---- expiry alerts as messages (message center) ----

    EXPIRY_WARNING_DAYS = 30   # "expires soon" from this many days before
    EXPIRED_RECENT_DAYS = 30   # "expired" for documents that expired this recently

    def get_dashboard_panels(self):
        # No panel: the alerts are messages in the top-bar bell. Opening the
        # dashboard posts any new ones at once, besides the daily job.
        self._daily_messages()
        return []

    def post_expiry_messages(self, today=None):
        """Post a message for each document that expires within
        EXPIRY_WARNING_DAYS or expired within EXPIRED_RECENT_DAYS, through
        `messages.post`. The key holds the document and its expiry date, so
        each alert is posted once, also after it was read or deleted, and a
        new expiry date gives a new alert. Returns how many were new; never
        raises (it runs from the dashboard and the scheduler)."""
        functions = self.core.functions
        if not functions.available('messages.post'):
            return 0
        today = today or date.today()
        created = 0
        try:
            docs = self.Document.query.filter(
                self.Document.expiry_date.isnot(None),
                self.Document.expiry_date >= today - timedelta(days=self.EXPIRED_RECENT_DAYS),
                self.Document.expiry_date <= today + timedelta(days=self.EXPIRY_WARNING_DAYS),
            ).order_by(self.Document.expiry_date).all()
            for doc in docs:
                name = (doc.name or 'Untitled').replace('[', '(').replace(']', ')')
                when = doc.expiry_date.strftime('%d/%m/%Y')
                link = f'[open it](/documents/view/{doc.id})'
                if doc.expiry_date < today:
                    key, level = f'expired:{doc.id}:{doc.expiry_date.isoformat()}', 'danger'
                    text = f'✕ Document "{name}" expired on {when} — {link}'
                else:
                    key, level = f'expiry-soon:{doc.id}:{doc.expiry_date.isoformat()}', 'warning'
                    text = f'📄 Document "{name}" expires on {when} — {link}'
                result = functions.call('messages.post', source=self.module_id, text=text,
                                        level=level, key=key)
                created += 1 if result.get('created') else 0
        except Exception:
            self._db.session.rollback()
            self.logger.warning('Could not post the document expiry messages', exc_info=True)
        return created

    # ---- report integration ----

    def get_report_sections(self):
        return [{
            'id': 'documents',
            'title': 'Documents',
            'description': 'Documents with amounts. Optionally include attached files as ZIP archive.',
            'selected_default': False,
            'query_fn': self._report_query,
            'columns': [
                {'key': 'date', 'label': 'Date', 'width': 3},
                {'key': 'name', 'label': 'Name', 'width': 5},
                {'key': 'category', 'label': 'Category', 'width': 3},
                {'key': 'amount_eur', 'label': 'Amount (EUR)', 'width': 3},
            ],
            'total_field': 'amount_eur',
            'has_files': True,
            'files_fn': self._report_files,
            'list_fn': self._report_list,
        }]

    def _report_query(self, start_date, end_date, doc_ids=None):
        query = self.Document.query.filter(
            self.Document.amount.isnot(None),
            self.Document.document_date >= start_date,
            self.Document.document_date <= end_date,
        )
        if doc_ids:
            query = query.filter(self.Document.id.in_(doc_ids))
        docs = query.order_by(self.Document.document_date).all()
        return [{
            'date': d.document_date.strftime('%d/%m/%Y') if d.document_date else '',
            'name': d.name,
            'category': d.category or '',
            'amount_eur': d.amount or 0.0,
        } for d in docs]

    def _report_files(self, start_date, end_date, doc_ids=None):
        """Return document files for ZIP archive.

        Args:
            start_date, end_date: date range
            doc_ids: optional list of document IDs to include (None = all)

        Returns list of dicts: {name: str, storage_key: str}
        """
        query = self.Document.query.filter(
            self._db.or_(
                self._db.and_(
                    self.Document.document_date >= start_date,
                    self.Document.document_date <= end_date,
                ),
                self.Document.document_date.is_(None),
            )
        )
        if doc_ids:
            query = query.filter(self.Document.id.in_(doc_ids))
        docs = query.order_by(self.Document.document_date).all()

        files = []
        for doc in docs:
            doc_files = self.DocumentFile.query.filter_by(document_id=doc.id).all()
            date_str = doc.document_date.strftime('%Y%m%d') if doc.document_date else 'nodate'
            safe_name = doc.name.replace('/', '_').replace('\\', '_').replace(' ', '_')
            for df in doc_files:
                ext = df.original_filename.rsplit('.', 1)[-1] if df.original_filename and '.' in df.original_filename else ''
                fname = f"{date_str}_{safe_name}"
                if df.original_filename:
                    fname += f"_{df.original_filename}"
                elif ext:
                    fname += f".{ext}"
                files.append({
                    'name': fname,
                    'storage_key': df.file_path,
                })
        return files

    def _report_list(self, start_date, end_date):
        """Return document list for report file picker (AJAX)."""
        docs = self.Document.query.filter(
            self._db.or_(
                self._db.and_(
                    self.Document.document_date >= start_date,
                    self.Document.document_date <= end_date,
                ),
                self.Document.document_date.is_(None),
            )
        ).order_by(self.Document.document_date).all()
        result = []
        for d in docs:
            file_count = self.DocumentFile.query.filter_by(document_id=d.id).count()
            result.append({
                'id': d.id,
                'name': d.name + (f' ({file_count} files)' if file_count > 0 else ' (no files)'),
                'date': d.document_date.strftime('%d/%m/%Y') if d.document_date else 'No date',
                'category': d.category or '',
                'files': file_count,
            })
        return result
