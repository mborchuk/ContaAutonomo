"""
Contracts of the functions in the registry (functions.py). A contract is the
agreement between the code that calls a function and every implementation of
it — the core's or a module's — so it lives here, in the core, and not in the
module that happens to implement it today.

Change a contract only by adding an optional parameter or a result field.
Anything else is a new version: bump `version` and keep the old behaviour
until every caller has moved.

Results are plain JSON-ready values (dicts, lists, strings, numbers), never
database objects, so an implementation can keep its data however it likes.
"""
from functions import Contract, Param

_DATE_FROM = Param('date_from', 'date', 'Earliest date, YYYY-MM-DD')
_DATE_TO = Param('date_to', 'date', 'Latest date, YYYY-MM-DD')
_PAGE = Param('page', 'int', 'Page number, from 1', minimum=1, maximum=100_000)
_PER_PAGE = Param('per_page', 'int', 'Rows per page', minimum=1, maximum=100)
_YEAR = Param('year', 'int', 'Tax year', required=True, minimum=2000, maximum=2100)
_QUARTER = Param('quarter', 'int', 'Quarter, 1-4', required=True, minimum=1, maximum=4)

_PAGED = ('{"data": [record], "page": int, "per_page": int, "total": int}')

_CONTRACTS = (
    # --- Ledger data ------------------------------------------------------- #
    Contract(
        'ledger.list_invoices', 1,
        'Invoices issued to customers, newest first. Filters: date_from/date_to '
        '(invoice date), client (part of the customer name), status (draft, '
        'pending, issued, paid, cancelled).',
        params=(_DATE_FROM, _DATE_TO,
                Param('client', 'str', 'Part of the customer name', max_length=100),
                Param('status', 'str', 'Invoice status', max_length=20),
                _PAGE, _PER_PAGE),
        returns=_PAGED + '; record: id, invoice_number, client_name, customer_id, '
                'amount_usd, amount_eur, currency, exchange_rate, invoice_date, due_date, '
                'status, has_pdf',
        assistant=True, record_kind='invoice',
        assistant_fields=('id', 'invoice_number', 'invoice_date', 'client_name',
                          'amount_eur', 'currency', 'status')),
    Contract(
        'ledger.list_customers', 1,
        'Customer list (name, country, VAT treatment).',
        returns='{"data": [record]}; record: id, name, vat_number, country, email, '
                'tax_type, is_default',
        assistant=True, record_kind='customer',
        assistant_fields=('id', 'name', 'country', 'tax_type')),
    Contract(
        'ledger.list_expenses', 1,
        'Business expenses, newest first. Filters: date_from/date_to (expense date), '
        'category (exact name, e.g. Software, Travel).',
        params=(_DATE_FROM, _DATE_TO,
                Param('category', 'str', 'Expense category, exact name', max_length=100),
                _PAGE, _PER_PAGE),
        returns=_PAGED + '; record: id, contractor_id, amount, currency, category, '
                'description, expense_date, invoice_number',
        assistant=True, record_kind='expense',
        assistant_fields=('id', 'expense_date', 'category', 'description', 'amount',
                          'currency')),

    # --- Spanish tax drafts ----------------------------------------------- #
    Contract(
        'tax.draft_303', 1,
        'Modelo 303 (IVA) draft boxes for one quarter. Needs year and quarter (1-4).',
        params=(_YEAR, _QUARTER),
        returns='{"form": "303", "year": int, "quarter": int, "boxes": {box: amount}, ...}',
        assistant=True, record_kind='draft',
        assistant_fields=('form', 'year', 'quarter', 'boxes')),
    Contract(
        'tax.draft_130', 1,
        'Modelo 130 (IRPF) draft boxes for one quarter. Needs year and quarter (1-4).',
        params=(_YEAR, _QUARTER),
        returns='{"form": "130", "year": int, "quarter": int, "boxes": {box: amount}, ...}',
        assistant=True, record_kind='draft',
        assistant_fields=('form', 'year', 'quarter', 'boxes')),

    # --- AI ---------------------------------------------------------------- #
    Contract(
        'ai.text_status', 1,
        'Whether text questions can be answered, by which provider, and what to '
        'configure if not.',
        returns='{"available": bool, "provider": str or null, "reason": str or null}'),
    Contract(
        'ai.complete_text', 1,
        'The AI provider\'s reply to `user` under the `system` instruction.',
        params=(Param('system', 'str', 'Instruction', required=True, max_length=20_000),
                Param('user', 'str', 'Message', required=True, max_length=400_000),
                Param('max_tokens', 'int', 'Reply length limit', minimum=1, maximum=16_000)),
        returns='str', external=True),
    Contract(
        'ai.parse_document', 1,
        'The fields of an invoice or receipt read from a PDF or an image.',
        params=(Param('file_data', 'bytes', 'The file', required=True),
                Param('filename', 'str', 'Original file name', required=True, max_length=255),
                Param('doc_type', 'str', 'What the file is', choices=('invoice', 'expense'))),
        returns='{field: value}; fields per doc_type in modules/ai_parser/README.md',
        external=True),
)

CONTRACTS = {c.name: c for c in _CONTRACTS}
