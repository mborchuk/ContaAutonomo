"""
The core's own implementations of registry functions (contracts.py): the
data the core owns — invoices and customers. The app passes its models in
(`ModuleManager.register_core_functions`), so this file imports nothing from
app.py.
"""


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

    registry.register_core('ledger.list_invoices', list_invoices)
    registry.register_core('ledger.list_customers', list_customers)
