"""
Amounts of one invoice as the books record them — core data, no tax rules.

The amount in EUR rounded to the cent, and the customer's tax type saved at
issue (snap_customer) or, for drafts, the customer's current one. Pure
functions, no database queries: modules pass invoices in. What a tax type
means for VAT (output VAT, reverse charge) belongs to the tax module of the
country, not to the core.
"""
import json
from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount):
    """`amount` rounded to the cent, half up — as the books record it. A
    converted invoice stores its EUR amount with many decimals (for example 45.1234567);
    tax figures add the per-invoice cents, never the raw values."""
    return float(Decimal(str(amount or 0.0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def invoice_base_eur(invoice):
    """Taxable base of an invoice in EUR.

    In this app the stored invoice amount IS the taxable base (VAT is derived,
    never added to the total), and amount_eur is always populated. Rounded to
    the cent (`to_cents`).
    """
    return to_cents(invoice.amount_eur)


def customer_tax_type(invoice):
    """VAT treatment of the invoice's customer: the snapshot frozen at issue,
    else the customer's current setting."""
    snap = getattr(invoice, 'snap_customer', None)
    if snap:
        try:
            tax_type = json.loads(snap).get('tax_type')
            if tax_type:
                return tax_type
        except (ValueError, AttributeError):
            pass  # malformed snapshot: fall back to the customer
    customer = getattr(invoice, 'customer', None)
    return getattr(customer, 'tax_type', None) if customer else None
