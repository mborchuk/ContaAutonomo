"""Flask extensions shared by the core and its models, created without an app
and bound to it in app.py (`db.init_app(app)`)."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
