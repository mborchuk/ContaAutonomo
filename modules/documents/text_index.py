"""
Full-text index of the text read from stored PDFs (SQLite FTS5).

The text lives in `document_text.text` (one row per stored file, keyed by its
storage key, so duplicated documents share it). `document_text_fts` is an
FTS5 index over that column with external content: it holds only the index,
and this module keeps it in step on every insert and delete. Spanish accents
are folded (`unicode61 remove_diacritics 2`), so "factura electronica" finds
"factura electrónica".

Without FTS5 (another database, or SQLite built without it) `available()` is
False and the search falls back to a substring match on the text.
"""
import re

from markupsafe import Markup, escape
from sqlalchemy import text as sql

FTS = 'document_text_fts'
MAX_TERMS = 10
MAX_HITS = 500
SNIPPET_WORDS = 12
_START, _END = '\x02', '\x03'   # match markers inside snippets
_WORD = re.compile(r'\w+', re.UNICODE)


def create(connection):
    """Create the FTS5 index if it is missing; True when the index exists.
    Called when `document_text` is created and on every start."""
    if connection.dialect.name != 'sqlite':
        return False
    try:
        connection.execute(sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS} USING fts5("
            "text, content='document_text', content_rowid='id', "
            "tokenize='unicode61 remove_diacritics 2')"))
        return True
    except Exception:   # SQLite without FTS5
        return False


def drop(connection):
    if connection.dialect.name == 'sqlite':
        connection.execute(sql(f'DROP TABLE IF EXISTS {FTS}'))


def available(session):
    if session.get_bind().dialect.name != 'sqlite':
        return False
    return session.execute(sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :n"),
        {'n': FTS}).first() is not None


def rebuild(session):
    """Index every stored text again (after the index was created late)."""
    session.execute(sql(f"INSERT INTO {FTS}({FTS}) VALUES('rebuild')"))


def add(session, row_id, body):
    if body:
        session.execute(sql(f'INSERT INTO {FTS}(rowid, text) VALUES (:id, :t)'),
                        {'id': row_id, 't': body})


def remove(session, row_id, body):
    """Remove a row from the index; FTS5 needs the text that was indexed."""
    if body:
        session.execute(sql(f"INSERT INTO {FTS}({FTS}, rowid, text) "
                            "VALUES ('delete', :id, :t)"), {'id': row_id, 't': body})


def match_query(query):
    """The FTS5 query for what the user typed: every word must appear, each
    as a prefix ("fact" finds "factura"). Words only, so no FTS5 syntax
    reaches the index. None when nothing searchable is left."""
    words = _WORD.findall(query or '')[:MAX_TERMS]
    if not words:
        return None
    return ' '.join(f'"{w}"*' for w in words)


def search(session, query):
    """{storage key: snippet} of the stored texts matching `query`, best
    first. The snippet is safe HTML with the matches in <mark>."""
    match = match_query(query)
    if match is None:
        return {}
    if available(session):
        rows = session.execute(sql(
            f"SELECT t.storage_key, snippet({FTS}, 0, :s, :e, '…', :n) "
            f"FROM {FTS} JOIN document_text t ON t.id = {FTS}.rowid "
            f"WHERE {FTS} MATCH :q ORDER BY rank LIMIT :lim"),
            {'s': _START, 'e': _END, 'n': SNIPPET_WORDS, 'q': match,
             'lim': MAX_HITS}).all()
        return {key: _mark(snip) for key, snip in rows}
    like = f'%{query.strip()}%'
    rows = session.execute(sql(
        'SELECT storage_key, text FROM document_text WHERE text LIKE :q LIMIT :lim'),
        {'q': like, 'lim': MAX_HITS}).all()
    return {key: _around(body, query.strip()) for key, body in rows}


def _mark(snippet):
    """Escape the snippet, then turn the markers into <mark> tags."""
    safe = str(escape(snippet or ''))
    return Markup(safe.replace(_START, '<mark>').replace(_END, '</mark>'))


def _around(body, needle, width=80):
    """A plain-text snippet around the first match (fallback search)."""
    body = body or ''
    at = max(0, body.lower().find(needle.lower()))
    stop = at + len(needle)
    start, end = max(0, at - width), min(len(body), stop + width)
    return _mark(('…' if start else '') + body[start:at] + _START + body[at:stop] + _END
                 + body[stop:end] + ('…' if end < len(body) else ''))
