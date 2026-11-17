"""
Output VAT (IVA repercutido) of one invoice — core rules on core data.

An issued invoice carries the fiscal snapshot frozen at issue (snap_vat_rate,
snap_taxable_base, snap_vat_amount, snap_customer); legacy and draft invoices
derive the VAT from the customer's tax type and the current rate. Pure
functions, no database queries: the tax modules and Reports pass invoices in.
"""
import json


def invoice_base_eur(invoice):
    """Taxable base of an invoice in EUR.

    In this app the stored invoice amount IS the taxable base (VAT is derived,
    never added to the total), and amount_eur is always populated.
    """
    return invoice.amount_eur or 0.0


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


def invoice_output_vat(invoice, vat_rate):
    """Return (standard_base_eur, output_cuota_eur) for one invoice.

    Prefers the fiscal snapshot frozen at issue (snap_taxable_base /
    snap_vat_amount / snap_vat_rate); falls back to deriving from the
    customer's tax_type and the current rate for legacy/draft invoices without
    a snapshot. Only standard-rated operations feed the general-regime base;
    eu_b2b / non_eu carry no output VAT.
    """
    snap_rate = getattr(invoice, 'snap_vat_rate', None)
    if snap_rate is not None:
        # Issued invoice with a frozen snapshot.
        if snap_rate and snap_rate > 0:
            base = getattr(invoice, 'snap_taxable_base', None)
            if base is None:
                base = invoice_base_eur(invoice)
            return base, (getattr(invoice, 'snap_vat_amount', None) or 0.0)
        return 0.0, 0.0
    # Legacy / draft: derive from tax_type.
    if customer_tax_type(invoice) == 'standard':
        base = invoice_base_eur(invoice)
        return base, base * vat_rate
    return 0.0, 0.0


def invoice_vat_line(invoice, vat_rate):
    """IVA figures for one invoice row in a report.

    Returns {'vat_rate_pct', 'vat_eur', 'reverse_charge'}. Issued invoices use
    the snapshot frozen at issue; legacy and draft invoices derive the value
    from the customer's tax type and `vat_rate` (a fraction, e.g. 0.21).
    `reverse_charge` is True for intra-EU B2B customers (the customer accounts
    for the VAT).
    """
    _base, cuota = invoice_output_vat(invoice, vat_rate)
    snap_rate = getattr(invoice, 'snap_vat_rate', None)
    if snap_rate is not None:
        rate_pct = snap_rate or 0.0
    else:
        rate_pct = vat_rate * 100.0 if cuota else 0.0
    return {
        'vat_rate_pct': rate_pct,
        'vat_eur': cuota or 0.0,
        'reverse_charge': customer_tax_type(invoice) == 'eu_b2b',
    }
