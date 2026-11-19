"""
The core's own registry functions (functions.py): the data the core owns —
invoices, customers, invoice PDFs and messages — declared and implemented here, like a module does in
`get_functions()`. The app passes its models in
(`ModuleManager.register_core_functions`), so this file imports nothing from
app.py.
"""
from functions import Function, InvalidArguments, Param

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


def register(registry, core, Invoice, Customer, message_center=None, invoice_pdf=None):
    """Register the core functions on `registry`; `core` is CoreServices,
    `Invoice`, `Customer` the app's models, `message_center` the
    MessageCenter (message_center.py) and `invoice_pdf` the InvoicePdf
    (invoice_pdf.py), when the app has them."""

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
    if message_center is not None:
        register_messages(registry, message_center)
    if invoice_pdf is not None:
        register_invoice_pdf(registry, core, invoice_pdf)


def register_invoice_pdf(registry, core, invoice_pdf):
    """`invoice.render_pdf`, `invoice.list_pdf_templates` and `invoice.list_blocks`
    (invoice_pdf.py)."""

    def render_pdf(invoice_id):
        invoice = core.invoice_service.get(invoice_id)
        if invoice is None:
            raise InvalidArguments('invoice_id does not match an invoice')
        result = invoice_pdf.render(invoice)
        return {'pdf': result.pdf, 'template': result.template, 'fallback': result.fallback}

    def list_pdf_templates():
        return {'data': [{'id': t['id'], 'name': t['name'], 'module_id': t['module_id']}
                         for t in invoice_pdf.templates()]}

    registry.register_core(Function(
        'invoice.render_pdf', render_pdf,
        "An invoice's PDF in the template chosen in Settings → General, changed by "
        'the modules that adjust invoice PDFs (the Verifactu QR). The default '
        'template is used when the chosen one is not available. Builds the PDF '
        'only; it does not store it.',
        params=(Param('invoice_id', 'int', 'Invoice id', required=True, minimum=1),),
        returns='{"pdf": bytes, "template": str, "fallback": bool}'))
    registry.register_core(Function(
        'invoice.list_pdf_templates', list_pdf_templates,
        'The invoice PDF templates to choose from: the core files in '
        'invoice_templates/ and those of enabled modules (Invoice Designer).',
        returns='{"data": [record]}; record: id, name, module_id ("core" for the core files)'))
    registry.register_core(Function(
        'invoice.list_blocks', lambda: {'data': invoice_pdf.blocks()},
        'The blocks enabled modules add to invoice layouts (the Verifactu QR), '
        'for a module that builds invoice layouts (Invoice Designer). Each block '
        'carries its own render callable, so it is for modules only, not for the '
        'ledger assistant or the API.',
        returns='{"data": [block]}; block: id, label, help, required, slots, '
                'default_slot, options, render (callable), module_id'))


def register_messages(registry, center):
    """`messages.post` and `messages.unread_count` (message_center.py)."""
    from message_center import IMAGE_MAX_PX, LEVELS, MAX_KEY, MAX_TEXT

    def post(source, text, level='info', image=None, key=None):
        msg, created = center.post(source, text, level=level, image=image, key=key)
        return {'id': msg.id, 'created': created}

    registry.register_core(Function(
        'messages.post', post,
        'Show the owner a message in the message center (top-bar bell, Messages page). '
        'The text may hold emoji and [label](url) links to an app page (/...) or an '
        'http(s) URL. With `key`, the same source and key is posted only once, also '
        'after the message was read or deleted.',
        params=(Param('source', 'str', 'Who posts: the module id', required=True,
                      max_length=50),
                Param('text', 'str', 'Message text', required=True, max_length=MAX_TEXT),
                Param('level', 'str', 'How it is shown', choices=LEVELS),
                Param('image', 'str', f'Small picture, at most {IMAGE_MAX_PX}x{IMAGE_MAX_PX}: '
                      'a data:image/png|jpeg|gif|webp;base64 URI or a path in the app',
                      max_length=24_000),
                Param('key', 'str', 'Deduplication key', max_length=MAX_KEY)),
        returns='{"id": int, "created": bool}',
        read_only=False))
    registry.register_core(Function(
        'messages.unread_count', center.unread_count,
        'Number of messages the owner has not read yet.',
        returns='int'))
