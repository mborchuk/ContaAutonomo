"""
The error a REST API handler raises to answer with a JSON error. In the core,
not in the `api` module, so a module can raise it without importing another
module; `modules/api/index.py` re-exports it.
"""


class ApiError(Exception):
    """Raised inside handlers to short-circuit with a JSON error response.
    `message` is shown to the client, so it never carries exception text."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
