"""
Invoice Comments Module
Internal comments/notes on invoices — not visible on PDF.
"""

from module_manager import BaseModule
from flask import Blueprint, request, redirect, url_for, render_template_string
from datetime import datetime


class InvoiceCommentsModule(BaseModule):

    @property
    def module_id(self):
        return 'invoice_comments'

    @property
    def name(self):
        return 'Invoice Comments'

    @property
    def description(self):
        return 'Add internal comments to invoices for tracking notes, reminders, and communication history.'

    @property
    def version(self):
        return '1.0.0'

    @property
    def nav_items(self):
        return []

    def register_models(self, db):
        self._db = db

        class InvoiceComment(db.Model):
            __tablename__ = 'invoice_comment'
            __table_args__ = {'extend_existing': True}
            id = db.Column(db.Integer, primary_key=True)
            invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
            text = db.Column(db.Text, nullable=False)
            created_at = db.Column(db.DateTime, default=datetime.utcnow)

        self.InvoiceComment = InvoiceComment
        return {'InvoiceComment': InvoiceComment}

    def register_routes(self, app):
        bp = Blueprint('invoice_comments', __name__, url_prefix='/invoice-comments')
        login_required = self.core.login_required
        module = self

        @bp.route('/add/<int:invoice_id>', methods=['POST'])
        @login_required
        def add_comment(invoice_id):
            text = request.form.get('comment_text', '').strip()
            if text:
                comment = module.InvoiceComment(invoice_id=invoice_id, text=text)
                module._db.session.add(comment)
                module._db.session.commit()
                module.core.log_activity('invoice_comment_added', 'invoice',
                                         f'Comment on invoice #{invoice_id}')
            return redirect(url_for('view_invoice', id=invoice_id))

        @bp.route('/delete/<int:comment_id>', methods=['POST'])
        @login_required
        def delete_comment(comment_id):
            comment = module.InvoiceComment.query.get_or_404(comment_id)
            invoice_id = comment.invoice_id
            module._db.session.delete(comment)
            module._db.session.commit()
            return redirect(url_for('view_invoice', id=invoice_id))

        app.register_blueprint(bp)

    def get_invoice_view_panels(self, invoice):
        """Render comments panel for the invoice view page."""
        comments = self.InvoiceComment.query.filter_by(
            invoice_id=invoice.id
        ).order_by(self.InvoiceComment.created_at.desc()).all()

        html = render_template_string(COMMENTS_TEMPLATE,
                                      invoice=invoice,
                                      comments=comments)
        return [html]

    def get_create_form_html(self):
        """Inject comment field into invoice create form."""
        return COMMENT_FORM_HTML

    def get_edit_form_html(self, invoice):
        """Inject comment field into invoice edit form."""
        if self.core.invoice_service.is_locked(invoice):
            return None
        return COMMENT_FORM_HTML

    def on_invoice_created(self, invoice, request):
        """Save initial comment after invoice creation."""
        text = request.form.get('initial_comment', '').strip()
        if text:
            comment = self.InvoiceComment(invoice_id=invoice.id, text=text)
            self._db.session.add(comment)
            self._db.session.commit()

    def on_invoice_updated(self, invoice, request):
        """Save comment added during invoice edit."""
        text = request.form.get('initial_comment', '').strip()
        if text:
            comment = self.InvoiceComment(invoice_id=invoice.id, text=text)
            self._db.session.add(comment)
            self._db.session.commit()


COMMENTS_TEMPLATE = '''
<div style="margin-top: 30px; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px;">
    <h3 style="margin: 0 0 15px 0; font-size: 16px; color: #5b6bc0; border-bottom: 1px solid #eee; padding-bottom: 8px;">
        💬 Internal Comments ({{ comments|length }})
    </h3>
    <p style="color: #999; font-size: 12px; margin-bottom: 15px;">
        Comments are internal only — they won't appear on the invoice PDF.
    </p>

    <form method="POST" action="{{ url_for('invoice_comments.add_comment', invoice_id=invoice.id) }}"
          style="margin-bottom: 20px;">
        <div style="display: flex; gap: 8px;">
            <textarea name="comment_text" rows="2" placeholder="Add a comment..."
                      style="flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 5px;
                             font-size: 13px; font-family: inherit; resize: vertical;"
                      required></textarea>
            <button type="submit" class="btn btn-primary"
                    style="align-self: flex-end; padding: 8px 16px; white-space: nowrap;">
                Add
            </button>
        </div>
    </form>

    {% if comments %}
    <div style="max-height: 400px; overflow-y: auto;">
        {% for c in comments %}
        <div style="padding: 10px 0; {% if not loop.last %}border-bottom: 1px solid #f5f5f5;{% endif %}">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-size: 12px; color: #999;">
                    {{ c.created_at.strftime('%d/%m/%Y %H:%M') }}
                </span>
                <form method="POST"
                      action="{{ url_for('invoice_comments.delete_comment', comment_id=c.id) }}"
                      style="margin: 0;"
                      onsubmit="return confirm('Delete this comment?');">
                    <button type="submit"
                            style="background: none; border: none; color: #ccc; cursor: pointer;
                                   font-size: 12px; padding: 2px 6px;"
                            title="Delete comment">✕</button>
                </form>
            </div>
            <div style="font-size: 13px; color: #333; white-space: pre-wrap; word-break: break-word;">
                {{ c.text }}
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <p style="color: #bbb; font-size: 13px; text-align: center; padding: 10px 0;">
        No comments yet.
    </p>
    {% endif %}
</div>
'''

COMMENT_FORM_HTML = '''
<div style="background: #f0f7ff; padding: 12px 15px; border-radius: 6px; border: 1px solid #b3d4fc; margin-top: 10px;">
    <div style="font-weight: bold; color: #1565c0; margin-bottom: 8px;">💬 Add Comment</div>
    <textarea name="initial_comment" rows="2" placeholder="Optional internal comment (not visible on PDF)..."
              style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;
                     font-size: 13px; font-family: inherit; resize: vertical;"></textarea>
    <div style="color: #888; font-size: 11px; margin-top: 4px;">This comment will be saved with the invoice for internal reference.</div>
</div>
'''
