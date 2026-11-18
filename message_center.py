"""
Message center: short messages for the owner from the core and the modules.

A message appears in the top-bar bell until it is marked as read, and stays in
the history on the Messages page until it is deleted. Modules post through the
registry function `messages.post` (core_functions.py), never through this file.

Content, kept safe for the page:
- `text` is plain text (emoji welcome). `[label](url)` makes a link: an app
  page (`/documents/12`) opens in place, an `http(s)://` URL in a new tab.
  Anything else is shown as typed; nothing in the text is HTML.
- `image` is an optional small picture, at most IMAGE_MAX_PX square: a
  `data:image/...;base64,` URI, checked here, or a path in the app
  (`/static/...`), shown no larger than the limit. External images are refused:
  they would tell another site when the owner opens the app.
- `key`: the poster's deduplication key. One (source, key) is posted once,
  so a message that was read or deleted does not come back. Deleting only hides
  a message; its row keeps the key.

Every change is written to the activity log (category `messages`) with the
message id: posted, read, unread, deleted.
"""
import base64
import binascii
import io
import re
from urllib.parse import urlsplit

from markupsafe import Markup, escape

from functions import InvalidArguments
from timeutil import utcnow

LEVELS = ('info', 'warning', 'danger')
MAX_TEXT = 1000
MAX_KEY = 200
IMAGE_MAX_PX = 64                 # width and height limit of a message picture
IMAGE_MAX_BYTES = 16 * 1024       # decoded size limit of a data: picture
BELL_LIMIT = 10                   # unread messages listed in the bell

_SOURCE = re.compile(r'^[a-z][a-z0-9_]{0,49}$')
_LINK = re.compile(r'\[([^\[\]\n]{1,200})\]\(([^()\s]{1,500})\)')
_DATA_IMAGE = re.compile(r'^data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)$')
_APP_PATH = re.compile(r'^/(?!/)[A-Za-z0-9._~!$&\'*+,;=:@%/?#-]*$')
_PIL_FORMATS = {'png': 'PNG', 'jpeg': 'JPEG', 'gif': 'GIF', 'webp': 'WEBP'}


def link_target(url):
    """(href, external) for a link the text may carry, else None: a path in
    the app, or an absolute http(s) URL."""
    if _APP_PATH.match(url) and '\\' not in url:
        return url, False
    parts = urlsplit(url)
    if parts.scheme in ('http', 'https') and parts.netloc:
        return url, True
    return None


def render_text(text):
    """The message text as safe HTML: everything escaped, `[label](url)`
    turned into a link when the URL is allowed."""
    text = text or ''
    out, pos = [], 0
    for m in _LINK.finditer(text):
        out.append(escape(text[pos:m.start()]))
        target = link_target(m.group(2))
        if target is None:
            out.append(escape(m.group(0)))
        else:
            href, external = target
            extra = Markup(' target="_blank" rel="noopener noreferrer"') if external else Markup('')
            out.append(Markup('<a href="{}"{}>{}</a>').format(href, extra, m.group(1)))
        pos = m.end()
    out.append(escape(text[pos:]))
    return Markup('').join(out)


def check_image(value):
    """`value` if it is an allowed picture, else InvalidArguments. A data URI
    must decode to a PNG, JPEG, GIF or WebP of at most IMAGE_MAX_PX on each
    side and IMAGE_MAX_BYTES; an app path is accepted as given."""
    if not value:
        return None
    m = _DATA_IMAGE.match(value)
    if m:
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise InvalidArguments('image is not valid base64') from None
        if len(raw) > IMAGE_MAX_BYTES:
            raise InvalidArguments(f'image must be at most {IMAGE_MAX_BYTES // 1024} KB')
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(raw)) as img:
                fmt, (w, h) = img.format, img.size
        except (UnidentifiedImageError, OSError):
            raise InvalidArguments('image is not a picture') from None
        if fmt != _PIL_FORMATS[m.group(1)]:
            raise InvalidArguments('image type does not match its content')
        if w > IMAGE_MAX_PX or h > IMAGE_MAX_PX:
            raise InvalidArguments(f'image must be at most {IMAGE_MAX_PX}x{IMAGE_MAX_PX} pixels')
        return value
    if _APP_PATH.match(value) and '\\' not in value and '..' not in value:
        return value
    raise InvalidArguments('image must be a data:image URI or a path in the app')


class MessageCenter:
    """Posting, reading and deleting messages; `model` is the app's Message,
    `log` is CoreServices.log_activity."""

    def __init__(self, db, model, log):
        self._db = db
        self.Message = model
        self._log = log

    # --- Posting ------------------------------------------------------------ #

    def post(self, source, text, level='info', image=None, key=None):
        """(message, created). With `key`, an existing message of the same
        source and key is returned instead of a new one."""
        source = (source or '').strip()
        text = (text or '').strip()
        level = level or 'info'
        if not _SOURCE.match(source):
            raise InvalidArguments('source must be a module id (lower case, digits, _)')
        if not text or len(text) > MAX_TEXT:
            raise InvalidArguments(f'text must have 1 to {MAX_TEXT} characters')
        if level not in LEVELS:
            raise InvalidArguments(f'level must be one of: {", ".join(LEVELS)}')
        if key is not None and (not key.strip() or len(key) > MAX_KEY):
            raise InvalidArguments(f'key must have 1 to {MAX_KEY} characters')
        image = check_image(image)
        if key:
            existing = self.Message.query.filter_by(source=source, dedupe_key=key).first()
            if existing is not None:
                return existing, False
        msg = self.Message(source=source, text=text, level=level, image=image,
                           dedupe_key=key or None)
        self._db.session.add(msg)
        try:
            self._db.session.commit()
        except Exception:
            # Posted by a parallel request between the check and the insert.
            self._db.session.rollback()
            existing = self.Message.query.filter_by(source=source, dedupe_key=key).first()
            if key and existing is not None:
                return existing, False
            raise
        self._event('message_posted', msg)
        return msg, True

    # --- Reading ------------------------------------------------------------ #

    def get(self, message_id):
        """A message that is not deleted, or None."""
        msg = self._db.session.get(self.Message, message_id)
        return msg if msg is not None and msg.deleted_at is None else None

    def _visible(self):
        return self.Message.query.filter(self.Message.deleted_at.is_(None))

    def unread(self, limit=BELL_LIMIT):
        return (self._visible().filter(self.Message.read_at.is_(None))
                .order_by(self.Message.created_at.desc(), self.Message.id.desc())
                .limit(limit).all())

    def unread_count(self):
        return self._visible().filter(self.Message.read_at.is_(None)).count()

    def history(self, unread_only=False, page=1, per_page=50):
        """(messages, total) newest first, deleted ones left out."""
        query = self._visible()
        if unread_only:
            query = query.filter(self.Message.read_at.is_(None))
        total = query.count()
        rows = (query.order_by(self.Message.created_at.desc(), self.Message.id.desc())
                .offset((page - 1) * per_page).limit(per_page).all())
        return rows, total

    # --- Changing ----------------------------------------------------------- #

    def mark_read(self, message_id):
        msg = self.get(message_id)
        if msg is not None and msg.read_at is None:
            msg.read_at = utcnow()
            self._db.session.commit()
            self._event('message_read', msg)
        return msg

    def mark_unread(self, message_id):
        msg = self.get(message_id)
        if msg is not None and msg.read_at is not None:
            msg.read_at = None
            self._db.session.commit()
            self._event('message_unread', msg)
        return msg

    def mark_all_read(self):
        """Mark every unread message as read; returns how many."""
        rows = self._visible().filter(self.Message.read_at.is_(None)).all()
        now = utcnow()
        for msg in rows:
            msg.read_at = now
        self._db.session.commit()
        for msg in rows:
            self._event('message_read', msg)
        return len(rows)

    def delete(self, message_id):
        msg = self.get(message_id)
        if msg is not None:
            msg.deleted_at = utcnow()
            self._db.session.commit()
            self._event('message_deleted', msg)
        return msg

    def _event(self, action, msg):
        """Activity-log entry with the message id; the Logs page links it to
        the history."""
        try:
            self._log(action, 'messages', {'id': msg.id, 'source': msg.source,
                                           'text': msg.text[:80]})
        except Exception:  # pragma: no cover - logging must not break messages
            pass
