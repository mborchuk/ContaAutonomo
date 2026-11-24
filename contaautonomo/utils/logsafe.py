"""Values from a request, made safe for one log line: line breaks and other
control characters become spaces, so a value cannot start a forged entry."""


def clean(value, max_length=200):
    text = '' if value is None else str(value)
    return ''.join(' ' if ord(c) < 32 or ord(c) == 127 else c for c in text)[:max_length]
