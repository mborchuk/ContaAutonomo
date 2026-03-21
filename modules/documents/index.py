#!/usr/bin/env python3
"""
Documents Module
Store and manage documents from various sources (tax authority, social security, etc.)
with search, filtering, and file download.
"""

from module_manager import BaseModule
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash)
from datetime import datetime


class DocumentsModule(BaseModule):

    @property
    def module_id(self):
        return 'documents'

    @property
    def name(self):
        return 'Documents'

    @property
    def description(self):
        return 'Store and manage documents from tax authority, social security, bank, and other sources'

    @property
    def version(self):
        return '0.1.0'

    @property
    def nav_items(self):
        return [
            {'label': 'Documents', 'endpoint': 'documents.documents_index', 'icon': '📁'}
        ]

    def register_models(self, db):
        self._db = db

        class Document(db.Model):
            __tablename__ = 'document'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(300), nullable=False)
            source = db.Column(db.String(100))
            document_date = db.Column(db.Date)
            description = db.Column(db.Text)
            file_path = db.Column(db.String(500), nullable=False)
            original_filename = db.Column(db.String(300))
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.Document = Document
        return {}  # Table exists in core

    def register_routes(self, app):
        bp = Blueprint('documents', __name__,
                       template_folder='templates',
                       url_prefix='/documents')
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

        @bp.route('/<int:id>/edit', methods=['GET', 'POST'])
        @login_required
        def documents_edit(id):
            return module._edit_document(id)

        @bp.route('/<int:id>/download')
        @login_required
        def documents_download(id):
            return module._download_document(id)

        @bp.route('/<int:id>/delete', methods=['POST'])
        @login_required
        def documents_delete(id):
            return module._delete_document(id)

        app.register_blueprint(bp)

    # --- Business Logic ---

    def _list_documents(self):
        q = request.args.get('q', '').strip()
        source = request.args.get('source', '').strip()
        year = request.args.get('year', type=int)

        query = self.Document.query
        if q:
            query = query.filter(
                self._db.or_(
                    self.Document.name.ilike(f'%{q}%'),
                    self.Document.description.ilike(f'%{q}%')
                )
            )
        if source:
            query = query.filter_by(source=source)
        if year:
            query = query.filter(
                self._db.extract('year', self.Document.document_date) == year
            )

        documents = query.order_by(self.Document.document_date.desc()).all()

        sources = [r[0] for r in self._db.session.query(
            self.Document.source
        ).distinct().filter(self.Document.source.isnot(None)).all()]

        years_query = self._db.session.query(
            self._db.extract('year', self.Document.document_date)
        ).distinct().filter(self.Document.document_date.isnot(None)).all()
        years = sorted([int(y[0]) for y in years_query if y[0]], reverse=True)

        return render_template('documents.html',
                             documents=documents, sources=sources, years=years)

    def _create_document(self):
        if request.method == 'POST':
            try:
                file = request.files.get('file')
                if not file or not file.filename:
                    flash('Please select a file to upload.', 'danger')
                    return redirect(url_for('documents.documents_create'))

                from werkzeug.utils import secure_filename
                original_filename = file.filename
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                safe_name = secure_filename(original_filename)
                filename = f"{timestamp}_{safe_name}"
                file_path = self.core.save_file(file, 'documents_files', filename)

                doc_date = None
                date_str = request.form.get('document_date')
                if date_str:
                    doc_date = datetime.strptime(date_str, '%Y-%m-%d').date()

                doc = self.Document(
                    name=request.form['name'],
                    source=request.form.get('source') or None,
                    document_date=doc_date,
                    description=request.form.get('description') or None,
                    file_path=file_path,
                    original_filename=original_filename
                )
                self._db.session.add(doc)
                self._db.session.commit()
                flash('Document added successfully!', 'success')
                return redirect(url_for('documents.documents_index'))
            except Exception as e:
                self._db.session.rollback()
                flash(f'Error adding document: {str(e)}', 'danger')

        return render_template('document_form.html', document=None)

    def _edit_document(self, id):
        doc = self.Document.query.get_or_404(id)
        if request.method == 'POST':
            try:
                doc.name = request.form['name']
                doc.source = request.form.get('source') or None
                doc.description = request.form.get('description') or None
                date_str = request.form.get('document_date')
                doc.document_date = (
                    datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
                )

                file = request.files.get('file')
                if file and file.filename:
                    from werkzeug.utils import secure_filename
                    original_filename = file.filename
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    safe_name = secure_filename(original_filename)
                    filename = f"{timestamp}_{safe_name}"
                    new_path = self.core.save_file(file, 'documents_files', filename)
                    self.core.delete_file(doc.file_path)
                    doc.file_path = new_path
                    doc.original_filename = original_filename

                self._db.session.commit()
                flash('Document updated successfully!', 'success')
                return redirect(url_for('documents.documents_index'))
            except Exception as e:
                self._db.session.rollback()
                flash(f'Error updating document: {str(e)}', 'danger')

        return render_template('document_form.html', document=doc)

    def _download_document(self, id):
        doc = self.Document.query.get_or_404(id)
        return self.core.send_file(doc.file_path, doc.original_filename)

    def _delete_document(self, id):
        doc = self.Document.query.get_or_404(id)
        try:
            self.core.delete_file(doc.file_path)
            self._db.session.delete(doc)
            self._db.session.commit()
            flash('Document deleted successfully!', 'success')
        except Exception as e:
            self._db.session.rollback()
            flash(f'Error deleting document: {str(e)}', 'danger')
        return redirect(url_for('documents.documents_index'))
