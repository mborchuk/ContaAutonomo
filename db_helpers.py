"""Primary-key lookups for Flask-SQLAlchemy models.

Replace the legacy ``Model.query.get()`` / ``Model.query.get_or_404()``
(deprecated in SQLAlchemy 2) in code that has a model class but no direct
handle on the ``db`` object, such as modules.
"""
from flask import abort


def db_get(model, ident):
    """The row with primary key `ident`, or None."""
    return model.query.session.get(model, ident)


def db_get_or_404(model, ident):
    """The row with primary key `ident`; aborts with 404 when missing."""
    obj = db_get(model, ident)
    if obj is None:
        abort(404)
    return obj
