"""
The core's own registry functions (functions.py): the data the core owns —
invoices and customers — declared and implemented here, like a module does in
`get_functions()`. The app passes its models in
(`ModuleManager.register_core_functions`), so this file imports nothing from
app.py.
"""
from functions import Function, Param

DATE_FROM = Param('date_from', 'date', 'Earliest date, YYYY-MM-DD')
DATE_TO = Param('date_to', 'date', 'Latest date, YYYY-MM-DD')
PAGE = Param('page', 'int', 'Page number, from 1', minimum=1, maximum=100_000)
PER_PAGE = Param('per_page', 'int', 'Rows per page', minimum=1, maximum=100)
PAGED = '{"data": [record], "page": int, "per_page": int, "total": int}'


def serialize_invoice(inv, invoice_service):
    return {
        'id': inv.id,
        'invoice_number': inv.invoice_number,
        'client_name': inv.client_name,
        'customer_id': inv.customer_id,
        'amount_usd': inv.amount_usd,
        'amount_eur': inv.amount_eur,
        'currency': inv.currency,
        'exchange_rate': inv.exchange_rate,
        'invoice_date': inv.invoice_date.isoformat() if inv.invoice_date else None,
        'due_date': inv.due_date.isoformat() if inv.due_date else None,
        'status': inv.status,
        'has_pdf': invoice_service.has_pdf(inv),
    }


def serialize_customer(c):
    return {
        'id': c.id,
        'name': c.name,
        'vat_number': c.vat_number,
        'country': c.country,
        'email': c.email,
        'tax_type': c.tax_type,
        'is_default': bool(c.is_default),
    }


def register(registry, core, Invoice, Customer):
    """Register the core functions on `registry`; `core` is CoreServices and
    `Invoice`, `Customer` the app's models."""

    def list_invoices(date_from=None, date_to=None, client=None, status=None,
                      page=1, per_page=20):
        query = Invoice.query
        if status:
            query = query.filter_by(status=status)
        if date_from:
            query = query.filter(Invoice.invoice_date >= date_from)
        if date_to:
            query = query.filter(Invoice.invoice_date <= date_to)
        if client:
            query = query.filter(Invoice.client_name.ilike(f'%{client}%'))
        total = query.count()
        rows = (query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
                .offset((page - 1) * per_page).limit(per_page).all())
        svc = core.invoice_service
        return {'data': [serialize_invoice(inv, svc) for inv in rows],
                'page': page, 'per_page': per_page, 'total': total}

    def list_customers():
        return {'data': [serialize_customer(c)
                         for c in Customer.query.order_by(Customer.name).all()]}

    registry.register_core(Function(
        'ledger.list_invoices', list_invoices,
        'Invoices issued to customers, newest first. Filters: date_from/date_to '
        '(invoice date), client (part of the customer name), status (draft, '
        'pending, issued, paid, cancelled).',
        params=(DATE_FROM, DATE_TO,
                Param('client', 'str', 'Part of the customer name', max_length=100),
                Param('status', 'str', 'Invoice status', max_length=20),
                PAGE, PER_PAGE),
        returns=PAGED + '; record: id, invoice_number, client_name, customer_id, '
                'amount_usd, amount_eur, currency, exchange_rate, invoice_date, due_date, '
                'status, has_pdf',
        assistant=True, record_kind='invoice',
        assistant_fields=('id', 'invoice_number', 'invoice_date', 'client_name',
                          'amount_eur', 'currency', 'status')))
    registry.register_core(Function(
        'ledger.list_customers', list_customers,
        'Customer list (name, country, VAT treatment).',
        returns='{"data": [record]}; record: id, name, vat_number, country, email, '
                'tax_type, is_default',
        assistant=True, record_kind='customer',
        assistant_fields=('id', 'name', 'country', 'tax_type')))
