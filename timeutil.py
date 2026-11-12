"""Time helpers shared by the core and modules."""
from datetime import datetime, timezone


def utcnow():
    """Current UTC time as a naive datetime.

    Replaces the deprecated ``datetime.utcnow()`` with the same value: the
    database columns store naive UTC timestamps, so existing rows stay
    comparable with new ones.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
