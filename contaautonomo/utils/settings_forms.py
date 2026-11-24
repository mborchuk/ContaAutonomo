"""Helpers for settings forms, shared by the core and modules.

Kept free of app and module_manager imports so both can use it without an
import cycle.
"""


def secret_from_form(form, field, current):
    """New value for a stored secret posted from a settings form (C-1).

    Secrets are never rendered back into the page, so an empty field means
    "keep the stored value". The `<field>__clear` checkbox removes it.
    """
    if form.get(f'{field}__clear'):
        return ''
    value = (form.get(field) or '').strip()
    return value if value else current
