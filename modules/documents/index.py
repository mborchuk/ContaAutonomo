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
import os
from db_helpers import db_get, db_get_or_404
from timeutil import utcnow

from . import dublin_core, integrity


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
        return '0.7.0'

    @property
    def nav_items(self):
        return [
            {'label': 'All Documents', 'endpoint': 'documents.documents_index', 'icon': '📁',
             'group': 'Documents'},
            {'label': 'Categories', 'endpoint': 'documents.categories_index', 'icon': '🏷️',
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
            created_at = db.Column(db.DateTime, default=utcnow)
            updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

        class DocumentCategory(db.Model):
            __tablename__ = 'document_category'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(100), unique=True, nullable=False)
            color = db.Column(db.String(7), default='#e2e3e5')

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

        self.Document = Document
        self.DocumentCategory = DocumentCategory
        self.DocumentFile = DocumentFile
        self.DocumentFileVersion = DocumentFileVersion
        self.DocumentConfig = DocumentConfig
        self.DocumentHistory = DocumentHistory
        self.DocumentNote = DocumentNote
        return {'DocumentCategory': DocumentCategory, 'DocumentFile': DocumentFile,
                'DocumentFileVersion': DocumentFileVersion,
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
                job_id='documents.expiry_messages', func=self.post_expiry_messages,
                job_type='daily', time_str='07:30',
                description='Messages for documents about to expire or just expired',
                timeout=120)
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error('Failed to register the expiry-messages job: %s', e)

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
        return render_template('document_view.html', doc=doc, doc_files=doc_files,
                               versions=versions, short=integrity.short,
                               history=history, notes=notes,
                               category_colors=category_colors,
                               today=date.today())

    def _list_documents(self):
        query = self.Document.query

        q = request.args.get('q', '').strip()
        if q:
            like = f'%{q}%'
            query = query.filter(
                self.Document.name.ilike(like) |
                self.Document.description.ilike(like) |
                self.Document.tags.ilike(like) |
                self.Document.identifier.ilike(like) |
                self.Document.reference_number.ilike(like)
            )

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

    def _delete_document(self, id):
        doc = db_get_or_404(self.Document, id)
        try:
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

        if action == 'delete':
            for doc in docs:
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
        self.post_expiry_messages()
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
