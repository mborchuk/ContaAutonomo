"""
Output VAT (IVA repercutido) of one invoice, under the Spanish rules.

An issued invoice carries the fiscal snapshot frozen at issue (snap_vat_rate,
snap_taxable_base, snap_vat_amount, snap_customer); legacy and draft invoices
derive the VAT from the customer's tax type and the current rate: only
`standard` customers carry output VAT; `eu_b2b` is an intra-EU reverse charge
and `non_eu` an export. Pure functions, no database queries: the module passes
invoices in. Other modules read these figures through the registry function
`vat.invoice_lines` (Reports shows them in its income section).
"""
from invoice_amounts import customer_tax_type, invoice_base_eur, to_cents


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
            base = invoice_base_eur(invoice) if base is None else to_cents(base)
            return base, to_cents(getattr(invoice, 'snap_vat_amount', None))
        return 0.0, 0.0
    # Legacy / draft: derive from tax_type.
    if customer_tax_type(invoice) == 'standard':
        base = invoice_base_eur(invoice)
        return base, to_cents(base * vat_rate)
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
