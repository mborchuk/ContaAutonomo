from module_manager import BaseModule
from flask import Blueprint, render_template


class HelloModule(BaseModule):
    @property
    def module_id(self):
        return "hello"

    @property
    def name(self):
        return "Hello"

    def register_models(self, db):
        class HelloNote(db.Model):
            __tablename__ = "hello_note"
            id = db.Column(db.Integer, primary_key=True)
            text = db.Column(db.String(200), nullable=False)
        self.HelloNote = HelloNote
        return {"HelloNote": HelloNote}

    def register_routes(self, app):
        bp = Blueprint("hello", __name__, url_prefix="/hello")

        @bp.route("/")
        def index():
            notes = self.HelloNote.query.all()
            return render_template("hello/index.html", notes=notes)

        app.register_blueprint(bp)
