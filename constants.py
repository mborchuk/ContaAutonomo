"""Centralized constants — avoid magic numbers/strings scattered across the app.

Import from here rather than re-declaring values inline.
"""

# --- Tax defaults (%) ---
DEFAULT_VAT_RATE = 21.0       # Spain default; Tax Poland module overrides to 23
DEFAULT_IRPF_RATE = 20.0      # Income tax advance

# --- Sessions / uploads ---
SESSION_LIFETIME_SECONDS = 3600          # 1 hour
MAX_CONTENT_LENGTH_BYTES = 50 * 1024 * 1024  # 50 MB upload cap

# --- Pagination ---
DOCUMENTS_PER_PAGE = 50
INVOICES_PER_PAGE = 20
API_DEFAULT_PER_PAGE = 20
API_MAX_PER_PAGE = 100

# --- API rate limits (requests/minute) ---
API_READ_LIMIT = '60/minute'
API_WRITE_LIMIT = '20/minute'
API_HEAVY_LIMIT = '10/minute'

# --- ECB feed cache ---
ECB_CACHE_TTL_SECONDS = 14400  # 4 hours

# --- Storage subfolders (storage keys are built from these) ---
FILE_DIRS = {
    'invoices': 'invoices_pdf',
    'expenses': 'expenses_files',
    'documents': 'documents_files',
    'tax_forms': 'tax_forms',
    'logos': 'invoice_logos',
    'signatures': 'pdf_signature_files',
    'backups': 'backups',
}
