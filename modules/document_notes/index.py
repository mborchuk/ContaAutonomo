"""
Document Notes Module
Multiple titled notes per document — for tracking different topics,
questions, or follow-ups separately.
"""

from module_manager import BaseModule
from flask import Blueprint, request, redirect, url_for, render_template_string
from datetime import datetime


class DocumentNotesModule(BaseModule):

    @property
    def module_id(self):
        return 'document_notes'

    @property
    def name(self):
        return 'Document Notes'

    @property
    def description(self):
        return 'Add multiple titled notes to documents for tracking different topics or questions.'

    @property
    def version(self):
        return '1.0.0'

    @property
    def nav_items(self):
        return []

    def register_models(self, db):
        self._db = db

        class DocumentNote(db.Model):
            __tablename__ = 'document_note'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
            title = db.Column(db.String(200), nullable=False)
            text = db.Column(db.Text, nullable=False)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        self.DocumentNote = DocumentNote
        return {'DocumentNote': DocumentNote}

    def register_routes(self, app):
        bp = Blueprint('document_notes', __name__, url_prefix='/document-notes')
        login_required = self.core.login_required
        module = self

        @bp.route('/add/<int:doc_id>', methods=['POST'])
        @login_required
        def add_note(doc_id):
            title = request.form.get('note_title', '').strip()
            text = request.form.get('note_text', '').strip()
            if title and text:
                note = module.DocumentNote(document_id=doc_id, title=title, text=text)
                module._db.session.add(note)
                module._db.session.commit()
                module.core.log_activity('document_note_added', 'document',
                                         f'Note "{title}" on document #{doc_id}')
            return redirect(url_for('documents.documents_view', id=doc_id))

        @bp.route('/edit/<int:note_id>', methods=['POST'])
        @login_required
        def edit_note(note_id):
            note = module.DocumentNote.query.get_or_404(note_id)
            title = request.form.get('note_title', '').strip()
            text = request.form.get('note_text', '').strip()
            if title and text:
                note.title = title
                note.text = text
                module._db.session.commit()
            return redirect(url_for('documents.documents_view', id=note.document_id))

        @bp.route('/delete/<int:note_id>', methods=['POST'])
        @login_required
        def delete_note(note_id):
            note = module.DocumentNote.query.get_or_404(note_id)
            doc_id = note.document_id
            module._db.session.delete(note)
            module._db.session.commit()
            return redirect(url_for('documents.documents_view', id=doc_id))

        app.register_blueprint(bp)

    def get_capabilities(self):
        return [{
            'type': 'document_view_panel',
            'name': 'Document Notes',
            'action': self._render_notes_panel,
        }]

    def _render_notes_panel(self, doc):
        notes = self.DocumentNote.query.filter_by(
            document_id=doc.id
        ).order_by(self.DocumentNote.created_at.desc()).all()
        return render_template_string(NOTES_PANEL, doc=doc, notes=notes)


NOTES_PANEL = '''
<div style="margin-top: 24px; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #eee; padding-bottom: 8px; margin-bottom: 15px;">
        <h3 style="margin: 0; font-size: 16px; color: #5b6bc0;">📝 Notes ({{ notes|length }})</h3>
        <button type="button" onclick="document.getElementById('new-note-form').style.display = document.getElementById('new-note-form').style.display === 'none' ? 'block' : 'none'"
                class="btn btn-primary" style="padding: 4px 12px; font-size: 12px;">+ Add Note</button>
    </div>

    <!-- Add note form (hidden by default) -->
    <form id="new-note-form" method="POST"
          action="{{ url_for('document_notes.add_note', doc_id=doc.id) }}"
          style="display: none; background: #f8f9ff; padding: 14px; border-radius: 6px; border: 1px solid #d0d7ff; margin-bottom: 16px;">
        <div style="margin-bottom: 8px;">
            <input type="text" name="note_title" placeholder="Note title (e.g. Payment status, Legal review)"
                   required style="width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-weight: 500;">
        </div>
        <div style="margin-bottom: 8px;">
            <textarea name="note_text" rows="3" placeholder="Write your note..."
                      required style="width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-family: inherit; resize: vertical;"></textarea>
        </div>
        <div style="display: flex; gap: 6px;">
            <button type="submit" class="btn btn-success" style="padding: 5px 14px; font-size: 12px;">Save</button>
            <button type="button" onclick="this.closest('form').style.display='none'"
                    class="btn btn-secondary" style="padding: 5px 14px; font-size: 12px;">Cancel</button>
        </div>
    </form>

    {% if notes %}
    {% for note in notes %}
    <div style="background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 14px; margin-bottom: 10px;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 6px;">
            <div>
                <strong style="font-size: 14px; color: #333;">{{ note.title }}</strong>
                <span style="font-size: 11px; color: #999; margin-left: 8px;">
                    {{ note.created_at.strftime('%d/%m/%Y %H:%M') }}
                    {% if note.updated_at and note.updated_at != note.created_at %}
                    · edited {{ note.updated_at.strftime('%d/%m/%Y %H:%M') }}
                    {% endif %}
                </span>
            </div>
            <div style="display: flex; gap: 4px; flex-shrink: 0;">
                <button type="button" onclick="var e=this.closest('[data-note]'); e.querySelector('.note-view').style.display='none'; e.querySelector('.note-edit').style.display='block';"
                        style="background: none; border: none; color: #999; cursor: pointer; font-size: 12px; padding: 2px 6px;" title="Edit">✏️</button>
                <form method="POST" action="{{ url_for('document_notes.delete_note', note_id=note.id) }}"
                      style="margin: 0;" onsubmit="return confirm('Delete this note?');">
                    <button type="submit" style="background: none; border: none; color: #ccc; cursor: pointer; font-size: 12px; padding: 2px 6px;" title="Delete">✕</button>
                </form>
            </div>
        </div>

        <div data-note="{{ note.id }}">
            <!-- View mode -->
            <div class="note-view" style="font-size: 13px; color: #555; white-space: pre-wrap; word-break: break-word;">{{ note.text }}</div>

            <!-- Edit mode (hidden) -->
            <form class="note-edit" method="POST" action="{{ url_for('document_notes.edit_note', note_id=note.id) }}"
                  style="display: none;">
                <input type="text" name="note_title" value="{{ note.title }}" required
                       style="width: 100%; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-weight: 500; margin-bottom: 6px;">
                <textarea name="note_text" rows="3" required
                          style="width: 100%; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-family: inherit; resize: vertical; margin-bottom: 6px;">{{ note.text }}</textarea>
                <div style="display: flex; gap: 6px;">
                    <button type="submit" class="btn btn-success" style="padding: 4px 12px; font-size: 11px;">Save</button>
                    <button type="button" onclick="var e=this.closest('[data-note]'); e.querySelector('.note-edit').style.display='none'; e.querySelector('.note-view').style.display='block';"
                            class="btn btn-secondary" style="padding: 4px 12px; font-size: 11px;">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    {% endfor %}
    {% else %}
    <p style="color: #bbb; font-size: 13px; text-align: center; padding: 10px 0;">No notes yet. Click "+ Add Note" to create one.</p>
    {% endif %}
</div>
'''
